"""阶段零实测 — 内置模型尺寸、关节、驱动与物理基线.

只测量本脚本自己 loadModel 出来的对象; 不清理、不修改场景中原有对象。
每个模型测完即删除自己加载的根对象, 并把仿真恢复到测量前的状态。

结果写入 docs/measurement/phase0_measurements.json, 作为阶段一场景
构建与配置的**唯一**数值来源。

用法:
    .venv/Scripts/python.exe docs/measurement/measure_phase0.py
"""

from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path

from coppeliasim_zmqremoteapi_client import RemoteAPIClient

MODEL_BASE = Path(r"C:\Program Files\CoppeliaRobotics\CoppeliaSimEdu\models")
OUT = Path(__file__).resolve().parent / "phase0_measurements.json"

MODELS = {
    "conveyor": "equipment/conveyors/generic conveyor (belt).ttm",
    "ur5": "robots/non-mobile/UR5.ttm",
    "suction_pad": "components/grippers/suction pad.ttm",
    "pioneer_p3dx": "robots/mobile/pioneer p3dx.ttm",
}


# ── 小工具 ────────────────────────────────────────────────────

def norm_handle(value) -> int:
    """loadModel 在不同 4.10 补丁版可能返回 int 或 (result, handle)。"""
    if isinstance(value, (tuple, list)):
        return int(value[-1])
    return int(value)


def alias(sim, h: int) -> str:
    try:
        return sim.getObjectAlias(h) or ""
    except Exception:
        return ""


def obj_type(sim, h: int) -> int:
    try:
        return int(sim.getObjectType(h))
    except Exception:
        return -1


def fparam(sim, h: int, p: int):
    v = sim.getObjectFloatParam(h, p)
    return v[1] if isinstance(v, (tuple, list)) and len(v) > 1 else v


def iparam(sim, h: int, p: int):
    v = sim.getObjectInt32Param(h, p)
    return v[1] if isinstance(v, (tuple, list)) and len(v) > 1 else v


def model_bbox(sim, h: int) -> dict | None:
    names = ("min_x", "min_y", "min_z", "max_x", "max_y", "max_z")
    vals = []
    for n in names:
        p = getattr(sim, f"objfloatparam_modelbbox_{n}", None)
        if p is None:
            return None
        try:
            vals.append(float(fparam(sim, h, p)))
        except Exception:
            return None
    return {
        "min": vals[:3],
        "max": vals[3:],
        "size": [vals[3] - vals[0], vals[4] - vals[1], vals[5] - vals[2]],
    }


def subtree(sim, root: int) -> list[int]:
    try:
        tree = sim.getObjectsInTree(root, sim.handle_all, 0)
    except Exception:
        return []
    out, seen = [], set()
    for h in tree or []:
        if h is None or int(h) < 0 or int(h) == root:
            continue
        if int(h) in seen:
            continue
        seen.add(int(h))
        out.append(int(h))
    return out


def shape_size(sim, h: int) -> list[float] | None:
    try:
        v = sim.getShapeBB(h)
        size = v[0] if isinstance(v, (tuple, list)) and len(v) == 2 else v
        if isinstance(size, (tuple, list)) and len(size) >= 3:
            return [float(size[0]), float(size[1]), float(size[2])]
    except Exception:
        pass
    return None


def parent_chain(sim, obj: int, root: int) -> list[int]:
    """从 obj 向上走到 root（不含 root），返回 [obj, parent, ...]。"""
    chain, cur = [], obj
    for _ in range(64):
        if cur < 0 or cur == root:
            break
        chain.append(cur)
        try:
            cur = int(sim.getObjectParent(cur))
        except Exception:
            break
    return chain


def chain_joints(sim, root: int, tip: int) -> list[int]:
    """按 base→tip 顺序返回运动链上的关节句柄。"""
    chain = parent_chain(sim, tip, root)
    joints = [h for h in chain if obj_type(sim, h) == sim.object_joint_type]
    joints.reverse()
    return joints


def wait_for_stopped(sim, timeout: float = 10.0):
    """stopSimulation 是异步的；等它真正回到 stopped 再加载下一个模型。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if int(sim.getSimulationState()) == sim.simulation_stopped:
                return True
        except Exception:
            pass
        time.sleep(0.05)
    return False


def start_stepping(sim, steps: int = 60):
    """临时启动 stepping 推进若干步，返回 (state_before, t0, t1)。"""
    state_before = int(sim.getSimulationState())
    if state_before == sim.simulation_stopped:
        sim.startSimulation()
        deadline = time.time() + 10.0
        while time.time() < deadline:
            if int(sim.getSimulationState()) != sim.simulation_stopped:
                break
            time.sleep(0.05)
    sim.setStepping(True)
    t0 = float(sim.getSimulationTime())
    step_n(sim, steps)
    t1 = float(sim.getSimulationTime())
    return state_before, t0, t1


def restore_sim_state(sim, state_before: int):
    try:
        sim.setStepping(False)
    except Exception:
        pass
    if state_before == sim.simulation_stopped:
        try:
            sim.stopSimulation()
        except Exception:
            pass
        wait_for_stopped(sim)


def remove_quiet(sim, handle: int):
    """删除模型时必须删整棵子树 — 只删根会把子对象变成孤立场景对象。"""
    try:
        tree = sim.getObjectsInTree(handle, sim.handle_all, 0)
        handles = [int(x) for x in (tree or []) if x is not None and int(x) >= 0]
        if int(handle) not in handles:
            handles.append(int(handle))
        sim.removeObjects(handles)
    except Exception:
        try:
            sim.removeObjects([int(handle)])
        except Exception:
            pass


def step_once(sim, timeout: float = 2.0) -> bool:
    """推进一个 stepping 步并等待 simTime 真正前进。"""
    t = float(sim.getSimulationTime())
    sim.step()
    deadline = time.time() + timeout
    while time.time() < deadline:
        if float(sim.getSimulationTime()) > t + 1e-9:
            return True
        time.sleep(0.001)
    return False


def step_n(sim, n: int) -> int:
    done = 0
    for _ in range(n):
        if not step_once(sim):
            break
        done += 1
    return done


# ── 各模型测量 ────────────────────────────────────────────────

def measure_conveyor(sim) -> dict:
    wait_for_stopped(sim)
    path = MODELS["conveyor"]
    h = norm_handle(sim.loadModel(str(MODEL_BASE / path)))
    out = {"model": path, "bbox": model_bbox(sim, h)}
    try:
        children = subtree(sim, h)
        pads, scripts = [], []
        for c in children:
            a = alias(sim, c)
            t = obj_type(sim, c)
            if a == "pad" and t == sim.object_shape_type:
                pads.append(c)
            if t == sim.object_script_type:
                scripts.append({
                    "handle": c,
                    "alias": a,
                    "script_type": iparam(sim, c, getattr(sim, "scriptintparam_type", -1))
                    if hasattr(sim, "scriptintparam_type") else None,
                })
        out["pad_count"] = len(pads)
        out["scripts"] = scripts

        # 上表面行程 = 最高一排 pad 的局部 X 范围
        entries = []
        for p in pads:
            try:
                pos = sim.getObjectPosition(p, h)
            except Exception:
                continue
            size = shape_size(sim, p)
            half_z = size[2] / 2.0 if size else 0.0
            entries.append((p, pos, size, half_z))
        if entries:
            best_top = max(pos[2] + half_z for _, pos, _, half_z in entries)
            thicknesses = [2 * half_z for _, _, _, half_z in entries if half_z > 0]
            tol = min(thicknesses) if thicknesses else 1e-3
            top = [(pos, size) for _, pos, size, half_z in entries
                   if abs(pos[2] + half_z - best_top) <= max(tol, 1e-4)]
            out["top_run"] = {
                "min_x": min(p[0] for p, _ in top),
                "max_x": max(p[0] for p, _ in top),
                "surface_off": best_top,
                "pad_size": top[0][1] if top else None,
                "top_pad_count": len(top),
            }

        # 驱动探测: 启动仿真, 观察 pad 是否移动, 并试写 __ctrl__ 带速
        state_before = int(sim.getSimulationState())
        entry = sim.getObjectPosition(pads[0], h) if pads else None
        state_before, t0, t1 = start_stepping(sim, 30)
        moved_after_30 = None
        if pads:
            moved_after_30 = math.dist(
                list(sim.getObjectPosition(pads[0], h)), list(entry))
        try:
            raw = sim.readCustomTableData(h, "__state__")
            out["default_state"] = raw
        except Exception as e:
            out["default_state"] = f"<error: {e}>"
        try:
            payload = (sim.packTable({"vel": 0.0})
                       if hasattr(sim, "packTable") else json.dumps({"vel": 0.0}))
            sim.setBufferProperty(h, "customData.__ctrl__", payload)
            step_n(sim, 20)
            raw0 = sim.readCustomTableData(h, "__state__")
            out["ctrl_zero_state"] = raw0
        except Exception as e:
            out["ctrl_zero_state"] = f"<error: {e}>"
        out["probe"] = {
            "sim_time_before": t0,
            "sim_time_after": t1,
            "pad_moved_m_after_30_steps": moved_after_30,
        }
        restore_sim_state(sim, state_before)
    finally:
        remove_quiet(sim, h)
    return out


def measure_ur5(sim) -> dict:
    wait_for_stopped(sim)
    path = MODELS["ur5"]
    h = norm_handle(sim.loadModel(str(MODEL_BASE / path)))
    out = {"model": path, "bbox": model_bbox(sim, h)}
    try:
        children = subtree(sim, h)
        joints = [c for c in children if obj_type(sim, c) == sim.object_joint_type]
        # 按运动链顺序重排 (从 connection/tip 向上找)
        tip = None
        for c in children:
            if alias(sim, c) in ("connection", "tip", "flange"):
                tip = c
                if alias(sim, c) == "connection":
                    break
        if tip is not None:
            joints = chain_joints(sim, h, tip)
        out["tip"] = {
            "handle": tip,
            "alias": alias(sim, tip) if tip is not None else None,
            "type": obj_type(sim, tip) if tip is not None else None,
            "local_pos": list(sim.getObjectPosition(tip, h)) if tip is not None else None,
            "local_quat": list(sim.getObjectQuaternion(tip, h)) if tip is not None else None,
        }
        out["joints"] = []
        for j in joints:
            try:
                cyclic, interval = sim.getJointInterval(j)
            except Exception:
                cyclic, interval = None, None
            out["joints"].append({
                "handle": j,
                "alias": alias(sim, j),
                "pos": float(sim.getJointPosition(j)),
                "mode": int(sim.getJointMode(j)[0]) if isinstance(
                    sim.getJointMode(j), (tuple, list)) else int(sim.getJointMode(j)),
                "cyclic": bool(cyclic) if cyclic is not None else None,
                "interval": list(interval) if interval is not None else None,
                "local_pos": list(sim.getObjectPosition(j, h)),
            })
        out["joint_count"] = len(joints)
        # 关节原点间距之和 — 可达半径上界参考
        out["chain_span"] = sum(
            math.dist(out["joints"][i]["local_pos"],
                      out["joints"][i + 1]["local_pos"])
            for i in range(len(out["joints"]) - 1)
        ) if len(out["joints"]) > 1 else None
        out["scripts"] = [{"handle": c, "alias": alias(sim, c)}
                          for c in children if obj_type(sim, c) == sim.object_script_type]
    finally:
        remove_quiet(sim, h)
    return out


def measure_suction(sim) -> dict:
    wait_for_stopped(sim)
    path = MODELS["suction_pad"]
    h = norm_handle(sim.loadModel(str(MODEL_BASE / path)))
    out = {"model": path, "bbox": model_bbox(sim, h)}
    try:
        children = subtree(sim, h)
        out["children"] = []
        for c in children:
            t = obj_type(sim, c)
            item = {
                "handle": c,
                "alias": alias(sim, c),
                "type": t,
                "local_pos": list(sim.getObjectPosition(c, h)),
                "local_quat": list(sim.getObjectQuaternion(c, h)),
            }
            if t == sim.object_proximitysensor_type:
                item["sensor"] = {
                    "local_pos": list(sim.getObjectPosition(c, h)),
                    "local_matrix": list(sim.getObjectMatrix(c, h)),
                }
            if t == sim.object_forcesensor_type:
                item["link"] = {
                    "local_pos": list(sim.getObjectPosition(c, h)),
                }
            if t == sim.object_shape_type:
                item["size"] = shape_size(sim, c)
            out["children"].append(item)
        # 接触面 = 包围盒最低点
        bb = out["bbox"]
        out["contact_face_local_z"] = bb["min"][2] if bb else None
        out["pad_thickness"] = (bb["size"][2] if bb else None)
    finally:
        remove_quiet(sim, h)
    return out


def measure_p3dx(sim) -> dict:
    wait_for_stopped(sim)
    path = MODELS["pioneer_p3dx"]
    h = norm_handle(sim.loadModel(str(MODEL_BASE / path)))
    out = {"model": path, "bbox": model_bbox(sim, h)}
    try:
        children = subtree(sim, h)
        joints = [c for c in children if obj_type(sim, c) == sim.object_joint_type]
        out["joints"] = []
        for j in joints:
            item = {
                "handle": j,
                "alias": alias(sim, j),
                "pos": float(sim.getJointPosition(j)),
                "local_pos": list(sim.getObjectPosition(j, h)),
            }
            if hasattr(sim, "jointintparam_motor_enabled"):
                try:
                    item["motor_enabled"] = int(iparam(
                        sim, j, sim.jointintparam_motor_enabled))
                except Exception:
                    pass
            out["joints"].append(item)
        wheels = [c for c in children
                  if obj_type(sim, c) == sim.object_shape_type
                  and "wheel" in alias(sim, c).lower()]
        out["wheels"] = [
            {"handle": c, "alias": alias(sim, c), "size": shape_size(sim, c),
             "local_pos": list(sim.getObjectPosition(c, h))}
            for c in wheels
        ]
        # 质量 / 惯量（车体根 shape）
        for prop in ("mass",):
            p = getattr(sim, f"shapefloatparam_{prop}", None)
            if p is not None:
                try:
                    out[f"root_{prop}"] = fparam(sim, h, p)
                except Exception:
                    pass
        inertia_attrs = [a for a in dir(sim) if "inertia" in a.lower()]
        out["inertia_attrs"] = inertia_attrs
        for a in inertia_attrs:
            if a.startswith("shapefloatparam_inertia"):
                try:
                    out[a] = fparam(sim, h, getattr(sim, a))
                except Exception:
                    pass
        out["script_children"] = [
            {"handle": c, "alias": alias(sim, c)}
            for c in children if obj_type(sim, c) == sim.object_script_type
        ]
    finally:
        remove_quiet(sim, h)
    return out


# ── 主流程 ────────────────────────────────────────────────────

def main() -> int:
    client = RemoteAPIClient(host="localhost", port=23000)
    sim = client.require("sim")

    version = ""
    try:
        version = sim.getStringProperty(sim.handle_app, "productVersion") or ""
    except Exception:
        pass
    if not version:
        try:
            version = sim.getStringSignal("coppeliaSimVersion") or ""
        except Exception:
            pass

    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "model_base": str(MODEL_BASE),
        "version": version,
        "models_present": {
            k: (MODEL_BASE / v).exists() for k, v in MODELS.items()
        },
    }

    state_before = int(sim.getSimulationState())
    if state_before == sim.simulation_stopped:
        sim.startSimulation()
        deadline = time.time() + 10.0
        while time.time() < deadline:
            if int(sim.getSimulationState()) != sim.simulation_stopped:
                break
            time.sleep(0.05)
    sim.setStepping(True)
    t0 = float(sim.getSimulationTime())
    step_n(sim, 20)
    t1 = float(sim.getSimulationTime())
    restore_sim_state(sim, state_before)
    report["stepping"] = {
        "steps": 20, "t0": t0, "t1": t1, "advanced": t1 > t0,
    }

    report["conveyor"] = measure_conveyor(sim)
    report["ur5"] = measure_ur5(sim)
    report["suction_pad"] = measure_suction(sim)
    report["pioneer_p3dx"] = measure_p3dx(sim)

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n[OK] 写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
