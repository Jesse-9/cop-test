"""scene_base.py — v1 基础层单工位场景构建 (L1_S1).

只构建基础层范围:
    地面 + 层级根 + 仓库/工位静态平台
    内置 conveyor + Sensor_PickStation / Sensor_ConveyorExit
    内置 UR5 + 内置 suction pad + SuctionPad_01_TCP + Sensor_SuctionPad_01
    内置 pioneer p3dx + 载台/挡边 + Sensor_AGV_Dock

不构建五岛、不构建多传送带、不加载旧版 build_scene 的任何对象。
构建完成后输出别名/类型/父对象/世界位姿清单, 并执行场景契约断言。
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass, field
from typing import Optional

from . import config_base as cfg
from . import physics as phys
from .conveyor import configure_conveyor_model
from .layout_base import LayoutSpec, default_layout, validate_layout

KEEP_DEFAULT = {
    "DefaultCamera", "XYZCameraProxy", "XViewCamera", "YViewCamera",
    "ZViewCamera", "NXViewCamera", "NYViewCamera", "NZViewCamera",
    "DefaultLights", "LightA", "LightB", "LightC", "LightD",
}

_ALL_TYPES = (
    "object_shape_type", "object_dummy_type", "object_joint_type",
    "object_proximitysensor_type", "object_visionsensor_type",
    "object_graph_type", "object_light_type", "object_camera_type",
    "object_mirror_type", "object_octree_type", "object_pointcloud_type",
    "object_forcesensor_type", "object_script_type",
)


class SceneBuildError(RuntimeError):
    """场景构建或契约校验失败"""


# ═══════════════════════════════════════════════════════════════
# 基础工具
# ═══════════════════════════════════════════════════════════════

def model_path(key: str) -> str:
    return os.path.join(cfg.MODEL_BASE, cfg.MODEL_PATHS[key])


def all_objects(sim) -> list[int]:
    handles: list[int] = []
    for name in _ALL_TYPES:
        t = getattr(sim, name)
        i = 0
        while True:
            try:
                h = sim.getObjects(i, t)
            except Exception:
                break
            if h is None or int(h) < 0:
                break
            handles.append(int(h))
            i += 1
    return handles


def alias_of(sim, handle: int) -> str:
    try:
        return sim.getObjectAlias(handle) or ""
    except Exception:
        return ""


def subtree(sim, root: int) -> list[int]:
    try:
        tree = sim.getObjectsInTree(root, sim.handle_all, 0) or []
    except Exception:
        return []
    out = []
    for h in tree:
        if h is None or int(h) == int(root):
            continue
        try:
            sim.getObjectType(int(h))      # 过滤脚本清理期间失效的句柄
        except Exception:
            continue
        out.append(int(h))
    return out


def alias_index(sim) -> dict[str, list[int]]:
    """一次全场景枚举建立 alias → handles 索引 (启动/加载期使用)。"""
    index: dict[str, list[int]] = {}
    for handle in all_objects(sim):
        alias = alias_of(sim, handle)
        if alias:
            index.setdefault(alias, []).append(int(handle))
    return index


def _resolve_indexed(index: dict[str, list[int]], alias: str,
                     required: bool = True) -> int:
    hits = index.get(alias, [])
    if len(hits) > 1:
        raise SceneBuildError(f"别名重复: {alias!r} → {hits}")
    if not hits:
        if required:
            raise SceneBuildError(f"场景中找不到别名: {alias!r}")
        return -1
    return hits[0]


def resolve_alias(sim, alias: str, required: bool = True,
                  index: Optional[dict[str, list[int]]] = None) -> int:
    """按完整的扁平别名匹配; 重复别名视为构建错误。"""
    if index is None:
        index = alias_index(sim)
    return _resolve_indexed(index, alias, required)


def clear_scene(sim) -> int:
    """清空场景中非默认对象 (阶段一构建器是场景的唯一所有者)."""
    removed = 0
    for _ in range(3):          # 删父对象会留下孤立子对象, 迭代清理
        handles = [h for h in all_objects(sim)
                   if alias_of(sim, h) not in KEEP_DEFAULT]
        if not handles:
            break
        try:
            sim.removeObjects(handles)
        except Exception:
            for h in handles:
                try:
                    sim.removeObjects([h])
                except Exception:
                    pass
        removed += len(handles)
        time.sleep(0.05)     # 让脚本 cleanup 完成后再做下一轮
    return removed


def _create_box(sim, alias: str, size, world_pos, color=(0.6, 0.6, 0.6),
                parent: Optional[int] = None, static: bool = True) -> int:
    h = int(sim.createPrimitiveShape(sim.primitiveshape_cuboid, list(size)))
    sim.setObjectAlias(h, alias)
    if parent is not None and parent >= 0:
        sim.setObjectParent(h, parent, True)
    sim.setObjectPosition(h, list(world_pos), sim.handle_world)
    try:
        sim.setObjectColor(h, 0, sim.colorcomponent_ambient_diffuse,
                           list(color))
    except Exception:
        pass
    if static:
        phys.apply_static_shape(sim, h, respondable=True)
    return h


def _strip_scripts(sim, root: int) -> list[int]:
    """删除模型自带的 customization/child script (4.10 冻结根因)."""
    removed = []
    for h in subtree(sim, root):
        try:
            if sim.getObjectType(h) == sim.object_script_type:
                sim.removeObjects([h])
                removed.append(h)
        except Exception:
            continue
    return removed


def _load_model(sim, key: str, alias: str, world_pos, parent: int,
                orientation=None, strip_scripts: bool = True) -> int:
    path = model_path(key)
    if not os.path.exists(path):
        raise SceneBuildError(f"必需的内置模型缺失: {path}")
    h = int(sim.loadModel(path))
    sim.setObjectAlias(h, alias)
    # 挂父会清掉根 shape 的 static (CLAUDE.md 坑 7), 先记录后写回
    root_static = _safe_int(sim, h, getattr(sim, "shapeintparam_static", None))
    if parent is not None and parent >= 0:
        sim.setObjectParent(h, parent, True)
    if world_pos is not None:
        sim.setObjectPosition(h, list(world_pos), sim.handle_world)
    if orientation is not None:
        sim.setObjectOrientation(h, list(orientation), sim.handle_world)
    if root_static:
        try:
            sim.setObjectInt32Param(h, sim.shapeintparam_static, 1)
        except Exception:
            pass
    if strip_scripts:
        _strip_scripts(sim, h)
    return h


def _safe_int(sim, h: int, param) -> Optional[int]:
    if param is None:
        return None
    try:
        return int(sim.getObjectInt32Param(h, param))
    except Exception:
        return None


def _model_bbox(sim, handle: int):
    names = ("min_x", "min_y", "min_z", "max_x", "max_y", "max_z")
    vals = []
    for n in names:
        p = getattr(sim, f"objfloatparam_modelbbox_{n}", None)
        if p is None:
            return None
        try:
            vals.append(float(sim.getObjectFloatParam(handle, p)))
        except Exception:
            return None
    return vals


def _shape_size(sim, handle: int):
    try:
        v = sim.getShapeBB(handle)
        size = v[0] if isinstance(v, (tuple, list)) and len(v) == 2 else v
        return [float(size[0]), float(size[1]), float(size[2])]
    except Exception:
        return None


# ── 姿态/轴向几何工具 (工具安装契约与证据) ──────────────────────

def _quat_matrix(q) -> tuple[tuple[float, ...], ...]:
    """四元数 [qx,qy,qz,qw] → 3x3 旋转矩阵 (行主序)."""
    x, y, z, w = [float(v) for v in q[:4]]
    return (
        (1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)),
        (2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)),
        (2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)),
    )


def _world_axis(sim, handle: int, axis: int):
    """对象局部轴 (0=X/1=Y/2=Z) 在世界系中的方向。"""
    r = _quat_matrix(sim.getObjectQuaternion(handle, sim.handle_world))
    return (r[0][axis], r[1][axis], r[2][axis])


def _dot(a, b) -> float:
    return sum(float(x) * float(y) for x, y in zip(a, b))


def _quat_equivalent(q1, q2, tol: float = 1e-4) -> bool:
    """q 与 -q 表示同一旋转。"""
    a = [float(v) for v in q1[:4]]
    b = [float(v) for v in q2[:4]]
    same = max(abs(x - y) for x, y in zip(a, b))
    neg = max(abs(x + y) for x, y in zip(a, b))
    return min(same, neg) <= tol


def _shape_max_z(sim, shape: int, frame: int) -> float:
    """shape 包围盒在 frame 坐标系中的最大 Z (保守粗筛, 非网格距离)."""
    pose = list(sim.getObjectPose(shape, frame))
    size = _shape_size(sim, shape)
    if size is None:
        raise SceneBuildError(
            f"无法读取 shape 包围盒: {alias_of(sim, shape)!r}")
    r = _quat_matrix(pose[3:])
    return max(
        pose[2] + r[2][0] * sx * size[0] / 2.0
        + r[2][1] * sy * size[1] / 2.0
        + r[2][2] * sz * size[2] / 2.0
        for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)
    )


def _end_link_max_z(sim, connection: int) -> float:
    """connection 上游末端实体 link 在 connection 坐标系的最大 Z.

    只取 connection 的父级子树, 并排除 connection 自身及其子树 (已挂载的
    吸盘), 避免自引用。使用 shape 包围盒, 结果是保守粗筛而非网格净距。
    """
    parent = int(sim.getObjectParent(connection))
    if parent < 0:
        raise SceneBuildError("connection 没有父对象, 无法测量末端 link")
    excluded = {int(connection)} | set(subtree(sim, connection))
    shapes = [h for h in [parent] + subtree(sim, parent)
              if h not in excluded
              and sim.getObjectType(h) == sim.object_shape_type]
    if not shapes:
        raise SceneBuildError("connection 上游没有可测量的实体 link shape")
    return max(_shape_max_z(sim, h, connection) for h in shapes)


def arm_joint_modes_ok(sim, joints) -> tuple[bool, list[int]]:
    """检查机械臂关节是否全部为 kinematic。

    4.10 的 ``sim.getJointMode`` 实测返回 ``(mode, options)``, 必须归一化;
    返回 ``(ok, modes)`` 便于契约断言记录实际读数。
    """
    modes = []
    for joint in joints:
        mode = sim.getJointMode(joint)
        modes.append(int(mode[0] if isinstance(mode, (tuple, list)) else mode))
    kinematic = int(getattr(sim, "jointmode_kinematic", 0))
    return all(mode == kinematic for mode in modes), modes


def _chain_joints(sim, root: int, tip: int) -> list[int]:
    chain, cur = [], int(tip)
    for _ in range(64):
        if cur < 0 or cur == root:
            break
        chain.append(cur)
        try:
            cur = int(sim.getObjectParent(cur))
        except Exception:
            break
    joints = [h for h in chain
              if sim.getObjectType(h) == sim.object_joint_type]
    joints.reverse()
    return joints


def _measure_top_run(sim, belt: int, surface_off_hint: float = 0.0):
    """实测上表面 pad 的局部 X 范围与带面偏移。"""
    pads = [h for h in subtree(sim, belt)
            if sim.getObjectType(h) == sim.object_shape_type
            and alias_of(sim, h) == "pad"]
    if not pads:
        raise SceneBuildError("传送带子树内没有 pad shape")
    entries = []
    for p in pads:
        pos = list(sim.getObjectPosition(p, belt))
        size = _shape_size(sim, p)
        half_z = size[2] / 2.0 if size else 0.0
        entries.append((pos, half_z))
    best_top = max(pos[2] + hz for pos, hz in entries)
    thicknesses = [2 * hz for _, hz in entries if hz > 0]
    tol = min(thicknesses) if thicknesses else 1e-3
    top = [pos for pos, hz in entries
           if abs(pos[2] + hz - best_top) <= max(tol, 1e-4)]
    return {
        "min_x": min(p[0] for p in top),
        "max_x": max(p[0] for p in top),
        "surface_off": best_top,
        "pad_count": len(pads),
        "top_pad_count": len(top),
    }


# ═══════════════════════════════════════════════════════════════
# 场景对象
# ═══════════════════════════════════════════════════════════════

@dataclass
class Stage1Scene:
    sim: object
    layout: Optional[LayoutSpec] = None
    handles: dict[str, int] = field(default_factory=dict)
    robot_joints: list[int] = field(default_factory=list)
    robot_connection: int = -1
    robot_root: int = -1
    suction_root: int = -1
    tcp: int = -1
    suction_sensor: int = -1
    suction_model_sensor: int = -1
    suction_link: int = -1
    agv_root: int = -1
    agv_left_wheel: int = -1
    agv_right_wheel: int = -1
    conveyor_meta: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    build_report: dict = field(default_factory=dict)

    # ── 句柄访问 ────────────────────────────────────────────

    def handle(self, key: str) -> int:
        if key in self.handles:
            return self.handles[key]
        return resolve_alias(self.sim, key, required=False)

    def require(self, key: str) -> int:
        h = self.handle(key)
        if h < 0:
            raise SceneBuildError(f"缺少场景对象: {key!r}")
        return h

    # ── 物料 ────────────────────────────────────────────────

    def spawn_material(self, uid: int, alias: str,
                       position=None, orientation=None) -> int:
        """生成物料; orientation 为世界四元数 [qx,qy,qz,qw] (可选)."""
        pos = list(cfg.MATERIAL_SPAWN_POS if position is None else position)
        h = int(self.sim.createPrimitiveShape(
            self.sim.primitiveshape_cuboid, list(cfg.MATERIAL_SIZE)))
        self.sim.setObjectAlias(h, alias)
        part_root = self.handle(cfg.ALIASES["roots"][4])  # Part
        if part_root >= 0:
            self.sim.setObjectParent(h, part_root, True)
        if orientation is None:
            self.sim.setObjectPosition(h, pos, self.sim.handle_world)
        else:
            self.sim.setObjectPose(
                h, [*pos, *[float(v) for v in orientation]],
                self.sim.handle_world)
        color = cfg.MATERIAL_COLORS.get(alias, (0.8, 0.8, 0.8))
        try:
            self.sim.setObjectColor(h, 0,
                                    self.sim.colorcomponent_ambient_diffuse,
                                    list(color))
        except Exception:
            pass
        info = phys.apply_material_physics(self.sim, h)
        info["alias"] = alias
        info["uid"] = uid
        info["spawn_pos"] = pos
        self.build_report.setdefault("materials", {})[alias] = info
        return h

    def material_pose(self, handle: int) -> list[float]:
        return list(self.sim.getObjectPose(handle, self.sim.handle_world))

    def remove_material(self, handle: int) -> None:
        try:
            self.sim.removeObjects([int(handle)])
        except Exception:
            pass

    # ── 传感器 ──────────────────────────────────────────────

    def check_sensor(self, alias: str, entity: int) -> Optional[int]:
        """静默检测指定实体; 命中返回句柄, 否则 None。"""
        sensor = self.handle(alias)
        if sensor < 0:
            return None
        try:
            res = self.sim.checkProximitySensor(sensor, int(entity))
        except Exception:
            return None
        if isinstance(res, (tuple, list)) and len(res) >= 4:
            if res[0] and res[3] is not None and int(res[3]) >= 0:
                return int(res[3])
            return None
        return int(res) if res not in (None, 0, -1) else None

    def pick_sensor_hits(self, handles: list[int]) -> list[int]:
        return [h for h in handles if self.check_sensor(
            cfg.ALIASES["pick_sensor"], h) is not None]

    def suction_sensor_hits(self, handles: list[int]) -> list[int]:
        return [h for h in handles if self.check_sensor(
            cfg.ALIASES["suction_sensor"], h) is not None]

    def agv_dock_hit(self, agv_handle: int) -> bool:
        sensor = self.handle(cfg.ALIASES["agv_dock_sensor"])
        if sensor < 0:
            return False
        # 车体根 + 轮子/载台都是 AGV 子树的一部分
        candidates = [agv_handle] + subtree(self.sim, agv_handle)
        return any(self.check_sensor(cfg.ALIASES["agv_dock_sensor"], c)
                   for c in candidates)

    # ── 诊断 ────────────────────────────────────────────────

    def describe(self) -> list[dict]:
        rows = []
        index = alias_index(self.sim)
        for alias in sorted(set(cfg.REQUIRED_ALIASES) | set(self.handles)):
            h = self.handles.get(alias)
            if h is None:
                hits = index.get(alias, [])
                h = hits[0] if len(hits) == 1 else -1
            if h < 0:
                continue
            parent = -1
            try:
                parent = int(self.sim.getObjectParent(h))
            except Exception:
                pass
            rows.append({
                "alias": alias,
                "handle": h,
                "type": int(self.sim.getObjectType(h)),
                "parent": alias_of(self.sim, parent) if parent >= 0 else "",
                "world_pos": [round(float(v), 4)
                              for v in self.sim.getObjectPosition(
                                  h, self.sim.handle_world)],
            })
        return rows

    def tool_geometry(self) -> dict:
        """工具安装几何快照 (构建报告与运行证据共用)."""
        sim = self.sim
        pad, tcp = self.suction_root, self.tcp
        out = {
            "version": cfg.SUCTION_TOOL_VERSION,
            "ok": pad >= 0 and tcp >= 0,
            "mount_z": cfg.SUCTION_MOUNT_Z,
            "mount_quaternion": list(cfg.SUCTION_MOUNT_QUATERNION),
            "flange_protrusion_z": cfg.SUCTION_FLANGE_PROTRUSION_Z,
            "link_clearance_design": cfg.SUCTION_LINK_CLEARANCE,
        }
        if not out["ok"]:
            out["error"] = "吸盘或 TCP 句柄缺失"
            return out
        out["handles"] = {
            "connection": self.robot_connection,
            "pad": pad,
            "tcp": tcp,
            "sensor": self.suction_sensor,
            "model_sensor": self.suction_model_sensor,
            "link": self.suction_link,
        }
        out["joint_positions"] = [
            float(sim.getJointPosition(j)) for j in self.robot_joints]
        try:
            out["sim_time"] = float(sim.getSimulationTime())
        except Exception:
            out["sim_time"] = None
        out["pad_world_pose"] = [float(v) for v in sim.getObjectPose(
            pad, sim.handle_world)]
        out["tcp_world_pose"] = [float(v) for v in sim.getObjectPose(
            tcp, sim.handle_world)]
        out["tcp_in_pad"] = [float(v) for v in sim.getObjectPose(
            tcp, pad)]
        if self.robot_connection >= 0:
            out["pad_in_connection"] = [float(v) for v in sim.getObjectPose(
                pad, self.robot_connection)]
        z_pad = _world_axis(sim, pad, 2)
        out["suction_normal_world"] = [-z_pad[0], -z_pad[1], -z_pad[2]]
        for name, handle in (("sensor", self.suction_sensor),
                             ("model_sensor", self.suction_model_sensor)):
            if handle >= 0:
                out[f"{name}_axis_world"] = list(
                    _world_axis(sim, handle, 2))
        if self.robot_connection >= 0:
            try:
                link_max = _end_link_max_z(sim, self.robot_connection)
                back_plane = cfg.SUCTION_MOUNT_Z - cfg.SUCTION_BACK_LOCAL_Z
                out["end_link_max_z_in_connection"] = link_max
                out["pad_back_plane_z"] = back_plane
                out["link_clearance"] = back_plane - link_max
            except Exception as exc:
                out["ok"] = False
                out["end_link_max_z_in_connection"] = None
                out["link_clearance"] = None
                out["clearance_error"] = str(exc)
        return out

    def verify(self) -> dict:
        checks = []

        def check(name, ok, detail=""):
            checks.append({"name": name, "ok": bool(ok), "detail": detail})
            return ok

        if self.layout is not None:
            layout_report = validate_layout(self.layout)
            check("layout_static_validation", layout_report["ok"],
                  f"layout={self.layout.name} "
                  f"({sum(1 for c in layout_report['checks'] if c['ok'])}"
                  f"/{len(layout_report['checks'])} checks)")

        index = alias_index(self.sim)
        for alias in cfg.REQUIRED_ALIASES:
            hits = index.get(alias, [])
            check(f"alias_unique:{alias}", len(hits) == 1,
                  f"{len(hits)} 个匹配")

        stopper_hits = index.get(cfg.LEGACY_STOPPER_ALIAS, [])
        check("no_stopper", len(stopper_hits) == 0,
              f"当前生产场景不构建挡停体, found={len(stopper_hits)}")

        check("robot_joint_count", len(self.robot_joints) == 6,
              f"{len(self.robot_joints)} 个关节")
        try:
            modes_ok, modes = arm_joint_modes_ok(self.sim, self.robot_joints)
            check("ur5_joint_modes", modes_ok,
                  f"modes={modes} (期望全部 "
                  f"{self.sim.jointmode_kinematic})")
        except Exception as exc:
            check("ur5_joint_modes", False, f"无法读取: {exc}")

        tcp = self.tcp
        check("tcp_exists", tcp >= 0, f"handle={tcp}")

        if self.conveyor_meta:
            design = cfg.CONVEYOR_DESIGN_LENGTH
            actual = self.conveyor_meta.get("actual_length")
            check("conveyor_length", actual is not None
                  and abs(actual - design) <= 0.01,
                  f"设计={design:.4f} 实测={actual}")
            run = self.conveyor_meta.get("top_run")
            if run:
                check("pick_zone_inside_top_run",
                      run["min_x"] <= cfg.CONVEYOR_STOP_LOCAL_X <= run["max_x"],
                      f"stop_local={cfg.CONVEYOR_STOP_LOCAL_X:.4f} "
                      f"run={run['min_x']:.4f}..{run['max_x']:.4f}")
                sensor_x = (self.layout.pick_sensor_local_x
                            if self.layout is not None
                            else cfg.CONVEYOR_STOP_LOCAL_X)
                check("pick_sensor_inside_top_run",
                      run["min_x"] <= sensor_x <= run["max_x"],
                      f"sensor_local={sensor_x:.4f} "
                      f"run={run['min_x']:.4f}..{run['max_x']:.4f}")
            check("conveyor_script_present",
                  self.conveyor_meta.get("script_handle", -1) >= 0,
                  f"script_handle={self.conveyor_meta.get('script_handle')}")
            accel = self.conveyor_meta.get("config_accel")
            check("conveyor_accel",
                  accel is not None and abs(accel - cfg.CONVEYOR_ACCEL) < 1e-6,
                  f"accel={accel} (期望 {cfg.CONVEYOR_ACCEL})")
            stop_time = (cfg.CONVEYOR_SPEED / accel) if accel else float("inf")
            check("conveyor_stop_time",
                  stop_time <= cfg.CONVEYOR_MAX_STOP_TIME,
                  f"{stop_time:.3f} s <= {cfg.CONVEYOR_MAX_STOP_TIME:.3f} s")

        if self.agv_left_wheel >= 0:
            mode = self.sim.getJointMode(self.agv_left_wheel)
            mode = int(mode[0]) if isinstance(mode, (tuple, list)) else int(mode)
            check("agv_drive_joint_dynamic",
                  mode == self.sim.jointmode_dynamic,
                  f"mode={mode} (期望 {self.sim.jointmode_dynamic})")
        if self.agv_root >= 0:
            mass = phys.get_mass(self.sim, self.agv_root)
            check("agv_mass_present", mass is not None and mass > 0,
                  f"mass={mass}")

        if tcp >= 0 and self.suction_root >= 0:
            pad = self.suction_root
            local = list(self.sim.getObjectPosition(tcp, pad))
            check("tcp_contact_offset",
                  all(abs(local[i] - cfg.TCP_LOCAL_POS[i])
                      <= cfg.SUCTION_MOUNT_POS_TOL for i in range(3)),
                  f"TCP local={[round(v, 5) for v in local]} "
                  f"期望={list(cfg.TCP_LOCAL_POS)}")
            tcp_q = self.sim.getObjectQuaternion(tcp, pad)
            check("tcp_local_orientation",
                  _quat_equivalent(tcp_q, cfg.TCP_LOCAL_QUATERNION),
                  f"TCP quat in pad={[round(float(v), 6) for v in tcp_q]}")

            if self.robot_connection >= 0:
                mount = list(self.sim.getObjectPose(pad, self.robot_connection))
                expected_mount = [0.0, 0.0, cfg.SUCTION_MOUNT_Z]
                check(
                    "pad_mount_pose",
                    all(abs(mount[i] - expected_mount[i])
                        <= cfg.SUCTION_MOUNT_POS_TOL for i in range(3))
                    and _quat_equivalent(mount[3:],
                                         cfg.SUCTION_MOUNT_QUATERNION),
                    f"pad in connection={[round(float(v), 6) for v in mount]} "
                    f"期望 z={cfg.SUCTION_MOUNT_Z:.6f} "
                    f"quat={list(cfg.SUCTION_MOUNT_QUATERNION)}")

            z_pad = _world_axis(self.sim, pad, 2)
            z_tcp = _world_axis(self.sim, tcp, 2)
            check("tcp_pad_axis",
                  _dot(z_tcp, z_pad) >= 1.0 - cfg.SUCTION_AXIS_DOT_TOL,
                  f"z_tcp·z_pad={_dot(z_tcp, z_pad):.6f}")
            check("suction_model_sensor_present",
                  self.suction_model_sensor >= 0,
                  f"handle={self.suction_model_sensor}")
            for name, handle in (
                    ("suction_sensor_axis", self.suction_sensor),
                    ("suction_model_sensor_axis", self.suction_model_sensor)):
                if handle < 0:
                    continue
                z_sensor = _world_axis(self.sim, handle, 2)
                dot = _dot(z_sensor, (-z_pad[0], -z_pad[1], -z_pad[2]))
                check(name, dot >= 1.0 - cfg.SUCTION_AXIS_DOT_TOL,
                      f"z_sensor·(-z_pad)={dot:.6f}")

            check("suction_link_present", self.suction_link >= 0,
                  f"handle={self.suction_link}")
            if self.suction_link >= 0:
                marker = list(self.sim.getObjectPosition(
                    self.suction_link, pad))
                check("tcp_contact_marker",
                      math.dist(marker, local) <= cfg.SUCTION_MOUNT_POS_TOL,
                      f"Link={[round(v, 5) for v in marker]} "
                      f"TCP={[round(v, 5) for v in local]}")

            if self.robot_connection >= 0:
                try:
                    link_max = _end_link_max_z(
                        self.sim, self.robot_connection)
                    back_plane = (cfg.SUCTION_MOUNT_Z
                                  - cfg.SUCTION_BACK_LOCAL_Z)
                    gap = back_plane - link_max
                    check("suction_mount_clearance",
                          gap >= cfg.SUCTION_LINK_CLEARANCE
                          - cfg.SUCTION_MOUNT_POS_TOL,
                          f"pad 背面 z={back_plane:.6f} "
                          f"末端 link max z={link_max:.6f} gap={gap:.6f}")
                except Exception as exc:
                    # 未知几何结果不能按通过处理
                    check("suction_mount_clearance", False,
                          f"无法测量: {exc}")

        layout = self.layout if self.layout is not None else default_layout()
        for name, point in layout.targets().items():
            d = math.dist(layout.arm_base, point[:3])
            check(f"arm_reach:{name}", d <= cfg.REACH_LIMIT,
                  f"{d:.3f} m <= {cfg.REACH_LIMIT:.3f} m")

        if tcp >= 0:
            z = float(self.sim.getObjectPosition(
                tcp, self.sim.handle_world)[2])
            check("tcp_above_island", z > layout.island_top_z,
                  f"TCP z={z:.3f}")

        self.build_report["tool"] = self.tool_geometry()
        ok = all(c["ok"] for c in checks)
        self.build_report["checks"] = checks
        self.build_report["ok"] = ok
        return self.build_report


# ═══════════════════════════════════════════════════════════════
# 构建流程
# ═══════════════════════════════════════════════════════════════

def _create_hierarchy_roots(sim) -> dict[str, int]:
    roots = {}
    for name in cfg.ALIASES["roots"]:
        h = int(sim.createDummy(0.01))
        sim.setObjectAlias(h, name)
        sim.setObjectName(h, name)
        roots[name] = h
    return roots


def _create_floor(sim) -> int:
    h = _create_box(sim, cfg.ALIASES["floor"], cfg.FLOOR_SIZE,
                    cfg.FLOOR_CENTER, color=(0.25, 0.25, 0.28))
    return h


def _find_scripts(sim, root: int) -> list[int]:
    """安全枚举脚本对象 (写 __config__ 可能触发模型脚本重建子对象)。"""
    out = []
    for h in subtree(sim, root):
        try:
            if sim.getObjectType(h) == sim.object_script_type:
                out.append(h)
        except Exception:
            continue
    return out


def _create_conveyor(sim, parent: int,
                     layout: LayoutSpec) -> tuple[int, dict]:
    # conveyor 的 customization 脚本是**唯一** pad 写入者, 必须保留;
    # 只剥离 UR5/suction/p3dx 的脚本 (CLAUDE.md §12/§13).
    belt_pos = (layout.conveyor_center[0], layout.conveyor_center[1],
                cfg.CONVEYOR_SURFACE_Z - cfg.CONVEYOR_SURFACE_OFFSET)
    belt = _load_model(sim, "conveyor", cfg.ALIASES["conveyor"],
                       belt_pos, parent, strip_scripts=False)

    # 构建期等比缩放到设计长度 (4.10 scaleObjects 是标量等比缩放)
    bbox = _model_bbox(sim, belt)
    if bbox is None:
        raise SceneBuildError("无法读取传送带 native 包围盒")
    native_len = bbox[3] - bbox[0]
    if native_len <= 1e-6:
        raise SceneBuildError(f"传送带 native 长度异常: {native_len}")
    scale = cfg.CONVEYOR_DESIGN_LENGTH / native_len
    if abs(scale - 1.0) > 1e-6:
        sim.scaleObjects([belt], scale, True)
        sim.setObjectPosition(belt, list(belt_pos), sim.handle_world)
        bbox = _model_bbox(sim, belt)
    actual_length = (bbox[3] - bbox[0]) if bbox else native_len * scale
    actual_width = (bbox[4] - bbox[1]) if bbox else cfg.CONVEYOR_NATIVE_WIDTH * scale

    run = _measure_top_run(sim, belt)
    # 以实测带面偏移反推模型根高度, 保证带面精确落在 CONVEYOR_SURFACE_Z
    sim.setObjectPosition(
        belt, [layout.conveyor_center[0], layout.conveyor_center[1],
               cfg.CONVEYOR_SURFACE_Z - run["surface_off"]],
        sim.handle_world)
    run["actual_length"] = actual_length
    run["actual_width"] = actual_width
    run["native_length"] = native_len
    run["scale"] = scale
    run["top_run"] = {"min_x": run["min_x"], "max_x": run["max_x"]}

    # 内置 conveyor 脚本: 保留时由脚本驱动 pad, Python 只用 __ctrl__ 调速
    scripts = _find_scripts(sim, belt)
    run["script_handle"] = scripts[0] if scripts else -1
    run["drive_mode"] = (cfg.DRIVE_SOURCE_SCRIPT if scripts
                         else "PYTHON_PAD_DRIVEN")
    if not scripts:
        run["warning"] = ("conveyor 模型没有 customization 脚本; "
                          "履带视觉将不会由脚本驱动")

    # 入口/停止区 dummy: 由**实测上表面行程**推出, 不用包围盒端点
    entry_local = run["min_x"] + cfg.CONVEYOR_ENTRY_INSET
    stop_local = run["max_x"] - cfg.CONVEYOR_STOP_INSET
    run["entry_local_x"] = entry_local
    run["stop_local_x"] = stop_local

    surface_off = run["surface_off"]
    entry = int(sim.createDummy(0.02))
    sim.setObjectAlias(entry, cfg.ALIASES["conveyor_entry"])
    sim.setObjectParent(entry, belt, True)
    sim.setObjectPosition(entry, [entry_local, 0.0, surface_off],
                          sim.handle_parent)

    stop = int(sim.createDummy(0.02))
    sim.setObjectAlias(stop, cfg.ALIASES["conveyor_stop"])
    sim.setObjectParent(stop, belt, True)
    sim.setObjectPosition(stop, [stop_local, 0.0, surface_off],
                          sim.handle_parent)

    # 当前生产场景不构建挡停体: 物料由 Sensor_PickStation 触发零速,
    # 停稳后按实测位姿抓取 (步骤 1.1 B 组流程)。

    # 传感器 (圆柱体积朝下, 覆盖物料顶面但不接触带面)
    _create_cylinder_sensor(
        sim, cfg.ALIASES["pick_sensor"], parent=belt,
        local_pos=[layout.pick_sensor_local_x, 0.0,
                   cfg.PICK_SENSOR_Z - cfg.CONVEYOR_SURFACE_Z],
        local_orientation=cfg.PICK_SENSOR_ORIENTATION,
        range_=cfg.PICK_SENSOR_RANGE, radius=cfg.PICK_SENSOR_RADIUS)
    _create_cylinder_sensor(
        sim, cfg.ALIASES["exit_sensor"], parent=belt,
        local_pos=[cfg.EXIT_SENSOR_LOCAL_X, 0.0,
                   cfg.EXIT_SENSOR_Z - cfg.CONVEYOR_SURFACE_Z],
        local_orientation=cfg.PICK_SENSOR_ORIENTATION,
        range_=cfg.EXIT_SENSOR_RANGE, radius=cfg.EXIT_SENSOR_RADIUS)

    run["entry_world"] = list(sim.getObjectPosition(entry, sim.handle_world))
    run["stop_world"] = list(sim.getObjectPosition(stop, sim.handle_world))
    run["surface_z"] = cfg.CONVEYOR_SURFACE_Z

    # 停车加速度: 模型默认 0.05 m/s² 会让 0.2 m/s 的带面滑行数秒。
    # 必须在 simulation start 之前写入, 脚本 init 才会读取。
    # 放在构建末尾写, 避免写表触发脚本重建子对象影响前面的枚举。
    model_cfg = configure_conveyor_model(
        sim, belt, cfg.CONVEYOR_ACCEL, cfg.CONVEYOR_SPEED)
    if model_cfg is None:
        raise SceneBuildError("无法写入 conveyor customData.__config__")
    run["config_accel"] = model_cfg.get("accel")
    run["config_target_vel"] = model_cfg.get("targetVel")
    scripts = _find_scripts(sim, belt)
    if scripts:
        run["script_handle"] = scripts[0]
    return belt, run


def _create_cylinder_sensor(sim, alias: str, parent: int, local_pos,
                            local_orientation, range_: float,
                            radius: float) -> int:
    int_params = list(cfg.SENSOR_INTPARAMS)
    float_params = [
        0.0, float(range_),          # offset, range
        0.0, 0.0, 0.0, 0.0, 0.0,     # x/y sizes, far sizes, inside gap
        float(radius), float(radius),  # radius, radius far
        0.0, 0.0, 0.0,               # angle, threshold angle, min distance
        0.005, 0.0, 0.0,             # sensing point size + reserved
    ]
    h = int(sim.createProximitySensor(
        getattr(sim, "proximitysensor_cylinder"), 16, cfg.SENSOR_OPTIONS,
        int_params, float_params))
    sim.setObjectAlias(h, alias)
    sim.setObjectParent(h, parent, True)
    sim.setObjectPosition(h, list(local_pos), sim.handle_parent)
    sim.setObjectOrientation(h, list(local_orientation), sim.handle_parent)
    return h


def _create_robot(sim, island: int, layout: LayoutSpec) -> dict:
    arm = _load_model(sim, "ur5", cfg.ALIASES["robot"], layout.arm_base,
                      island)
    # 全臂非动态, 否则 kinematic 关节会让连杆自由落体 (CLAUDE.md 坑 9)。
    # 同时关闭机械臂/吸盘的 respondable: 运动学臂不参与动力学位移,
    # 但可响应连杆会在下探时把动态物料推离取料位 (4.10 实测)。
    for h in subtree(sim, arm):
        if sim.getObjectType(h) == sim.object_shape_type:
            phys.set_static(sim, h, True)
            phys.set_respondable(sim, h, False)
    if sim.getObjectType(arm) == sim.object_shape_type:
        phys.set_static(sim, arm, True)
        phys.set_respondable(sim, arm, False)

    connection = -1
    for h in subtree(sim, arm):
        if alias_of(sim, h) == "connection":
            connection = h
            if sim.getObjectType(h) == sim.object_forcesensor_type:
                break
    if connection < 0:
        raise SceneBuildError("UR5 子树内找不到 connection 末端")

    joints = _chain_joints(sim, arm, connection)
    if len(joints) != 6:
        raise SceneBuildError(f"UR5 关节数不是 6: {len(joints)}")

    low, high = cfg.ARM_JOINT_LIMIT
    for j, q in zip(joints, cfg.ARM_SEED_JOINTS):
        try:
            sim.setJointInterval(j, False, [low, high - low])
        except Exception as exc:
            raise SceneBuildError(f"设置关节限位失败: {exc}") from exc
        sim.setJointMode(j, sim.jointmode_kinematic, 0)
        sim.setJointPosition(j, float(q))

    # 吸盘: 挂到 connection。内置模型本地 -Z 才是吸附接触方向, 因此绕
    # connection X 轴转 π, 并沿 +Z 平移 d, 使接触面真正朝外/朝下。
    pad = _load_model(sim, "suction_pad", cfg.ALIASES["suction"],
                      None, connection)
    sim.setObjectPose(
        pad,
        [0.0, 0.0, cfg.SUCTION_MOUNT_Z, *cfg.SUCTION_MOUNT_QUATERNION],
        connection)
    for h in subtree(sim, pad):
        if sim.getObjectType(h) == sim.object_shape_type:
            phys.set_static(sim, h, True)
            phys.set_respondable(sim, h, False)
    if sim.getObjectType(pad) == sim.object_shape_type:
        phys.set_static(sim, pad, True)
        phys.set_respondable(sim, pad, False)

    sensor = -1
    link = -1
    model_sensor = -1
    for h in subtree(sim, pad):
        a = alias_of(sim, h)
        if a == "Sensor" and sim.getObjectType(h) == sim.object_proximitysensor_type:
            model_sensor = h
        elif a == "Link" and sim.getObjectType(h) == sim.object_forcesensor_type:
            link = h
    if model_sensor >= 0:
        # 模型自带传感器朝外且体积不可控, 保留为诊断对象, 改用专用接触传感器
        sim.setObjectAlias(model_sensor, "SuctionPad_01_ModelSensor")

    tcp = int(sim.createDummy(0.01))
    sim.setObjectAlias(tcp, cfg.ALIASES["tcp"])
    sim.setObjectParent(tcp, pad, True)
    # 完整指定 TCP 相对 pad 的位姿: +Z 与 pad +Z 同向, 接触点在 pad -Z。
    sim.setObjectPose(
        tcp,
        [*cfg.TCP_LOCAL_POS, *cfg.TCP_LOCAL_QUATERNION],
        sim.handle_parent)

    # 专用吸盘接触传感器: 从 TCP 接触面向下 3cm、半径 6cm 的圆柱体积,
    # 只覆盖物料顶面, 不触及传送带 (带面在 TCP 下方 10cm).
    sensor = _create_cylinder_sensor(
        sim, cfg.ALIASES["suction_sensor"], parent=tcp,
        local_pos=[0.0, 0.0, 0.0],
        local_orientation=cfg.PICK_SENSOR_ORIENTATION,
        range_=cfg.SUCTION_SENSOR_RANGE, radius=cfg.SUCTION_SENSOR_RADIUS)

    return {
        "arm": arm,
        "connection": connection,
        "joints": joints,
        "pad": pad,
        "sensor": sensor,
        "model_sensor": model_sensor,
        "link": link,
        "tcp": tcp,
    }


def _create_agv(sim, mobile_root: int, layout: LayoutSpec) -> dict:
    root = _load_model(sim, "pioneer_p3dx", cfg.ALIASES["agv"],
                       (layout.agv_dock[0], layout.agv_dock[1],
                        cfg.AGV_ROOT_Z), mobile_root)

    joints = {alias_of(sim, h): h for h in subtree(sim, root)
              if sim.getObjectType(h) == sim.object_joint_type}
    left = joints.get("leftMotor", -1)
    right = joints.get("rightMotor", -1)
    if left < 0 or right < 0:
        raise SceneBuildError("pioneer p3dx 找不到 leftMotor/rightMotor")

    # 载台 (与车体刚性连接, 静态+可响应)
    tray = _create_box(
        sim, cfg.ALIASES["agv_tray"], cfg.AGV_TRAY_SIZE,
        (layout.agv_dock[0], layout.agv_dock[1],
         cfg.AGV_ROOT_Z + cfg.AGV_TRAY_CENTER_LOCAL_Z),
        color=(0.55, 0.55, 0.60), parent=root)
    phys.apply_kinematic_shape(sim, tray, respondable=True)
    phys.set_friction(sim, tray, cfg.AGV_TRAY_FRICTION)

    # 低矮挡边: 不阻碍从上方放料
    z_wall = cfg.AGV_ROOT_Z + cfg.AGV_CHASSIS_TOP_LOCAL_Z + \
        cfg.AGV_TRAY_THICKNESS + cfg.AGV_WALL_HEIGHT / 2.0
    off = cfg.AGV_WALL_OFFSET
    for key, size, dx, dy in (
            ("agv_wall_front", (cfg.AGV_WALL_THICKNESS, 0.30,
                                cfg.AGV_WALL_HEIGHT), off, 0.0),
            ("agv_wall_back", (cfg.AGV_WALL_THICKNESS, 0.30,
                               cfg.AGV_WALL_HEIGHT), -off, 0.0),
            ("agv_wall_left", (0.30, cfg.AGV_WALL_THICKNESS,
                               cfg.AGV_WALL_HEIGHT), 0.0, off),
            ("agv_wall_right", (0.30, cfg.AGV_WALL_THICKNESS,
                                cfg.AGV_WALL_HEIGHT), 0.0, -off)):
        wall = _create_box(
            sim, cfg.ALIASES[key], size,
            (layout.agv_dock[0] + dx, layout.agv_dock[1] + dy, z_wall),
            color=(0.45, 0.45, 0.50), parent=root)
        phys.apply_kinematic_shape(sim, wall, respondable=True)
        phys.set_friction(sim, wall, cfg.AGV_TRAY_FRICTION)

    _create_cylinder_sensor(
        sim, cfg.ALIASES["agv_dock_sensor"], parent=mobile_root,
        local_pos=[layout.agv_dock[0], layout.agv_dock[1],
                   cfg.AGV_DOCK_SENSOR_Z],
        local_orientation=cfg.PICK_SENSOR_ORIENTATION,
        range_=cfg.AGV_DOCK_SENSOR_RANGE, radius=cfg.AGV_DOCK_SENSOR_RADIUS)

    return {"root": root, "left": left, "right": right, "tray": tray}


def _cache_required_handles(sim, scene: "Stage1Scene") -> None:
    """构建期一次性缓存运行循环要用的句柄 (CLAUDE.md §3)。

    `Stage1Scene.handle()` 在句柄未缓存时会枚举全场景; 传感器/入口/停止区
    在每步控制循环里被读取, 必须在启动时缓存。找不到的别名保持缺失,
    由 `verify()` 的契约断言报告。
    """
    index = alias_index(sim)
    for key in ("conveyor_entry", "conveyor_stop", "pick_sensor",
                "exit_sensor", "agv_dock_sensor", "place_target_station"):
        alias = cfg.ALIASES[key]
        if alias in scene.handles:
            continue
        hits = index.get(alias, [])
        if len(hits) == 1:
            scene.handles[alias] = hits[0]


def build_scene(sim, clear: bool = True,
                layout: Optional[LayoutSpec] = None) -> Stage1Scene:
    """构建单工位场景并返回句柄/标定信息。

    ``layout`` 默认为当前生产布局 L1_S1; 候选布局必须先通过静态几何
    校验, 失败时在清场之前抛出 ``SceneBuildError``。当前构建器不创建
    挡停体 (步骤 1.1 B 组流程)。
    """
    layout = default_layout() if layout is None else layout
    layout_report = validate_layout(layout)
    if not layout_report["ok"]:
        failed = [c for c in layout_report["checks"] if not c["ok"]]
        raise SceneBuildError(
            f"布局 {layout.name!r} 静态校验失败: " + "; ".join(
                f"{c['check']}({c['detail']})" for c in failed))

    if clear:
        clear_scene(sim)

    roots = _create_hierarchy_roots(sim)
    scene = Stage1Scene(sim=sim, layout=layout)
    if layout.include_stopper:
        scene.warnings.append(
            f"布局 {layout.name!r} 的 include_stopper 标记为历史证据保留; "
            "当前 v1 基础层场景不构建挡停体")
    scene.handles.update({name: h for name, h in roots.items()})
    scene.build_report["layout"] = layout_report

    floor = _create_floor(sim)
    scene.handles[cfg.ALIASES["floor"]] = floor

    # 仓库 (视觉/供料来源) 与两个静态平台
    warehouse = _create_box(
        sim, cfg.ALIASES["warehouse"], cfg.WAREHOUSE_SIZE,
        (layout.warehouse_pos[0], layout.warehouse_pos[1],
         cfg.WAREHOUSE_SIZE[2] / 2.0),
        color=(0.45, 0.35, 0.25), parent=roots["Warehouse"])
    scene.handles[cfg.ALIASES["warehouse"]] = warehouse

    island = _create_box(
        sim, cfg.ALIASES["island"], layout.island_size,
        (layout.island_pos[0], layout.island_pos[1],
         layout.island_top_z / 2.0),
        color=(0.35, 0.40, 0.50), parent=roots["Island"])
    scene.handles[cfg.ALIASES["island"]] = island

    station = _create_box(
        sim, cfg.ALIASES["station"], layout.station_size,
        (layout.station_pos[0], layout.station_pos[1],
         layout.station_top_z / 2.0),
        color=(0.35, 0.45, 0.40), parent=roots["Island"])
    scene.handles[cfg.ALIASES["station"]] = station

    belt, conv_meta = _create_conveyor(sim, roots["Conveyor"], layout)
    scene.handles[cfg.ALIASES["conveyor"]] = belt
    scene.conveyor_meta = conv_meta

    robot = _create_robot(sim, island, layout)
    scene.robot_root = robot["arm"]
    scene.robot_connection = robot["connection"]
    scene.robot_joints = robot["joints"]
    scene.suction_root = robot["pad"]
    scene.tcp = robot["tcp"]
    scene.suction_sensor = robot["sensor"]
    scene.suction_model_sensor = robot["model_sensor"]
    scene.suction_link = robot["link"]
    scene.handles[cfg.ALIASES["robot"]] = robot["arm"]
    scene.handles[cfg.ALIASES["suction"]] = robot["pad"]
    scene.handles[cfg.ALIASES["tcp"]] = robot["tcp"]
    scene.handles[cfg.ALIASES["suction_sensor"]] = robot["sensor"]

    agv = _create_agv(sim, roots["Mobile"], layout)
    scene.agv_root = agv["root"]
    scene.agv_left_wheel = agv["left"]
    scene.agv_right_wheel = agv["right"]
    scene.handles[cfg.ALIASES["agv"]] = agv["root"]
    scene.handles[cfg.ALIASES["agv_tray"]] = agv["tray"]

    target = int(sim.createDummy(0.01))
    sim.setObjectAlias(target, cfg.ALIASES["ik_target"])
    sim.setObjectParent(target, roots["Island"], True)
    sim.setObjectPose(target, [*layout.pick_tcp_pose], sim.handle_world)
    scene.handles[cfg.ALIASES["ik_target"]] = target

    # 运行循环只读缓存; 构建期做一次全场景解析 (CLAUDE.md §3)
    _cache_required_handles(sim, scene)

    report = scene.verify()
    if not report["ok"]:
        failed = [c for c in report["checks"] if not c["ok"]]
        raise SceneBuildError(
            "场景契约校验失败: " + "; ".join(
                f"{c['name']}({c['detail']})" for c in failed))
    return scene


def adopt_scene(sim, layout: Optional[LayoutSpec] = None) -> Stage1Scene:
    """把当前已加载的 .ttt 场景重新解析为 Stage1Scene。

    句柄是会话内运行时值: 每次 loadScene 后必须按 alias 重新解析,
    不得复用上一轮的句柄 (步骤 1.2 §6.7)。本函数只读查询, 不修改场景。
    """
    layout = default_layout() if layout is None else layout
    scene = Stage1Scene(sim=sim, layout=layout)
    index = alias_index(sim)

    for name in cfg.ALIASES["roots"]:
        scene.handles[name] = _resolve_indexed(index, name)

    for key, alias in cfg.ALIASES.items():
        if key == "roots":
            continue
        h = _resolve_indexed(index, alias,
                             required=alias in cfg.REQUIRED_ALIASES)
        if h >= 0:
            scene.handles[alias] = h

    arm = scene.handles[cfg.ALIASES["robot"]]
    connection = -1
    for h in subtree(sim, arm):
        if alias_of(sim, h) == "connection":
            connection = h
            if sim.getObjectType(h) == sim.object_forcesensor_type:
                break
    if connection < 0:
        raise SceneBuildError("UR5 子树内找不到 connection 末端")
    joints = _chain_joints(sim, arm, connection)
    if len(joints) != 6:
        raise SceneBuildError(f"UR5 关节数不是 6: {len(joints)}")

    pad = scene.handles[cfg.ALIASES["suction"]]
    model_sensor = -1
    link = -1
    for h in subtree(sim, pad):
        a = alias_of(sim, h)
        if (a == "SuctionPad_01_ModelSensor"
                and sim.getObjectType(h) == sim.object_proximitysensor_type):
            model_sensor = h
        elif a == "Link" and sim.getObjectType(h) == sim.object_forcesensor_type:
            link = h

    agv = scene.handles[cfg.ALIASES["agv"]]
    agv_joints = {alias_of(sim, h): h for h in subtree(sim, agv)
                  if sim.getObjectType(h) == sim.object_joint_type}
    left = agv_joints.get("leftMotor", -1)
    right = agv_joints.get("rightMotor", -1)
    if left < 0 or right < 0:
        raise SceneBuildError("pioneer p3dx 找不到 leftMotor/rightMotor")

    belt = scene.handles[cfg.ALIASES["conveyor"]]
    bbox = _model_bbox(sim, belt)
    run = _measure_top_run(sim, belt)
    run["top_run"] = {"min_x": run["min_x"], "max_x": run["max_x"]}
    run["actual_length"] = (bbox[3] - bbox[0]) if bbox else None
    run["actual_width"] = (bbox[4] - bbox[1]) if bbox else None
    scripts = _find_scripts(sim, belt)
    run["script_handle"] = scripts[0] if scripts else -1
    run["drive_mode"] = (cfg.DRIVE_SOURCE_SCRIPT if scripts
                         else "PYTHON_PAD_DRIVEN")
    try:
        model_cfg = sim.readCustomTableData(belt, "__config__")
    except Exception:
        model_cfg = None
    if isinstance(model_cfg, dict):
        run["config_accel"] = model_cfg.get("accel")
        run["config_target_vel"] = model_cfg.get("targetVel")
    entry = scene.handles[cfg.ALIASES["conveyor_entry"]]
    stop = scene.handles[cfg.ALIASES["conveyor_stop"]]
    run["entry_local_x"] = float(sim.getObjectPosition(entry, belt)[0])
    run["stop_local_x"] = float(sim.getObjectPosition(stop, belt)[0])
    run["entry_world"] = list(sim.getObjectPosition(entry, sim.handle_world))
    run["stop_world"] = list(sim.getObjectPosition(stop, sim.handle_world))
    run["surface_z"] = cfg.CONVEYOR_SURFACE_Z
    scene.conveyor_meta = run

    scene.robot_root = arm
    scene.robot_connection = connection
    scene.robot_joints = joints
    scene.suction_root = pad
    scene.tcp = scene.handles[cfg.ALIASES["tcp"]]
    scene.suction_sensor = scene.handles[cfg.ALIASES["suction_sensor"]]
    scene.suction_model_sensor = model_sensor
    scene.suction_link = link
    scene.agv_root = agv
    scene.agv_left_wheel = left
    scene.agv_right_wheel = right
    return scene
