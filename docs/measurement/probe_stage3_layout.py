"""probe_stage3_layout.py — 阶段三布局静态筛选与当前场景只读测量.

用法:
    # 只做静态筛选 (不需要 CoppeliaSim)
    .venv/Scripts/python docs/measurement/probe_stage3_layout.py

    # 连接当前已加载的 stage3 场景, 追加只读测量
    .venv/Scripts/python docs/measurement/probe_stage3_layout.py --sim
    .venv/Scripts/python docs/measurement/probe_stage3_layout.py --sim \
        --out docs/measurement/stage3_layout_<stamp>.json

本探针不构建/不修改场景; 生产布局的完整 simIK/碰撞/动作质量证据来自
`src/run_stage3.py` 的 closed_loop / route_baseline 实机运行.
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

from stage3 import config as cfg
from stage3.layout import (default_layout, screen_candidates,
                           validate_layout, yaw_from_quat)


def static_report() -> dict:
    layout = default_layout()
    report = validate_layout(layout)
    screen = screen_candidates()
    return {
        "selected_layout": layout.name,
        "static_ok": report["ok"],
        "checks": report["checks"],
        "candidates": screen["candidates"],
        "units": {spec.unit_id: spec.as_dict() for spec in layout.units},
        "agv_route": {
            "initial_yaw": layout.agv_initial_yaw,
            "route_x": layout.agv_route_x,
            "route_y": list(layout.agv_route_y),
            "route_rect": list(layout.agv_route_rect),
        },
        "note": screen["note"],
    }


def sim_report() -> dict:
    from coppeliasim_zmqremoteapi_client import RemoteAPIClient
    from stage3.scene import adopt_stage3_scene

    client = RemoteAPIClient(host="localhost", port=23000)
    sim = client.require("sim")
    scene = adopt_stage3_scene(sim, default_layout())
    units = {}
    for unit_id in ("A", "B"):
        unit = scene.unit(unit_id)
        targets = unit.spec.targets()
        units[unit_id] = {
            "handles": unit.as_dict()["handles"],
            "arm_base": list(unit.spec.arm_base),
            "targets": {
                name: {
                    "pose": [round(float(v), 6) for v in pose],
                    "distance_from_base": round(
                        math.dist(unit.spec.arm_base, pose[:3]), 6),
                }
                for name, pose in targets.items()
            },
            "joint_positions": [
                round(float(sim.getJointPosition(j)), 6)
                for j in unit.robot_joints],
            "tcp_pose": [round(float(v), 6) for v in sim.getObjectPose(
                unit.tcp, sim.handle_world)],
            "dock_sensor_hit": scene.agv_dock_hit_at(unit_id, scene.agv_root),
        }
    agv_pose = list(sim.getObjectPose(scene.agv_root, sim.handle_world))
    return {
        "sim_time": round(float(sim.getSimulationTime()), 4),
        "sim_state": int(sim.getSimulationState()),
        "layout_name": scene.stage3_layout.name,
        "units": units,
        "agv": {
            "pose": [round(float(v), 6) for v in agv_pose],
            "yaw": round(yaw_from_quat(agv_pose[3:7]), 6),
            "expected_yaw": cfg.AGV_INITIAL_YAW,
        },
        "pick_zone": {
            "world": [round(float(v), 6) for v in cfg.PICK_ZONE_CENTER],
            "handle": scene.handle(cfg.ALIASES["pick_zone"]),
        },
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="阶段三布局筛选/只读测量")
    ap.add_argument("--sim", action="store_true",
                    help="连接 localhost:23000 并读取当前 stage3 场景")
    ap.add_argument("--out", default=None,
                    help="JSON 输出路径 (默认打印到 stdout)")
    ns = ap.parse_args(argv)

    payload = {"timestamp": datetime.now().strftime("%Y%m%d_%H%M%S"),
               "static": static_report()}
    if ns.sim:
        payload["sim"] = sim_report()
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if ns.out:
        path = Path(ns.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"[OK] 写入 {path}")
    else:
        print(text)
    return 0 if payload["static"]["static_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
