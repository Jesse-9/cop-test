"""probe_stage4_layout.py — 阶段四布局静态筛选与当前场景只读测量.

用法:
    # 只做静态筛选 (不需要 CoppeliaSim)
    .venv/Scripts/python docs/measurement/probe_stage4_layout.py

    # 连接当前已加载的 stage4 场景, 追加只读测量
    .venv/Scripts/python docs/measurement/probe_stage4_layout.py --sim
    .venv/Scripts/python docs/measurement/probe_stage4_layout.py --sim --ik

`--ik` 会为每台臂建立独立 simIK environment, 求解本岛 pick/place/dock
目标, 记录关节解与是否使用备用组; 求解结束后恢复 IK target dummy 位姿.
本探针不启动仿真、不驱动传送带、不创建/删除场景对象; 完整搬运/AGV 证据
来自 `src/run_stage4.py` 的验收场景.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from stage1 import config as base
from stage4 import config as cfg
from stage4.layout import default_layout, line_specs, validate_layout


def static_report() -> dict:
    layout = default_layout()
    report = validate_layout(layout)
    return {
        "layout_name": layout.name,
        "static_ok": report["ok"],
        "checks": report["checks"],
        "units": {spec.unit_id: spec.as_dict() for spec in layout.units},
        "lines": {line_id: spec.as_dict()
                  for line_id, spec in layout.lines.items()},
        "docks": {key: list(value)
                  for key, value in layout.dock_positions.items()},
        "agv_route_rect": list(layout.agv_route_rect),
        "note": "静态筛选不代替 simIK/碰撞查询; 生产运行证据来自 "
                "src/run_stage4.py",
    }


def _target_poses(unit) -> dict:
    if unit.unit_id == "A":
        return {
            "line_s1a_pick": base.tcp_pose_above(
                line_specs()["line_s1a"].pick_material_pose),
            "handoff_ac_place": base.tcp_pose_above(
                (cfg.HANDOFF_AC["position"][0],
                 cfg.HANDOFF_AC["position"][1],
                 base.STATION_TOP_Z + base.MATERIAL_HALF_HEIGHT)),
            "dock_A_place": list(unit.spec.agv_tcp_pose),
        }
    if unit.unit_id == "B":
        return {
            "line_s1b_pick": base.tcp_pose_above(
                line_specs()["line_s1b"].pick_material_pose),
            "dock_B_place": list(unit.spec.agv_tcp_pose),
        }
    if unit.unit_id == "C":
        return {
            "handoff_ac_pick": base.tcp_pose_above(
                (cfg.HANDOFF_AC["position"][0],
                 cfg.HANDOFF_AC["position"][1],
                 base.STATION_TOP_Z + base.MATERIAL_HALF_HEIGHT)),
            "line_ce_place": base.tcp_pose_above(
                line_specs()["line_ce"].place_material_pose),
        }
    if unit.unit_id == "D":
        return {
            "line_s2d_pick": base.tcp_pose_above(
                line_specs()["line_s2d"].pick_material_pose),
            "dock_D_place": list(unit.spec.agv_tcp_pose),
        }
    if unit.unit_id == "E":
        poses = {
            "line_ce_pick": base.tcp_pose_above(
                line_specs()["line_ce"].pick_material_pose),
            "dock_E_place": list(unit.spec.agv_tcp_pose),
        }
        for key in ("buffer_e_c", "buffer_e_d"):
            pos = cfg.E_BUFFERS[key]["position"]
            poses[f"{key}_place"] = base.tcp_pose_above(
                (pos[0], pos[1],
                 base.ISLAND_TOP_Z + base.MATERIAL_HALF_HEIGHT))
        return poses
    return {}


def sim_report(with_ik: bool = False) -> dict:
    from coppeliasim_zmqremoteapi_client import RemoteAPIClient
    from stage1.ur5_ik_controller import Ur5IkController
    from stage4.scene import adopt_stage4_scene

    client = RemoteAPIClient(host="localhost", port=23000)
    sim = client.require("sim")
    simIK = client.require("simIK")
    scene = adopt_stage4_scene(sim, default_layout())
    units = {}
    for unit_id in cfg.UNIT_IDS:
        unit = scene.unit(unit_id)
        poses = _target_poses(unit)
        row = {
            "handles": unit.as_dict()["handles"],
            "arm_base": list(unit.spec.arm_base),
            "targets": {
                name: {
                    "tcp_pose": [round(float(v), 6) for v in pose],
                    "distance_from_base": round(
                        math.dist(unit.spec.arm_base, pose[:3]), 6),
                }
                for name, pose in poses.items()
            },
            "joint_positions": [
                round(float(sim.getJointPosition(j)), 6)
                for j in unit.robot_joints],
            "tcp_pose": [round(float(v), 6) for v in sim.getObjectPose(
                unit.tcp, sim.handle_world)],
            "dock_sensor_hit": (
                scene.agv_dock_hit_at(unit_id, scene.agv_root)
                if unit.spec.has_dock else None),
        }
        if with_ik:
            ik = Ur5IkController(
                sim, simIK, unit.robot_root, unit.tcp, unit.ik_target,
                unit.robot_joints)
            ik.initialize()
            target_handle = unit.ik_target
            original = list(sim.getObjectPose(target_handle,
                                              sim.handle_world))
            solutions = {}
            try:
                for name, pose in poses.items():
                    result = ik.solve(list(pose))
                    joints = [round(float(q), 6) for q in result.joints]
                    within = all(base.ARM_JOINT_LIMIT[0] <= q
                                 <= base.ARM_JOINT_LIMIT[1] for q in joints) \
                        if result.joints else False
                    solutions[name] = {
                        "success": bool(result.success),
                        "used_backup": bool(getattr(result, "used_backup",
                                                    False)),
                        "message": getattr(result, "message", ""),
                        "joints": joints,
                        "within_limits": within,
                    }
            finally:
                sim.setObjectPose(target_handle, original,
                                  sim.handle_world)
            row["ik"] = solutions
        units[unit_id] = row
    agv_pose = list(sim.getObjectPose(scene.agv_root, sim.handle_world))
    from stage3.agv_route import pose_yaw
    return {
        "sim_time": round(float(sim.getSimulationTime()), 4),
        "sim_state": int(sim.getSimulationState()),
        "layout_name": scene.stage4_layout.name,
        "units": units,
        "lines": {
            line_id: {
                "handle": meta["handle"],
                "entry_world": [round(float(v), 6)
                                for v in meta["entry_world"]],
                "stop_world": [round(float(v), 6)
                               for v in meta["stop_world"]],
                "actual_length": round(float(meta["actual_length"]), 6),
                "actual_width": round(float(meta["actual_width"]), 6),
                "script_handle": meta["script_handle"],
                "config_accel": meta["config_accel"],
                "config_target_vel": meta["config_target_vel"],
            }
            for line_id, meta in scene.line_meta.items()
        },
        "agv": {
            "pose": [round(float(v), 6) for v in agv_pose],
            "yaw": round(pose_yaw(agv_pose), 6),
            "expected_yaw": cfg.AGV_INITIAL_YAW,
            "at_dock": {
                dock: scene.agv_dock_hit_at(dock, scene.agv_root)
                for dock in ("A", "B", "D", "E")},
        },
        "contract": {
            "checks": len(scene.verify().get("checks", [])),
            "ok": bool(scene.verify().get("ok")),
        },
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="阶段四布局筛选/只读测量")
    ap.add_argument("--sim", action="store_true",
                    help="连接 localhost:23000 并读取当前 stage4 场景")
    ap.add_argument("--ik", action="store_true",
                    help="为每台臂求解本岛目标 (不启动仿真)")
    ap.add_argument("--out", default=None,
                    help="JSON 输出路径 (默认打印到 stdout)")
    ns = ap.parse_args(argv)

    payload = {"timestamp": datetime.now().strftime("%Y%m%d_%H%M%S"),
               "static": static_report()}
    if ns.sim:
        payload["sim"] = sim_report(with_ik=ns.ik)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if ns.out:
        path = Path(ns.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"[OK] 写入 {path}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
