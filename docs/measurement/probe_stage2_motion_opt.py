"""probe_stage2_motion_opt.py — 阶段 2.01 腕部分支与锁轴 A/B 规划探针.

在已加载、已停止的 stage1/stage2 场景上 (不启动仿真):

  1. 采集 station/AGV 目标位姿的多解 IK 候选, 统计 q[4] 归一化角、
     腕部分支与奇异裕量, 验证分支拒绝规则;
  2. 对 station 与 AGV 分别按 ``free`` / ``lock`` 两种策略规划
     approach → lift → transfer → retreat, 输出逐段 TV、最大单步、
     点数、q[0] 变化、分支、最小净空与规划耗时.

本探针只在影子臂上规划, 不移动真实机械臂、不保存场景; 结束后删除
物料与 Shadow_* 会话资源.
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from coppeliasim_zmqremoteapi_client import RemoteAPIClient

from stage1 import config as base
from stage1.material_manager import MaterialManager
from stage1.scene import adopt_scene
from stage1.ur5_ik_controller import Ur5IkController
from stage2 import config as cfg
from stage2.branch import wrist_branch
from stage2.contact import (contact_pose, quat_conjugate, quat_multiply,
                            quat_rotate)
from stage2.path_planning import Goal, MotionContext
from stage2.runtime import build_stage2_runtime
from stage2.suction import Stage2SuctionController
from stage2.unit_controller import transfer_goals

OUT = Path("docs/measurement")
POLICIES = ("no_split_free", "split_free", "split_lock", "production")


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="阶段 2.01 腕部分支/锁轴 A/B 规划探针")
    parser.add_argument(
        "--no-branch-guard", action="store_true",
        help="测量用: 关闭腕部分支拒绝 (不用于生产), 与默认策略做 A/B")
    args = parser.parse_args()
    if args.no_branch_guard:
        import stage2.path_planning as planning
        planning.wrist_branch = lambda q: ("positive", 9.0)
        planning.branch_ok = lambda q, anchor, margin: (True, "positive", 9.0)
    policies = ("no_split_free",) if args.no_branch_guard else POLICIES
    client = RemoteAPIClient(host="localhost", port=23000)
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
        "motion_opt_version": cfg.MOTION_OPT_VERSION,
        "branch_guard": not args.no_branch_guard,
        "policies": list(policies),
        "layout": scene.layout.name,
        "branch_rule": {
            "joint_index": cfg.WRIST_BRANCH_JOINT,
            "margin": cfg.WRIST_BRANCH_MARGIN,
        },
        "wrist_candidates": {},
        "plans": {},
    }
    try:
        runtime = build_stage2_runtime(sim, scene, ik)
        report["startup"] = runtime.startup
        material = scene.spawn_material(
            1, "Probe_Part_0001", position=scene.layout.pick_material_pose)
        manager.register(1, "Probe_Part_0001", sim_handle=material)
        runtime.update_materials(manager, 1, carrying=False)
        pick_pose = list(sim.getObjectPose(material, sim.handle_world))
        box = Stage2SuctionController(
            sim, manager, scene.tcp, scene.suction_sensor,
            pad_handle=scene.suction_root).material_box(material)

        poses = {
            "pre_pick": contact_pose(pick_pose, box,
                                     cfg.PRE_APPROACH_CLEARANCE),
            "pick": contact_pose(pick_pose, box, 0.0),
            "lift": contact_pose(pick_pose, box,
                                 cfg.PRE_APPROACH_CLEARANCE),
            "rise": _raise(contact_pose(pick_pose, box,
                                        cfg.PRE_APPROACH_CLEARANCE),
                           cfg.TRANSFER_RISE_HEIGHT),
            "pre_station": _raise(scene.layout.station_tcp_pose,
                                  cfg.PRE_APPROACH_CLEARANCE),
            "station": list(scene.layout.station_tcp_pose),
            "pre_agv": _raise(scene.layout.agv_tcp_pose,
                              cfg.PRE_APPROACH_CLEARANCE),
            "agv": list(scene.layout.agv_tcp_pose),
        }
        seeds = list(cfg.IK_SEED_VARIANTS) + [list(base.ARM_SEED_JOINTS)]
        for name, pose in poses.items():
            report["wrist_candidates"][name] = _wrist_scan(ik, pose, seeds)

        start_q = ik.joint_positions()
        start_tcp = ik.tcp_pose()
        for target in ("station", "agv"):
            place = (scene.layout.station_tcp_pose if target == "station"
                     else scene.layout.agv_tcp_pose)
            report["plans"][target] = {}
            for policy in policies:
                report["plans"][target][policy] = _plan_chain(
                    scene, runtime, ik, manager, pick_pose, box, place,
                    target, policy, start_q, start_tcp)
        report["comparison"] = _comparison(report["plans"])
    finally:
        if runtime is not None:
            report["destroy"] = runtime.destroy()
        if material is not None:
            scene.remove_material(material)
        for index in range(500):
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
    suffix = "" if report["branch_guard"] else "_nobranch"
    path = OUT / f"stage2_motion_opt_{report['timestamp']}{suffix}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    print("written", path)
    return 0


def _raise(pose, height):
    out = list(pose)
    out[2] += float(height)
    return out


def _wrist_scan(ik, pose, seeds) -> dict:
    results = ik.solve_candidates(list(pose), seeds, max_candidates=12,
                                  dedupe_tol=cfg.IK_DEDUPE_TOL)
    rows = []
    for result in results:
        if not result.success:
            continue
        label, margin = wrist_branch(result.joints)
        rows.append({
            "q": [round(float(v), 6) for v in result.joints],
            "wrist_branch": label,
            "wrist_margin": round(float(margin), 6),
            "q4_wrapped": round(float(
                math.remainder(result.joints[cfg.WRIST_BRANCH_JOINT],
                               2.0 * math.pi)), 6),
        })
    margins = [row["wrist_margin"] for row in rows]
    return {
        "pose": [round(float(v), 6) for v in pose],
        "candidates": rows,
        "min_margin": round(min(margins), 6) if margins else None,
        "branches": sorted({row["wrist_branch"] for row in rows}),
    }


def _plan_chain(scene, runtime, ik, manager, pick_pose, box, place, target,
                policy, start_q, start_tcp) -> dict:
    planner = runtime.planner
    out = {"policy": policy, "stages": {}, "ok": True}
    q = list(start_q)
    tcp = list(start_tcp)
    use_lock = policy in ("split_lock", "production")
    split = policy in ("split_free", "split_lock")

    approach_goals = [
        Goal("pre_pick", contact_pose(pick_pose, box,
                                      cfg.PRE_APPROACH_CLEARANCE)),
        Goal("pick", contact_pose(pick_pose, box, 0.0), cartesian=True,
             allow_target_contact=True, final_contact=True),
    ]
    context = MotionContext(
        phase="CONTACT", target_handle=1, material_pose=list(pick_pose),
        material_box=box, carrying=False, cup_radius=runtime.cup_radius,
        snapshot=f"probe-approach-{target}-{policy}")
    plan = planner.plan(q, approach_goals, context, start_pose=tcp,
                        lock_fallback=False)
    out["stages"]["approach"] = _record(plan)
    if not plan.ok:
        out["ok"] = False
        return out
    q = plan.waypoints[-1]
    tcp = planner.checker.tcp_at(q)
    attach = _relative_transform(tcp, pick_pose)
    runtime.set_attach_transform(attach)
    runtime.update_materials(manager, 1, carrying=True)

    lift_goal = _with_lock(
        Goal("lift", contact_pose(pick_pose, box,
                                  cfg.PRE_APPROACH_CLEARANCE),
             cartesian=True), use_lock)
    context = MotionContext(phase="LIFT", target_handle=-1, carrying=True,
                            snapshot=f"probe-lift-{target}-{policy}",
                            allow_start_load_contact=True)
    plan = planner.plan(q, [lift_goal], context, start_pose=tcp,
                        lock_fallback=False)
    out["stages"]["lift"] = _record(plan)
    if not plan.ok:
        out["ok"] = False
        return out
    q = plan.waypoints[-1]
    tcp = planner.checker.tcp_at(q)

    goals = transfer_goals(place, tcp, scene.layout.arm_base)
    if split:
        transit = next(goal for goal in goals if goal.name == "transit")
        high = Goal("high_above_place",
                    [place[0], place[1], transit.pose[2], *place[3:7]],
                    strict_orientation=True)
        goals.insert(goals.index(transit) + 1, high)
    if use_lock:
        goals = [_with_lock(goal, True) for goal in goals]
    else:
        goals = [replace(goal, lock_joint=None) for goal in goals]
    context = MotionContext(phase="PLACE", target_handle=-1, carrying=True,
                            snapshot=f"probe-transfer-{target}-{policy}")
    plan = planner.plan(q, goals, context, start_pose=tcp,
                        lock_fallback=False)
    out["stages"]["transfer"] = _record(plan)
    if not plan.ok:
        out["ok"] = False
        return out
    q = plan.waypoints[-1]
    tcp = planner.checker.tcp_at(q)
    runtime.set_attach_transform(None)

    material_pose = list(place)
    material_pose[2] -= (contact_pose(pick_pose, box, 0.0)[2]
                         - pick_pose[2])
    retreat = _with_lock(Goal("retreat", _raise(tcp,
                                                cfg.PRE_APPROACH_CLEARANCE),
                              cartesian=True, allow_target_contact=True),
                         use_lock)
    context = MotionContext(
        phase="RETREAT", target_handle=1,
        material_pose=material_pose, material_box=box, carrying=False,
        cup_radius=runtime.cup_radius,
        snapshot=f"probe-retreat-{target}-{policy}")
    plan = planner.plan(q, [retreat], context, start_pose=tcp,
                        lock_fallback=False)
    out["stages"]["retreat"] = _record(plan)
    out["ok"] = out["ok"] and plan.ok
    runtime.update_materials(manager, 1, carrying=False)
    return out


def _with_lock(goal: Goal, enabled: bool) -> Goal:
    if enabled and goal.name in cfg.Q0_LOCK_SEGMENTS:
        return replace(goal, lock_joint=cfg.Q0_LOCK_JOINT,
                       lock_tol=cfg.Q0_LOCK_TOL)
    return replace(goal, lock_joint=None)


def _record(plan) -> dict:
    return plan.summary_dict()


def _comparison(plans) -> dict:
    out = {}
    for target, policies in plans.items():
        out[target] = {}
        for policy, data in policies.items():
            stages = data.get("stages", {})
            segments = []
            for stage in stages.values():
                segments.extend(
                    (stage.get("metrics") or {}).get("segments", []))
            out[target][policy] = {
                "ok": data.get("ok"),
                "segments": [{
                    "name": segment.get("name"),
                    "tv_total": segment.get("tv_total"),
                    "max_step": segment.get("max_step"),
                    "points": segment.get("points"),
                    "q0_delta": segment.get("q0_delta"),
                    "lock_ok": segment.get("lock_ok"),
                    "wrist_margin_min": segment.get("wrist_margin_min"),
                    "min_workcell_distance": segment.get(
                        "min_workcell_distance"),
                } for segment in segments],
                "tv_total": round(sum(float(segment.get("tv_total") or 0.0)
                                      for segment in segments), 6),
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
