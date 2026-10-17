"""一次性实验: 验证 simIK addElementFromScene → 关节目标 的完整控制链.

不属于交付代码; 用于在编写阶段一模块前确认 4.10 的真实返回形态。
运行后清空本脚本创建的对象。
"""

from __future__ import annotations

import json
import math
import time

from coppeliasim_zmqremoteapi_client import RemoteAPIClient

MODEL_BASE = r"C:\Program Files\CoppeliaRobotics\CoppeliaSimEdu\models"

KEEP = {"DefaultCamera", "XYZCameraProxy", "XViewCamera", "YViewCamera",
        "ZViewCamera", "NXViewCamera", "NYViewCamera", "NZViewCamera",
        "DefaultLights", "LightA", "LightB", "LightC", "LightD"}


def clear_scene(sim):
    types = [sim.object_shape_type, sim.object_dummy_type, sim.object_joint_type,
             sim.object_proximitysensor_type, sim.object_visionsensor_type,
             sim.object_graph_type, sim.object_light_type, sim.object_camera_type,
             sim.object_mirror_type, sim.object_octree_type,
             sim.object_pointcloud_type, sim.object_forcesensor_type,
             sim.object_script_type]
    handles = []
    for t in types:
        i = 0
        while True:
            try:
                h = sim.getObjects(i, t)
            except Exception:
                break
            if h is None or h < 0:
                break
            try:
                a = sim.getObjectAlias(h)
            except Exception:
                a = ""
            if a not in KEEP:
                handles.append(int(h))
            i += 1
    if handles:
        sim.removeObjects(handles)
    # 二次清理孤立子对象
    handles = []
    for t in types:
        i = 0
        while True:
            try:
                h = sim.getObjects(i, t)
            except Exception:
                break
            if h is None or h < 0:
                break
            try:
                a = sim.getObjectAlias(h)
            except Exception:
                a = ""
            if a not in KEEP:
                handles.append(int(h))
            i += 1
    if handles:
        sim.removeObjects(handles)


def subtree(sim, root):
    tree = sim.getObjectsInTree(root, sim.handle_all, 0) or []
    return [int(h) for h in tree if h is not None and int(h) != root]


def strip_scripts(sim, root):
    removed = []
    for h in subtree(sim, root):
        if sim.getObjectType(h) == sim.object_script_type:
            try:
                sim.removeObjects([h])
                removed.append(h)
            except Exception:
                pass
    return removed


def chain_joints(sim, root, tip):
    chain, cur = [], tip
    while cur >= 0 and cur != root:
        chain.append(cur)
        cur = int(sim.getObjectParent(cur))
    return [h for h in reversed(chain)
            if sim.getObjectType(h) == sim.object_joint_type]


def step_n(sim, n):
    for _ in range(n):
        t = sim.getSimulationTime()
        sim.step()
        deadline = time.time() + 2
        while sim.getSimulationTime() <= t + 1e-9 and time.time() < deadline:
            time.sleep(0.001)


def main():
    client = RemoteAPIClient(host="localhost", port=23000)
    sim = client.require("sim")
    simIK = client.require("simIK")

    if sim.getSimulationState() != sim.simulation_stopped:
        sim.stopSimulation()
        time.sleep(0.5)
    clear_scene(sim)

    arm = int(sim.loadModel(MODEL_BASE + r"\robots\non-mobile\UR5.ttm"))
    sim.setObjectAlias(arm, "Robot_UR5_01")
    print("arm", arm, "scripts stripped", strip_scripts(sim, arm))

    # 全臂非动态 (4.10 kinematic + dynamic 连杆会塌陷)
    n_static = 0
    for h in subtree(sim, arm):
        if sim.getObjectType(h) == sim.object_shape_type:
            sim.setObjectInt32Param(h, sim.shapeintparam_static, 1)
            n_static += 1
    print("shapes made static", n_static)

    conn = None
    for h in subtree(sim, arm):
        if sim.getObjectAlias(h) == "connection":
            conn = h
            break
    joints = chain_joints(sim, arm, conn)
    print("joints", joints)
    seed = [0.0, -math.pi / 2, math.pi / 2, 0.0, 0.0, 0.0]
    for j, q in zip(joints, seed):
        sim.setJointMode(j, sim.jointmode_kinematic, 0)
        sim.setJointPosition(j, q)
    print("modes", [sim.getJointMode(j) for j in joints])
    print("conn world", sim.getObjectPosition(conn, sim.handle_world),
          sim.getObjectQuaternion(conn, sim.handle_world))

    pad = int(sim.loadModel(MODEL_BASE + r"\components\grippers\suction pad.ttm"))
    sim.setObjectParent(pad, conn, True)
    sim.setObjectPose(pad, [0, 0, 0, 0, 0, 0, 1], conn)
    sim.setObjectAlias(pad, "SuctionPad_01")
    print("pad scripts stripped", strip_scripts(sim, pad))
    tcp = int(sim.createDummy(0.01))
    sim.setObjectParent(tcp, pad, True)
    sim.setObjectPosition(tcp, [0, 0, -0.015], sim.handle_parent)
    sim.setObjectAlias(tcp, "SuctionPad_01_TCP")
    print("tcp world", sim.getObjectPosition(tcp, sim.handle_world))

    target = int(sim.createDummy(0.01))
    sim.setObjectAlias(target, "IK_Target_01")

    env = simIK.createEnvironment()
    group = simIK.createGroup(env)
    simIK.setGroupCalculation(env, group, simIK.method_pseudo_inverse, 0.0, 100)
    ret = simIK.addElementFromScene(env, group, arm, tcp, target,
                                    simIK.constraint_pose)
    print("addElementFromScene ->", ret)
    print("group joints ->", simIK.getGroupJoints(env, group) if hasattr(simIK, "getGroupJoints") else "no getGroupJoints")

    # 目标: 机器人前方 0.55m, 高 0.45m, 工具 Z 朝上 (接触面朝下)
    pose = [0.55, 0.0, 0.45, 0.0, 0.0, 0.0, 1.0]
    sim.setObjectPose(target, pose, sim.handle_world)
    simIK.syncFromSim(env, [group])
    res = simIK.handleGroup(env, group, {})
    print("handleGroup ->", res)

    ik_joints = simIK.getGroupJoints(env, group)
    ik_positions = [simIK.getJointPosition(env, ij) for ij in ik_joints]
    print("ik joints", ik_joints)
    print("ik positions", [round(v, 4) for v in ik_positions])

    # 用关节目标驱动 (不是直接写位置)。注意: 目标必须在 startSimulation 之后下发,
    # 否则会被 startSimulation 重置 (4.10 实测)。
    sim.setStepping(True)
    sim.startSimulation()
    for _ in range(50):
        if sim.getSimulationState() != sim.simulation_stopped:
            break
        time.sleep(0.05)
    for j, q in zip(joints, ik_positions):
        sim.setJointTargetPosition(j, q, [1.5, 6.0, 10.0])
    print("sim started:", sim.getSimulationState(), "t=", sim.getSimulationTime())
    for i in range(120):
        t = sim.getSimulationTime()
        sim.step()
        deadline = time.time() + 2
        while sim.getSimulationTime() <= t + 1e-9 and time.time() < deadline:
            time.sleep(0.001)
        if i in (0, 9, 39, 79, 119):
            print(f"  step {i}: t={sim.getSimulationTime():.2f} "
                  f"tcp={[round(v,4) for v in sim.getObjectPosition(tcp, sim.handle_world)]} "
                  f"q={[round(sim.getJointPosition(j),3) for j in joints]}")
    sim.setStepping(False)
    sim.stopSimulation()
    time.sleep(0.5)

    print("final tcp", sim.getObjectPosition(tcp, sim.handle_world))
    print("final target", sim.getObjectPosition(target, sim.handle_world))

    clear_scene(sim)
    print("scene cleared")


if __name__ == "__main__":
    main()
