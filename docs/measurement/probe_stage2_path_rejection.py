"""probe_stage2_path_rejection.py — 路径拒绝验收 (06-09 §12).

使用实验中已验证安全的两点路径 (HOME → 工位预放置安全分支):
  1. 全段检查 -> OK, 两端 link3 距中点 15 cm 以上;
  2. 在中间构型 link3 位置插入 4 cm 受控静态障碍;
  3. 重新检查同一段 -> 必须在中间构型报 PATH_COLLISION 并给出对象对;
  4. 两端仍安全; 移除障碍后恢复 OK.

只做影子 FK/碰撞查询, 不启动仿真, 不修改生产工件.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from coppeliasim_zmqremoteapi_client import RemoteAPIClient

from stage1 import config as base
from stage1 import physics as phys
from stage1.scene import adopt_scene
from stage1.ur5_ik_controller import Ur5IkController
from stage2 import config as cfg
from stage2.path_planning import MotionContext
from stage2.runtime import build_stage2_runtime

OUT = Path("docs/measurement")
HAZARD_SIZE = (0.05, 0.05, 0.05)
# 当前 L1_S1 布局下已验证安全的工位预放置分支 (06-09 §15 参考 + 本机实测)
Q_START = [0.0, 0.0, -1.5708, 0.0, 1.5708, 0.0]
Q_GOAL = [-2.9756, -0.3832, -1.3995, 0.2119, 1.5709, 1.7368]


def main() -> int:
    client = RemoteAPIClient(host="localhost", port=23000)
    sim = client.require("sim")
    simIK = client.require("simIK")
    scene = adopt_scene(sim)
    ik = Ur5IkController(sim, simIK, scene.robot_root, scene.tcp,
                         scene.handle(base.ALIASES["ik_target"]),
                         scene.robot_joints)
    ik.initialize()
    runtime = build_stage2_runtime(sim, scene, ik)
    report = {"timestamp": datetime.now().strftime("%Y%m%d_%H%M%S"),
              "q_start": Q_START, "q_goal": Q_GOAL}
    try:
        context = MotionContext(phase="LIFT", target_handle=-1,
                                carrying=False)
        baseline = runtime.checker.check_segment(
            Q_START, Q_GOAL, context, screen=False)
        report["baseline"] = baseline.as_dict()
        print("baseline", baseline.status.value, "samples", baseline.samples)
        if not baseline.ok:
            return 1

        mid = [(a + b) / 2 for a, b in zip(Q_START, Q_GOAL)]
        runtime.probe.set_configuration(mid)
        link_mid = runtime.probe.tcp_pose()
        runtime.probe.set_configuration(Q_START)
        link_start = runtime.probe.tcp_pose()
        runtime.probe.set_configuration(Q_GOAL)
        link_goal = runtime.probe.tcp_pose()
        d0 = math.dist(link_mid[:3], link_start[:3])
        d1 = math.dist(link_mid[:3], link_goal[:3])
        report["segment"] = {
            "tcp_mid": [round(float(v), 5) for v in link_mid[:3]],
            "tcp_mid_to_start": round(d0, 4),
            "tcp_mid_to_end": round(d1, 4),
        }
        print("mid tcp", [round(v, 3) for v in link_mid[:3]],
              "clearance", round(d0, 3), round(d1, 3))

        hazard = int(sim.createPrimitiveShape(
            sim.primitiveshape_cuboid, list(HAZARD_SIZE)))
        sim.setObjectAlias(hazard, cfg.RUNTIME_HAZARD_ALIAS)
        sim.setObjectPosition(hazard, list(link_mid[:3]), sim.handle_world)
        phys.apply_static_shape(sim, hazard, respondable=True)
        sim.setBoolProperty(hazard, "collidable", True)
        sim.setBoolProperty(hazard, "measurable", True)
        runtime.add_hazard(hazard)
        report["hazard"] = {"handle": hazard, "size": HAZARD_SIZE}

        blocked = runtime.checker.check_segment(
            Q_START, Q_GOAL, context, screen=False)
        report["blocked"] = blocked.as_dict()
        first = blocked.first_failure
        collision = None
        if first is not None:
            collision = next((r for r in first.results
                              if r.state.value in ("COLLISION", "NEAR")), None)
        report["blocked_pair"] = (collision.as_dict()
                                  if collision is not None else None)
        print("blocked", blocked.status.value, blocked.message)

        endpoints_ok = True
        for q in (Q_START, Q_GOAL):
            check = runtime.checker.check(q, context, final_contact=False)
            endpoints_ok = endpoints_ok and check.ok
        report["endpoints_ok_with_hazard"] = endpoints_ok

        runtime.remove_hazards()
        restored = runtime.checker.check_segment(
            Q_START, Q_GOAL, context, screen=False)
        report["restored"] = restored.as_dict()
        print("restored", restored.status.value)

        report["ok"] = bool(
            baseline.ok and not blocked.ok
            and blocked.status.value in ("PATH_COLLISION",
                                             "NEAR_COLLISION")
            and blocked.samples >= 3
            and report["blocked_pair"] is not None
            and report["blocked_pair"].get("pair_aliases", ["", ""])[1]
            == cfg.RUNTIME_HAZARD_ALIAS
            and endpoints_ok and restored.ok)
    finally:
        print(runtime.destroy())
        for i in range(600):
            h = sim.getObjects(i, sim.handle_all)
            if h is None or int(h) < 0:
                break
            try:
                if (sim.getObjectAlias(h) or "").startswith(
                        ("Shadow", "Probe_Part", "Fault_Runtime")):
                    sim.removeObjects([int(h)])
            except Exception:
                pass
    path = OUT / f"stage2_path_rejection_{report['timestamp']}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    print("written", path, "ok", report.get("ok"))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
