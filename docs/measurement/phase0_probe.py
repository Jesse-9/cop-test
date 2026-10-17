"""阶段0 §1.2/§1.3 实测（非破坏性）
- 只测量本脚本自己 loadModel 出来的子树，不触碰场景中已有对象
- 测量完即 removeObject 自己加载的根，恢复场景
- stepping 仅在仿真未运行时临时启动，测完恢复原状态
"""
import json
from pathlib import Path

from coppeliasim_zmqremoteapi_client import RemoteAPIClient

MODEL_BASE = Path(r"C:\Program Files\CoppeliaRobotics\CoppeliaSimEdu\models")
OUT = Path("docs/measurement")


def subtree(sim, root):
    """遍历子树 (root 自身除外)"""
    out = []
    stack = [root]
    while stack:
        h = stack.pop()
        try:
            n = sim.getObjectChildCount(h)
        except Exception:
            continue
        for i in range(n):
            try:
                ch = sim.getObjectChild(h, i)
            except Exception:
                continue
            if ch >= 0:
                out.append(ch)
                stack.append(ch)
    return out


def alias(sim, h):
    try:
        return sim.getObjectAlias(h) or ""
    except Exception:
        return ""


def fparam(sim, h, p):
    v = sim.getObjectFloatParameter(h, p)
    return v[1] if isinstance(v, (tuple, list)) and len(v) > 1 else v


def obj_size(sim, h):
    return [fparam(sim, h, sim.objfloatparam_objbbox_max_x) - fparam(sim, h, sim.objfloatparam_objbbox_min_x),
            fparam(sim, h, sim.objfloatparam_objbbox_max_y) - fparam(sim, h, sim.objfloatparam_objbbox_min_y),
            fparam(sim, h, sim.objfloatparam_objbbox_max_z) - fparam(sim, h, sim.objfloatparam_objbbox_min_z)]


def collect(sim, root, label):
    """收集子树内的关节与 shape, 形成结构化报告"""
    joints, shapes = [], []
    for h in subtree(sim, root):
        try:
            t = sim.getObjectType(h)
        except Exception:
            continue
        if t == sim.object_joint_type:
            joints.append({
                "handle": h, "alias": alias(sim, h),
                "pos_local_to_root": sim.getObjectPosition(h, root),
            })
        elif t == sim.object_shape_type:
            shapes.append({
                "handle": h, "alias": alias(sim, h),
                "pos_local_to_root": sim.getObjectPosition(h, root),
                "size": obj_size(sim, h),
                "static": sim.getObjectInt32Parameter(h, sim.shapeintparam_static),
                "respondable": sim.getObjectInt32Parameter(h, sim.shapeintparam_respondable),
            })
    return {"label": label, "root": root, "joints": joints, "shapes": shapes}


def main():
    client = RemoteAPIClient(host="localhost", port=23000)
    sim = client.require("sim")
    report = {"coppeliaSimVersion_signal": sim.getStringSignal("coppeliaSimVersion")}

    # ── §1.2 步骤6: stepping 推进 ≥20 步 ──
    state_before = sim.getSimulationState()
    print("simulation state before:", state_before)
    if state_before != 4:
        sim.startSimulation()
    sim.setStepping(True)
    t0 = sim.getSimulationTime()
    steps = 0
    for _ in range(20):
        try:
            sim.step()
            steps += 1
        except Exception as e:
            print("step error:", e)
            break
    t1 = sim.getSimulationTime()
    dt = (t1 - t0)
    print(f"stepping OK: {steps} steps, simTime {t0:.3f} -> {t1:.3f} (Δ={dt:.3f}s)")
    report["stepping"] = {"steps": steps, "t0": t0, "t1": t1, "dt": dt,
                          "advanced": dt > 0}
    sim.setStepping(False)
    if state_before != 4:
        sim.stopSimulation()

    # ── UR5 ──
    r = sim.loadModel(str(MODEL_BASE / "robots/non-mobile/UR5.ttm"))
    ur5 = r[1] if isinstance(r, (tuple, list)) and len(r) > 1 else r
    info = collect(sim, ur5, "UR5")
    print(f"\nUR5: {len(info['joints'])} joints, {len(info['shapes'])} shapes")
    for j in info["joints"]:
        print("  joint", j["alias"], j["handle"])
    report["ur5"] = info
    sim.removeObject(ur5)
    print("UR5 移除 OK")

    # ── suction pad ──
    r = sim.loadModel(str(MODEL_BASE / "components/grippers/suction pad.ttm"))
    sp = r[1] if isinstance(r, (tuple, list)) and len(r) > 1 else r
    info = collect(sim, sp, "suction_pad")
    print(f"\nsuction pad: {len(info['joints'])} joints, {len(info['shapes'])} shapes")
    for s in info["shapes"]:
        print("  shape", s["alias"], "size", [round(x, 5) for x in s["size"]])
    # 记录子对象类型分布 (Link/Sensor/dummy 等)
    types = {}
    for h in subtree(sim, sp):
        try:
            t = sim.getObjectType(h)
            types[t] = types.get(t, 0) + 1
        except Exception:
            pass
    info["child_type_counts"] = types
    print("  child types:", types)
    report["suction_pad"] = info
    sim.removeObject(sp)
    print("suction pad 移除 OK")

    # ── conveyor ──
    r = sim.loadModel(str(MODEL_BASE / "equipment/conveyors/generic conveyor (belt).ttm"))
    cv = r[1] if isinstance(r, (tuple, list)) and len(r) > 1 else r
    info = collect(sim, cv, "conveyor")
    pads = [s for s in info["shapes"] if s["alias"] == "pad"]
    print(f"\nconveyor: {len(pads)} pads, {len(info['shapes'])} shapes")
    if pads:
        zs = [p["pos_local_to_root"][2] for p in pads]
        top_z = max(zs)
        top_pads = [p for p in pads if abs(p["pos_local_to_root"][2] - top_z) < 1e-4]
        xs = [p["pos_local_to_root"][0] for p in top_pads]
        print(f"  top-run z_local={top_z:.4f}, x range [{min(xs):.4f}, {max(xs):.4f}] "
              f"({len(top_pads)} pads)")
        info["top_run"] = {"z_local": top_z, "x_min": min(xs), "x_max": max(xs),
                           "n_pads": len(top_pads)}
    # 脚本子对象
    script_children = [h for h in subtree(sim, cv)
                       if alias(sim, h).lower().find("script") >= 0]
    print("  script-ish children:", [(h, alias(sim, h)) for h in script_children])
    info["script_children"] = [{"handle": h, "alias": alias(sim, h)}
                               for h in script_children]
    report["conveyor"] = info
    sim.removeObject(cv)
    print("conveyor 移除 OK")

    # ── pioneer p3dx ──
    r = sim.loadModel(str(MODEL_BASE / "robots/mobile/pioneer p3dx.ttm"))
    agv = r[1] if isinstance(r, (tuple, list)) and len(r) > 1 else r
    info = collect(sim, agv, "pioneer_p3dx")
    print(f"\np3dx: {len(info['joints'])} joints, {len(info['shapes'])} shapes")
    wheels = []
    for j in info["joints"]:
        a = j["alias"]
        if a in ("leftMotor", "rightMotor"):
            # 找该关节的子 shape (轮子本体)
            wheel_shape = None
            for s in info["shapes"]:
                # 位置接近关节位置者视为该轮的轮体
                d = sum((s["pos_local_to_root"][k] - j["pos_local_to_root"][k]) ** 2
                        for k in range(3)) ** 0.5
                if d < 0.06:
                    wheel_shape = s
                    break
            wheels.append({
                "joint": j["handle"], "alias": a,
                "joint_pos_local": j["pos_local_to_root"],
                "wheel_size_xyz": wheel_shape["size"] if wheel_shape else None,
            })
    for w in wheels:
        print("  wheel", w["alias"], "size", w["wheel_size_xyz"],
              "pos", [round(v, 4) for v in w["joint_pos_local"]])
    if len(wheels) >= 2:
        ys = [w["joint_pos_local"][1] for w in wheels]
        info["wheel_base_measured"] = abs(ys[0] - ys[1])
        print(f"  wheel_base (Y 间距) = {info['wheel_base_measured']:.4f} m")
        for w in wheels:
            if w["wheel_size_xyz"]:
                # 轮直径 = 竖直方向尺寸 (Z)
                info.setdefault("wheel_radius_measured", w["wheel_size_xyz"][2] / 2.0)
        print(f"  wheel_radius = {info.get('wheel_radius_measured')}")
    report["pioneer_p3dx"] = info
    sim.removeObject(agv)
    print("p3dx 移除 OK")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "phase0_measurements.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\nWROTE", OUT / "phase0_measurements.json")


if __name__ == "__main__":
    main()