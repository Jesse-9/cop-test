"""run_step1_1.py — 步骤 1.1 测量、布局扫描与 A/B/C 试验入口.

用法:
    .venv/Scripts/python.exe src/run_step1_1.py --phase measure
    .venv/Scripts/python.exe src/run_step1_1.py --phase layout
    .venv/Scripts/python.exe src/run_step1_1.py --phase experiment \
        --selected-layout L1_S1
    .venv/Scripts/python.exe src/run_step1_1.py --phase all
    # 冒烟: 只跑 B 组 1 次
    .venv/Scripts/python.exe src/run_step1_1.py --phase experiment \
        --selected-layout L1_S1 --groups B --repeats 1

流程:
  measure    → 基线场景世界位姿/包围盒
  layout     → 候选布局构建 + 31 个离散 TCP 目标 simIK 扫描 + 边界余量 + 选择
  experiment → A/B/C 停带/抓取试验矩阵 + 证据 JSONL/JSON

本步骤只做离散 IK 与受控停带可行性; 不声明路径无碰撞, 也不替代阶段二
的碰撞/安全停机验收 (06-07 §7)。
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime

from coppeliasim_zmqremoteapi_client import RemoteAPIClient

from stage1 import config as base
from stage1.conveyor_controller import BeltGeometry, ConveyorController
from stage1.material_manager import MaterialManager
from stage1.scene import build_scene
from stage1.suction_controller import SuctionController
from stage1.ur5_ik_controller import Ur5IkController
from stage1.run import connect, start_stepping, stop_simulation
from stage1_1 import config as cfg
from stage1_1.baseline import find_pad, run_baseline_flow
from stage1_1.layout_eval import (
    IkScanner,
    layout_targets,
    measure_scene,
    select_layout,
    verify_selected_layout_motion,
)
from stage1_1.pickup import PickupTrial

TRIAL_STEP_LIMIT = 3000
TRIAL_WALL_SECONDS = 180.0


# ═══════════════════════════════════════════════════════════════
# 基础设施
# ═══════════════════════════════════════════════════════════════

def setup_logger(path: str) -> logging.Logger:
    logger = logging.getLogger("step11")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    fh = logging.FileHandler(path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(message)s"))
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


def git_info() -> dict:
    def run(args):
        try:
            return subprocess.run(args, capture_output=True, text=True,
                                  timeout=5).stdout.strip()
        except Exception:
            return ""
    return {
        "commit": run(["git", "rev-parse", "HEAD"]),
        "branch": run(["git", "rev-parse", "--abbrev-ref", "HEAD"]),
        "dirty": bool(run(["git", "status", "--porcelain"])),
    }


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def _append_jsonl(path: str, record: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def _settle(sim, steps: int = 5) -> None:
    for _ in range(steps):
        sim.step()


def _select_layout_by_name(name: str):
    for layout in cfg.CANDIDATE_LAYOUTS:
        if layout.name == name:
            return layout
    raise SystemExit(f"未知候选布局: {name!r} "
                     f"(可用: {[l.name for l in cfg.CANDIDATE_LAYOUTS]})")


# ═══════════════════════════════════════════════════════════════
# 测量阶段
# ═══════════════════════════════════════════════════════════════

def run_measure_phase(sim, stamp: str, out_dir: str, logger) -> dict:
    stop_simulation(sim)
    scene = build_scene(sim, clear=True, layout=cfg.BASELINE_LAYOUT)
    measurement = measure_scene(sim, scene)
    record = {
        "timestamp": stamp,
        "config_version": cfg.CONFIG_VERSION,
        "git": git_info(),
        "phase": "measure",
        "baseline_layout": cfg.BASELINE_LAYOUT.name,
        "allowed_pick_region": {
            "x": list(cfg.ALLOWED_PICK_REGION.x),
            "y": list(cfg.ALLOWED_PICK_REGION.y),
            "z_center": cfg.ALLOWED_PICK_REGION.z_center,
            "z_tol": cfg.ALLOWED_PICK_REGION.z_tol,
        },
        "candidate_coordinates": [
            {
                "name": layout.name,
                "arm_base": list(layout.arm_base),
                "island_pos": list(layout.island_pos),
                "station_pos": list(layout.station_pos),
                "station_tcp": layout.station_tcp_pose,
                "agv_dock": list(layout.agv_dock),
                "agv_tcp": layout.agv_tcp_pose,
                "include_stopper": layout.include_stopper,
                "pick_sensor_local_x": layout.pick_sensor_local_x,
            }
            for layout in cfg.CANDIDATE_LAYOUTS
        ],
        "build_report": scene.build_report,
        "measurement": measurement,
    }
    path = os.path.join(out_dir, f"{cfg.EVIDENCE_PREFIX}_measure_{stamp}.json")
    _write_json(path, record)
    logger.info("[MEASURE] 写入 %s (%d 个对象)",
                path, len(measurement["objects"]))
    return {"path": path, "record": record}


# ═══════════════════════════════════════════════════════════════
# 布局扫描阶段
# ═══════════════════════════════════════════════════════════════

def _reach_margins(layout, targets: dict[str, list[float]]) -> dict:
    groups = {
        "pick": [v for k, v in targets.items() if k.startswith("pick_")],
        "station": [targets["station"], targets["station_approach"]],
        "agv": [targets["agv"], targets["agv_approach"]],
    }
    out = {}
    for name, poses in groups.items():
        worst = max(math.dist(layout.arm_base, p[:3]) for p in poses)
        out[name] = round(base.REACH_LIMIT - worst, 5)
    return out


def run_layout_phase(sim, simIK, stamp: str, out_dir: str,
                     logger) -> dict:
    """构建每个候选并扫描; 返回 selection 与完整报告。"""
    candidates = []
    for layout in cfg.CANDIDATE_LAYOUTS:
        targets = layout_targets(layout)
        report = {
            "layout": layout.name,
            "coordinates": {
                "arm_base": list(layout.arm_base),
                "island_pos": list(layout.island_pos),
                "station_pos": list(layout.station_pos),
                "agv_dock": list(layout.agv_dock),
                "station_tcp": layout.station_tcp_pose,
                "agv_tcp": layout.agv_tcp_pose,
                "include_stopper": layout.include_stopper,
            },
            "target_count": len(targets),
            "reach_margins": _reach_margins(layout, targets),
        }
        try:
            stop_simulation(sim)
            scene = build_scene(sim, clear=True, layout=layout)
            report["static_ok"] = True
            report["scene_build"] = scene.build_report
            report["measurement"] = measure_scene(sim, scene)
            start_stepping(sim)
            _settle(sim, 5)
            scanner = IkScanner(sim, simIK, scene)
            scan = scanner.scan(targets)
            scan["position_probe_available"] = scanner.position_probe_available
            scan["boundary"] = scanner.boundary(layout)
            report.update(scan)
            stop_simulation(sim)
            logger.info(
                "[LAYOUT] %s pose_ok=%s failed=%d boundary_margins=%s",
                layout.name, scan["all_pose_ok"], len(scan["failed_names"]),
                {k: v["margin_m"] for k, v in scan["boundary"].items()})
        except Exception as exc:
            stop_simulation(sim)
            report.update({
                "static_ok": False,
                "all_pose_ok": False,
                "all_joints_within_limits": False,
                "error": f"{type(exc).__name__}: {exc}",
                "failed_names": [],
                "boundary": {},
            })
            logger.warning("[LAYOUT] %s 构建/扫描失败: %s", layout.name, exc)
        candidates.append(report)

    decision = select_layout(candidates)
    logger.info("[LAYOUT] 选择: %s (rule=%s)",
                decision.get("selected"), decision.get("rule"))

    motion = None
    if decision.get("ok"):
        selected = _select_layout_by_name(decision["selected"])
        try:
            stop_simulation(sim)
            scene = build_scene(sim, clear=True, layout=selected)
            start_stepping(sim)
            _settle(sim, 5)
            motion = verify_selected_layout_motion(sim, simIK, scene, selected)
            stop_simulation(sim)
            logger.info("[LAYOUT] %s 实际运动验证 all_ok=%s",
                        selected.name, motion["all_ok"])
        except Exception as exc:
            stop_simulation(sim)
            motion = {"layout": selected.name, "all_ok": False,
                      "error": f"{type(exc).__name__}: {exc}"}
            logger.warning("[LAYOUT] %s 运动验证失败: %s", selected.name, exc)

    record = {
        "timestamp": stamp,
        "config_version": cfg.CONFIG_VERSION,
        "git": git_info(),
        "phase": "layout",
        "allowed_pick_region": {
            "x": list(cfg.ALLOWED_PICK_REGION.x),
            "y": list(cfg.ALLOWED_PICK_REGION.y),
            "z_center": cfg.ALLOWED_PICK_REGION.z_center,
            "z_tol": cfg.ALLOWED_PICK_REGION.z_tol,
        },
        "candidates": candidates,
        "selection": decision,
        "motion_verification": motion,
    }
    path = os.path.join(out_dir, f"{cfg.EVIDENCE_PREFIX}_layout_{stamp}.json")
    _write_json(path, record)
    logger.info("[LAYOUT] 写入 %s", path)
    return {"path": path, "record": record, "selection": decision,
            "motion_verification": motion}


# ═══════════════════════════════════════════════════════════════
# 组件与试验运行
# ═══════════════════════════════════════════════════════════════

def _build_components(sim, simIK, scene, logger) -> dict:
    belt = scene.handle(base.ALIASES["conveyor"])
    entry = scene.handle(base.ALIASES["conveyor_entry"])
    stop = scene.handle(base.ALIASES["conveyor_stop"])
    entry_world = tuple(sim.getObjectPosition(entry, sim.handle_world))
    stop_world = tuple(sim.getObjectPosition(stop, sim.handle_world))
    conveyor = ConveyorController(sim)
    conveyor.register(base.ALIASES["conveyor"], belt, entry_world, stop_world,
                      base.CONVEYOR_SURFACE_Z)
    materials = MaterialManager()
    ik = Ur5IkController(
        sim, simIK, scene.robot_root, scene.tcp,
        scene.handle(base.ALIASES["ik_target"]), scene.robot_joints)
    ik.initialize()
    suction = SuctionController(
        sim, materials, tcp_handle=scene.tcp,
        sensor_handle=scene.suction_sensor,
        link_handle=scene.suction_link,
        release_parent=scene.handle(base.ALIASES["roots"][4]),
        logger=logger,
        pad_handle=scene.suction_root)
    return {
        "conveyor": conveyor,
        "geometry": BeltGeometry(entry_world, stop_world),
        "materials": materials,
        "ik": ik,
        "suction": suction,
        "agv": None,
    }


def _reset_arm(sim, ik) -> None:
    if ik is None:
        return
    ik.apply_joints(list(base.ARM_SEED_JOINTS))
    for _ in range(max(1, int(cfg.ARM_RESET_TIMEOUT / base.SIM_STEP))):
        sim.step()
        if ik.converged(list(base.ARM_SEED_JOINTS)) and ik.stopped():
            break


def _cleanup_material(scene, comp, handle: int, uid: int) -> None:
    if handle is not None and int(handle) >= 0:
        scene.remove_material(int(handle))
    comp["materials"].remove(int(uid))
    comp["suction"].forget(int(uid))


def _run_trial(sim, trial: PickupTrial, logger) -> dict:
    wall = time.time() + TRIAL_WALL_SECONDS
    steps = 0
    while not trial.finished and steps < TRIAL_STEP_LIMIT:
        trial.step()
        steps += 1
        if time.time() > wall:
            trial.abort("WALL_CLOCK_TIMEOUT")
            break
    if not trial.finished:
        trial.abort(f"TRIAL_STEP_LIMIT: {TRIAL_STEP_LIMIT}")
    return trial.result()


def _new_trial(sim, scene, comp, layout, group: str, label: str, x: float,
               yaw: float, repeat: int, uid: int, pad: int,
               full_pick: bool, logger) -> PickupTrial:
    use_arm = group == "B"
    trial = PickupTrial(
        sim=sim, scene=scene, conveyor=comp["conveyor"],
        geometry=comp["geometry"], layout=layout, group=group,
        initial_x=x, initial_label=label, initial_yaw=yaw,
        repeat_index=repeat, uid=uid, materials=comp["materials"],
        ik=comp["ik"] if use_arm else None,
        suction=comp["suction"] if (use_arm and full_pick) else None,
        pad_handle=pad, full_pick=full_pick, logger=logger)
    trial.start()
    return trial


# ═══════════════════════════════════════════════════════════════
# A 组
# ═══════════════════════════════════════════════════════════════

def run_group_a(sim, scene, comp, layout, stamp: str, out_dir: str,
                logger, repeats: int,
                trial_path: str) -> tuple[list[dict], list[dict]]:
    baseline_records = []
    for i in range(cfg.BASELINE_FLOW_RUNS):
        uid = i + 1
        rec = run_baseline_flow(sim, scene, comp, uid, logger=logger)
        rec["phase"] = "baseline_flow"
        baseline_records.append(rec)
        _cleanup_material(scene, comp, rec.get("material_handle", -1), uid)

    pad = find_pad(sim, scene.handle(base.ALIASES["conveyor"]))
    logger.info("[A] pad handle=%s", pad)
    records = []
    uid = 101
    for label in cfg.INITIAL_POSITIONS:
        x = cfg.INITIAL_POSITIONS[label]
        for r in range(repeats):
            yaw = cfg.YAW_OFFSETS[r % len(cfg.YAW_OFFSETS)]
            trial = _new_trial(sim, scene, comp, layout, "A", label, x, yaw,
                               r, uid, pad, False, logger)
            rec = _run_trial(sim, trial, logger)
            rec["phase"] = "A_stop_matrix"
            records.append(rec)
            _append_jsonl(trial_path, rec)
            _cleanup_material(scene, comp, rec.get("material_handle", -1), uid)
            uid += 1
            if r % 5 == 0:
                logger.info("[A] %s %d/%d state=%s gate=%s", label, r + 1,
                            repeats, rec["state"], rec["gate_ok"])
    return records, baseline_records


# ═══════════════════════════════════════════════════════════════
# B 组
# ═══════════════════════════════════════════════════════════════

def run_group_b(sim, scene, comp, layout, stamp: str, out_dir: str,
                logger, repeats: int, trial_path: str) -> list[dict]:
    pad = find_pad(sim, scene.handle(base.ALIASES["conveyor"]))
    logger.info("[B] pad handle=%s", pad)
    records = []
    uid = 1
    for label in cfg.INITIAL_POSITIONS:
        x = cfg.INITIAL_POSITIONS[label]
        for r in range(repeats):
            yaw = cfg.YAW_OFFSETS[r % len(cfg.YAW_OFFSETS)]
            trial = _new_trial(sim, scene, comp, layout, "B", label, x, yaw,
                               r, uid, pad, False, logger)
            rec = _run_trial(sim, trial, logger)
            rec["phase"] = "B_precheck"
            records.append(rec)
            _append_jsonl(trial_path, rec)
            _cleanup_material(scene, comp, rec.get("material_handle", -1), uid)
            _reset_arm(sim, comp["ik"])
            uid += 1
            if r % 5 == 0:
                logger.info("[B] %s %d/%d state=%s gate=%s", label, r + 1,
                            repeats, rec["state"], rec["gate_ok"])

    # 每个初始位置做 1 次完整取放验证 (含吸附/抬升/放置/释放)
    uid = 201
    for label in cfg.INITIAL_POSITIONS:
        x = cfg.INITIAL_POSITIONS[label]
        for r in range(cfg.FULL_PICK_VALIDATIONS):
            trial = _new_trial(sim, scene, comp, layout, "B", label, x,
                               cfg.YAW_OFFSETS[r % len(cfg.YAW_OFFSETS)],
                               100 + r, uid, pad, True, logger)
            rec = _run_trial(sim, trial, logger)
            rec["phase"] = "B_full_pick"
            records.append(rec)
            _append_jsonl(trial_path, rec)
            _cleanup_material(scene, comp, rec.get("material_handle", -1), uid)
            _reset_arm(sim, comp["ik"])
            uid += 1
            logger.info("[B] 完整取放 %s state=%s success=%s", label,
                        rec["state"], rec["success"])
    return records


# ═══════════════════════════════════════════════════════════════
# C 组
# ═══════════════════════════════════════════════════════════════

def run_group_c(sim, scene, comp, layout, stamp: str, out_dir: str,
                logger, repeats: int, trial_path: str) -> list[dict]:
    pad = find_pad(sim, scene.handle(base.ALIASES["conveyor"]))
    logger.info("[C] pad handle=%s", pad)
    records = []
    uid = 1
    for label in cfg.INITIAL_POSITIONS:
        x = cfg.INITIAL_POSITIONS[label]
        for r in range(repeats):
            yaw = cfg.YAW_OFFSETS[r % len(cfg.YAW_OFFSETS)]
            trial = _new_trial(sim, scene, comp, layout, "C", label, x, yaw,
                               r, uid, pad, False, logger)
            rec = _run_trial(sim, trial, logger)
            rec["phase"] = "C_physics_position"
            records.append(rec)
            _append_jsonl(trial_path, rec)
            _cleanup_material(scene, comp, rec.get("material_handle", -1), uid)
            uid += 1
            if r % 5 == 0:
                logger.info("[C] %s %d/%d transported=%s dx=%s", label,
                            r + 1, repeats, rec["transported"],
                            rec["displacement_x"])

    # 从入口生成: 检查摩擦输送能否把物料带到检测区
    x_entry = base.CONVEYOR_ENTRY_WORLD[0]
    uid = 101
    for r in range(cfg.C_ENTRY_REPEATS):
        trial = _new_trial(sim, scene, comp, layout, "C", cfg.C_ENTRY_LABEL,
                           x_entry, cfg.YAW_OFFSETS[r % len(cfg.YAW_OFFSETS)],
                           r, uid, pad, False, logger)
        rec = _run_trial(sim, trial, logger)
        rec["phase"] = "C_entry"
        records.append(rec)
        _append_jsonl(trial_path, rec)
        _cleanup_material(scene, comp, rec.get("material_handle", -1), uid)
        uid += 1
        if r % 5 == 0:
            logger.info("[C] entry %d/%d transported=%s dx=%s", r + 1,
                        cfg.C_ENTRY_REPEATS, rec["transported"],
                        rec["displacement_x"])
    return records


# ═══════════════════════════════════════════════════════════════
# 汇总
# ═══════════════════════════════════════════════════════════════

def _stats(values) -> dict | None:
    vals = [float(v) for v in values
            if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not vals:
        return None
    return {"count": len(vals), "min": round(min(vals), 5),
            "max": round(max(vals), 5),
            "mean": round(sum(vals) / len(vals), 5)}


def _subset_stats(records: list[dict]) -> dict:
    def col(key, values):
        return {key: _stats([r.get(key) for r in records])}
    final_x = [r["material_final_pose"][0] for r in records
               if r.get("material_final_pose")]
    stop_x = [r["material_stop_pose"][0] for r in records
              if r.get("material_stop_pose")]
    delay = []
    for r in records:
        if r.get("stop_command_time") is not None \
                and r.get("sensor_trigger_time") is not None:
            delay.append(r["stop_command_time"] - r["sensor_trigger_time"])
    return {
        "total": len(records),
        "success": sum(1 for r in records if r.get("success")),
        "gate_ok": sum(1 for r in records if r.get("gate_ok")),
        "inside_allowed_region": sum(
            1 for r in records if r.get("inside_allowed_region")),
        "pad_stop_modes": dict(Counter(
            r.get("pad_stop_mode", "") for r in records)),
        "stop_command_reasons": dict(Counter(
            r.get("stop_command_reason", "") for r in records)),
        "sensor_triggered": sum(
            1 for r in records if r.get("sensor_trigger_time") is not None),
        "failure_reasons": dict(Counter(
            r.get("failure_reason", "") for r in records
            if r.get("failure_reason"))),
        "sensor_to_stop_command_delay_s": _stats(delay),
        "pad_stop_duration_s": _stats(
            [r.get("pad_stop_duration_s") for r in records]),
        "pad_stop_distance_m": _stats(
            [r.get("pad_stop_distance_m") for r in records]),
        "material_displacement_from_trigger_m": _stats(
            [r.get("material_displacement_from_trigger_m")
             for r in records]),
        "material_settle_time_s": _stats(
            [r.get("material_settle_time_s") for r in records]),
        "material_stop_x": _stats(stop_x),
        "material_final_x": _stats(final_x),
        "compensation_count": _stats(
            [r.get("compensation_count") for r in records]),
        "place_position_error_m": _stats(
            [r.get("place_position_error_m") for r in records]),
        "transported": sum(1 for r in records if r.get("transported")),
        "reached_detection_zone": sum(
            1 for r in records if r.get("reached_detection_zone")),
    }


def aggregate_trials(records: list[dict]) -> dict:
    out = _subset_stats(records)
    out["by_phase"] = {}
    for phase in sorted({r.get("phase", "") for r in records}):
        out["by_phase"][phase] = _subset_stats(
            [r for r in records if r.get("phase", "") == phase])
    out["by_position"] = {}
    for label in cfg.INITIAL_POSITIONS:
        subset = [r for r in records
                  if r.get("initial_position") == label]
        out["by_position"][label] = _subset_stats(subset)
    entry = [r for r in records
             if r.get("initial_position") == cfg.C_ENTRY_LABEL]
    if entry:
        out["by_position"][cfg.C_ENTRY_LABEL] = _subset_stats(entry)
    return out


# ═══════════════════════════════════════════════════════════════
# 试验阶段
# ═══════════════════════════════════════════════════════════════

def run_experiment_phase(sim, simIK, selected_layout, stamp: str,
                         out_dir: str, logger, args,
                         layout_record: dict | None = None) -> dict:
    groups = [g for g in args.groups if g]
    summary = {
        "timestamp": stamp,
        "config_version": cfg.CONFIG_VERSION,
        "git": git_info(),
        "phase": "experiment",
        "selected_layout": selected_layout.name,
        "selected_layout_coordinates": {
            "arm_base": list(selected_layout.arm_base),
            "island_pos": list(selected_layout.island_pos),
            "station_pos": list(selected_layout.station_pos),
            "agv_dock": list(selected_layout.agv_dock),
        },
        "layout_evidence": (layout_record or {}).get("path"),
        "matrix": {
            "speeds_mps": list(cfg.SPEEDS_MPS),
            "speeds_pending": list(cfg.SPEEDS_PENDING),
            "initial_positions": dict(cfg.INITIAL_POSITIONS),
            "repeats_per_combo": args.repeats,
            "yaw_offsets": list(cfg.YAW_OFFSETS),
            "groups": {g: cfg.GROUP_SPECS[g] for g in groups},
            "c_entry_repeats": cfg.C_ENTRY_REPEATS,
            "full_pick_validations": cfg.FULL_PICK_VALIDATIONS,
            "baseline_flow_runs": cfg.BASELINE_FLOW_RUNS,
        },
        "groups": {},
        "baseline_flow": [],
    }

    for group in groups:
        group_layout = (cfg.BASELINE_LAYOUT if group == "A"
                        else selected_layout)
        logger.info("=" * 70)
        logger.info("[EXPERIMENT] group=%s layout=%s", group,
                    group_layout.name)
        stop_simulation(sim)
        scene = build_scene(sim, clear=True, layout=group_layout)
        start_stepping(sim)
        _settle(sim, 5)
        comp = _build_components(sim, simIK, scene, logger)
        trial_path = os.path.join(
            out_dir, f"{cfg.EVIDENCE_PREFIX}_trials_{group}_{stamp}.jsonl")
        if os.path.exists(trial_path):
            os.remove(trial_path)

        if group == "A":
            records, baseline = run_group_a(
                sim, scene, comp, group_layout, stamp, out_dir, logger,
                args.repeats, trial_path)
            summary["baseline_flow"] = baseline
            _write_json(os.path.join(
                out_dir, f"{cfg.EVIDENCE_PREFIX}_baseline_flow_{stamp}.json"),
                {"timestamp": stamp, "config_version": cfg.CONFIG_VERSION,
                 "git": git_info(), "records": baseline})
        elif group == "B":
            records = run_group_b(
                sim, scene, comp, group_layout, stamp, out_dir, logger,
                args.repeats, trial_path)
        else:
            records = run_group_c(
                sim, scene, comp, group_layout, stamp, out_dir, logger,
                args.repeats, trial_path)

        summary["groups"][group] = {
            "spec": cfg.GROUP_SPECS[group],
            "scene_layout": group_layout.name,
            # 迁移后 stage1 构建器不再创建挡停体; 保留 request 字段以区分
            # 历史 A 组配置与当前实际场景。
            "stopper_requested": group_layout.include_stopper,
            "stopper_present": False,
            "build_ok": scene.build_report.get("ok"),
            "trial_file": trial_path,
            "summary": aggregate_trials(records),
        }
        stop_simulation(sim)

    path = os.path.join(out_dir,
                        f"{cfg.EVIDENCE_PREFIX}_experiments_{stamp}.json")
    _write_json(path, summary)
    summary["path"] = path
    logger.info("[EXPERIMENT] 写入 %s", path)
    print(json.dumps(_printable_summary(summary), ensure_ascii=False, indent=2))
    return summary


def _printable_summary(summary: dict) -> dict:
    out = {k: v for k, v in summary.items() if k != "baseline_flow"}
    for group, info in out.get("groups", {}).items():
        if isinstance(info.get("summary"), dict):
            s = info["summary"]
            info["summary"] = {k: s[k] for k in
                               ("total", "success", "gate_ok",
                                "failure_reasons", "pad_stop_modes")}
    return out


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="步骤 1.1 布局与无挡板取料验证")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=23000)
    ap.add_argument("--phase", choices=("measure", "layout", "experiment",
                                        "all"), default="all")
    ap.add_argument("--out-dir", default=cfg.EVIDENCE_DIR)
    ap.add_argument("--log", default=cfg.LOG_FILE)
    ap.add_argument("--repeats", type=int, default=cfg.REPEATS_PER_COMBO)
    ap.add_argument("--groups", default="A,B,C",
                    help="逗号分隔的组别 (A/B/C)")
    ap.add_argument("--selected-layout", default=None,
                    help="experiment 阶段使用的候选名; 省略时自动扫描选择")
    ap.add_argument("--layout-report", default=None,
                    help="复用已有 layout JSON (取其 selection.selected)")
    ap.add_argument("--keep-running", action="store_true")
    ns = ap.parse_args(argv)
    ns.groups = [g.strip() for g in ns.groups.split(",") if g.strip()]
    for g in ns.groups:
        if g not in cfg.GROUP_SPECS:
            ap.error(f"未知组别: {g}")
    if ns.repeats < 1:
        ap.error("--repeats 必须 >= 1")
    if ns.phase == "experiment" and not (
            ns.selected_layout or ns.layout_report):
        # 未指定布局时自动先跑布局扫描, 保证证据可追溯
        ns.phase = "all"

    logger = setup_logger(ns.log)
    client, sim, simIK = connect(ns.host, ns.port)
    stamp = _stamp()
    os.makedirs(ns.out_dir, exist_ok=True)

    try:
        layout_result = None
        if ns.phase in ("measure", "all"):
            run_measure_phase(sim, stamp, ns.out_dir, logger)
        if ns.phase in ("layout", "all"):
            layout_result = run_layout_phase(sim, simIK, stamp, ns.out_dir,
                                             logger)
        if ns.phase in ("experiment", "all"):
            if layout_result is not None:
                selection = layout_result["selection"]
                if not selection.get("ok"):
                    logger.error("[EXPERIMENT] 没有可用候选布局: %s",
                                 selection.get("reason"))
                    return 2
                selected = _select_layout_by_name(selection["selected"])
                layout_record = layout_result
            elif ns.layout_report:
                with open(ns.layout_report, encoding="utf-8") as f:
                    saved = json.load(f)
                name = (saved.get("selection", {}).get("selected")
                        or ns.selected_layout)
                selected = _select_layout_by_name(name)
                layout_record = {"path": ns.layout_report}
            else:
                selected = _select_layout_by_name(ns.selected_layout)
                layout_record = None
            run_experiment_phase(sim, simIK, selected, stamp, ns.out_dir,
                                 logger, ns, layout_record)
        return 0
    finally:
        if not ns.keep_running:
            stop_simulation(sim)


if __name__ == "__main__":
    raise SystemExit(main())
