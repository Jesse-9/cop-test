"""阶段一联调测试 — 单台 UR5 关节运动闭环 (重构方案 §9.9 阶段一).

需要 CoppeliaSim 4.10+ 在线且 ZMQ remote API 已激活; 不在线时整体 skip。

⚠ 本测试**会清空并用只含 A 岛机械臂的场景覆盖当前 CoppeliaSim 场景**
   (这是阶段一"只加载 A 岛机械臂"的要求), 且不自动还原。因此默认跳过,
   需显式设 `FACTORY_LIVE_SCENE_REBUILD=1` 才执行。跑完用
   `python src/build_scene.py` 恢复完整六臂场景。

流程 (§9.9 阶段一):
  1. 清场并只构建 A 岛机械臂 (§9.7 第 5 条)
  2. 六轴切 kinematic 模式
  3. joint1 相对 HOME 增加 0.3 rad, 保持约 1s 后回 HOME
  4. 逐步采样并断言: 实际位置变化 / 目标误差收敛 / 仿真时间推进

产物: factory_arm_phase1.log — 供计划文档 §6 验证记录引用。
"""

import logging
import os

import pytest

import build_scene as bs
import factory_config as cfg
from factory_robot import RobotArmDevice
from factory_scene import find_by_alias

ISLAND = "A"
JOINT_INDEX = 0                  # joint1 — 底座回转
DELTA = 0.3                      # rad — 相对 HOME 的增量
HOLD = 1.0                       # s   — 到位后保持时长

# [maxVel, maxAccel, maxJerk] (rad/s, rad/s², rad/s³)
# 0.5 rad/s → 0.3 rad 行程约 0.6s, 肉眼可见 (关节默认 1.57 rad/s 只有 0.19s)
MOTION_PARAMS = [0.5, 1.0, 5.0]
MOVE_SECONDS = DELTA / MOTION_PARAMS[0] + 0.4
SETTLE_TIMEOUT = 8.0             # 单次到位等待上限 (HOME 大行程需数秒)

TOL = cfg.ARM_POSITION_TOLERANCE
LOG_PATH = "factory_arm_phase1.log"

#: 破坏性联调的显式开关 — 未设置时整个模块 skip
REBUILD_ENV = "FACTORY_LIVE_SCENE_REBUILD"


def _record(sim, device, trace, log=None, every=5):
    """采样一次并 (可选) 写日志 — Sample joint state one step.

    反馈读取失败时 read_positions/read_targets 返回 None (不兜 0.0), 此处
    直接断言失败, 而不是把一个假的 0.0 采样写进轨迹。
    """
    pos = device.read_positions()
    target = device.read_targets()
    assert pos is not None and target is not None, \
        "关节反馈读取失败 (read_positions/read_targets 返回 None)"
    entry = {
        "t": sim.getSimulationTime(),
        "pos": pos,
        "target": target,
        "err": device.max_error(),
    }
    trace.append(entry)
    if log is not None and len(trace) % every == 0:
        log(f"  t={entry['t']:7.3f}s  j1={entry['pos'][0]:+.4f} rad  "
            f"j1目标={entry['target'][0]:+.4f}  max_err={entry['err']:.4f}")
    return entry


def _drive(sim, device, seconds, trace, log=None):
    """按固定时长步进采样 — Step the sim for a fixed duration."""
    for _ in range(max(1, int(round(seconds / cfg.SIM_STEP)))):
        sim.step()
        _record(sim, device, trace, log)
    return trace[-1]


def _drive_until_settled(sim, device, trace, log=None,
                         timeout: float = SETTLE_TIMEOUT) -> bool:
    """步进直到六轴实际位置收敛到目标 (§9.4 到位判据) 或超时。

    判据: max|current - target| < cfg.ARM_POSITION_TOLERANCE, 且连续两次
    采样都满足 (避免恰好穿过容差带的瞬时值)。
    """
    settled = 0
    for _ in range(int(round(timeout / cfg.SIM_STEP))):
        sim.step()
        entry = _record(sim, device, trace, log)
        settled = settled + 1 if entry["err"] < TOL else 0
        if settled >= 2:
            return True
    return False


@pytest.fixture(scope="module")
def phase1(coppeliasim_available):
    """建只含 A 岛机械臂的场景, 启动 stepping 仿真, 返回 (sim, device, log)。"""
    if not coppeliasim_available:
        pytest.skip("CoppeliaSim 未运行, 跳过阶段一联调")
    if os.environ.get(REBUILD_ENV) != "1":
        pytest.skip(
            f"阶段一联调会清空当前 CoppeliaSim 场景, 替换为「只含 A 岛机械臂」"
            f"且不自动还原, 故默认跳过; 确认可重建时设 {REBUILD_ENV}=1 再跑"
        )

    from coppeliasim_zmqremoteapi_client import RemoteAPIClient
    try:
        client = RemoteAPIClient(host="localhost", port=23000)
        sim = client.require("sim")
    except Exception as e:                      # noqa: BLE001
        pytest.skip(f"无法连接 CoppeliaSim: {e}")

    try:
        sim.stopSimulation()
    except Exception:                           # noqa: BLE001
        pass

    log_lines: list[str] = []
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        f.write("阶段一联调 — 单台 UR5 单关节运动闭环 (§9.9 阶段一)\n")

    def log(msg: str):
        log_lines.append(msg)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(msg + "\n")

    bs.clear_scene(sim)
    bs.build_scene(sim, arm_islands=[ISLAND])   # §9.7 第 5 条: 首次只启用 A 岛

    sim.setStepping(True)
    sim.startSimulation()

    device = RobotArmDevice(sim, ISLAND, cfg.ISLAND_ROBOTS[ISLAND],
                            logging.getLogger("robot-phase1"))
    log(f"设备: {device.alias} enabled={device.enabled} "
        f"joints={device.joints} tcp={device.tcp_handle} "
        f"gripper={device.gripper_handle}")
    try:
        yield sim, device, log
    finally:
        try:
            sim.stopSimulation()
        except Exception:                       # noqa: BLE001
            pass
        print(f"\n[阶段一] 注意: 场景已被替换为「只含 A 岛机械臂」且未自动还原。"
              f"\n         恢复完整六臂场景: .venv/Scripts/python src/build_scene.py")


def test_only_island_a_arm_exists_and_reaches_kinematic_mode(phase1):
    """只构建 A 岛机械臂, 且六轴切 kinematic 后写回校验一致。"""
    sim, device, log = phase1

    assert device.enabled, "A 岛机械臂未在场景中找到"
    # 其余五台不在场景中 (§9.9 阶段一: 只加载 A 岛机械臂)
    for key in ("B", "C", "D", "E_C", "E_D"):
        handle = find_by_alias(
            sim, cfg.ISLAND_ROBOTS[key]["alias"],
            sim.object_shape_type, sim.object_dummy_type,
        )
        assert handle < 0, f"{key} 岛机械臂不应在阶段一场景中"

    assert device.apply_control_mode("kinematic") is True
    assert device.joint_modes == [sim.jointmode_kinematic] * len(device.joints)
    log(f"控制模式: {device.control_mode} 写回={device.joint_modes}")


def test_single_joint_moves_settles_and_returns_home(phase1):
    """joint1 从 HOME +0.3 rad → 保持 1s → 回 HOME, 全程可测。"""
    sim, device, log = phase1
    home = cfg.ARM_ANIMATIONS["pick_and_place"][0]
    home_j1 = home[JOINT_INDEX]
    trace: list[dict] = []

    # 0) 六轴先到 HOME (大行程, 用运动轮廓慢慢走)
    device.command(home, MOTION_PARAMS)
    log(f"── 阶段 0: 六轴去 HOME {home}")
    assert _drive_until_settled(sim, device, trace, log), \
        f"HOME 未在 {SETTLE_TIMEOUT}s 内到位, max_err={device.max_error():.4f}"
    rest = device.read_positions()
    assert abs(rest[JOINT_INDEX] - home_j1) < TOL
    trace_at_home = len(trace)   # HOME 之后的轨迹才算"其余五轴不被误动"

    # 1) joint1 增加 DELTA, 保持约 1s
    log(f"── 阶段 1: joint{JOINT_INDEX + 1} {home_j1:+.3f} → "
        f"{home_j1 + DELTA:+.3f} rad (运动轮廓 {MOTION_PARAMS})")
    t_start = sim.getSimulationTime()
    trace_before_move = len(trace)
    device.command_joint(JOINT_INDEX, home_j1 + DELTA, MOTION_PARAMS)
    _drive(sim, device, MOVE_SECONDS + HOLD, trace, log)

    excursion = max(e["pos"][JOINT_INDEX]
                    for e in trace[trace_before_move:]) - rest[JOINT_INDEX]
    assert excursion >= DELTA * 0.9, \
        f"joint1 实际位移仅 {excursion:.4f} rad (期望 ≈{DELTA})"

    # 2) 收敛到目标 (§9.4 判据)
    assert _drive_until_settled(sim, device, trace, log), \
        f"joint1 未收敛, max_err={device.max_error():.4f}"
    hold_pos = device.read_positions()[JOINT_INDEX]
    assert abs(hold_pos - (home_j1 + DELTA)) < TOL, \
        f"joint1 停在 {hold_pos:+.4f}, 期望 {home_j1 + DELTA:+.4f}"

    # 3) 回 HOME
    log("── 阶段 2: joint1 回 HOME")
    device.command(home, MOTION_PARAMS)
    assert _drive_until_settled(sim, device, trace, log), \
        f"joint1 未回到 HOME, max_err={device.max_error():.4f}"
    back = device.read_positions()
    assert abs(back[JOINT_INDEX] - home_j1) < TOL

    # 4) 其余五轴在 HOME 到位后全程不被误动 (command_joint 只改索引 0)
    for i in range(1, len(device.joints)):
        drift = max(abs(e["pos"][i] - home[i])
                    for e in trace[trace_at_home:])
        assert drift < TOL, f"joint{i + 1} 被误动, 偏移 {drift:.4f} rad"

    # 5) 仿真时间确实推进
    span = trace[-1]["t"] - t_start
    assert span > MOVE_SECONDS, f"仿真时间只推进 {span:.3f}s"

    log(f"── 结果: 位移={excursion:.4f} rad, 保持点={hold_pos:+.4f} rad, "
        f"回零={back[JOINT_INDEX]:+.4f} rad, 末次 max_err={device.max_error():.4f}, "
        f"仿真时间 {trace[0]['t']:.2f}→{trace[-1]['t']:.2f}s")
