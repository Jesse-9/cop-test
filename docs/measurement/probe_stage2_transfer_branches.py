"""probe_stage2_transfer_branches.py — 诊断工位转运的安全 IK 分支与路径.

不做完整任务, 只回答三个问题:
  1. 从 lift 构型/HOME 构型求解 pre_place 时有哪些 IK 分支;
  2. 哪些分支的关节段本身不碰撞;
  3. 哪些分支的法向放置下降链完整通过检查.
"""

from __future__ import annotations

import json
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


def main() -> int:
    client = RemoteAPIClient(host="localhost", port=23000)
    sim = client.require("sim")
    simIK = client.require("simIK")
    scene = adopt_scene(sim)
    ik = Ur5IkController(sim, simIK, scene.robot_root, scene.tcp,
                         scene.handle(base.ALIASES["ik_target"]),
                         scene.robot_joints)
    ik.initialize()
    runtime = build_stage2_runtime(sim, scene, ik)
    material = None
    manager = MaterialManager()
    report = {"timestamp": datetime.now().strftime("%Y%m%d_%H%M%S")}
    try:
        material = scene.spawn_material(1, "Probe_Part_0001",
                                        position=scene.layout.pick_material_pose)
        manager.register(1, "Probe_Part_0001", sim_handle=material)
        runtime.update_materials(manager, 1, carrying=False)
        pose = list(sim.getObjectPose(material, sim.handle_world))
        box = Stage2SuctionController(sim, manager, scene.tcp,
                                      scene.suction_sensor,
                                      pad_handle=scene.suction_root
                                      ).material_box(material)
        ctx = MotionContext(phase="CONTACT", target_handle=material,
                            material_pose=pose, material_box=box)
        pre = contact_pose(pose, box, cfg.PRE_APPROACH_CLEARANCE)
        pick = contact_pose(pose, box, 0.0)
        plan = runtime.planner.plan(
            ik.joint_positions(),
            [Goal("pre_pick", pre),
             Goal("pick", pick, cartesian=True, allow_target_contact=True,
                  final_contact=True)], ctx, start_pose=ik.tcp_pose())
        print("approach", plan.status.value)
        if not plan.ok:
            return 1
        lift_q = plan.waypoints[-1]
        for joint, q in zip(scene.robot_joints, lift_q):
            sim.setJointPosition(joint, float(q))
        tcp_lift = list(sim.getObjectPose(scene.tcp, sim.handle_world))
        attach = _relative_transform(tcp_lift, pose)
        runtime.set_attach_transform(attach)
        runtime.update_materials(manager, 1, carrying=True)
        place_ctx = MotionContext(phase="PLACE", target_handle=-1,
                                  carrying=True)
        place = scene.layout.station_tcp_pose
        pre_place = list(place)
        pre_place[2] += cfg.PRE_APPROACH_CLEARANCE
        report["lift_q"] = [round(float(v), 5) for v in lift_q]
        report["pre_place"] = pre_place
        for label, seed in (("from_lift", lift_q),
                            ("from_home", list(base.ARM_SEED_JOINTS))):
            runtime.checker.budget = PlanBudget(wall_s=60.0, max_samples=6000)
            candidates = runtime.planner.solve_pose(
                pre_place, seed, thorough=True)
            rows = []
            print(f"{label}: {len(candidates)} candidates")
            for index, q in enumerate(candidates):
                seg = runtime.checker.check_segment(
                    seed, q, place_ctx, screen=True)
                chain_ok, chain, status, message = (False, [], None, "")
                if seg.ok:
                    chain_ok, chain, status, message = \
                        runtime.planner._cartesian_chain(
                            q, pre_place,
                            Goal("place", place, cartesian=True,
                                 strict_orientation=True,
                                 allow_final_load_contact=True), place_ctx)
                rows.append({
                    "index": index,
                    "q": [round(float(v), 4) for v in q],
                    "segment_ok": seg.ok,
                    "segment_status": seg.status.value,
                    "chain_ok": chain_ok,
                    "chain_status": (status.value if status else None),
                    "chain_message": message,
                    "chain_points": len(chain),
                })
                print("  ", index,
                      "seg", seg.status.value,
                      "chain", (status.value if status else "-"),
                      "chain_ok", chain_ok,
                      "q", rows[-1]["q"])
                if not chain_ok and message:
                    print("     ", message)
            report[label] = rows
    finally:
        if runtime is not None:
            print(runtime.destroy())
        if material is not None:
            scene.remove_material(material)
        for i in range(500):
            h = sim.getObjects(i, sim.handle_all)
            if h is None or int(h) < 0:
                break
            try:
                if (sim.getObjectAlias(h) or "").startswith(
                        ("Shadow", "Probe_Part")):
                    sim.removeObjects([int(h)])
            except Exception:
                pass
    path = Path("docs/measurement") / (
        f"stage2_transfer_branches_{report['timestamp']}.json")
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    print("written", path)
    return 0


def _relative_transform(tcp_pose, material_pose):
    from stage2.contact import quat_conjugate, quat_multiply, quat_rotate
    p = [float(v) for v in tcp_pose]
    m = [float(v) for v in material_pose]
    rel = quat_rotate(quat_conjugate(p[3:7]),
                      [m[0] - p[0], m[1] - p[1], m[2] - p[2]])
    q = quat_multiply(quat_conjugate(p[3:7]), m[3:7])
    return [*rel, *q]


if __name__ == "__main__":
    raise SystemExit(main())
