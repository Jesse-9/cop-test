"""layout_eval.py — 候选布局的目标集合、simIK 扫描与选择 (06-07 §3).

本模块只回答两个问题:
  1. 离散 TCP 目标是否可达、关节是否在限位内、允许区边界外还有多少余量;
  2. 哪个候选布局在满足全部目标的前提下余量最大。

它不检查 waypoint 之间的路径碰撞, 也不检查真实设备安全; 这些属于阶段二
(06-05 §3.2–§3.7)。"可达"在这里只表示 simIK 在离散点上返回成功。
"""

from __future__ import annotations

import math
from typing import Optional

from stage1 import config as base
from stage1.layout import LayoutSpec
from stage1.ur5_ik_controller import Ur5IkController
from . import config as cfg

# 允许区 9 个采样点的稳定命名, 与 PickRegion.boundary_points() 的顺序一致。
PICK_POINT_NAMES = (
    "pick_center",
    "pick_xmin_ymin", "pick_xmin_ymax",
    "pick_xmax_ymin", "pick_xmax_ymax",
    "pick_xmin_ycenter", "pick_xmax_ycenter",
    "pick_xcenter_ymin", "pick_xcenter_ymax",
)

# 选定布局后做实际关节运动验证的关键点 (中心 + 四角)。
KEY_PICK_POINTS = (
    "pick_center",
    "pick_xmin_ymin", "pick_xmax_ymin",
    "pick_xmin_ymax", "pick_xmax_ymax",
)


# ═══════════════════════════════════════════════════════════════
# 目标集合
# ═══════════════════════════════════════════════════════════════

def _tcp_above(material_center, dz: float) -> list[float]:
    return [float(material_center[0]), float(material_center[1]),
            float(material_center[2]) + float(dz),
            *base.TOOL_TARGET_QUATERNION]


def layout_targets(layout: LayoutSpec) -> dict[str, list[float]]:
    """生成候选布局的离散 TCP 目标。

    取料区: 9 个允许区采样点 × (抓取 / 垂直接近 / 抬升)。
    另加工位与 AGV 的放置点和接近点。
    """
    targets: dict[str, list[float]] = {}
    points = cfg.ALLOWED_PICK_REGION.boundary_points()
    for name, center in zip(PICK_POINT_NAMES, points):
        targets[name] = _tcp_above(center, base.MATERIAL_HALF_HEIGHT)
        targets[f"{name}_approach"] = _tcp_above(
            center, base.MATERIAL_HALF_HEIGHT + cfg.PICK_APPROACH_CLEARANCE)
        targets[f"{name}_lift"] = _tcp_above(
            center, base.MATERIAL_HALF_HEIGHT + cfg.PICK_LIFT_CLEARANCE)

    targets["station"] = list(layout.station_tcp_pose)
    targets["station_approach"] = [*layout.station_tcp_pose[:3],
                                   *base.TOOL_TARGET_QUATERNION]
    targets["station_approach"][2] += cfg.PICK_APPROACH_CLEARANCE
    targets["agv"] = list(layout.agv_tcp_pose)
    targets["agv_approach"] = [*layout.agv_tcp_pose[:3],
                               *base.TOOL_TARGET_QUATERNION]
    targets["agv_approach"][2] += cfg.PICK_APPROACH_CLEARANCE
    return targets


# ═══════════════════════════════════════════════════════════════
# simIK 扫描
# ═══════════════════════════════════════════════════════════════

def _round(values, digits: int = 5):
    if values is None:
        return None
    return [round(float(v), digits) for v in values]


def scan_targets(pose_ik, position_ik, targets: dict[str, list[float]]) -> dict:
    """逐个目标求解 pose 约束与 position-only 诊断约束。

    ``pose_ik`` 是生产约束 (位置 + 姿态); ``position_ik`` 只用于区分
    “位置不可达”和“姿态不可达”。生产可达性只认 pose 结果。
    """
    records = []
    failed = []
    for name, pose in targets.items():
        pose_res = pose_ik.solve(list(pose))
        pos_res = position_ik.solve(list(pose)) if position_ik is not None else None
        joints_ok = (pose_ik.joints_within_limits(pose_res.joints)
                     if pose_res.success else False)
        record = {
            "name": name,
            "pose": _round(pose),
            "pose_success": bool(pose_res.success),
            "pose_precision": _round(pose_res.precision),
            "pose_reason": int(pose_res.reason),
            "pose_message": pose_res.message,
            "pose_joints": _round(pose_res.joints) if pose_res.success else None,
            "pose_used_backup": bool(pose_res.used_backup),
            "pose_used_seed": bool(pose_res.used_seed),
            "joints_within_limits": bool(joints_ok),
            "position_success": bool(pos_res.success) if pos_res else None,
            "position_precision": _round(pos_res.precision) if pos_res else None,
            "position_reason": int(pos_res.reason) if pos_res else None,
        }
        records.append(record)
        if not (record["pose_success"] and record["joints_within_limits"]):
            failed.append(name)

    return {
        "targets": records,
        "all_pose_ok": not failed,
        "all_position_ok": all(t["position_success"]
                               for t in records
                               if t["position_success"] is not None),
        "all_joints_within_limits": all(t["joints_within_limits"]
                                        for t in records),
        "failed_names": failed,
    }


def boundary_scan(pose_ik, center_pose, region,
                  step: float = cfg.BOUNDARY_SCAN_STEP,
                  max_distance: float = cfg.BOUNDARY_SCAN_MAX,
                  directions=cfg.BOUNDARY_SCAN_DIRECTIONS) -> dict:
    """从允许区中心沿 ±X/±Y 向外探测, 记录最后可达距离与首次失败。

    余量 = 最后可达距离 - 允许区在该方向的半宽。负值表示允许区边界
    本身已经不可达, 该候选不能用于生产。
    """
    cx, cy, cz = [float(v) for v in center_pose[:3]]
    out: dict[str, dict] = {}
    n_steps = int(math.floor(max_distance / step + 1e-9))
    for label, (dx, dy) in directions:
        if dx > 0:
            extent = region.x[1] - cx
        elif dx < 0:
            extent = cx - region.x[0]
        elif dy > 0:
            extent = region.y[1] - cy
        else:
            extent = cy - region.y[0]
        last_ok = 0.0
        first_failure: Optional[float] = None
        for i in range(1, n_steps + 1):
            dist = i * step
            pose = [cx + dx * dist, cy + dy * dist, cz,
                    *base.TOOL_TARGET_QUATERNION]
            res = pose_ik.solve(pose)
            if not res.success or not pose_ik.joints_within_limits(res.joints):
                first_failure = dist
                break
            last_ok = dist
        out[label] = {
            "extent_m": round(extent, 5),
            "last_reachable_m": round(last_ok, 5),
            "first_failure_m": (round(first_failure, 5)
                                if first_failure is not None else None),
            "margin_m": round(last_ok - extent, 5),
            "status": ("failed" if first_failure is not None
                       else "scan_limit"),
        }
    return out


# ═══════════════════════════════════════════════════════════════
# 布局选择
# ═══════════════════════════════════════════════════════════════

def select_layout(candidate_reports: list[dict]) -> dict:
    """在全部目标可达的候选中选边界余量最大者。

    规则在运行前固定:
      - 静态几何通过;
      - 全部 pose 目标成功且关节在限位内;
      - 选择 ``min(boundary margins)`` 最大者;
      - 并列时比较 ``min(reach margins)``, 再按名称字典序。
    """
    eligible = []
    ineligible: dict[str, str] = {}
    scores: dict[str, dict] = {}
    for report in candidate_reports:
        name = report.get("layout", "?")
        reason = None
        if not report.get("static_ok", False):
            reason = "静态几何校验失败"
        elif not report.get("all_pose_ok", False):
            reason = "存在不可达的 pose 目标"
        elif not report.get("all_joints_within_limits", True):
            reason = "IK 解超出关节限位"
        if reason is not None:
            ineligible[name] = reason
            continue

        margins = [v.get("margin_m") for v in report.get("boundary", {}).values()]
        margins = [float(m) for m in margins if m is not None]
        boundary_score = min(margins) if margins else float("-inf")
        reach = [float(v) for v in report.get("reach_margins", {}).values()]
        reach_score = min(reach) if reach else float("-inf")
        scores[name] = {"boundary_margin_m": round(boundary_score, 5),
                        "reach_margin_m": round(reach_score, 5)}
        eligible.append((boundary_score, reach_score, name))

    if not eligible:
        return {"ok": False, "selected": None, "ineligible": ineligible,
                "scores": scores,
                "reason": "没有候选通过全部目标与关节限位检查"}

    eligible.sort(key=lambda item: (-item[0], -item[1], item[2]))
    boundary_score, reach_score, name = eligible[0]
    return {
        "ok": True,
        "selected": name,
        "score": round(boundary_score, 5),
        "reach_score": round(reach_score, 5),
        "ineligible": ineligible,
        "scores": scores,
        "rule": "max min(boundary margin); tie -> min(reach margin) -> name",
    }


# ═══════════════════════════════════════════════════════════════
# 实机扫描 / 运动验证
# ═══════════════════════════════════════════════════════════════

class IkScanner:
    """为一台已构建的候选臂建立 pose / position-only 两个 IK 环境。"""

    def __init__(self, sim, simIK, scene) -> None:
        self.sim = sim
        self.simIK = simIK
        self.scene = scene
        self.pose_ik = Ur5IkController(
            sim, simIK, scene.robot_root, scene.tcp,
            scene.handle(base.ALIASES["ik_target"]), scene.robot_joints,
            constraint=simIK.constraint_pose)
        position_constraint = getattr(
            simIK, "constraint_position", simIK.constraint_pose)
        self.position_probe_available = hasattr(simIK, "constraint_position")
        self.position_ik = Ur5IkController(
            sim, simIK, scene.robot_root, scene.tcp,
            scene.handle(base.ALIASES["ik_target"]), scene.robot_joints,
            constraint=position_constraint)
        self.pose_ik.initialize()
        self.position_ik.initialize()

    def scan(self, targets: dict[str, list[float]]) -> dict:
        return scan_targets(self.pose_ik, self.position_ik, targets)

    def boundary(self, layout: LayoutSpec) -> dict:
        return boundary_scan(self.pose_ik, layout.pick_tcp_pose,
                             cfg.ALLOWED_PICK_REGION)


def _move_to(sim, ik, name: str, pose: list[float],
             timeout: float = cfg.ARM_MOVE_TIMEOUT) -> dict:
    """求解并实际驱动关节到目标; 记录真实 TCP 误差与关节状态。"""
    res = ik.solve(list(pose))
    record = {
        "name": name,
        "pose": _round(pose),
        "ik_success": bool(res.success),
        "ik_message": res.message,
        "ik_precision": _round(res.precision),
        "ik_used_backup": bool(res.used_backup),
        "ik_used_seed": bool(res.used_seed),
        "ik_joints": _round(res.joints) if res.success else None,
        "success": False,
        "elapsed_s": None,
        "tcp_pos_error_m": None,
        "tcp_ang_error_rad": None,
        "joints": None,
        "joints_within_limits": None,
        "joint_speed": None,
        "tcp_speed": None,
        "failure_reason": "",
    }
    if not res.success:
        record["failure_reason"] = "IK 不可达"
        return record

    ik.apply_joints(res.joints)
    t0 = float(sim.getSimulationTime())
    max_steps = max(1, int(round(timeout / base.SIM_STEP)))
    for _ in range(max_steps):
        sim.step()
        pos_err = ik.tcp_position_error(pose)
        ang_err = ik.tcp_orientation_error(pose)
        record.update({
            "elapsed_s": round(float(sim.getSimulationTime()) - t0, 3),
            "tcp_pos_error_m": round(float(pos_err), 5),
            "tcp_ang_error_rad": round(float(ang_err), 5),
            "joints": _round(ik.joint_positions()),
            "joints_within_limits": bool(ik.joints_within_limits()),
            "joint_speed": round(float(ik.joint_speed()), 5),
            "tcp_speed": round(float(ik.tcp_speed()), 5),
        })
        if (record["tcp_pos_error_m"] <= base.ARM_TCP_POS_TOL
                and record["tcp_ang_error_rad"] <= base.ARM_TCP_ANG_TOL
                and record["joints_within_limits"]
                and ik.converged(res.joints)
                and ik.stopped()):
            record["success"] = True
            return record
    record["failure_reason"] = (
        f"未在 {timeout:.1f}s 内满足 TCP/关节/速度判据")
    return record


def verify_selected_layout_motion(sim, simIK, scene,
                                  layout: LayoutSpec) -> dict:
    """对选定布局做实际运动验证 (离散目标, 不检查路径碰撞)。

    调用前仿真必须处于 stepping 运行状态; 本函数会推进 sim.step()。
    """
    ik = Ur5IkController(
        sim, simIK, scene.robot_root, scene.tcp,
        scene.handle(base.ALIASES["ik_target"]), scene.robot_joints,
        constraint=simIK.constraint_pose)
    ik.initialize()
    targets = layout_targets(layout)
    sequence: list[tuple[str, list[float]]] = []
    for name in KEY_PICK_POINTS:
        sequence.append((f"{name}_approach", targets[f"{name}_approach"]))
        sequence.append((name, targets[name]))
    sequence.append(("pick_center_lift", targets["pick_center_lift"]))
    sequence.append(("station_approach", targets["station_approach"]))
    sequence.append(("station", targets["station"]))
    sequence.append(("agv_approach", targets["agv_approach"]))
    sequence.append(("agv", targets["agv"]))

    records = [_move_to(sim, ik, name, pose) for name, pose in sequence]
    # 回到 seed, 便于后续试验从确定状态开始
    ik.apply_joints(list(base.ARM_SEED_JOINTS))
    for _ in range(max(1, int(cfg.ARM_RESET_TIMEOUT / base.SIM_STEP))):
        sim.step()
        if ik.converged(list(base.ARM_SEED_JOINTS)) and ik.stopped():
            break
    return {
        "layout": layout.name,
        "targets": records,
        "all_ok": all(r["success"] for r in records),
        "failed": [r["name"] for r in records if not r["success"]],
        "note": "仅离散 IK/运动验证; 不声明路径无碰撞 (阶段二)",
    }


# ═══════════════════════════════════════════════════════════════
# 场景测量
# ═══════════════════════════════════════════════════════════════

def quat_rotate(q, v) -> list[float]:
    """用四元数 [x,y,z,w] 旋转向量 (用于局部包围盒转世界轴对齐盒)。"""
    x, y, z, w = [float(a) for a in q[:4]]
    vx, vy, vz = [float(a) for a in v[:3]]
    # t = 2 * cross(q.xyz, v)
    tx = 2.0 * (y * vz - z * vy)
    ty = 2.0 * (z * vx - x * vz)
    tz = 2.0 * (x * vy - y * vx)
    return [vx + w * tx + (y * tz - z * ty),
            vy + w * ty + (z * tx - x * tz),
            vz + w * tz + (x * ty - y * tx)]


def _local_bbox(sim, handle: int):
    """局部包围盒 (min, max); 先 getShapeBB, 再退回 model bbox 参数。"""
    try:
        bb = sim.getShapeBB(handle)
        size = bb[0] if isinstance(bb, (tuple, list)) and len(bb) == 2 else bb
        half = [float(v) / 2.0 for v in size[:3]]
        return [-half[0], -half[1], -half[2]], half
    except Exception:
        pass
    names = ("min_x", "min_y", "min_z", "max_x", "max_y", "max_z")
    vals = []
    for name in names:
        param = getattr(sim, f"objfloatparam_modelbbox_{name}", None)
        if param is None:
            return None
        try:
            vals.append(float(sim.getObjectFloatParam(handle, param)))
        except Exception:
            return None
    return vals[:3], vals[3:]


def object_bbox_world(sim, handle: int) -> Optional[dict]:
    """返回对象的**世界轴对齐**包围盒 (由局部 BB 与位姿变换)。"""
    local = _local_bbox(sim, handle)
    if local is None:
        return None
    mn, mx = local
    try:
        pose = [float(v) for v in sim.getObjectPose(handle, sim.handle_world)]
    except Exception:
        return None
    corners = []
    for sx in (mn[0], mx[0]):
        for sy in (mn[1], mx[1]):
            for sz in (mn[2], mx[2]):
                rotated = quat_rotate(pose[3:7], [sx, sy, sz])
                corners.append([pose[i] + rotated[i] for i in range(3)])
    return {
        "local_min": [round(v, 5) for v in mn],
        "local_max": [round(v, 5) for v in mx],
        "world_min": [round(min(c[i] for c in corners), 5) for i in range(3)],
        "world_max": [round(max(c[i] for c in corners), 5) for i in range(3)],
    }


def measure_scene(sim, scene) -> dict:
    """保存当前场景对象的别名、句柄、父对象、世界位姿与外包围盒。"""
    rows = []
    for row in scene.describe():
        item = dict(row)
        item["bbox"] = object_bbox_world(sim, row["handle"])
        rows.append(item)
    return {
        "layout": scene.layout.name if scene.layout is not None else "",
        "units": {"length": "m", "angle": "rad"},
        "conveyor": {
            key: scene.conveyor_meta.get(key)
            for key in ("actual_length", "actual_width", "entry_local_x",
                        "stop_local_x", "entry_world", "stop_world",
                        "surface_z", "script_handle", "drive_mode",
                        "config_accel")
        },
        "objects": rows,
    }
