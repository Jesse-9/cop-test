"""baseline.py — A 组“阶段一当前流程”对照 (06-07 §4.2/§4.3).

用真实的 `Stage1UnitController` 跑一次完整“传送带→UR5→工位”任务,
只在外部包一层 `ConveyorController.set_speed` 和取料传感器轮询, 记录:

  - 首次 sensor 命中时刻/位姿;
  - 首次零速命令时刻, 以及它发生在 sensor 命中之前还是之后;
  - 带面停止时刻与停车期间物料位移;
  - 任务最终状态与工位位置误差。

这是“确认当前流程是否发零速”的证据, 不预设其行为。
"""

from __future__ import annotations

import logging
import math
from typing import Optional

from stage1 import config as base
from stage1.material_manager import MaterialManager
from stage1.scene import alias_of, subtree
from stage1.unit_controller import Stage1UnitController, UnitTaskState


def find_pad(sim, belt_handle: int) -> int:
    for h in subtree(sim, belt_handle):
        try:
            if (sim.getObjectType(h) == sim.object_shape_type
                    and alias_of(sim, h) == "pad"):
                return int(h)
        except Exception:
            continue
    return -1


def _linear_speed(sim, handle: int) -> float:
    try:
        v = sim.getObjectVelocity(handle)
        lin = v[0] if isinstance(v, (tuple, list)) and len(v) == 2 \
            else list(v)[:3]
        return math.sqrt(sum(float(c) * float(c) for c in lin[:3]))
    except Exception:
        return 0.0


def _dist(a, b) -> float:
    return math.sqrt(sum((float(a[i]) - float(b[i])) ** 2 for i in range(3)))


def run_baseline_flow(sim, scene, comp, uid: int,
                      logger: Optional[logging.Logger] = None,
                      max_sim_time: float = 30.0) -> dict:
    """跑一次阶段一完整任务并返回停带时序记录。

    调用前仿真必须已经处于 stepping 运行状态。材料在入口生成;
    任务结束 (DONE/FAILED) 或超过 ``max_sim_time`` 后返回。
    """
    materials: MaterialManager = comp["materials"]
    material = scene.spawn_material(uid, f"Part_{uid:04d}")
    materials.register(uid, f"Part_{uid:04d}", sim_handle=material)

    controller = Stage1UnitController(
        sim=sim, scene=scene, materials=materials,
        conveyor=comp["conveyor"], belt_geometry=comp["geometry"],
        ik=comp["ik"], suction=comp["suction"], agv=comp["agv"],
        target="station", uid=uid, fault=base.FAULT_NONE, logger=logger)

    belt = scene.handle(base.ALIASES["conveyor"])
    pad_handle = -1
    speed_commands: list[list[float]] = []
    first_hit_time = None
    hit_pose = None
    hit_pad_pos = None
    hit_pad_vel = None
    first_zero_time = None
    zero_pose = None
    pad_at_zero = None
    zero_pad_vel = None
    pad_stop_time = None
    pad_stop_distance = None
    pad_prev = None
    pad_confirm = 0
    start_time = float(sim.getSimulationTime())
    steps = 0
    traj = []
    last_logged: Optional[float] = None
    original_set_speed = comp["conveyor"].set_speed

    def logged_set_speed(alias, velocity):
        nonlocal last_logged, first_zero_time, zero_pose
        nonlocal pad_at_zero, zero_pad_vel
        velocity = float(velocity)
        now = float(sim.getSimulationTime())
        if last_logged is None or abs(velocity - last_logged) > 1e-9:
            speed_commands.append([round(now, 5), velocity])
            last_logged = velocity
        if abs(velocity) < 1e-9 and first_zero_time is None:
            first_zero_time = round(now, 5)
            zero_pose = [round(float(v), 5) for v in
                         sim.getObjectPose(material, sim.handle_world)]
            if pad_handle >= 0:
                try:
                    pad_at_zero = list(sim.getObjectPosition(pad_handle, belt))
                except Exception:
                    pad_at_zero = None
            zero_pad_vel = round(float(
                comp["conveyor"].read_actual_velocity(
                    base.ALIASES["conveyor"])), 5)
            if logger:
                logger.info("[BASELINE] uid=%s 首次零速命令 t=%.2f", uid, now)
        return original_set_speed(alias, velocity)

    comp["conveyor"].set_speed = logged_set_speed

    try:
        while not controller.finished:
            t = float(sim.getSimulationTime())
            if t - start_time > max_sim_time:
                controller._fail(f"BASELINE_TIME_LIMIT: {max_sim_time:.1f}s")
                break
            if pad_handle < 0 and steps % 5 == 0:
                pad_handle = find_pad(sim, belt)
            pad_pos = None
            if pad_handle >= 0:
                try:
                    pad_pos = list(sim.getObjectPosition(pad_handle, belt))
                except Exception:
                    pad_pos = None
            pad_vel = comp["conveyor"].read_actual_velocity(
                base.ALIASES["conveyor"])
            mat_pose = list(sim.getObjectPose(material, sim.handle_world))

            hit = scene.check_sensor(base.ALIASES["pick_sensor"], material)
            if hit is not None and first_hit_time is None:
                first_hit_time = round(t, 5)
                hit_pose = [round(float(v), 5) for v in mat_pose]
                hit_pad_pos = list(pad_pos) if pad_pos is not None else None
                hit_pad_vel = round(float(pad_vel), 5)
                if logger:
                    logger.info("[BASELINE] uid=%s 首次 sensor 命中 t=%.2f "
                                "pad_vel=%.4f", uid, t, pad_vel)

            if first_zero_time is not None and pad_stop_time is None \
                    and pad_pos is not None:
                if pad_prev is None:
                    pad_prev = pad_at_zero if pad_at_zero is not None else pad_pos
                moved = _dist(pad_pos, pad_prev)
                if pad_vel <= 0.01 and moved <= 0.002:
                    pad_confirm += 1
                else:
                    pad_confirm = 0
                if pad_confirm >= 2:
                    pad_stop_time = round(t, 5)
                    if pad_at_zero is not None:
                        pad_stop_distance = round(
                            _dist(pad_pos, pad_at_zero), 5)
                pad_prev = list(pad_pos)

            if steps % 5 == 0:
                traj.append([round(t, 3),
                             round(float(mat_pose[0]), 5),
                             round(float(mat_pose[1]), 5),
                             round(float(mat_pose[2]), 5),
                             round(float(pad_vel), 5)])
            controller.step()
            steps += 1
    finally:
        comp["conveyor"].set_speed = original_set_speed

    final_pose = list(sim.getObjectPose(material, sim.handle_world))
    expected = cfg_station_material_pose()
    return {
        "uid": uid,
        "material_handle": int(material),
        "task_state": controller.state.name,
        "failure_reason": controller.failure_reason,
        "steps": steps,
        "sim_time_start": round(start_time, 3),
        "sim_time_end": round(float(sim.getSimulationTime()), 3),
        "first_sensor_hit_time": first_hit_time,
        "first_sensor_hit_pose": hit_pose,
        "first_sensor_hit_pad_velocity": hit_pad_vel,
        "first_zero_command_time": first_zero_time,
        "first_zero_pose": zero_pose,
        "first_zero_pad_velocity": zero_pad_vel,
        "zero_command_before_sensor": (
            None if first_zero_time is None or first_hit_time is None
            else bool(first_zero_time < first_hit_time)),
        "sensor_to_zero_delay_s": (
            None if first_zero_time is None or first_hit_time is None
            else round(first_zero_time - first_hit_time, 5)),
        "pad_stop_time": pad_stop_time,
        "pad_stop_duration_s": (
            None if pad_stop_time is None or first_zero_time is None
            else round(pad_stop_time - first_zero_time, 5)),
        "pad_stop_distance_m": pad_stop_distance,
        "material_displacement_hit_to_zero_m": (
            None if hit_pose is None or zero_pose is None
            else round(_dist(hit_pose, zero_pose), 5)),
        "speed_commands": speed_commands,
        "controller_events": list(controller.event_log[-40:]),
        "material_final_pose": [round(float(v), 5) for v in final_pose],
        "material_place_error_m": round(
            _dist(final_pose[:3], expected), 5),
        "material_linear_speed_final": round(_linear_speed(sim, material), 5),
        "trajectory_every_steps": 5,
        "trajectory": traj,
        "ok": bool(controller.state is UnitTaskState.DONE),
    }


def cfg_station_material_pose():
    """阶段一基线工位物料中心 (用于位置误差; 不依赖运行时布局)。"""
    return tuple(base.STATION_MATERIAL_POSE)
