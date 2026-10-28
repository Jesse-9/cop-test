"""probe_stage2_agv_transfer.py — 06-15 水平带载转运规划探针.

在已加载、已停止的 stage2 场景上 (影子臂, 不启动仿真):

  - 准备带载起始构型: approach → lift 到 H_transfer;
  - 对 station/AGV 分别规划 TRANSFERRING(pre_place) 与 PLACING(place),
    比较 greedy / beam 整链选链;
  - 扫描水平段 lift→pre_place 的 IK 候选覆盖 (只做可达性统计, 不是安全检查);
  - 导出逐段 TV、q[0]/q[3] 变化、净空与规划耗时.

06-15 方案已移除 rise/transit 路径点及其策略/偏移/绕行开关, 本探针不再
提供 2.02 的路径变体矩阵. 探针只做**规划** (影子 FK/碰撞查询), 不驱动
真实机械臂; 执行速度/加速度/jerk 证据来自 ``run_stage2.py`` 的真实运行报告.

用法 (CoppeliaSim 已启动, 场景已加载并停止):
    .venv/Scripts/python docs/measurement/probe_stage2_agv_transfer.py
    .venv/Scripts/python docs/measurement/probe_stage2_agv_transfer.py \
        --targets agv --chain-modes greedy beam
"""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from coppeliasim_zmqremoteapi_client import RemoteAPIClient

from stage1 import config as base
from stage1.material_manager import MaterialManager
from stage1.scene import adopt_scene
from stage1.ur5_ik_controller import Ur5IkController
from stage2 import config as cfg
from stage2.contact import (contact_pose, quat_conjugate, quat_multiply,
                            quat_rotate)
from stage2.metrics import chain_cost
from stage2.path_planning import Goal, MotionContext
from stage2.runtime import build_stage2_runtime
from stage2.scene_contract import stage2_config_digest
from stage2.suction import Stage2SuctionController
from stage2.trajectory import cartesian_poses
from stage2.unit_controller import (horizontal_transfer_check, lift_pose,
                                    transfer_goal_variants, transfer_goals)

OUT = Path("docs/measurement")


@contextmanager
def chain_mode(mode: str):
    """临时切换整链搜索模式; 不修改工件/场景."""
    with mock.patch.object(cfg, "CARTESIAN_CHAIN_SEARCH", mode):
        yield


def main() -> int:
    parser = argparse.ArgumentParser(
        description="06-15 水平带载转运规划探针")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=23000)
    parser.add_argument("--chain-modes", nargs="+", default=["greedy", "beam"])
    parser.add_argument("--targets", nargs="+",
                        choices=("agv", "station"), default=["agv", "station"])
    parser.add_argument("--pick-pose", nargs=3, type=float, default=None,
                        help="传送带停止后的实测物料中心 (x y z); 省略时用布局")
    parser.add_argument("--skip-coverage", action="store_true")
    args = parser.parse_args()

    client = RemoteAPIClient(host=args.host, port=args.port)
    sim = client.require("sim")
    simIK = client.require("simIK")
    if int(sim.getSimulationState()) != int(sim.simulation_stopped):
        print("仿真未停止", file=sys.stderr)
        return 2
    scene = adopt_scene(sim)
    ik = Ur5IkController(sim, simIK, scene.robot_root, scene.tcp,
                         scene.handle(base.ALIASES["ik_target"]),
                         scene.robot_joints)
    ik.initialize()
    runtime = None
    material = None
    manager = MaterialManager()
    report = {
        "timestamp": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "transfer_trajectory_version": cfg.TRANSFER_TRAJECTORY_VERSION,
        "motion_opt_version": cfg.MOTION_OPT_VERSION,
        "transfer_height": cfg.TRANSFER_HEIGHT,
        "config_digest": stage2_config_digest(),
        "layout": scene.layout.name,
        "contract_version": cfg.STAGE2_CONTRACT_VERSION,
        "chain_modes": list(args.chain_modes),
        "plans": [],
        "coverage": {},
        "evidence_scope": (
            "shadow planning only; execution/profile evidence comes from "
            "run_stage2.py reports"),
    }
    try:
        runtime = build_stage2_runtime(sim, scene, ik)
        report["startup"] = runtime.startup
        pick_position = (list(args.pick_pose) if args.pick_pose
                        else list(scene.layout.pick_material_pose))
        report["pick_source"] = ("cli" if args.pick_pose
                                 else "layout.pick_material_pose")
        report["pick_position"] = [round(float(v), 6)
                                   for v in pick_position]
        material = scene.spawn_material(
            1, "Probe_Part_0001", position=pick_position)
        manager.register(1, "Probe_Part_0001", sim_handle=material)
        runtime.update_materials(manager, 1, carrying=False)
        pick_pose = list(sim.getObjectPose(material, sim.handle_world))
        box = Stage2SuctionController(
            sim, manager, scene.tcp, scene.suction_sensor,
            pad_handle=scene.suction_root).material_box(material)
        start_q, start_tcp, attach, preparation = _prepare_carrying(
            runtime, manager, scene, pick_pose, box)
        report["preparation"] = preparation
        report["start_q"] = [round(float(v), 6) for v in start_q]
        report["start_tcp"] = [round(float(v), 6) for v in start_tcp]
        report["pick_pose"] = [round(float(v), 6) for v in pick_pose]
        report["attach_transform"] = [round(float(v), 6) for v in attach]
        runtime.set_attach_transform(attach)
        runtime.update_materials(manager, 1, carrying=True)

        for target in args.targets:
            place = (scene.layout.agv_tcp_pose if target == "agv"
                     else scene.layout.station_tcp_pose)
            report["plans"].extend(_plan_matrix(
                runtime, start_q, start_tcp, place, target, args))
            if not args.skip_coverage:
                report["coverage"][target] = _coverage_scan(
                    runtime, start_tcp, place, target)
        report["summary"] = _summary(report["plans"])
    finally:
        if runtime is not None:
            report["destroy"] = runtime.destroy()
        if material is not None:
            scene.remove_material(material)
        for index in range(800):
            handle = sim.getObjects(index, sim.handle_all)
            if handle is None or int(handle) < 0:
                break
            try:
                alias = sim.getObjectAlias(int(handle)) or ""
                if alias.startswith(("Shadow", "Probe_Part")):
                    sim.removeObjects([int(handle)])
            except Exception:
                pass
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"stage2_agv_transfer_{report['timestamp']}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    print("written", path)
    return 0


# ═══════════════════════════════════════════════════════════════
# 准备带载起始构型
# ═══════════════════════════════════════════════════════════════

def _prepare_carrying(runtime, manager, scene, pick_pose, box):
    planner = runtime.planner
    manager_state = manager
    context = MotionContext(
        phase="CONTACT", target_handle=1, material_pose=list(pick_pose),
        material_box=box, carrying=False, cup_radius=runtime.cup_radius,
        snapshot="probe-agv-approach")
    goals = [
        Goal("pre_pick", contact_pose(pick_pose, box,
                                      cfg.PRE_APPROACH_CLEARANCE)),
        Goal("pick", contact_pose(pick_pose, box, 0.0), cartesian=True,
             allow_target_contact=True, final_contact=True),
    ]
    plan = planner.plan(
        [float(runtime.sim.getJointPosition(joint))
         for joint in scene.robot_joints],
        goals, context, start_pose=runtime.probe.tcp_pose(),
        lock_fallback=False)
    if not plan.ok:
        raise RuntimeError(f"approach 规划失败: {plan.status.value}: "
                           f"{plan.message}")
    preparation = {"approach": plan.summary_dict()}
    start_q = list(plan.waypoints[-1])
    start_tcp = planner.checker.tcp_at(start_q)
    if start_tcp is None:
        raise RuntimeError("无法读取 lift 起始 TCP")
    attach = _relative_transform(start_tcp, pick_pose)
    runtime.set_attach_transform(attach)
    runtime.update_materials(manager_state, 1, carrying=True)
    context = MotionContext(phase="LIFT", target_handle=-1, carrying=True,
                            snapshot="probe-agv-lift",
                            allow_start_load_contact=True)
    lift = Goal("lift", lift_pose(contact_pose(pick_pose, box, 0.0),
                                  base.TOOL_TARGET_QUATERNION),
                cartesian=True, lock_joint=cfg.Q0_LOCK_JOINT)
    plan = planner.plan(start_q, [lift], context, start_pose=start_tcp,
                        lock_fallback=False)
    preparation["lift"] = plan.summary_dict()
    if not plan.ok:
        raise RuntimeError(f"lift 规划失败: {plan.status.value}: "
                           f"{plan.message}")
    end_q = list(plan.waypoints[-1])
    end_tcp = planner.checker.tcp_at(end_q)
    if end_tcp is None:
        raise RuntimeError("无法读取 lift 结束 TCP")
    return end_q, end_tcp, attach, preparation


# ═══════════════════════════════════════════════════════════════
# 计划矩阵: TRANSFERRING → PLACING
# ═══════════════════════════════════════════════════════════════

def _plan_matrix(runtime, start_q, start_tcp, place, target, args) -> list[dict]:
    planner = runtime.planner
    out: list[dict] = []
    for mode in args.chain_modes:
        chosen = None
        for name, goals, info in transfer_goal_variants(place, start_tcp):
            with chain_mode(mode):
                transfer_context = MotionContext(
                    phase="TRANSFER", target_handle=-1, carrying=True,
                    snapshot=f"probe-transfer-{target}")
                transfer = planner.plan(
                    start_q, goals, transfer_context,
                    start_pose=start_tcp, lock_fallback=False,
                    direct_only=True,
                    wall_s=cfg.TRANSFER_PLAN_WALL_BUDGET_S)
                check = None
                if transfer.ok:
                    check = horizontal_transfer_check(
                        transfer.waypoints, planner.checker,
                        orientation=place[3:7])
            out.append(_record(target, f"transfer:{name}", mode, transfer,
                               check, info))
            if transfer.ok and (check is None or check["ok"]):
                chosen = transfer
                break
        if chosen is None:
            continue
        transfer_q = list(chosen.waypoints[-1])
        transfer_tcp = planner.checker.tcp_at(transfer_q)
        place_goal = Goal("place", list(place), cartesian=True,
                          strict_orientation=True,
                          allow_final_load_contact=True,
                          lock_joint=cfg.Q0_LOCK_JOINT)
        with chain_mode(mode):
            placing_context = MotionContext(
                phase="PLACE", target_handle=-1, carrying=True,
                snapshot=f"probe-placing-{target}")
            placing = planner.plan(transfer_q, [place_goal], placing_context,
                                   start_pose=transfer_tcp,
                                   lock_fallback=False, direct_only=True,
                                   wall_s=cfg.TRANSFER_PLAN_WALL_BUDGET_S)
        out.append(_record(target, "placing", mode, placing, None))
    return out


def _record(target, stage, mode, plan, check, info=None) -> dict:
    cost = chain_cost(plan.waypoints) if plan.waypoints else {}
    totals = (plan.metrics or {}).get("totals", {})
    segments = []
    for segment in (plan.metrics or {}).get("segments", []):
        segments.append({
            "name": segment.get("name"),
            "tv_total": segment.get("tv_total"),
            "tv_q0": segment.get("tv")[0] if segment.get("tv") else None,
            "tv_q3": (segment.get("tv")[3]
                      if segment.get("tv") and len(segment["tv"]) > 3
                      else None),
            "q0_delta": segment.get("q0_delta"),
            "max_step": segment.get("max_step"),
            "points": segment.get("points"),
            "lock_ok": segment.get("lock_ok"),
            "wrist_margin_min": segment.get("wrist_margin_min"),
            "min_workcell_distance": segment.get("min_workcell_distance"),
            "min_distance": segment.get("min_distance"),
        })
    return {
        "target": target,
        "stage": stage,
        "chain_mode": mode,
        "info": info,
        "status": plan.status.value,
        "ok": plan.ok,
        "message": plan.message,
        "strategy": plan.strategy,
        "lock_policy": plan.lock_policy,
        "wall_s": round(float(plan.wall_s), 3),
        "samples": plan.samples,
        "segments": segments,
        "totals": totals,
        "chain_cost": cost,
        "chain_search": plan.chain_search,
        "diagnostics": plan.diagnostics,
        "horizontal_check": check,
    }


# ═══════════════════════════════════════════════════════════════
# 候选覆盖 (水平段 IK 覆盖, 不是安全检查)
# ═══════════════════════════════════════════════════════════════

def _coverage_scan(runtime, start_tcp, place, target) -> dict:
    planner = runtime.planner
    pre = transfer_goals(place)[0].pose
    seeds = list(cfg.IK_SEED_VARIANTS) + [list(base.ARM_SEED_JOINTS)]
    poses = cartesian_poses(start_tcp, pre, cfg.CARTESIAN_IK_STEP)
    rows = []
    previous = None
    for index, pose in enumerate(poses):
        candidates = planner.solve_pose(
            pose, previous or seeds[0], thorough=True,
            extra_seeds=seeds[1:])
        q3 = [round(float(value[3]), 6) for value in candidates]
        rows.append({
            "index": index,
            "pose": [round(float(v), 6) for v in pose],
            "candidates": len(candidates),
            "q3_values": q3,
            "q3_span": (round(max(q3) - min(q3), 6) if q3 else None),
        })
        previous = candidates[0] if candidates else previous
    counts = [row["candidates"] for row in rows]
    return {
        "target": target,
        "samples": len(rows),
        "min_candidates": min(counts) if counts else 0,
        "max_candidates": max(counts) if counts else 0,
        "zero_coverage_samples": sum(1 for count in counts if count == 0),
        "note": "IK coverage only; safety/edges are checked by the planner",
        "rows": rows,
    }


def _summary(plans) -> dict:
    out = {}
    for row in plans:
        key = (row["target"], row["stage"], row["chain_mode"])
        out["|".join(str(v) for v in key)] = {
            "ok": row["ok"],
            "status": row["status"],
            "tv_total": row["totals"].get("tv_total"),
            "cost": row["chain_cost"].get("cost"),
            "tv_q0": row["chain_cost"].get("tv_q0"),
            "tv_q3": row["chain_cost"].get("tv_q3"),
            "wall_s": row["wall_s"],
        }
    return out


def _relative_transform(tcp_pose, material_pose):
    p = [float(v) for v in tcp_pose]
    m = [float(v) for v in material_pose]
    rel = quat_rotate(quat_conjugate(p[3:7]),
                      [m[0] - p[0], m[1] - p[1], m[2] - p[2]])
    q = quat_multiply(quat_conjugate(p[3:7]), m[3:7])
    return [*rel, *q]


if __name__ == "__main__":
    raise SystemExit(main())
