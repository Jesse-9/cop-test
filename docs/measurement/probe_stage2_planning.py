"""probe_stage2_planning.py — 阶段二运行时/规划器实机联调探针.

在已加载的 stage1 基线场景 (仿真停止态) 上:
  1. 构建 stage2 运行时 (影子臂/碰撞集合/查询自检/FK);
  2. 在取料点生成物料, 设置目标角色;
  3. 依次规划 approach / lift / transfer / retreat 四个运动;
  4. 输出 MotionPlan 状态、策略、段数、采样与耗时, 结束后删除物料与
     会话资源, 不保存场景.

运行:
    .venv/Scripts/python.exe docs/measurement/probe_stage2_planning.py
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from coppeliasim_zmqremoteapi_client import RemoteAPIClient

from stage1 import config as base
from stage1.material_manager import MaterialManager
from stage1.scene import adopt_scene
from stage1.ur5_ik_controller import Ur5IkController
from stage2 import config as cfg
from stage2.contact import contact_pose
from stage2.path_planning import Goal, MotionContext, PlanBudget
from stage2.runtime import build_stage2_runtime
from stage2.suction import Stage2SuctionController

OUT = Path("docs/measurement")


def main() -> int:
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
    report = {"timestamp": datetime.now().strftime("%Y%m%d_%H%M%S"),
              "plans": {}}
    try:
        runtime = build_stage2_runtime(sim, scene, ik)
        report["startup"] = runtime.startup
        print("startup fk", runtime.startup["fk"]["ok"],
              "cup_r", runtime.startup["cup_radius_measured"])

        material = scene.spawn_material(1, "Probe_Part_0001",
                                        position=scene.layout.pick_material_pose)
        manager.register(1, "Probe_Part_0001", sim_handle=material)
        runtime.update_materials(manager, 1, carrying=False)
        pose = list(sim.getObjectPose(material, sim.handle_world))
        box = Stage2SuctionController(sim, manager, scene.tcp,
                                      scene.suction_sensor,
                                      pad_handle=scene.suction_root
                                      ).material_box(material)
        context = MotionContext(phase="CONTACT", target_handle=material,
                                material_pose=pose, material_box=box,
                                snapshot="probe")
        pre = contact_pose(pose, box, cfg.PRE_APPROACH_CLEARANCE)
        pick = contact_pose(pose, box, 0.0)
        goals = [Goal("pre_pick", pre),
                 Goal("pick", pick, cartesian=True,
                      allow_target_contact=True, final_contact=True)]
        current_q = ik.joint_positions()
        current_tcp = ik.tcp_pose()
        plan = runtime.planner.plan(current_q, goals, context,
                                    start_pose=current_tcp)
        report["plans"]["approach"] = plan.as_dict()
        print("approach", plan.status.value, plan.strategy,
              "segments", len(plan.segments), "samples", plan.samples,
              f"{plan.wall_s:.2f}s", plan.message)
        if not plan.ok:
            print("  attempts:", json.dumps(plan.attempts, ensure_ascii=False))
        if plan.ok:
            # 规划成功后再继续用计划终点模拟携带
            tcp_pick = plan.waypoints[-1]
            for joint, q in zip(scene.robot_joints, tcp_pick):
                sim.setJointPosition(joint, float(q))
            attach = _relative_transform(
                list(sim.getObjectPose(scene.tcp, sim.handle_world)), pose)
            runtime.set_attach_transform(attach)
            runtime.update_materials(manager, 1, carrying=True)
            lift = contact_pose(pose, box, cfg.PRE_APPROACH_CLEARANCE)
            ctx_lift = MotionContext(phase="LIFT", target_handle=-1,
                                     carrying=True, snapshot="probe-lift")
            plan_lift = runtime.planner.plan(
                tcp_pick, [Goal("lift", lift, cartesian=True)], ctx_lift,
                start_pose=list(sim.getObjectPose(scene.tcp,
                                                  sim.handle_world)))
            report["plans"]["lift"] = plan_lift.as_dict()
            print("lift", plan_lift.status.value, plan_lift.strategy,
                  f"{plan_lift.wall_s:.2f}s", plan_lift.message)
            if plan_lift.ok:
                for joint, q in zip(scene.robot_joints, plan_lift.waypoints[-1]):
                    sim.setJointPosition(joint, float(q))
                place = scene.layout.station_tcp_pose
                pre_place = list(place)
                pre_place[2] += cfg.PRE_APPROACH_CLEARANCE
                tcp_now = list(sim.getObjectPose(scene.tcp,
                                                 sim.handle_world))
                rise = list(tcp_now)
                rise[2] += cfg.TRANSFER_RISE_HEIGHT
                base_x, base_y = scene.layout.arm_base[:2]
                dx = float(base_x) - float(place[0])
                dy = float(base_y) - float(place[1])
                norm = math.hypot(dx, dy)
                if norm > 1e-9:
                    dx, dy = dx / norm, dy / norm
                transit = [
                    place[0] + dx * cfg.TRANSFER_TRANSIT_OFFSET,
                    place[1] + dy * cfg.TRANSFER_TRANSIT_OFFSET,
                    max(cfg.TRANSFER_TRANSIT_HEIGHT_MIN, place[2] + 0.40),
                    *place[3:7],
                ]
                ctx_place = MotionContext(phase="PLACE", target_handle=-1,
                                          carrying=True, snapshot="probe-place")
                goals = [
                    Goal("rise", rise, cartesian=True),
                    Goal("transit", transit, strict_orientation=True),
                    Goal("pre_place", pre_place, strict_orientation=True),
                    Goal("place", place, cartesian=True,
                         strict_orientation=True,
                         allow_final_load_contact=True),
                ]
                plan_place = runtime.planner.plan(
                    plan_lift.waypoints[-1], goals, ctx_place,
                    start_pose=tcp_now)
                report["plans"]["transfer"] = plan_place.as_dict()
                print("transfer", plan_place.status.value,
                      plan_place.strategy, f"{plan_place.wall_s:.2f}s",
                      plan_place.message)
                if not plan_place.ok:
                    print("  attempts:", json.dumps(plan_place.attempts,
                                                    ensure_ascii=False))
    finally:
        if runtime is not None:
            print("destroy", runtime.destroy())
        if material is not None:
            scene.remove_material(material)
        for i in range(500):
            h = sim.getObjects(i, sim.handle_all)
            if h is None or int(h) < 0:
                break
            try:
                alias = sim.getObjectAlias(h) or ""
                if alias.startswith(("Shadow", "Probe_Part")):
                    sim.removeObjects([int(h)])
            except Exception:
                pass
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"stage2_planning_{report['timestamp']}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    print("written", path)
    return 0


def _relative_transform(tcp_pose, material_pose):
    from stage2.contact import (quat_conjugate, quat_multiply, quat_rotate)
    p = [float(v) for v in tcp_pose]
    m = [float(v) for v in material_pose]
    rel = quat_rotate(quat_conjugate(p[3:7]),
                      [m[0] - p[0], m[1] - p[1], m[2] - p[2]])
    q = quat_multiply(quat_conjugate(p[3:7]), m[3:7])
    return [*rel, *q]


if __name__ == "__main__":
    raise SystemExit(main())
