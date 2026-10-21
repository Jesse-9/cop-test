"""probe_stage2_ik_candidates.py — 阶段二多解 IK 与安全分支探针.

在真实 4.10 场景 (停止态) 中:
  1. 在 UR5 同基座加载影子臂, 校验 TCP/连杆 FK 与真实臂一致;
  2. 用给定的 curate seed 枚举取料/预接近/放置位姿的多个 IK 解;
  3. 对每个解用碰撞集合检查 workcell/mount/自碰撞, 输出 SAFE/BAD.

用途: 验证阶段二多解规划在当前原布局上确实存在安全构型, 并冻结
``stage2.config`` 的 seed 集合. 本脚本不修改生产场景 (结束时清理影子对象).

运行:
    .venv/Scripts/python.exe docs/measurement/probe_stage2_ik_candidates.py
"""

from __future__ import annotations

import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from coppeliasim_zmqremoteapi_client import RemoteAPIClient

from stage1 import config as base
from stage1 import physics as phys
from stage1.scene import (_chain_joints, _load_model, adopt_scene, alias_of,
                          subtree)
from stage1.ur5_ik_controller import Ur5IkController
from stage2 import config as cfg

OUT = Path("docs/measurement")

SEEDS = [
    [0.0, 0.0, -math.pi / 2, 0.0, math.pi / 2, 0.0],
    [0.0, 0.0, math.pi / 2, 0.0, math.pi / 2, 0.0],
    [0.0, -0.8, -1.2, 0.0, math.pi / 2, 0.0],
    [0.0, -0.8, 1.2, 0.0, math.pi / 2, 0.0],
    [0.0, 0.8, -1.2, 0.0, math.pi / 2, 0.0],
    [0.0, 0.8, 1.2, 0.0, math.pi / 2, 0.0],
    [0.0, 0.0, -math.pi / 2, 0.0, -math.pi / 2, 0.0],
    [0.0, 0.0, math.pi / 2, 0.0, -math.pi / 2, 0.0],
    [0.0, -0.8, -1.2, 0.0, -math.pi / 2, 0.0],
    [0.0, 0.8, 1.2, 0.0, -math.pi / 2, 0.0],
    [0.0, -0.8, 1.2, 0.0, -math.pi / 2, 0.0],
    [0.0, 0.8, -1.2, 0.0, -math.pi / 2, 0.0],
    [0.0, -1.5, -0.5, -2.0, 1.57, -1.5],
    [0.0, 1.5, -0.5, 2.0, 1.57, 1.5],
    [0.0, 0.0, -2.5, -1.57, 1.57, 0.0],
    [0.0, 0.0, 2.5, 1.57, 1.57, 0.0],
]


def find(root, alias):
    for h in [root] + subtree(SIM, root):
        if alias_of(SIM, h) == alias:
            return h
    return -1


def check(q):
    for j, value in zip(SHADOW_JOINTS, q):
        SIM.setJointPosition(j, float(value))
    work = SIM.checkCollision(COLL_ROBOT, COLL_WORKCELL)
    mount = SIM.checkCollision(COLL_NO_BASE, COLL_MOUNT)
    bad = []
    for i in range(len(LINKS)):
        for j in range(i + cfg.UR5_SELF_PAIR_MIN_GAP, len(LINKS)):
            if SIM.checkCollision(LINKS[i], LINKS[j])[0]:
                bad.append((i + 1, j + 1))
    for k in range(5):
        if SIM.checkCollision(BODY, LINKS[k])[0]:
            bad.append(("pad", k + 1))
    return {
        "ok": not work[0] and not mount[0] and not bad,
        "workcell": _q(work),
        "mount": _q(mount),
        "self": bad,
    }


def _q(ret):
    return {"collision": int(ret[0]), "pair": [int(v) for v in ret[1]]}


def main() -> int:
    global SIM, SHADOW_JOINTS, LINKS, BODY
    global COLL_ROBOT, COLL_WORKCELL, COLL_NO_BASE, COLL_MOUNT
    client = RemoteAPIClient(host="localhost", port=23000)
    SIM = client.require("sim")
    simIK = client.require("simIK")
    if int(SIM.getSimulationState()) != int(SIM.simulation_stopped):
        print("仿真未停止", file=sys.stderr)
        return 2
    scene = adopt_scene(SIM)
    island_root = scene.handle(base.ALIASES["roots"][2])

    arm = _load_model(SIM, "ur5", "Shadow_UR5_01", scene.layout.arm_base,
                      island_root)
    for h in [arm] + subtree(SIM, arm):
        if SIM.getObjectType(h) == SIM.object_shape_type:
            phys.set_static(SIM, h, True)
            phys.set_respondable(SIM, h, False)
    connection = -1
    for h in subtree(SIM, arm):
        if alias_of(SIM, h) == "connection":
            connection = h
            if SIM.getObjectType(h) == SIM.object_forcesensor_type:
                break
    SHADOW_JOINTS = _chain_joints(SIM, arm, connection)
    low, high = base.ARM_JOINT_LIMIT
    for j, q in zip(SHADOW_JOINTS, base.ARM_SEED_JOINTS):
        SIM.setJointInterval(j, False, [low, high - low])
        SIM.setJointMode(j, SIM.jointmode_kinematic, 0)
        SIM.setJointPosition(j, float(q))
    pad = _load_model(SIM, "suction_pad", "Shadow_SuctionPad_01", None,
                      connection)
    SIM.setObjectPose(pad, [0.0, 0.0, base.SUCTION_MOUNT_Z,
                            *base.SUCTION_MOUNT_QUATERNION], connection)
    for h in [pad] + subtree(SIM, pad):
        if SIM.getObjectType(h) == SIM.object_shape_type:
            phys.set_static(SIM, h, True)
            phys.set_respondable(SIM, h, False)
    LINKS = [find(arm, f"link{i}_visible")
             for i in range(1, cfg.UR5_LINK_COUNT + 1)]
    BODY = find(pad, "Body")

    workcell = [h for h in subtree(SIM, scene.handle(base.ALIASES["conveyor"]))
                if alias_of(SIM, h) == "pad"]
    for alias in (base.ALIASES["station"], base.ALIASES["warehouse"],
                  base.ALIASES["floor"]):
        workcell.append(scene.handle(alias))
    agv = scene.agv_root
    for h in subtree(SIM, agv):
        if SIM.getObjectType(h) == SIM.object_shape_type and not alias_of(
                SIM, h).startswith(("leftWheel", "rightWheel", "caster")):
            workcell.append(h)
    workcell = [h for h in workcell if h >= 0]
    COLL_ROBOT = int(SIM.createCollection(0))
    for h in LINKS + [BODY]:
        SIM.addItemToCollection(COLL_ROBOT, SIM.handle_single, h, 0)
    COLL_WORKCELL = int(SIM.createCollection(0))
    for h in workcell:
        SIM.addItemToCollection(COLL_WORKCELL, SIM.handle_single, h, 0)
    COLL_NO_BASE = int(SIM.createCollection(0))
    for h in LINKS[1:] + [BODY]:
        SIM.addItemToCollection(COLL_NO_BASE, SIM.handle_single, h, 0)
    COLL_MOUNT = int(SIM.createCollection(0))
    SIM.addItemToCollection(COLL_MOUNT, SIM.handle_single,
                            scene.handle(base.ALIASES["island"]), 0)

    ik = Ur5IkController(SIM, simIK, scene.robot_root, scene.tcp,
                         scene.handle(base.ALIASES["ik_target"]),
                         scene.robot_joints)
    ik.initialize()
    layout = scene.layout
    poses = {
        "pre_pick": [layout.pick_material_pose[0],
                     layout.pick_material_pose[1], 0.65, 0, 0, 0, 1],
        "pick": list(layout.pick_tcp_pose),
        "lift": [layout.pick_material_pose[0],
                 layout.pick_material_pose[1], 0.65, 0, 0, 0, 1],
        "pre_station": [layout.station_tcp_pose[0],
                        layout.station_tcp_pose[1],
                        layout.station_tcp_pose[2] + 0.15, 0, 0, 0, 1],
        "station": list(layout.station_tcp_pose),
        "pre_agv": [layout.agv_tcp_pose[0], layout.agv_tcp_pose[1],
                    layout.agv_tcp_pose[2] + 0.15, 0, 0, 0, 1],
        "agv": list(layout.agv_tcp_pose),
    }
    report = {"timestamp": datetime.now().strftime("%Y%m%d_%H%M%S"),
              "seeds": SEEDS, "poses": {}}
    try:
        for name, pose in poses.items():
            t0 = time.time()
            candidates = ik.solve_candidates(pose, SEEDS,
                                             max_candidates=cfg.IK_MAX_CANDIDATES)
            rows = []
            seen = []
            for result in candidates:
                q = [round(float(v), 4) for v in result.joints]
                if any(math.dist(q, other) <= 0.05 for other in seen):
                    continue
                seen.append(q)
                rows.append({"q": q, **check(result.joints)})
            report["poses"][name] = {
                "pose": [round(float(v), 5) for v in pose],
                "elapsed_s": round(time.time() - t0, 3),
                "solutions": len(rows),
                "safe": sum(1 for row in rows if row["ok"]),
                "rows": rows,
            }
            print(f"{name:12s} solutions={len(rows):2d} "
                  f"safe={report['poses'][name]['safe']:2d} "
                  f"{report['poses'][name]['elapsed_s']:.2f}s")
            for row in rows:
                print("   ", "SAFE" if row["ok"] else "BAD ", row["q"],
                      "work", row["workcell"], "mount", row["mount"],
                      "self", row["self"])
    finally:
        for coll in (COLL_ROBOT, COLL_WORKCELL, COLL_NO_BASE, COLL_MOUNT):
            try:
                SIM.destroyCollection(coll)
            except Exception:
                pass
        for root in (arm, pad):
            try:
                SIM.removeObjects([root])
            except Exception:
                pass
        for i in range(400):
            h = SIM.getObjects(i, SIM.handle_all)
            if h is None or int(h) < 0:
                break
            try:
                if (SIM.getObjectAlias(h) or "").startswith("Shadow"):
                    SIM.removeObjects([int(h)])
            except Exception:
                pass

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"stage2_ik_candidates_{report['timestamp']}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    print("written", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
