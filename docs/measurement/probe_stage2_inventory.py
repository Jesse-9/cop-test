"""probe_stage2_inventory.py — 阶段二 P2-0 只读几何盘点 + 4.10 碰撞 API 探针。

用途:
  1. 记录当前已加载场景 (stage1 v002 工件) 的 UR5/吸盘/conveyor/工位/AGV
     真实 shape 的 alias、父级、包围盒、collidable/measurable/static/
     respondable 属性;
  2. 用两个临时相交方块实测 4.10 `sim.checkCollision` / `sim.checkDistance`
     的返回形态和 collection 语义; 临时对象在探针结束时删除, 不保存场景。

运行 (仿真必须处于停止态):
    .venv/Scripts/python.exe docs/measurement/probe_stage2_inventory.py
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime
from pathlib import Path

from coppeliasim_zmqremoteapi_client import RemoteAPIClient

OUT_DIR = Path("docs/measurement")


def _shape_size(sim, h):
    try:
        v = sim.getShapeBB(h)
        size = v[0] if isinstance(v, (tuple, list)) and len(v) == 2 else v
        return [round(float(size[i]), 6) for i in range(3)]
    except Exception:
        return None


def _props(sim, h):
    out = {}
    for name, getter in (
            ("static", lambda: sim.getObjectInt32Param(h, sim.shapeintparam_static)),
            ("respondable", lambda: sim.getObjectInt32Param(
                h, sim.shapeintparam_respondable)),
            ("kinematic", lambda: sim.getObjectInt32Param(
                h, sim.shapeintparam_kinematic)),
            ("collidable", lambda: bool(sim.getBoolProperty(h, "collidable"))),
            ("measurable", lambda: bool(sim.getBoolProperty(h, "measurable"))),
    ):
        try:
            out[name] = int(getter())
        except Exception as exc:
            out[name] = f"ERR:{type(exc).__name__}"
    return out


def _subtree(sim, root):
    try:
        tree = sim.getObjectsInTree(root, sim.handle_all, 0) or []
    except Exception:
        return []
    return [int(h) for h in tree if h is not None and int(h) != int(root)]


def _describe(sim, h):
    try:
        parent = int(sim.getObjectParent(h))
    except Exception:
        parent = -1
    return {
        "handle": int(h),
        "alias": sim.getObjectAlias(h) or "",
        "type": int(sim.getObjectType(h)),
        "parent": parent,
        "parent_alias": (sim.getObjectAlias(parent) or "") if parent >= 0 else "",
        "world_pos": [round(float(v), 6)
                      for v in sim.getObjectPosition(h, sim.handle_world)],
        "bbox": _shape_size(sim, h),
        "props": _props(sim, h),
    }


def _probe_collision_api(sim):
    """用临时对象实测 checkCollision/checkDistance/collection 语义。"""
    out = {}
    a = int(sim.createPrimitiveShape(sim.primitiveshape_cuboid,
                                     [0.1, 0.1, 0.1]))
    b = int(sim.createPrimitiveShape(sim.primitiveshape_cuboid,
                                     [0.1, 0.1, 0.1]))
    c = int(sim.createPrimitiveShape(sim.primitiveshape_cuboid,
                                     [0.1, 0.1, 0.1]))
    sim.setObjectAlias(a, "Probe_A")
    sim.setObjectAlias(b, "Probe_B")
    sim.setObjectAlias(c, "Probe_C")
    sim.setObjectPosition(a, [2.0, 2.0, 0.05], sim.handle_world)
    sim.setObjectPosition(b, [2.05, 2.0, 0.05], sim.handle_world)  # 与 A 相交
    sim.setObjectPosition(c, [2.5, 2.0, 0.05], sim.handle_world)   # 远离
    try:
        out["checkCollision_overlap"] = _jsonable(sim.checkCollision(a, b))
        out["checkCollision_separated"] = _jsonable(sim.checkCollision(a, c))
        out["checkDistance_1cm"] = _jsonable(
            sim.checkDistance(a, c, 0.2))
        out["checkDistance_outside_threshold"] = _jsonable(
            sim.checkDistance(a, c, 0.01))
        out["checkDistance_no_threshold"] = _jsonable(
            sim.checkDistance(a, c, 0.0))

        # collection: 同一 collection 与自身检查时, 同一对象不与自身比较
        coll = int(sim.createCollection(0))
        sim.addItemToCollection(coll, sim.handle_single, a, 0)
        sim.addItemToCollection(coll, sim.handle_single, b, 0)
        sim.addItemToCollection(coll, sim.handle_single, c, 0)
        out["collection_self"] = _jsonable(sim.checkCollision(coll, coll))

        coll2 = int(sim.createCollection(0))
        sim.addItemToCollection(coll2, sim.handle_single, b, 0)
        out["collection_vs_collection"] = _jsonable(
            sim.checkCollision(coll, coll2))

        # collidable flag 是否覆盖查询参与性
        sim.setBoolProperty(c, "collidable", False)
        out["collidable_false_still_collides"] = _jsonable(
            sim.checkCollision(a, c))
        out["special_property_get"] = _jsonable(
            sim.getObjectSpecialProperty(a))
        out["special_property_collidable_const"] = int(
            sim.objectspecialproperty_collidable)
        out["selfcollision_intparam_constant"] = getattr(
            sim, "objintparam_collection_self_collision_indicator", None)
        out["sim_constants_present"] = {
            n: bool(hasattr(sim, n))
            for n in ("handle_single", "handle_tree", "handle_all",
                      "handle_chain", "destroyCollection",
                      "setObjectSpecialProperty", "getObjectSpecialProperty")
        }
        out["distance_between_collections"] = _jsonable(
            sim.checkDistance(coll, coll2, 0.0))
    finally:
        for h in (a, b, c):
            try:
                sim.removeObjects([h])
            except Exception:
                pass
        for h in (out.get("_coll"),):
            pass
    return out


def _jsonable(value):
    if isinstance(value, (tuple, list)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, bytes):
        return value.hex()
    return repr(value)


def main() -> int:
    client = RemoteAPIClient(host="localhost", port=23000)
    sim = client.require("sim")
    state = int(sim.getSimulationState())
    if state != int(sim.simulation_stopped):
        print(f"仿真未停止 (state={state}); 请先 stop", file=sys.stderr)
        return 2

    alias_by_handle = {}
    objects = []
    for i in range(400):
        h = sim.getObjects(i, sim.handle_all)
        if h is None or int(h) < 0:
            break
        h = int(h)
        row = _describe(sim, h)
        alias_by_handle[h] = row["alias"]
        objects.append(row)

    def find(alias):
        for row in objects:
            if row["alias"] == alias:
                return row["handle"]
        return -1

    report = {
        "timestamp": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "simulator": {
            "version": sim.getStringProperty(sim.handle_app, "productVersion"),
            "engine_code": int(sim.getInt32Param(sim.intparam_dynamic_engine)),
            "step": float(sim.getSimulationTimeStep()),
            "state": state,
            "sim_time": float(sim.getSimulationTime()),
        },
        "object_count": len(objects),
        "ur5": [],
        "suction": [],
        "conveyor": [],
        "agv": [],
        "station": [],
        "api_probe": _probe_collision_api(sim),
    }

    groups = {
        "ur5": find("Robot_UR5_01"),
        "suction": find("SuctionPad_01"),
        "conveyor": find("Conveyor_In"),
        "agv": find("Mobile_AGV_P3DX"),
    }
    for key, root in groups.items():
        if root < 0:
            continue
        report[key].append(_describe(sim, root))
        for h in _subtree(sim, root):
            report[key].append(_describe(sim, h))
    for alias in ("Island_A", "Island_A_Station", "Floor", "Warehouse_Raw"):
        h = find(alias)
        if h >= 0:
            report["station"].append(_describe(sim, h))

    # 顶层全量清单 (不含临时探针对象)
    report["objects"] = [row for row in objects
                         if not row["alias"].startswith("Probe_")]

    # 关键统计
    ur5_links = [r for r in report["ur5"]
                 if r["alias"] in ("link1_visible", "link2_visible",
                                   "link3_visible", "link4_visible",
                                   "link5_visible", "link6_visible",
                                   "link7_visible", "link", "Body")]
    pads = [r for r in report["conveyor"] if r["alias"] == "pad"]
    report["summary"] = {
        "ur5_link_shapes": len(ur5_links),
        "ur5_aliases": sorted({r["alias"] for r in report["ur5"]
                               if r["alias"]}),
        "conveyor_pads": len(pads),
        "conveyor_shape_aliases": sorted(
            {r["alias"] for r in report["conveyor"] if r["type"] == 0}),
        "agv_shape_count": sum(1 for r in report["agv"] if r["type"] == 0),
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"stage2_inventory_{report['timestamp']}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(json.dumps({"written": str(out), **report["summary"],
                      "api_probe": report["api_probe"]},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
