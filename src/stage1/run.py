"""run.py — 阶段一单元闭环运行入口 (stage1.run).

用法:
    .venv/Scripts/python.exe src/run_stage1.py --target station
    .venv/Scripts/python.exe src/run_stage1.py --target agv
    .venv/Scripts/python.exe src/run_stage1.py --target both --time 90
    .venv/Scripts/python.exe src/run_stage1.py --target station --fault ik_unreachable
    # 或安装后: uv run run-stage1 --target station

流程: 连接 → 构建阶段一场景 → 启动 stepping → 逐任务运行
      → 收集场景/物料/AGV 证据 → 停止仿真 → 写 JSON 报告。
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time
from datetime import datetime

from coppeliasim_zmqremoteapi_client import RemoteAPIClient

from . import config as cfg
from . import physics as phys
from .conveyor_controller import BeltGeometry, ConveyorController
from .material_manager import MaterialManager
from .pioneer_controller import PioneerController
from .scene import Stage1Scene, build_scene, alias_of, subtree
from .unit_controller import Stage1UnitController, UnitTaskState
from .suction_controller import SuctionController
from .ur5_ik_controller import Ur5IkController

# 原始运行输出不入库; 被报告引用的证据按需复制到
# docs/implementation/evidence/ (见该目录 README)。
REPORT_DIR = os.path.join("docs", "implementation", "runs")


# ═══════════════════════════════════════════════════════════════
# 连接 / 日志 / 仿真生命周期
# ═══════════════════════════════════════════════════════════════

def connect(host: str, port: int):
    client = RemoteAPIClient(host=host, port=port)
    sim = client.require("sim")
    simIK = client.require("simIK")
    version = ""
    try:
        version = sim.getStringProperty(sim.handle_app, "productVersion") or ""
    except Exception:
        pass
    print(f"[OK] connected to CoppeliaSim {version or '(version unknown)'}")
    return client, sim, simIK


def setup_logger(path: str) -> logging.Logger:
    logger = logging.getLogger("stage1")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    fh = logging.FileHandler(path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(message)s"))
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


def wait_state(sim, predicate, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if predicate(int(sim.getSimulationState())):
                return True
        except Exception:
            pass
        time.sleep(0.05)
    return False


def start_stepping(sim) -> None:
    if int(sim.getSimulationState()) != sim.simulation_stopped:
        sim.stopSimulation()
        wait_state(sim, lambda s: s == sim.simulation_stopped)
    sim.setStepping(True)
    sim.startSimulation()
    wait_state(sim, lambda s: s != sim.simulation_stopped)


def stop_simulation(sim) -> None:
    try:
        sim.setStepping(False)
    except Exception:
        pass
    try:
        sim.stopSimulation()
    except Exception:
        pass
    wait_state(sim, lambda s: s == sim.simulation_stopped)


# ═══════════════════════════════════════════════════════════════
# 组件装配
# ═══════════════════════════════════════════════════════════════

def build_components(sim, simIK, scene: Stage1Scene, logger):
    belt = scene.handle(cfg.ALIASES["conveyor"])
    entry = scene.handle(cfg.ALIASES["conveyor_entry"])
    stop = scene.handle(cfg.ALIASES["conveyor_stop"])
    entry_world = tuple(sim.getObjectPosition(entry, sim.handle_world))
    stop_world = tuple(sim.getObjectPosition(stop, sim.handle_world))

    conveyor = ConveyorController(sim)
    conveyor.register(cfg.ALIASES["conveyor"], belt, entry_world, stop_world,
                      cfg.CONVEYOR_SURFACE_Z)
    geometry = BeltGeometry(entry_world, stop_world)

    materials = MaterialManager()

    ik = Ur5IkController(
        sim, simIK, scene.robot_root, scene.tcp,
        scene.handle(cfg.ALIASES["ik_target"]), scene.robot_joints)
    ik.initialize()

    suction = SuctionController(
        sim, materials, tcp_handle=scene.tcp,
        sensor_handle=scene.suction_sensor,
        link_handle=scene.suction_link,
        release_parent=scene.handle(cfg.ALIASES["roots"][4]),
        logger=logger)

    agv = PioneerController(
        sim, scene.agv_left_wheel, scene.agv_right_wheel,
        cfg.AGV_WHEEL_RADIUS, cfg.AGV_WHEEL_BASE,
        chassis_handle=scene.agv_root)

    return {
        "conveyor": conveyor,
        "geometry": geometry,
        "materials": materials,
        "ik": ik,
        "suction": suction,
        "agv": agv,
    }


# ═══════════════════════════════════════════════════════════════
# 故障注入准备
# ═══════════════════════════════════════════════════════════════

def prepare_fault(sim, scene: Stage1Scene, fault: str,
                  materials: MaterialManager, logger) -> list[str]:
    notes = []
    if fault == cfg.FAULT_SENSOR_DISABLED:
        sensor = scene.handle(cfg.ALIASES["pick_sensor"])
        if sensor >= 0:
            sim.removeObjects([sensor])
            scene.handles[cfg.ALIASES["pick_sensor"]] = -1
            notes.append("已移除 Sensor_PickStation (传感器无读数)")
    elif fault == cfg.FAULT_UID_MISMATCH:
        wrong = scene.spawn_material(99, "Part_Wrong",
                                     position=scene.layout.pick_material_pose)
        materials.register(99, "Part_Wrong", sim_handle=wrong)
        notes.append("已在取料位生成非目标物料 Part_Wrong (uid=99)")
    elif fault == cfg.FAULT_AGV_BLOCKED:
        obs = int(sim.createPrimitiveShape(
            sim.primitiveshape_cuboid, [0.20, 0.50, 0.30]))
        sim.setObjectAlias(obs, "Fault_AGV_Obstacle")
        sim.setObjectPosition(
            obs, [scene.layout.agv_dock[0] + 0.55,
                  scene.layout.agv_dock[1], 0.15],
            sim.handle_world)
        phys.apply_static_shape(sim, obs, respondable=True)
        notes.append("已放置 AGV 路径障碍 Fault_AGV_Obstacle")
    return notes


# ═══════════════════════════════════════════════════════════════
# 证据采集
# ═══════════════════════════════════════════════════════════════

def _pose_dict(sim, handle: int) -> dict:
    return {
        "alias": alias_of(sim, handle),
        "handle": int(handle),
        "pose": [round(float(v), 5)
                 for v in sim.getObjectPose(handle, sim.handle_world)],
    }


def collect_evidence(sim, scene: Stage1Scene, comp, controller,
                     material_handle: int, target: str) -> dict:
    """采集证据; 仿真被外部停止/对象被移除时返回最小证据而不是抛异常。"""
    try:
        mat_pose = sim.getObjectPose(material_handle, sim.handle_world)
        expected = scene.layout.material_pose_for_target(target)
        tcp = sim.getObjectPose(scene.tcp, sim.handle_world)
        agv = comp["agv"]
        agv_pose = agv.pose()
        physics = phys.snapshot_physics(sim, material_handle)
    except Exception as exc:
        return {
            "target": target,
            "task": controller.summary() if controller else None,
            "error": f"证据采集失败: {exc}",
            "ok": False,
        }
    return {
        "target": target,
        "task": controller.summary() if controller else None,
        "material": {
            "pose": [round(float(v), 5) for v in mat_pose],
            "expected_pose": [round(float(v), 5) for v in expected],
            "position_error": round(math.dist(mat_pose[:3], expected[:3]), 5),
            "physics": physics,
            "dynamic_ok": physics.get("static") == 0,
            "respondable_ok": physics.get("respondable") == 1,
        },
        "tcp": {"pose": [round(float(v), 5) for v in tcp]},
        "arm_joints": [round(float(sim.getJointPosition(j)), 5)
                       for j in scene.robot_joints],
        "agv": {
            "pose": [round(float(v), 5) for v in agv_pose],
            "state": agv.state.name,
            "blocked": controller.agv_blocked if controller else False,
            "last_wheel_speeds": [round(agv.last_speeds.left, 4),
                                  round(agv.last_speeds.right, 4)],
        },
        "conveyor": {
            "drive_mode": comp["conveyor"].get(
                cfg.ALIASES["conveyor"]).drive_mode,
            "commanded_velocity": comp["conveyor"].get(
                cfg.ALIASES["conveyor"]).commanded_velocity,
            "actual_velocity": round(comp["conveyor"].read_actual_velocity(
                cfg.ALIASES["conveyor"]), 5),
        },
        "sim_time": round(float(sim.getSimulationTime()), 3),
    }


def collect_agv_load_follow(sim, agv_start_pose, agv_end_pose,
                            material_start_pos, material_handle) -> dict:
    mat_now = sim.getObjectPosition(material_handle, sim.handle_world)
    agv_dx = agv_end_pose[0] - agv_start_pose[0]
    mat_dx = mat_now[0] - material_start_pos[0]
    rel_x = float(mat_now[0]) - float(agv_end_pose[0])
    rel_y = float(mat_now[1]) - float(agv_end_pose[1])
    on_tray = (abs(rel_x) <= cfg.AGV_TRAY_XY_TOL
               and abs(rel_y) <= cfg.AGV_TRAY_XY_TOL
               and abs(float(mat_now[2])
                       - (cfg.AGV_TRAY_TOP_Z + cfg.MATERIAL_HALF_HEIGHT))
               <= cfg.AGV_TRAY_Z_TOL)
    return {
        "agv_dx": round(agv_dx, 4),
        "material_dx": round(mat_dx, 4),
        "follow_error": round(abs(agv_dx - mat_dx), 4),
        "material_z": round(float(mat_now[2]), 4),
        "relative_xy": [round(rel_x, 4), round(rel_y, 4)],
        "on_tray": bool(on_tray),
        "distance_ok": bool(agv_dx >= cfg.AGV_DRIVE_DISTANCE - 0.05),
    }


# ═══════════════════════════════════════════════════════════════
# conveyor 驱动探测
# ═══════════════════════════════════════════════════════════════

def probe_conveyor_drive(sim, scene: Stage1Scene, comp, steps: int = 10) -> dict:
    """确认内置 conveyor 脚本在驱动 pad, 并测量停车响应。

    1. 下发 CONVEYOR_SPEED, 先推进若干步等 customization 脚本重建 pad;
    2. 记录 pad 位移与 __state__.vel;
    3. 下发停止, 最多观察 20 步, 记录停车时间与滑行距离。
    """
    belt = scene.handle(cfg.ALIASES["conveyor"])
    conv = comp["conveyor"]
    conv.set_speed(cfg.ALIASES["conveyor"], cfg.CONVEYOR_SPEED)
    for _ in range(5):                 # 等脚本 init 重建 pad 句柄
        sim.step()
    pads = [h for h in subtree(sim, belt)
            if sim.getObjectType(h) == sim.object_shape_type
            and alias_of(sim, h) == "pad"]
    if not pads:
        return {"pad_count": 0, "pad_moved": 0.0,
                "error": "没有 pad shape"}
    p0 = list(sim.getObjectPosition(pads[0], belt))
    for _ in range(steps):
        sim.step()
    p1 = list(sim.getObjectPosition(pads[0], belt))
    moved = math.dist(p0, p1)
    handle = conv.get(cfg.ALIASES["conveyor"])
    running_velocity = conv.read_actual_velocity(cfg.ALIASES["conveyor"])

    # 停车响应: 模型 accel 已由 stage1_scene 写为 CONVEYOR_ACCEL
    conv.stop(cfg.ALIASES["conveyor"])
    t0 = float(sim.getSimulationTime())
    stop_steps = 0
    for _ in range(20):
        sim.step()
        stop_steps += 1
        if conv.read_actual_velocity(cfg.ALIASES["conveyor"]) <= 0.005:
            break
    p2 = list(sim.getObjectPosition(pads[0], belt))
    stop_time = float(sim.getSimulationTime()) - t0
    stop_moved = math.dist(p1, p2)
    return {
        "pad_count": len(pads),
        "pad_moved": round(moved, 5),
        "steps": steps,
        "drive_mode": handle.drive_mode,
        "script_handle": scene.conveyor_meta.get("script_handle", -1),
        "commanded_velocity": cfg.CONVEYOR_SPEED,
        "actual_velocity": round(running_velocity, 5),
        "script_drives_pads": moved > 1e-4,
        "model_accel": scene.conveyor_meta.get("config_accel"),
        "stop_time_s": round(stop_time, 3),
        "stop_steps": stop_steps,
        "stop_pad_moved_m": round(stop_moved, 5),
        "stop_ok": stop_time <= cfg.CONVEYOR_MAX_STOP_TIME,
    }


# ═══════════════════════════════════════════════════════════════
# 单任务运行
# ═══════════════════════════════════════════════════════════════

def run_task(sim, scene: Stage1Scene, comp, target: str, uid: int,
             fault: str, max_sim_time: float, logger) -> dict:
    materials: MaterialManager = comp["materials"]
    material = scene.spawn_material(uid, f"Part_{uid:04d}")
    materials.register(uid, f"Part_{uid:04d}", sim_handle=material)
    logger.info("=" * 70)
    logger.info("[TASK] uid=%s target=%s fault=%s spawn=%s", uid, target,
                fault, [round(v, 3) for v in cfg.MATERIAL_SPAWN_POS])

    controller = Stage1UnitController(
        sim=sim, scene=scene, materials=materials,
        conveyor=comp["conveyor"], belt_geometry=comp["geometry"],
        ik=comp["ik"], suction=comp["suction"], agv=comp["agv"],
        target=target, uid=uid, fault=fault, layout=scene.layout,
        logger=logger)

    agv_start_pose = comp["agv"].pose()
    material_start_pos = list(sim.getObjectPosition(material, sim.handle_world))
    task_start_sim_time = float(sim.getSimulationTime())

    wall_deadline = time.time() + max(120.0, max_sim_time * 6.0)
    steps = 0
    while not controller.finished:
        controller.step()
        steps += 1
        if sim.getSimulationTime() - task_start_sim_time > max_sim_time:
            controller._fail(f"SIM_TIME_LIMIT: {max_sim_time:.1f}s")
            break
        if time.time() > wall_deadline:
            controller._fail("WALL_CLOCK_TIMEOUT")
            break

    evidence = collect_evidence(sim, scene, comp, controller, material, target)
    evidence["steps"] = steps
    if target == "agv":
        # 等车体停稳后再取最终位姿
        for _ in range(60):
            if comp["agv"].is_stopped():
                break
            comp["agv"].stop()
            sim.step()
        mat_start = (controller._agv_material_start
                     if controller._agv_material_start is not None
                     else material_start_pos)
        try:
            follow = collect_agv_load_follow(
                sim, agv_start_pose, comp["agv"].pose(), mat_start, material)
            evidence["agv_load_follow"] = follow
            evidence["agv_follow_ok"] = (
                not controller.agv_blocked
                and follow["follow_error"] <= 0.08
                and follow["on_tray"]
                and follow["distance_ok"])
        except Exception as exc:
            evidence["agv_load_follow"] = {"error": str(exc)}
            evidence["agv_follow_ok"] = False

    ok = (controller.state is UnitTaskState.DONE
          and evidence["material"]["dynamic_ok"]
          and evidence["material"]["respondable_ok"])
    if target == "station":
        ok = ok and evidence["material"]["position_error"] <= cfg.MATERIAL_PLACE_TOL
    if target == "agv":
        ok = ok and evidence.get("agv_follow_ok", False)
    evidence["ok"] = bool(ok)
    logger.info("[TASK] uid=%s → %s (%s) steps=%s", uid,
                controller.state.name, controller.failure_reason, steps)
    return evidence, material


# ═══════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="阶段一单元闭环运行")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=23000)
    ap.add_argument("--target", choices=("station", "agv", "both"),
                    default="station")
    ap.add_argument("--fault", choices=cfg.FAULT_CHOICES,
                    default=cfg.FAULT_NONE)
    ap.add_argument("--time", type=float, default=90.0,
                    help="单任务最大仿真时间 (s)")
    ap.add_argument("--log", default=cfg.DEFAULT_LOG_FILE)
    ap.add_argument("--out-dir", default=REPORT_DIR)
    ap.add_argument("--repeat", type=int, default=1,
                    help="重复任务次数 (仅单目标; 用于 60s 连续运行)")
    ap.add_argument("--keep-running", action="store_true")
    ns = ap.parse_args(argv)

    logger = setup_logger(ns.log)
    client, sim, simIK = connect(ns.host, ns.port)

    try:
        stop_simulation(sim)
        scene = build_scene(sim, clear=True)
        logger.info("[SCENE] 构建完成: layout=%s %s", scene.layout.name, {
            "joints": len(scene.robot_joints),
            "arm_base": [round(v, 3) for v in scene.layout.arm_base],
            "station": [round(v, 3) for v in scene.layout.station_pos],
            "agv_dock": [round(v, 3) for v in scene.layout.agv_dock],
            "conveyor": {k: scene.conveyor_meta.get(k)
                         for k in ("actual_length", "surface_z",
                                   "entry_local_x", "stop_local_x")},
        })
    except Exception as exc:
        logger.exception("[SCENE] 构建失败: %s", exc)
        return 2

    os.makedirs(ns.out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    snapshot = {
        "timestamp": stamp,
        "config": {
            "target": ns.target,
            "fault": ns.fault,
            "max_sim_time": ns.time,
            "sim_step": cfg.SIM_STEP,
            "layout": scene.layout.name,
        },
        "layout": {
            "name": scene.layout.name,
            "arm_base": list(scene.layout.arm_base),
            "island_pos": list(scene.layout.island_pos),
            "station_pos": list(scene.layout.station_pos),
            "agv_dock": list(scene.layout.agv_dock),
        },
        "scene": scene.describe(),
        "build_report": scene.build_report,
        "measurements": "docs/measurement/phase0_measurements.json",
    }
    with open(os.path.join(ns.out_dir,
                           f"phase1_scene_snapshot_{stamp}.json"),
              "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    comp = build_components(sim, simIK, scene, logger)
    notes = prepare_fault(sim, scene, ns.fault, comp["materials"], logger)
    for n in notes:
        logger.info("[FAULT] %s", n)

    start_stepping(sim)
    logger.info("[SIM] stepping 启动, sim_time=%.3f", sim.getSimulationTime())
    drive_probe = probe_conveyor_drive(sim, scene, comp)
    logger.info("[CONVEYOR] 驱动探测: %s", drive_probe)

    if ns.repeat < 1:
        ap.error("--repeat 必须 >= 1")
    if ns.repeat > 1 and ns.target == "both":
        ap.error("--repeat>1 只能配合单一目标 (station 或 agv)")
    if ns.repeat > 1 and ns.target == "agv":
        ap.error("--repeat>1 不支持 agv 目标: 任务结束后车体已离开装卸位")
    targets = ["station", "agv"] if ns.target == "both" else [ns.target]
    targets = targets * ns.repeat if ns.target != "both" else targets
    results = []
    try:
        for idx, target in enumerate(targets, start=1):
            uid = idx
            evidence, material = run_task(
                sim, scene, comp, target, uid, ns.fault, ns.time, logger)
            results.append(evidence)
            if idx < len(targets):
                # 保持工位清洁, 便于重复任务/60s 连续运行
                scene.remove_material(material)
                comp["materials"].remove(uid)
    except Exception as exc:
        logger.exception("[TASK] 运行异常: %s", exc)
        results.append({"target": ns.target, "error": str(exc), "ok": False})
    finally:
        if ns.keep_running:
            logger.info("[SIM] --keep-running: 保持运行 (state=%s)",
                        sim.getSimulationState())
        else:
            stop_simulation(sim)

    summary = {
        "timestamp": stamp,
        "target": ns.target,
        "fault": ns.fault,
        "layout": scene.layout.name,
        "conveyor_drive_probe": drive_probe,
        "results": results,
        "all_ok": all(r.get("ok") for r in results),
    }
    out = os.path.join(ns.out_dir,
                       f"phase1_run_{ns.target}_{ns.fault}_{stamp}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    logger.info("[DONE] 报告写入 %s", out)
    return 0 if summary["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
