"""run.py — v1多岛 runner 与 CLI (06-16 §F4-6).

流程:
  生命周期 load(v1) → prepare_start (启动点校验)
    → 构建五台 IK/影子臂/碰撞集合/吸盘 + 四条线驱动表
    → 按场景批次生成 RAW_A/RAW_B/RAW_D
    → 协调器逐步执行 (每步只 `sim.step()` 一次)
    → 收集 UID/谱系/交接/资源/AGV/节拍证据
    → 停止仿真, 销毁 Shadow_* 会话资源, 写 JSON 报告.

本入口不 build、不清场; v1 工件必须先由
`flexible-factory build --parent <历史 stage3 工件>` 派生并保存.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable, Mapping, Optional

from ..simulation import config_base as base
from ..simulation import physics as phys
from ..simulation.component_builder import (
    build_components as build_stage1_components, connect, probe_conveyor_drive,
    setup_logger as setup_log, start_stepping, stop_simulation)
from ..simulation.scene_base import alias_of
from ..simulation.unit_controller_base import UnitTaskState
from ..simulation.ur5_ik import Ur5IkController
from ..simulation.motion.suction import Stage2SuctionController

from ..simulation import config as cfg
from .. import spec
from .batch import (BatchError, DEFAULT_BATCH_PROFILE, resolve_batch,
                    validate_batch)
from .coordinator import Stage4Coordinator
from ..persistence.db import CatalogDatabase
from ..persistence.runs import RunRecorder
from ..simulation.layout import line_specs
from ..domain.materials import MaterialRegistry
from ..simulation.runtime import build_v1_runtime, normalize_unit_ids

REPORT_DIR = str(spec.REPORT_DIR)


def setup_logger(path: str) -> logging.Logger:
    logger = setup_log(path)
    logger.name = "v1"
    return logger


# ═══════════════════════════════════════════════════════════════
# 组件
# ═══════════════════════════════════════════════════════════════

def build_components_v1(sim, simIK, scene, logger, unit_ids=None):
    """构建共享组件 + 指定工位 IK/吸盘 + v1运行时 + 物料谱系."""
    unit_ids = normalize_unit_ids(unit_ids)
    comp = build_stage1_components(sim, simIK, scene, logger)
    conveyor = comp["conveyor"]
    for line_id, line in line_specs().items():
        meta = scene.line(line_id)
        conveyor.register(
            line.conveyor, meta["handle"], meta["entry_world"],
            meta["stop_world"], base.CONVEYOR_SURFACE_Z)
        line_handle = scene.handle(line.conveyor)
        if line_handle < 0:
            raise RuntimeError(f"缺少物流线对象: {line.conveyor}")

    ik_by_unit = {}
    if "A" in unit_ids:
        ik_by_unit["A"] = comp["ik"]
    for unit_id in unit_ids:
        if unit_id == "A":
            continue
        logger.info("[V1] 初始化 %s IK environment ...", unit_id)
        unit = scene.unit(unit_id)
        ik = Ur5IkController(
            sim, simIK, unit.robot_root, unit.tcp, unit.ik_target,
            unit.robot_joints)
        ik.initialize()
        ik_by_unit[unit_id] = ik

    logger.info("[V1] 构建影子臂/碰撞集合 (units=%s) ...",
                list(unit_ids))
    runtime = build_v1_runtime(
        sim, scene, ik_by_unit, logger=logger, unit_ids=unit_ids)
    release_parent = scene.handle(base.ALIASES["roots"][4])
    suction = {}
    for unit_id in unit_ids:
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
    comp["flow"] = MaterialRegistry()
    return comp


# ═══════════════════════════════════════════════════════════════
# 证据
# ═══════════════════════════════════════════════════════════════

def _material_evidence(sim, materials, flow, uid: int) -> dict:
    rec = materials.by_uid(uid)
    row = flow.by_uid(uid)
    if rec is None:
        return {"uid": uid, "registered": False,
                "flow": (row.as_dict() if row is not None else None)}
    if rec.sim_handle is None:
        return {"uid": uid, "alias": rec.alias, "handle": None,
                "state": rec.state.value,
                "flow": (row.as_dict() if row is not None else None)}
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
        "flow": (row.as_dict() if row is not None else None),
    }


def _agv_evidence(agv) -> dict:
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
        "layout": scene.stage4_layout.as_dict(),
        "units": {unit_id: scene.unit(unit_id).as_dict()
                  for unit_id in cfg.UNIT_IDS},
        "tools": {unit_id: scene.tool_geometry_for(unit_id)
                  for unit_id in cfg.UNIT_IDS},
        "lines": dict(scene.line_meta),
        "build_report_stage4": scene.build_report.get("stage4", {}),
        "runtime": comp["runtime"].as_dict(),
    }


def snapshot_config(scenario: str, *, max_sim_time: float, scene,
                    batch: Mapping[str, int],
                    batch_profile: str = DEFAULT_BATCH_PROFILE) -> dict:
    """运行快照中的配置段; batch 必须是本次实际生效并通过校验的值."""
    return {
        "scenario": scenario,
        "max_sim_time": max_sim_time,
        "sim_step": base.SIM_STEP,
        "layout": scene.stage4_layout.name,
        "batch": dict(batch),
        "batch_profile": str(batch_profile or DEFAULT_BATCH_PROFILE),
        "batch_input_version": spec.BATCH_INPUT_VERSION,
    }


# ═══════════════════════════════════════════════════════════════
# 场景执行
# ═══════════════════════════════════════════════════════════════

def run_scenario_v1(sim, scene, comp, *, scenario: str,
                        max_sim_time: float, logger,
                        fault: str = cfg.FAULT_NONE,
                        batch: Optional[Mapping[str, int]] = None,
                        batch_profile: str = DEFAULT_BATCH_PROFILE,
                        catalog=None, run_recorder=None,
                        stop_event=None, pause_event=None,
                        pause_ack_event=None, on_start=None) -> dict:
    """在已加载/已建组件的 v1 场景上运行一个验收场景."""
    if scenario not in cfg.STAGE4_SCENARIOS:
        raise ValueError(f"未知 scenario: {scenario!r}")
    if batch is None:
        effective_batch = resolve_batch(scenario, profile=batch_profile)
    else:
        effective_batch = validate_batch(batch)
        if (batch_profile or DEFAULT_BATCH_PROFILE) != DEFAULT_BATCH_PROFILE:
            declared = resolve_batch(scenario, profile=batch_profile)
            if effective_batch != declared:
                raise BatchError(
                    f"batch profile {batch_profile!r} 与显式 batch 不一致: "
                    f"{effective_batch} != {declared}")
    effective_fault = (fault if fault in cfg.STAGE4_FAULT_CHOICES
                       and fault != cfg.FAULT_NONE
                       else cfg.SCENARIO_FAULT[scenario])
    logger.info("=" * 70)
    logger.info("[TASK][v1] scenario=%s fault=%s batch=%s profile=%s",
                scenario, effective_fault, effective_batch, batch_profile)

    coordinator = None
    summary = None
    try:
        coordinator = Stage4Coordinator(
            sim, scene, comp, scenario=scenario, fault=effective_fault,
            logger=logger, catalog=catalog, batch=effective_batch,
            on_controller=(lambda unit_id, controller: on_start(
                coordinator) if on_start else None) if on_start else None)
        coordinator.start(float(sim.getSimulationTime()))
        if on_start is not None:
            on_start(coordinator)

        task_start = float(sim.getSimulationTime())
        wall_deadline = time.time() + max(600.0, max_sim_time * 6.0)
        steps = 0
        while not coordinator.finished:
            if stop_event is not None and stop_event.is_set():
                coordinator._freeze("STOP_REQUESTED", coordinator._sim_time(),
                                    abort=True)
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
                coordinator._freeze(f"SIM_TIME_LIMIT: {max_sim_time:.1f}s",
                                    coordinator._sim_time(), abort=True)
                break
            if time.time() > wall_deadline:
                coordinator._freeze("WALL_CLOCK_TIMEOUT",
                                    coordinator._sim_time(), abort=True)
                break

        summary = coordinator.summary()
        acceptance = summary["acceptance"]
        materials = comp["materials"]
        flow = comp["flow"]
        evidence = {
            "scenario": scenario,
            "fault": effective_fault,
            "batch": dict(effective_batch),
            "batch_profile": str(batch_profile or DEFAULT_BATCH_PROFILE),
            "batch_input_version": spec.BATCH_INPUT_VERSION,
            "steps": steps,
            "sim_time": round(float(sim.getSimulationTime()), 4),
            "coordinator": summary,
            "acceptance": acceptance,
            "materials": {
                rec.uid: _material_evidence(sim, materials, flow, rec.uid)
                for rec in materials.all_records()},
            "agv": _agv_evidence(comp["agv"]),
            "ok": bool(acceptance.get("ok")),
        }
    except Exception as exc:
        if run_recorder is not None:
            run_recorder.finish(
                coordinator, summary,
                error=f"{type(exc).__name__}: {exc}")
        raise
    if run_recorder is not None:
        evidence["run_record"] = run_recorder.finish(coordinator, summary)
    return evidence


# ═══════════════════════════════════════════════════════════════
# 生命周期 runner
# ═══════════════════════════════════════════════════════════════

def run_loaded_scene_v1(sim, simIK, scene, *,
                            scenario: str = cfg.SCENARIO_FULL_FLOW,
                            target: str = "full_flow",
                            fault: str = cfg.FAULT_NONE,
                            batch_profile: str = DEFAULT_BATCH_PROFILE,
                            batch: Optional[Mapping[str, int]] = None,
                            unit_ids: Optional[Iterable[str]] = None,
                            max_sim_time: float = cfg.DEFAULT_MAX_SIM_TIME,
                            repeat: int = 1,
                            out_dir=REPORT_DIR, logger,
                            catalog=None,
                            run_record_db: Path | None = None,
                            run_record_source: str = "code",
                            keep_running: bool = False,
                            stop_event=None, pause_event=None,
                            pause_ack_event=None, on_start=None,
                            run_id: str | None = None,
                            artifact_id: str | None = None) -> dict:
    """v1生命周期 runner; 不 build、不清场."""
    if keep_running:
        raise ValueError(
            "v1 runner 不支持 --keep-running: 影子臂/碰撞集合必须在停止态"
            "销毁")
    if repeat != 1:
        raise ValueError("v1只支持单次有限批次, 不支持 repeat")
    scenario = target if target in cfg.STAGE4_SCENARIOS else scenario
    if scenario not in cfg.STAGE4_SCENARIOS:
        raise ValueError(f"未知 scenario: {scenario!r}")
    if fault not in cfg.STAGE4_FAULT_CHOICES:
        raise ValueError(f"未知 fault: {fault!r}")
    effective_fault = (fault if fault != cfg.FAULT_NONE
                       else cfg.SCENARIO_FAULT[scenario])
    # 批次在连接/构建组件之前解析并校验; 运行报告只记录实际生效的值.
    effective_batch = resolve_batch(scenario, profile=batch_profile,
                                    batch=batch)
    effective_unit_ids = normalize_unit_ids(unit_ids)
    profile_name = str(batch_profile or DEFAULT_BATCH_PROFILE)
    logger.info("[TASK][v1] batch 解析: scenario=%s profile=%s batch=%s",
                scenario, profile_name, effective_batch)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    recorder = None
    if run_record_db is not None:
        recorder = RunRecorder(
            CatalogDatabase(run_record_db), run_id=run_id or "",
            scenario=scenario, artifact_id=artifact_id or "",
            catalog_source=run_record_source,
            catalog_version=(catalog.version if catalog is not None else ""))
        started = recorder.start(
            sim_time=float(sim.getSimulationTime()))
        if not started.get("ok"):
            logger.error("[RUN_RECORD] 启动运行记录失败, 运行继续: %s",
                         started.get("error"))

    try:
        comp = build_components_v1(
            sim, simIK, scene, logger, unit_ids=effective_unit_ids)
    except Exception as exc:
        if recorder is not None:
            recorder.finish(
                None, None, error=f"{type(exc).__name__}: {exc}")
        raise
    try:
        snapshot = {
            "timestamp": stamp,
            "run_id": run_id,
            "artifact_id": artifact_id,
            "phase": "v1",
            "lifecycle_state": "SIMULATION_RUNNING",
            "scenario": scenario,
            "active_unit_ids": list(effective_unit_ids),
            "config": snapshot_config(
                scenario, max_sim_time=max_sim_time, scene=scene,
                batch=effective_batch, batch_profile=profile_name),
            "scene": collect_scene_snapshot(scene, comp),
            "contract": {
                "validated_by": "lifecycle.prepare_start",
                "phase": "v1",
            },
        }
        with open(out_dir / f"v1_scene_snapshot_{stamp}.json", "w",
                  encoding="utf-8") as handle:
            json.dump(snapshot, handle, ensure_ascii=False, indent=2)

        start_stepping(sim)
        logger.info("[SIM][v1] stepping 启动, sim_time=%.3f run_id=%s",
                    sim.getSimulationTime(), run_id)
        drive_probe = probe_conveyor_drive(sim, scene, comp)
        logger.info("[CONVEYOR][v1] S1→A 驱动探测: %s", drive_probe)
    except Exception as exc:
        if recorder is not None:
            recorder.finish(
                None, None, error=f"{type(exc).__name__}: {exc}")
        raise

    results = []
    runtime_destroy = {}
    try:
        evidence = run_scenario_v1(
            sim, scene, comp, scenario=scenario,
            max_sim_time=max_sim_time, logger=logger, fault=effective_fault,
            batch=effective_batch, batch_profile=profile_name,
            catalog=catalog, run_recorder=recorder,
            stop_event=stop_event, pause_event=pause_event,
            pause_ack_event=pause_ack_event, on_start=on_start)
        evidence["conveyor_drive_probe"] = drive_probe
        results.append(evidence)
    except Exception as exc:
        logger.exception("[TASK][v1] 运行异常: %s", exc)
        results.append({"scenario": scenario, "error": str(exc),
                        "ok": False,
                        "run_record": (None if recorder is None
                                       else recorder.result)})
    finally:
        try:
            stop_simulation(sim)
        finally:
            try:
                runtime_destroy = comp["runtime"].destroy()
            except Exception as exc:
                logger.exception("[V1] runtime 清理失败: %s", exc)
                runtime_destroy = {"error": f"{type(exc).__name__}: {exc}"}

    summary = {
        "timestamp": stamp,
        "run_id": run_id,
        "artifact_id": artifact_id,
        "phase": "v1",
        "scenario": scenario,
        "fault": effective_fault,
        "batch": dict(effective_batch),
        "batch_profile": profile_name,
        "batch_input_version": spec.BATCH_INPUT_VERSION,
        "active_unit_ids": list(effective_unit_ids),
        "layout": scene.stage4_layout.name,
        "catalog_source": "database" if catalog is not None else "code",
        "catalog_version": None if catalog is None else catalog.version,
        "run_record": results[0].get("run_record") if results else None,
        "results": results,
        "runtime_destroy": runtime_destroy,
        "all_ok": bool(results) and all(row.get("ok") for row in results),
    }
    out = out_dir / f"v1_run_{scenario}_{stamp}.json"
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
    logger.info("[DONE][v1] 报告写入 %s", out)
    return summary
