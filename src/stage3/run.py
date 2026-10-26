"""run.py — 阶段三双工位 runner 与 CLI (06-12 §4–§5).

流程:
  生命周期 load(stage3) → prepare_start (启动点校验)
    → 构建两台 IK/影子臂/碰撞集合/吸盘
    → 生成 1 个物料 UID, 提交 A 装载 / AGV 转运 / B 卸载任务
    → 协调器逐步执行, 每步只 `sim.step()` 一次
    → 收集 UID/交接/资源/AGV/动作质量证据
    → 停止仿真, 销毁 Shadow_* 会话资源, 写 JSON 报告.

本入口不 build、不清场; stage3 工件必须先由
`run_scene_lifecycle.py build --phase stage3 --parent <stage2>` 派生并保存.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from stage1 import config as base
from stage1 import physics as phys
from stage1.run import (connect, probe_conveyor_drive,
                        setup_logger as setup_stage1_logger,
                        start_stepping, stop_simulation)
from stage1.scene import alias_of
from stage1.unit_controller import UnitTaskState
from stage1.ur5_ik_controller import Ur5IkController
from stage2.suction import Stage2SuctionController

from . import config as cfg
from .coordinator import Stage3Coordinator
from .runtime import build_stage3_runtime

REPORT_DIR = os.path.join("docs", "implementation", "runs")


def setup_logger(path: str) -> logging.Logger:
    logger = setup_stage1_logger(path)
    logger.name = "stage3"
    return logger


# ═══════════════════════════════════════════════════════════════
# 组件
# ═══════════════════════════════════════════════════════════════

def build_components_stage3(sim, simIK, scene, logger):
    """构建共享组件 + 两台独立 IK/吸盘 + 阶段三运行时."""
    from stage1.run import build_components as build_stage1_components

    comp = build_stage1_components(sim, simIK, scene, logger)
    ik_by_unit = {"A": comp["ik"]}
    unit_b = scene.unit("B")
    ik_b = Ur5IkController(
        sim, simIK, unit_b.robot_root, unit_b.tcp, unit_b.ik_target,
        unit_b.robot_joints)
    ik_b.initialize()
    ik_by_unit["B"] = ik_b

    runtime = build_stage3_runtime(sim, scene, ik_by_unit, logger=logger)
    release_parent = scene.handle(base.ALIASES["roots"][4])
    suction = {}
    for unit_id in ("A", "B"):
        unit = scene.unit(unit_id)
        suction[unit_id] = Stage2SuctionController(
            sim, comp["materials"], tcp_handle=unit.tcp,
            sensor_handle=unit.suction_sensor,
            link_handle=unit.suction_link,
            release_parent=release_parent, logger=logger,
            pad_handle=unit.suction_root,
            cup_radius=runtime.arm(unit_id).cup_radius)
    comp["ik"] = ik_by_unit
    comp["suction"] = suction
    comp["runtime"] = runtime
    comp["scene"] = scene
    return comp


# ═══════════════════════════════════════════════════════════════
# 证据
# ═══════════════════════════════════════════════════════════════

def _material_evidence(sim, materials, uid: int) -> dict:
    rec = materials.by_uid(uid)
    if rec is None or rec.sim_handle is None:
        return {"uid": uid, "registered": rec is not None,
                "handle": None, "state": rec.state.value if rec else None}
    try:
        pose = [round(float(v), 6) for v in sim.getObjectPose(
            rec.sim_handle, sim.handle_world)]
        parent = int(sim.getObjectParent(rec.sim_handle))
        parent_alias = alias_of(sim, parent) if parent >= 0 else ""
        physics = phys.snapshot_physics(sim, rec.sim_handle)
    except Exception as exc:
        return {"uid": uid, "handle": rec.sim_handle,
                "state": rec.state.value,
                "error": f"{type(exc).__name__}: {exc}"}
    return {
        "uid": uid,
        "alias": rec.alias,
        "handle": rec.sim_handle,
        "state": rec.state.value,
        "pose": pose,
        "parent": parent,
        "parent_alias": parent_alias,
        "physics": physics,
        "dynamic_ok": physics.get("static") == 0,
        "respondable_ok": physics.get("respondable") == 1,
        "history": [
            {"sim_time": round(float(e.sim_time), 4),
             "from": e.from_state.value, "to": e.to_state.value,
             "reason": e.reason}
            for e in rec.history],
    }


def _agv_evidence(sim, agv, controller=None) -> dict:
    try:
        pose = [round(float(v), 6) for v in agv.pose()]
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    return {
        "pose": pose,
        "state": agv.state.name,
        "left_wheel": round(float(agv.last_speeds.left), 4),
        "right_wheel": round(float(agv.last_speeds.right), 4),
        "is_stopped": bool(agv.is_stopped()),
        "is_upright": bool(agv.is_upright()),
    }


def collect_scene_snapshot(scene, comp) -> dict:
    return {
        "layout": scene.stage3_layout.as_dict(),
        "units": {uid: scene.unit(uid).as_dict() for uid in ("A", "B")},
        "tools": {uid: scene.tool_geometry_for(uid) for uid in ("A", "B")},
        "build_report_stage3": scene.build_report.get("stage3", {}),
        "runtime": comp["runtime"].as_dict(),
        "conveyor": dict(scene.conveyor_meta or {}),
    }


# ═══════════════════════════════════════════════════════════════
# 场景执行
# ═══════════════════════════════════════════════════════════════

def run_scenario_stage3(sim, scene, comp, *, scenario: str,
                        max_sim_time: float, logger,
                        fault: str = cfg.FAULT_NONE,
                        stop_event=None, pause_event=None,
                        pause_ack_event=None, on_start=None) -> dict:
    """在已加载/已建组件的 stage3 场景上运行一个验收场景."""
    if scenario not in cfg.STAGE3_SCENARIOS:
        raise ValueError(f"未知 scenario: {scenario!r}")
    # 场景自带默认故障; 显式传入非 none 的故障优先 (便于单变量实验).
    effective_fault = (fault if fault in cfg.STAGE3_FAULT_CHOICES
                       and fault != cfg.FAULT_NONE
                       else cfg.SCENARIO_FAULT[scenario])
    materials = comp["materials"]
    uid = 1
    material = scene.spawn_material(uid, f"Part_{uid:04d}")
    materials.register(uid, f"Part_{uid:04d}", sim_handle=material)
    logger.info("=" * 70)
    logger.info("[TASK][stage3] scenario=%s fault=%s uid=%s", scenario,
                effective_fault, uid)

    coordinator = Stage3Coordinator(
        sim, scene, comp, scenario=scenario, fault=effective_fault,
        logger=logger,
        on_controller=(lambda unit_id, controller: on_start(
            coordinator) if on_start else None) if on_start else None)
    coordinator.start(float(sim.getSimulationTime()))
    if on_start is not None:
        on_start(coordinator)

    task_start = float(sim.getSimulationTime())
    wall_deadline = time.time() + max(300.0, max_sim_time * 10.0)
    steps = 0
    while not coordinator.finished:
        if stop_event is not None and stop_event.is_set():
            coordinator._abort("STOP_REQUESTED")
            break
        if pause_event is not None and pause_event.is_set():
            if pause_ack_event is not None:
                pause_ack_event.set()
            time.sleep(0.01)
            continue
        if pause_ack_event is not None:
            pause_ack_event.clear()
        coordinator.step()
        steps += 1
        if float(sim.getSimulationTime()) - task_start > max_sim_time:
            coordinator._abort(f"SIM_TIME_LIMIT: {max_sim_time:.1f}s")
            break
        if time.time() > wall_deadline:
            coordinator._abort("WALL_CLOCK_TIMEOUT")
            break

    summary = coordinator.summary()
    acceptance = summary["acceptance"]
    material_ev = _material_evidence(sim, materials, uid)
    evidence = {
        "scenario": scenario,
        "fault": effective_fault,
        "steps": steps,
        "sim_time": round(float(sim.getSimulationTime()), 4),
        "coordinator": summary,
        "acceptance": acceptance,
        "material": material_ev,
        "agv": _agv_evidence(sim, comp["agv"]),
        "ok": bool(acceptance.get("ok")),
    }
    return evidence


# ═══════════════════════════════════════════════════════════════
# 生命周期 runner
# ═══════════════════════════════════════════════════════════════

def run_loaded_scene_stage3(sim, simIK, scene, *,
                            scenario: str = cfg.SCENARIO_CLOSED_LOOP,
                            target: str = "closed_loop",
                            fault: str = cfg.FAULT_NONE,
                            max_sim_time: float = 300.0,
                            repeat: int = 1,
                            out_dir=REPORT_DIR, logger,
                            keep_running: bool = False,
                            stop_event=None, pause_event=None,
                            pause_ack_event=None, on_start=None,
                            run_id: str | None = None,
                            artifact_id: str | None = None) -> dict:
    """阶段三生命周期 runner; 不 build、不清场."""
    if keep_running:
        raise ValueError(
            "stage3 runner 不支持 --keep-running: 影子臂/碰撞集合必须在停止态"
            "销毁")
    if repeat != 1:
        raise ValueError("阶段三只支持单次 A→B 场景, 不支持 repeat")
    scenario = target if target in cfg.STAGE3_SCENARIOS else scenario
    if scenario not in cfg.STAGE3_SCENARIOS:
        raise ValueError(f"未知 scenario: {scenario!r}")
    if fault not in cfg.STAGE3_FAULT_CHOICES:
        raise ValueError(f"未知 fault: {fault!r}")
    effective_fault = (fault if fault != cfg.FAULT_NONE
                       else cfg.SCENARIO_FAULT[scenario])

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    comp = build_components_stage3(sim, simIK, scene, logger)
    snapshot = {
        "timestamp": stamp,
        "run_id": run_id,
        "artifact_id": artifact_id,
        "phase": "stage3",
        "lifecycle_state": "SIMULATION_RUNNING",
        "scenario": scenario,
        "config": {
            "scenario": scenario,
            "max_sim_time": max_sim_time,
            "sim_step": base.SIM_STEP,
            "layout": scene.stage3_layout.name,
        },
        "scene": collect_scene_snapshot(scene, comp),
        "contract": {
            "validated_by": "lifecycle.prepare_start",
            "phase": "stage3",
        },
    }
    with open(out_dir / f"phase3_scene_snapshot_{stamp}.json", "w",
              encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    start_stepping(sim)
    logger.info("[SIM][stage3] stepping 启动, sim_time=%.3f run_id=%s",
                sim.getSimulationTime(), run_id)
    drive_probe = probe_conveyor_drive(sim, scene, comp)
    logger.info("[CONVEYOR][stage3] 驱动探测: %s", drive_probe)

    results = []
    runtime_destroy = {}
    try:
        evidence = run_scenario_stage3(
            sim, scene, comp, scenario=scenario,
            max_sim_time=max_sim_time, logger=logger, fault=effective_fault,
            stop_event=stop_event, pause_event=pause_event,
            pause_ack_event=pause_ack_event, on_start=on_start)
        evidence["conveyor_drive_probe"] = drive_probe
        results.append(evidence)
    except Exception as exc:
        logger.exception("[TASK][stage3] 运行异常: %s", exc)
        results.append({"scenario": scenario, "error": str(exc),
                        "ok": False})
    finally:
        try:
            stop_simulation(sim)
        finally:
            try:
                runtime_destroy = comp["runtime"].destroy()
            except Exception as exc:
                logger.exception("[STAGE3] runtime 清理失败: %s", exc)
                runtime_destroy = {"error": f"{type(exc).__name__}: {exc}"}

    summary = {
        "timestamp": stamp,
        "run_id": run_id,
        "artifact_id": artifact_id,
        "phase": "stage3",
        "scenario": scenario,
        "fault": effective_fault,
        "layout": scene.stage3_layout.name,
        "results": results,
        "runtime_destroy": runtime_destroy,
        "all_ok": bool(results) and all(r.get("ok") for r in results),
    }
    out = out_dir / f"phase3_run_{scenario}_{stamp}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    logger.info("[DONE][stage3] 报告写入 %s", out)
    return summary


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def _manager(sim, simIK, ns):
    from stage1.lifecycle import LifecycleManager
    return LifecycleManager(
        sim, simIK, host=ns.host, port=ns.port, actor="cli",
        report_dir=Path(ns.out_dir))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="阶段三双工位运行")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=23000)
    ap.add_argument("--scenario", choices=cfg.STAGE3_SCENARIOS,
                    default=cfg.SCENARIO_CLOSED_LOOP)
    ap.add_argument("--fault", choices=cfg.STAGE3_FAULT_CHOICES,
                    default=cfg.FAULT_NONE)
    ap.add_argument("--time", type=float, default=300.0)
    ap.add_argument("--log", default=cfg.DEFAULT_LOG_FILE)
    ap.add_argument("--out-dir", default=REPORT_DIR)
    ap.add_argument("--artifact", default="current",
                    help="stage3 工件 ID (默认 current)")
    ap.add_argument("--keep-running", action="store_true",
                    help="阶段三不支持 (会话资源必须在停止态清理)")
    ns = ap.parse_args(argv)

    logger = setup_logger(ns.log)
    if ns.keep_running:
        logger.error("[LIFECYCLE] 阶段三 runner 不支持 --keep-running")
        return 2
    client, sim, simIK = connect(ns.host, ns.port)
    manager = _manager(sim, simIK, ns)
    try:
        manager.load(ns.artifact, phase="stage3")
        if manager.artifact is None or \
                manager.artifact.manifest.get("source_phase") != "stage3":
            logger.error("[LIFECYCLE] 不是 stage3 工件: %s", ns.artifact)
            return 2
        prepared = manager.prepare_start(target=ns.scenario)
    except Exception as exc:
        logger.error("[LIFECYCLE] 启动前置失败 (%s): %s",
                     getattr(exc, "code", "ERROR"), exc)
        return 3 if getattr(exc, "code", "") == "VALIDATION_FAILED" else 2

    try:
        summary = run_loaded_scene_stage3(
            sim, simIK, manager.scene, scenario=ns.scenario,
            target=ns.scenario, fault=ns.fault,
            max_sim_time=ns.time, out_dir=Path(ns.out_dir),
            logger=logger, run_id=prepared["run_id"],
            artifact_id=manager.session.artifact_id)
    except Exception as exc:
        logger.exception("[TASK][stage3] 运行异常: %s", exc)
        error = f"{type(exc).__name__}: {exc}"
        try:
            manager.stop()
        except Exception:
            pass
        manager.finish_run("FAILED", error)
        return 1

    lifecycle = manager.finish_run(
        "DONE" if summary.get("all_ok") else "FAILED")
    summary["lifecycle"] = lifecycle
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
