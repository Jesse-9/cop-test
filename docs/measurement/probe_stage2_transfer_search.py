"""probe_stage2_transfer_search.py — 搜索带载转运的可行中转构型.

固定 lift 构型与 station 放置目标, 枚举:
  - 高位的笛卡尔中转位姿 (x/y/z 网格, 工具竖直);
  - 关节空间高位中转构型 (肩/肘/腕角网格);
对每个中转检查 lift→transit 与 transit→pre_place 的带载路径, 以及
pre_place→place 的法向下降链. 输出安全组合, 供冻结局部规划候选.

场景会在结束时恢复 (只做影子 FK/碰撞查询, 不启动仿真).
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
    runtime = build_stage2_runtime(sim, scene, ik)
    material = None
    manager = MaterialManager()
    report = {"timestamp": datetime.now().strftime("%Y%m%d_%H%M%S")}
    try:
        material = scene.spawn_material(
            1, "Probe_Part_0001", position=[-0.3076, 0.0, 0.5])
        manager.register(1, "Probe_Part_0001", sim_handle=material)
        pose = list(sim.getObjectPose(material, sim.handle_world))
        box = Stage2SuctionController(sim, manager, scene.tcp,
                                      scene.suction_sensor,
                                      pad_handle=scene.suction_root
                                      ).material_box(material)
        pick_tcp = contact_pose(pose, box, 0.0)
        lift_tcp = contact_pose(pose, box, 0.15)
        # pick/lift 构型
        pick_candidates = runtime.planner.solve_pose(
            pick_tcp, [0, 0, -1.5708, 0, 1.5708, 0], thorough=True)
        pick_q = None
        for q in pick_candidates:
            err = runtime.planner._pose_error(q, pick_tcp)
            if err[0] < 0.01 and err[1] < 0.05:
                pick_q = q
                break
        lift_candidates = runtime.planner.solve_pose(
            lift_tcp, pick_q, thorough=True)
        lift_q = None
        for q in lift_candidates:
            err = runtime.planner._pose_error(q, lift_tcp)
            if err[0] < 0.01 and err[1] < 0.05:
                lift_q = q
                break
        print("pick_q", [round(v, 3) for v in pick_q] if pick_q else None)
        print("lift_q", [round(v, 3) for v in lift_q] if lift_q else None)
        runtime.update_materials(manager, 1, carrying=True)
        runtime.set_attach_transform([0.0, 0.0, -0.05, 0.0, 0.0, 0.0, 1.0])
        context = MotionContext(phase="PLACE", target_handle=-1,
                                carrying=True)
        place = scene.layout.station_tcp_pose
        pre_place = list(place)
        pre_place[2] += cfg.PRE_APPROACH_CLEARANCE
        pre_candidates = runtime.planner.solve_pose(
            pre_place, lift_q, thorough=True)
        print("pre candidates", len(pre_candidates))
        runtime.checker.budget = PlanBudget(wall_s=600, max_samples=60000)

        def segment_ok(a, b, allow=False):
            check = runtime.checker.check_segment(
                a, b, context, screen=True, allow_start_load_contact=allow)
            return check.ok, check.status.value, check.message

        def chain_ok(q):
            ok, chain, status, message = runtime.planner._cartesian_chain(
                q, pre_place, Goal("place", place, cartesian=True,
                                   strict_orientation=True,
                                   allow_final_load_contact=True), context)
            return ok, status.value if status else None, message

        results = []
        # 1) 关节空间高位中转网格
        joint_transits = []
        for q1 in (0.0, -1.5, 1.5, -3.0):
            for q2 in (-0.8, -1.4, 0.4):
                for q3 in (-1.8, -1.0, 0.6):
                    for q4 in (-1.5, 0.0, 1.5):
                        joint_transits.append([q1, q2, q3, q4, 1.5708, 0.0])
        print("joint transits", len(joint_transits))
        for q_t in joint_transits:
            ok1, s1, m1 = segment_ok(lift_q, q_t, allow=True)
            if not ok1:
                continue
            tcp_t = runtime.checker.tcp_at(q_t)
            row = {"kind": "joint", "q": [round(v, 4) for v in q_t],
                   "tcp": [round(v, 4) for v in (tcp_t or [])[:3]],
                   "lift_transit": s1, "branches": []}
            for index, q_pre in enumerate(pre_candidates):
                ok2, s2, m2 = segment_ok(q_t, q_pre)
                if not ok2:
                    continue
                ok3, s3, m3 = chain_ok(q_pre)
                row["branches"].append({
                    "index": index, "q": [round(v, 4) for v in q_pre],
                    "transit_pre": s2, "chain_ok": ok3,
                    "chain_status": s3, "chain_message": m3})
            if any(b["chain_ok"] for b in row["branches"]):
                results.append(row)
                print("SAFE joint transit", row["q"], "tcp", row["tcp"],
                      "good branches", [b["index"] for b in row["branches"]
                                        if b["chain_ok"]])
        # 2) 高位笛卡尔中转位姿
        poses = []
        for x in (-0.45, -0.20, 0.0):
            for y in (0.7, 0.9, 1.05):
                for z in (0.70, 0.80, 0.88):
                    poses.append([x, y, z, 0.0, 0.0, 0.0, 1.0])
        print("pose transits", len(poses))
        for p in poses:
            candidates = runtime.planner.solve_pose(p, lift_q, thorough=True)
            for q_t in candidates:
                err = runtime.planner._pose_error(q_t, p)
                if err[0] > 0.01 or err[1] > 0.05:
                    continue
                ok1, s1, m1 = segment_ok(lift_q, q_t, allow=True)
                if not ok1:
                    continue
                row = {"kind": "pose", "pose": p,
                       "q": [round(v, 4) for v in q_t],
                       "lift_transit": s1, "branches": []}
                for index, q_pre in enumerate(pre_candidates):
                    ok2, s2, m2 = segment_ok(q_t, q_pre)
                    if not ok2:
                        continue
                    ok3, s3, m3 = chain_ok(q_pre)
                    row["branches"].append({
                        "index": index, "q": [round(v, 4) for v in q_pre],
                        "transit_pre": s2, "chain_ok": ok3,
                        "chain_status": s3, "chain_message": m3})
                if any(b["chain_ok"] for b in row["branches"]):
                    results.append(row)
                    print("SAFE pose transit", [round(v, 3) for v in p[:3]],
                          "good branches", [b["index"] for b in
                                            row["branches"]
                                            if b["chain_ok"]])
        report["results"] = results
    finally:
        print(runtime.destroy())
        if material is not None:
            scene.remove_material(material)
        for i in range(600):
            h = sim.getObjects(i, sim.handle_all)
            if h is None or int(h) < 0:
                break
            try:
                if (sim.getObjectAlias(h) or "").startswith(
                        ("Shadow", "Probe_Part")):
                    sim.removeObjects([int(h)])
            except Exception:
                pass
    path = OUT / f"stage2_transfer_search_{report['timestamp']}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    print("written", path, "safe", len(report["results"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
