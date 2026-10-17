"""阶段一 §2.10 实测: pioneer p3dx 直线行驶与原地转向.

构建阶段一场景后, 只通过左右轮 setJointTargetVelocity 驱动 AGV:
  1. 直线 v=0.15 m/s, 约 0.7 m;
  2. 原地转向 w=0.6 rad/s, 约 90°;
记录位移、航向、轮速与是否发生位姿瞬移。

结果写入 docs/measurement/agv_drive_test.json。
"""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

# 直接运行本脚本时把 src/ 加入路径, 以便 import stage1 包
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from coppeliasim_zmqremoteapi_client import RemoteAPIClient

from stage1 import config as cfg
from stage1 import scene as sc
from stage1.pioneer_controller import PioneerController


def yaw(q) -> float:
    x, y, z, w = [float(v) for v in q[:4]]
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def step(sim, n=1):
    for _ in range(n):
        t = sim.getSimulationTime()
        sim.step()
        deadline = time.time() + 2
        while sim.getSimulationTime() <= t + 1e-9 and time.time() < deadline:
            time.sleep(0.001)


def main() -> int:
    client = RemoteAPIClient(host="localhost", port=23000)
    sim = client.require("sim")
    if sim.getSimulationState() != sim.simulation_stopped:
        sim.stopSimulation()
        time.sleep(0.5)
    scene = sc.build_scene(sim, clear=True)
    agv = PioneerController(sim, scene.agv_left_wheel, scene.agv_right_wheel,
                            cfg.AGV_WHEEL_RADIUS, cfg.AGV_WHEEL_BASE,
                            chassis_handle=scene.agv_root)
    sim.setStepping(True)
    sim.startSimulation()
    for _ in range(100):
        if sim.getSimulationState() != sim.simulation_stopped:
            break
        time.sleep(0.05)

    p0 = agv.pose()
    straight_cmds = []
    for _ in range(100):                    # 5 s
        straight_cmds.append(agv.set_cmd(cfg.AGV_DRIVE_SPEED, 0.0))
        step(sim)
    agv.stop()
    step(sim, 10)
    p1 = agv.pose()

    yaw0 = yaw(p1[3:7])
    turn_cmds = []
    for _ in range(50):                     # 2.5 s at w=0.6 → ~1.5 rad (≈86°)
        turn_cmds.append(agv.set_cmd(0.0, 0.6))
        step(sim)
    agv.stop()
    step(sim, 20)
    p2 = agv.pose()

    result = {
        "straight": {
            "dx": round(p1[0] - p0[0], 4),
            "dy": round(p1[1] - p0[1], 4),
            "yaw_drift": round(yaw(p1[3:7]) - yaw(p0[3:7]), 4),
            "target_distance": cfg.AGV_DRIVE_DISTANCE,
            "wheel_speeds_first": [round(straight_cmds[0].left, 4),
                                   round(straight_cmds[0].right, 4)],
            "wheel_speeds_last": [round(straight_cmds[-1].left, 4),
                                  round(straight_cmds[-1].right, 4)],
        },
        "turn": {
            "yaw_change": round(yaw(p2[3:7]) - yaw0, 4),
            "dx": round(p2[0] - p1[0], 4),
            "dy": round(p2[1] - p1[1], 4),
            "wheel_speeds_first": [round(turn_cmds[0].left, 4),
                                   round(turn_cmds[0].right, 4)],
            "wheel_speeds_last": [round(turn_cmds[-1].left, 4),
                                  round(turn_cmds[-1].right, 4)],
        },
        "wheel_radius": cfg.AGV_WHEEL_RADIUS,
        "wheel_base": cfg.AGV_WHEEL_BASE,
        "no_chassis_pose_writes": True,     # 本脚本只调用 set_cmd/stop
    }
    out = Path(__file__).resolve().parent / "agv_drive_test.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    sim.setStepping(False)
    sim.stopSimulation()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"[OK] {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
