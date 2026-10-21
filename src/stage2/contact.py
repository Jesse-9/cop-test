"""contact.py — 物料外表面接触几何 (06-09 §2).

阶段一用“TCP 到物料顶面中心的欧氏距离”判断接触; 阶段二改为在**物料局部
坐标系**中计算:

  - 带符号平面间隙 ``gap = p_local.z - bbox.max.z`` (正 = 外侧, 负 = 下探);
  - TCP 投影相对顶面中心的带符号横向偏移;
  - 杯口投影是否完整落在有效顶面内;
  - 工具探测方向 ``R_tcp * (0,0,-1)`` 与物料外法向 ``n`` 的对齐角
    (探测方向应与 ``-n`` 同向).

物料局部包围盒来自实测 shape 包围盒 ``min/max``, 不假定对象原点在几何
中心. 本模块是纯几何, 不导入 CoppeliaSim; 四元数约定 ``[qx,qy,qz,qw]``.

设计边界: 只支持当前刚性长方体物料的选定顶面, 不是任意网格的有符号距离.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

Vec3 = tuple[float, float, float]
Quat = tuple[float, float, float, float]


# ═══════════════════════════════════════════════════════════════
# 四元数
# ═══════════════════════════════════════════════════════════════

def quat_normalize(q: Sequence[float]) -> Quat:
    x, y, z, w = (float(v) for v in q[:4])
    n = math.sqrt(x * x + y * y + z * z + w * w)
    if n < 1e-12:
        return (0.0, 0.0, 0.0, 1.0)
    return (x / n, y / n, z / n, w / n)


def quat_conjugate(q: Sequence[float]) -> Quat:
    x, y, z, w = quat_normalize(q)
    return (-x, -y, -z, w)


def quat_multiply(a: Sequence[float], b: Sequence[float]) -> Quat:
    ax, ay, az, aw = quat_normalize(a)
    bx, by, bz, bw = quat_normalize(b)
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def quat_rotate(q: Sequence[float], v: Sequence[float]) -> Vec3:
    x, y, z, w = quat_normalize(q)
    vx, vy, vz = (float(c) for c in v[:3])
    cx = y * vz - z * vy
    cy = z * vx - x * vz
    cz = x * vy - y * vx
    ccx = y * cz - z * cy
    ccy = z * cx - x * cz
    ccz = x * cy - y * cx
    return (
        vx + 2.0 * (w * cx + ccx),
        vy + 2.0 * (w * cy + ccy),
        vz + 2.0 * (w * cz + ccz),
    )


def quat_axis_angle(axis: Sequence[float], angle: float) -> Quat:
    ax, ay, az = (float(v) for v in axis[:3])
    n = math.sqrt(ax * ax + ay * ay + az * az)
    if n < 1e-12:
        return (0.0, 0.0, 0.0, 1.0)
    half = 0.5 * float(angle)
    s = math.sin(half) / n
    return (ax * s, ay * s, az * s, math.cos(half))


def quat_from_two_vectors(a: Sequence[float], b: Sequence[float]) -> Quat:
    """把单位向量 a 旋转到单位向量 b 的最短旋转."""
    ax, ay, az = _normalize3(a)
    bx, by, bz = _normalize3(b)
    dot = max(-1.0, min(1.0, ax * bx + ay * by + az * bz))
    if dot > 1.0 - 1e-9:
        return (0.0, 0.0, 0.0, 1.0)
    if dot < -1.0 + 1e-9:
        axis = (1.0, 0.0, 0.0) if abs(ax) < 0.9 else (0.0, 1.0, 0.0)
        cx = ay * axis[2] - az * axis[1]
        cy = az * axis[0] - ax * axis[2]
        cz = ax * axis[1] - ay * axis[0]
        return quat_axis_angle((cx, cy, cz), math.pi)
    cx = ay * bz - az * by
    cy = az * bx - ax * bz
    cz = ax * by - ay * bx
    return quat_axis_angle((cx, cy, cz), math.acos(dot))


def quat_angle_between(a: Sequence[float], b: Sequence[float]) -> float:
    """两个姿态的最小夹角 (rad), 处理 q/-q 等价."""
    qa = quat_normalize(a)
    qb = quat_normalize(b)
    dot = abs(sum(qa[i] * qb[i] for i in range(4)))
    return 2.0 * math.acos(max(-1.0, min(1.0, dot)))


def rotate_about_axis(q: Sequence[float], axis: Sequence[float],
                      angle: float) -> Quat:
    """在世界坐标系中绕指定轴再旋转 angle (左乘)."""
    return quat_multiply(quat_axis_angle(axis, angle), q)


def _normalize3(v: Sequence[float]) -> Vec3:
    x, y, z = (float(c) for c in v[:3])
    n = math.sqrt(x * x + y * y + z * z)
    if n < 1e-12:
        return (0.0, 0.0, 1.0)
    return (x / n, y / n, z / n)


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(float(x) * float(y) for x, y in zip(a[:3], b[:3]))


def _add(a: Sequence[float], b: Sequence[float]) -> Vec3:
    return (float(a[0]) + float(b[0]),
            float(a[1]) + float(b[1]),
            float(a[2]) + float(b[2]))


def _sub(a: Sequence[float], b: Sequence[float]) -> Vec3:
    return (float(a[0]) - float(b[0]),
            float(a[1]) - float(b[1]),
            float(a[2]) - float(b[2]))


def _scale(a: Sequence[float], s: float) -> Vec3:
    return (float(a[0]) * s, float(a[1]) * s, float(a[2]) * s)


# ═══════════════════════════════════════════════════════════════
# 物料局部包围盒与表面
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class LocalBox:
    """物料对象局部坐标系中的轴对齐包围盒 (实测 min/max, 不假定居中)."""

    minimum: Vec3
    maximum: Vec3

    def __post_init__(self) -> None:
        for i in range(3):
            if not (float(self.minimum[i]) < float(self.maximum[i])):
                raise ValueError(
                    f"包围盒第 {i} 轴无效: min={self.minimum[i]!r} "
                    f"max={self.maximum[i]!r}")

    @classmethod
    def from_size(cls, size: Sequence[float],
                  center: Sequence[float] = (0.0, 0.0, 0.0)) -> "LocalBox":
        half = _scale(_normalize_size(size), 0.5)
        c = [float(v) for v in center[:3]]
        return cls(minimum=(c[0] - half[0], c[1] - half[1], c[2] - half[2]),
                   maximum=(c[0] + half[0], c[1] + half[1], c[2] + half[2]))

    @property
    def size(self) -> Vec3:
        return (self.maximum[0] - self.minimum[0],
                self.maximum[1] - self.minimum[1],
                self.maximum[2] - self.minimum[2])

    @property
    def center(self) -> Vec3:
        return ((self.minimum[0] + self.maximum[0]) / 2.0,
                (self.minimum[1] + self.maximum[1]) / 2.0,
                (self.minimum[2] + self.maximum[2]) / 2.0)

    @property
    def half_xy(self) -> tuple[float, float]:
        return ((self.maximum[0] - self.minimum[0]) / 2.0,
                (self.maximum[1] - self.minimum[1]) / 2.0)

    @property
    def top_z(self) -> float:
        return float(self.maximum[2])


def _normalize_size(size: Sequence[float]) -> Vec3:
    if len(size) != 3:
        raise ValueError(f"尺寸必须是 3 元素: {size!r}")
    return tuple(abs(float(v)) for v in size)


@dataclass(frozen=True)
class SurfaceFrame:
    """物料选定的外表面 (当前为局部 +Z 顶面)."""

    center_world: Vec3
    normal_world: Vec3
    tangent_u_world: Vec3
    tangent_v_world: Vec3
    half_u: float
    half_v: float
    top_z_local: float
    material_pose: tuple[float, ...]

    def as_dict(self) -> dict:
        return {
            "center_world": [round(v, 6) for v in self.center_world],
            "normal_world": [round(v, 6) for v in self.normal_world],
            "half_u": round(self.half_u, 6),
            "half_v": round(self.half_v, 6),
            "top_z_local": round(self.top_z_local, 6),
        }


def top_surface(material_pose: Sequence[float],
                box: LocalBox) -> SurfaceFrame:
    """物料对象局部 +Z 顶面在世界系中的中心、外法向与切向."""
    pose = [float(v) for v in material_pose]
    if len(pose) < 7:
        raise ValueError("物料位姿必须是 7 元素 (x,y,z,qx,qy,qz,qw)")
    q = pose[3:7]
    cx, cy, cz = box.center
    top_local = (cx, cy, box.top_z)
    center = _add(pose[:3], quat_rotate(q, top_local))
    normal = quat_rotate(q, (0.0, 0.0, 1.0))
    return SurfaceFrame(
        center_world=center,
        normal_world=normal,
        tangent_u_world=quat_rotate(q, (1.0, 0.0, 0.0)),
        tangent_v_world=quat_rotate(q, (0.0, 1.0, 0.0)),
        half_u=box.half_xy[0],
        half_v=box.half_xy[1],
        top_z_local=box.top_z,
        material_pose=tuple(pose),
    )


# ═══════════════════════════════════════════════════════════════
# 接触判定
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ContactLimits:
    max_gap: float
    min_signed_distance: float
    max_lateral: float
    max_normal_angle: float
    face_tolerance: float = 0.001
    require_cup_coverage: bool = True


@dataclass(frozen=True)
class ContactEvaluation:
    valid: bool
    reasons: tuple[str, ...]
    uid: int | None
    signed_distance: float
    lateral_offset: float
    offset_xy: tuple[float, float]
    normal_angle: float
    within_face: bool
    cup_covered: bool
    cup_radius: float
    surface: SurfaceFrame
    tool_position: Vec3
    tool_normal: Vec3

    def as_dict(self) -> dict:
        return {
            "valid": self.valid,
            "reasons": list(self.reasons),
            "uid": self.uid,
            "signed_distance": round(self.signed_distance, 6),
            "lateral_offset": round(self.lateral_offset, 6),
            "offset_xy": [round(v, 6) for v in self.offset_xy],
            "normal_angle": round(self.normal_angle, 6),
            "within_face": self.within_face,
            "cup_covered": self.cup_covered,
            "cup_radius": round(self.cup_radius, 6),
            "surface": self.surface.as_dict(),
            "tool_position": [round(v, 6) for v in self.tool_position],
            "tool_normal": [round(v, 6) for v in self.tool_normal],
        }


def evaluate_contact(tcp_pose: Sequence[float],
                     material_pose: Sequence[float],
                     box: LocalBox,
                     limits: ContactLimits,
                     cup_radius: float,
                     uid: int | None = None) -> ContactEvaluation:
    """计算 TCP 与物料顶面的接触评估.

    ``tcp_pose`` / ``material_pose`` 为世界位姿 ``[x,y,z,qx,qy,qz,qw]``。
    TCP 就是吸盘接触面中心, 其局部 -Z 是探测方向。
    """
    tcp = [float(v) for v in tcp_pose]
    mat = [float(v) for v in material_pose]
    if len(tcp) < 7 or len(mat) < 7:
        raise ValueError("TCP/物料位姿必须是 7 元素")

    frame = top_surface(mat, box)
    q_mat = mat[3:7]
    local = quat_rotate(quat_conjugate(q_mat), _sub(tcp[:3], mat[:3]))
    offset = (local[0] - box.center[0], local[1] - box.center[1])
    signed = local[2] - box.top_z
    lateral = math.hypot(offset[0], offset[1])
    hu, hv = box.half_xy

    within_face = (abs(offset[0]) <= hu + limits.face_tolerance
                   and abs(offset[1]) <= hv + limits.face_tolerance)
    # 圆形杯口完整落在矩形面内: 两个轴的半宽都要减去杯口半径.
    covered = (abs(offset[0]) + cup_radius <= hu + limits.face_tolerance
               and abs(offset[1]) + cup_radius <= hv + limits.face_tolerance)

    tool_normal = quat_rotate(tcp[3:7], (0.0, 0.0, -1.0))
    cos_align = -_dot(tool_normal, frame.normal_world)
    normal_angle = math.acos(max(-1.0, min(1.0, cos_align)))

    reasons: list[str] = []
    if not all(math.isfinite(v) for v in (*tcp[:7], *mat[:7])):
        reasons.append("pose_not_finite")
    if signed > limits.max_gap:
        reasons.append(
            f"gap_exceeded({signed:.4f}>{limits.max_gap:.4f})")
    if signed < limits.min_signed_distance:
        reasons.append(
            f"penetration_exceeded({signed:.4f}<"
            f"{limits.min_signed_distance:.4f})")
    if lateral > limits.max_lateral:
        reasons.append(
            f"lateral_offset_exceeded({lateral:.4f}>{limits.max_lateral:.4f})")
    if not within_face:
        reasons.append("outside_top_face")
    if limits.require_cup_coverage and not covered:
        reasons.append(
            f"cup_not_covered(offset=({offset[0]:.4f},{offset[1]:.4f}), "
            f"cup_r={cup_radius:.4f}, half=({hu:.4f},{hv:.4f}))")
    if normal_angle > limits.max_normal_angle:
        reasons.append(
            f"normal_angle_exceeded({normal_angle:.4f}>"
            f"{limits.max_normal_angle:.4f})")

    return ContactEvaluation(
        valid=not reasons,
        reasons=tuple(reasons),
        uid=uid,
        signed_distance=signed,
        lateral_offset=lateral,
        offset_xy=offset,
        normal_angle=normal_angle,
        within_face=within_face,
        cup_covered=covered,
        cup_radius=float(cup_radius),
        surface=frame,
        tool_position=tuple(tcp[:3]),
        tool_normal=tool_normal,
    )


def contact_pose(material_pose: Sequence[float], box: LocalBox,
                 clearance: float = 0.0,
                 yaw: float = 0.0) -> list[float]:
    """生成物料顶面中心沿外法向偏移 ``clearance`` 的 TCP 位姿.

    工具探测方向 (-Z) 与物料外法向相反; ``yaw`` 绕外法向旋转, 不改变
    圆形杯口与顶面的接触几何, 只用于改变腕部构型 (06-09 §5.3)。
    """
    frame = top_surface(material_pose, box)
    position = _add(frame.center_world,
                    _scale(frame.normal_world, float(clearance)))
    q_align = quat_from_two_vectors((0.0, 0.0, 1.0), frame.normal_world)
    q = (rotate_about_axis(q_align, frame.normal_world, float(yaw))
         if abs(float(yaw)) > 1e-12 else q_align)
    return [*position, *q]


def clearance_along_normal(tcp_pose: Sequence[float],
                           surface: SurfaceFrame) -> float:
    """TCP 沿表面外法向到表面中心的带符号距离."""
    return _dot(_sub(tcp_pose[:3], surface.center_world),
                surface.normal_world)
