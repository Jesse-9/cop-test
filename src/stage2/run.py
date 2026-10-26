"""run.py — 阶段二运行入口: 规划/执行/联锁/证据 (P2-7).

流程与阶段一 runner 对齐, 但:
  - 使用 ``stage2`` 组件 (影子臂/集合/规划器/阶段二吸盘/联锁);
  - 任务结束/异常/停止后无条件销毁会话资源 (Shadow_* 与集合);
  - 证据包含 plan/safety/stop/contact/compensation 与运行时自检结果;
  - ``--fault runtime_obstacle`` 在 TRANSFERRING 期间插入受控障碍, 用于
    验证运行期锁存 (实验副本, 不进入生产工件).

本入口不 build、不清场; 必须先通过生命周期加载 stage2 工件; 启动点校验
由 ``LifecycleManager.prepare_start()`` 统一执行 (含阶段二扩展契约)。
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
from stage1.run import (build_components as build_stage1_components,
                        collect_agv_load_follow, collect_evidence,
                        connect, probe_conveyor_drive, prepare_fault,
                        setup_logger as setup_stage1_logger,
                        start_stepping, stop_simulation)
from stage1.unit_controller import UnitTaskState

from . import config as cfg
from .runtime import build_stage2_runtime
from .suction import Stage2SuctionController
from .unit_controller import Stage2UnitController

REPORT_DIR = os.path.join("docs", "implementation", "runs")


def setup_logger(path: str) -> logging.Logger:
    logger = setup_stage1_logger(path)
    logger.name = "stage2"
    return logger


# ═══════════════════════════════════════════════════════════════
# 组件
# ═══════════════════════════════════════════════════════════════

def build_components_stage2(sim, simIK, scene, logger):
    comp = build_stage1_components(sim, simIK, scene, logger)
    runtime = build_stage2_runtime(sim, scene, comp["ik"], logger=logger)
    comp["suction"] = Stage2SuctionController(
        sim, comp["materials"], tcp_handle=scene.tcp,
        sensor_handle=scene.suction_sensor,
        link_handle=scene.suction_link,
        release_parent=scene.handle(base.ALIASES["roots"][4]),
        logger=logger, pad_handle=scene.suction_root,
        cup_radius=runtime.cup_radius)
    comp["runtime"] = runtime
    return comp


# ═══════════════════════════════════════════════════════════════
# 受控运行期障碍 (验收)
# ═══════════════════════════════════════════════════════════════

def inject_runtime_obstacle(sim, runtime, controller) -> int | None:
    plan = getattr(controller, "_last_plan", None)
    executor = getattr(controller, "_executor", None)
    if plan is None or executor is None or not plan.waypoints:
        return None
    index = min(executor.index + 1, len(plan.waypoints) - 1)
    try:
        runtime.probe.set_configuration(plan.waypoints[index])
        poses = runtime.probe.link_poses()
        point = poses[2][:3]
    except Exception:
        return None
    h = int(sim.createPrimitiveShape(sim.primitiveshape_cuboid,
                                     list(cfg.RUNTIME_HAZARD_SIZE)))
    sim.setObjectAlias(h, cfg.RUNTIME_HAZARD_ALIAS)
    sim.setObjectPosition(h, list(point), sim.handle_world)
    phys.apply_static_shape(sim, h, respondable=True)
    sim.setBoolProperty(h, "collidable", True)
    sim.setBoolProperty(h, "measurable", True)
    runtime.add_hazard(h)
    return h


# ═══════════════════════════════════════════════════════════════
# 单任务
# ═══════════════════════════════════════════════════════════════

def run_task_stage2(sim, scene, comp, target: str, uid: int, fault: str,
                    max_sim_time: float, logger, stop_event=None,
                    pause_event=None, pause_ack_event=None,
                    on_start=None) -> tuple[dict, int]:
    materials = comp["materials"]
    runtime = comp["runtime"]
    material = scene.spawn_material(uid, f"Part_{uid:04d}")
    materials.register(uid, f"Part_{uid:04d}", sim_handle=material)
    logger.info("=" * 70)
    logger.info("[TASK][stage2] uid=%s target=%s fault=%s", uid, target, fault)

    controller = Stage2UnitController(
        sim=sim, scene=scene, materials=materials,
        conveyor=comp["conveyor"], belt_geometry=comp["geometry"],
        ik=comp["ik"], suction=comp["suction"], agv=comp["agv"],
        target=target, uid=uid, fault=fault, layout=scene.layout,
        logger=logger, runtime=runtime)
    if on_start is not None:
        on_start(controller)

    agv_start_pose = comp["agv"].pose()
    material_start_pos = list(sim.getObjectPosition(material,
                                                    sim.handle_world))
    task_start_sim_time = float(sim.getSimulationTime())
    wall_deadline = time.time() + max(180.0, max_sim_time * 8.0)
    steps = 0
    hazard_injected = False
    while not controller.finished:
        if stop_event is not None and stop_event.is_set():
            controller._safety_fail("STOP_REQUESTED")
            break
        if pause_event is not None and pause_event.is_set():
            if pause_ack_event is not None:
                pause_ack_event.set()
            time.sleep(0.01)
            continue
        if pause_ack_event is not None:
            pause_ack_event.clear()
        if (fault == cfg.RUNTIME_HAZARD_FAULT and not hazard_injected
                and controller.state is UnitTaskState.TRANSFERRING
                and controller._executor is not None):
            handle = inject_runtime_obstacle(sim, runtime, controller)
            if handle is not None:
                hazard_injected = True
                logger.info("[FAULT] 运行期受控障碍 handle=%s", handle)
        controller.step()
        steps += 1
        if sim.getSimulationTime() - task_start_sim_time > max_sim_time:
            controller._safety_fail(f"SIM_TIME_LIMIT: {max_sim_time:.1f}s")
            break
        if time.time() > wall_deadline:
            controller._safety_fail("WALL_CLOCK_TIMEOUT")
            break

    evidence = collect_evidence(sim, scene, comp, controller, material, target)
    evidence["steps"] = steps
    evidence["stage2"] = {
        "controller": controller.summary(),
        "runtime": runtime.as_dict(),
        "hazard_injected": hazard_injected,
    }
    if target == "agv":
        for _ in range(60):
            if stop_event is not None and stop_event.is_set():
                break
            if comp["agv"].is_stopped():
                break
            comp["agv"].stop()
            sim.step()
        mat_start = (controller._agv_material_start
                     if controller._agv_material_start is not None
                     else material_start_pos)
        try:
            follow = collect_agv_load_follow(
                sim, agv_start_pose, comp["agv"].pose(), mat_start, material)
            evidence["agv_load_follow"] = follow
            evidence["agv_follow_ok"] = (
                not controller.agv_blocked
                and follow["follow_error"] <= 0.08
                and follow["on_tray"]
                and follow["distance_ok"])
        except Exception as exc:
            evidence["agv_load_follow"] = {"error": str(exc)}
            evidence["agv_follow_ok"] = False

    safety_ok = not controller.safety.engaged
    ok = (controller.state is UnitTaskState.DONE and safety_ok
          and evidence["material"]["dynamic_ok"]
          and evidence["material"]["respondable_ok"])
    if target == "station":
        ok = ok and evidence["material"]["position_error"] <= \
            base.MATERIAL_PLACE_TOL
    if target == "agv":
        ok = ok and evidence.get("agv_follow_ok", False)
    evidence["ok"] = bool(ok)
    logger.info("[TASK][stage2] uid=%s → %s (%s) steps=%s",
                uid, controller.state.name, controller.failure_reason, steps)
    return evidence, material


# ═══════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════

def run_loaded_scene_stage2(sim, simIK, scene, *, target: str = "station",
                            fault: str = base.FAULT_NONE,
                            max_sim_time: float = 120.0, repeat: int = 1,
                            out_dir=REPORT_DIR, logger, keep_running=False,
                            stop_event=None, pause_event=None,
                            pause_ack_event=None, on_start=None,
                            run_id: str | None = None,
                            artifact_id: str | None = None) -> dict:
    if keep_running:
        raise ValueError(
            "stage2 runner 不支持 --keep-running: 影子臂/碰撞集合必须在停止态"
            "销毁; 需要保留仿真请使用阶段一 keep-running 或先 stop 再手动启动")
    if repeat < 1:
        raise ValueError("repeat 必须 >= 1")
    if repeat > 1 and target in ("both", "agv"):
        raise ValueError("repeat>1 只支持单一 station 目标")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    comp = build_components_stage2(sim, simIK, scene, logger)
    runtime = comp["runtime"]
    notes = prepare_fault(sim, scene, fault if fault in base.FAULT_CHOICES
                          else base.FAULT_NONE,
                          comp["materials"], logger)
    for note in notes:
        logger.info("[FAULT] %s", note)
    if fault == cfg.RUNTIME_HAZARD_FAULT:
        logger.info("[FAULT] 运行期受控障碍将在 TRANSFERRING 阶段注入")

    snapshot = {
        "timestamp": stamp,
        "run_id": run_id,
        "artifact_id": artifact_id,
        "lifecycle_state": "SIMULATION_RUNNING",
        "config": {"target": target, "fault": fault,
                   "max_sim_time": max_sim_time,
                   "sim_step": base.SIM_STEP,
                   "layout": scene.layout.name},
        "scene": scene.describe(),
        "tool": scene.tool_geometry(),
        "stage2_runtime": runtime.as_dict(),
        "measurements": "docs/measurement/phase0_measurements.json",
    }
    with open(out_dir / f"phase2_scene_snapshot_{stamp}.json", "w",
              encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    start_stepping(sim)
    logger.info("[SIM][stage2] stepping 启动, sim_time=%.3f run_id=%s",
                sim.getSimulationTime(), run_id)
    drive_probe = probe_conveyor_drive(sim, scene, comp)
    logger.info("[CONVEYOR][stage2] 驱动探测: %s", drive_probe)

    targets = ["station", "agv"] if target == "both" else [target]
    targets = targets * repeat if target != "both" else targets
    results = []
    runtime_destroy = {}
    try:
        for idx, tgt in enumerate(targets, start=1):
            uid = idx
            evidence, material = run_task_stage2(
                sim, scene, comp, tgt, uid, fault, max_sim_time, logger,
                stop_event=stop_event, pause_event=pause_event,
                pause_ack_event=pause_ack_event, on_start=on_start)
            results.append(evidence)
            if idx < len(targets):
                runtime.forget_material(material)
                scene.remove_material(material)
                comp["materials"].remove(uid)
    except Exception as exc:
        logger.exception("[TASK][stage2] 运行异常: %s", exc)
        results.append({"target": target, "error": str(exc), "ok": False})
    finally:
        try:
            stop_simulation(sim)
        finally:
            runtime_destroy = runtime.destroy()

    summary = {
        "timestamp": stamp,
        "run_id": run_id,
        "artifact_id": artifact_id,
        "phase": "stage2",
        "target": target,
        "fault": fault,
        "layout": scene.layout.name,
        "stage2_runtime": runtime.as_dict(),
        "runtime_destroy": runtime_destroy,
        "conveyor_drive_probe": drive_probe,
        "results": results,
        "all_ok": bool(results) and all(r.get("ok") for r in results),
    }
    out = out_dir / f"phase2_run_{target}_{fault}_{stamp}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    logger.info("[DONE][stage2] 报告写入 %s", out)
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
    ap = argparse.ArgumentParser(description="阶段二物理约束与运动规划运行")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=23000)
    ap.add_argument("--target", choices=("station", "agv", "both"),
                    default="station")
    ap.add_argument("--fault", choices=cfg.STAGE2_FAULT_CHOICES,
                    default=base.FAULT_NONE)
    ap.add_argument("--time", type=float, default=120.0)
    ap.add_argument("--log", default="stage2_sim.log")
    ap.add_argument("--out-dir", default=REPORT_DIR)
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--artifact", default="current",
                    help="stage2 工件 ID (默认 current)")
    ap.add_argument("--keep-running", action="store_true",
                    help="阶段二不支持 (会话资源必须在停止态清理); 保留参数仅用于兼容")
    ns = ap.parse_args(argv)

    logger = setup_logger(ns.log)
    if ns.keep_running:
        logger.error("[LIFECYCLE] 阶段二 runner 不支持 --keep-running: "
                     "影子臂/碰撞集合必须在停止态销毁")
        return 2
    client, sim, simIK = connect(ns.host, ns.port)
    manager = _manager(sim, simIK, ns)
    try:
        manager.load(ns.artifact, phase="stage2")
        if manager.artifact is None or \
                manager.artifact.manifest.get("source_phase") != "stage2":
            logger.error("[LIFECYCLE] 不是 stage2 工件: %s", ns.artifact)
            return 2
        prepared = manager.prepare_start(target=ns.target)
    except Exception as exc:
        logger.error("[LIFECYCLE] 启动前置失败 (%s): %s",
                     getattr(exc, "code", "ERROR"), exc)
        return 3 if getattr(exc, "code", "") == "VALIDATION_FAILED" else 2

    try:
        summary = run_loaded_scene_stage2(
            sim, simIK, manager.scene, target=ns.target, fault=ns.fault,
            max_sim_time=ns.time, repeat=ns.repeat,
            out_dir=Path(ns.out_dir), logger=logger,
            keep_running=ns.keep_running, run_id=prepared["run_id"],
            artifact_id=manager.session.artifact_id)
    except Exception as exc:
        logger.exception("[TASK][stage2] 运行异常: %s", exc)
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
