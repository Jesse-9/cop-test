"""
阶段0实测脚本（临时，不入库）：测量内置模型原生尺寸，写入 docs/measurement/。
每个模型加载后读取 objfloatparameter_modelbbox，测完即移除对象。
"""
import json
import sys
from pathlib import Path

from coppeliasim_zmqremoteapi_client import RemoteAPIClient

MODEL_BASE = Path(r"C:\Program Files\CoppeliaRobotics\CoppeliaSimEdu\models")

MODELS = {
    "conveyor_belt":  "equipment/conveyors/generic conveyor (belt).ttm",
    "ur5":            "robots/non-mobile/UR5.ttm",
    "suction_pad":    "components/grippers/suction pad.ttm",
    "pioneer_p3dx":   "robots/mobile/pioneer p3dx.ttm",
}

def _scalar(v):
    # 4.10 ZMQ API: getObjectFloatParameter 返回 (成功标志, 值) 二元组
    if isinstance(v, (tuple, list)):
        return v[1] if len(v) > 1 else 0.0
    return v

def bbox_info(sim, handle):
    minx = _scalar(sim.getObjectFloatParameter(handle, sim.objfloatparam_modelbbox_min_x))
    maxx = _scalar(sim.getObjectFloatParameter(handle, sim.objfloatparam_modelbbox_max_x))
    miny = _scalar(sim.getObjectFloatParameter(handle, sim.objfloatparam_modelbbox_min_y))
    maxy = _scalar(sim.getObjectFloatParameter(handle, sim.objfloatparam_modelbbox_max_y))
    minz = _scalar(sim.getObjectFloatParameter(handle, sim.objfloatparam_modelbbox_min_z))
    maxz = _scalar(sim.getObjectFloatParameter(handle, sim.objfloatparam_modelbbox_max_z))
    return {
        "minx": minx, "maxx": maxx, "len_x": maxx - minx,
        "miny": miny, "maxy": maxy, "len_y": maxy - miny,
        "minz": minz, "maxz": maxz, "len_z": maxz - minz,
    }

def main():
    client = RemoteAPIClient(host="localhost", port=23000)
    sim = client.require("sim")
    results = {}
    handles = []
    for key, rel in MODELS.items():
        path = str(MODEL_BASE / rel)
        h = sim.loadModel(path)
        handles.append(h)
        info = bbox_info(sim, h)
        results[key] = {"model": rel, "bbox": info}
        print(f"{key}: {rel} -> {info}")
    # cleanup
    for h in handles:
        try:
            sim.removeObject(h)
        except Exception as e:
            print("remove failed:", h, e)
    out = Path("docs/measurement")
    out.mkdir(parents=True, exist_ok=True)
    (out / "native_models.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print("WROTE", out / "native_models.json")

if __name__ == "__main__":
    main()
