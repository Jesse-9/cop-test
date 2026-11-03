"""shadow.py — 同基座、同关节链、同工具偏移的影子 UR5 与几何探针.

影子臂是**会话资源**, 不写入 stage2 工件:
  - 由当前通过构建契约的内置 UR5/suction pad 模型复制, 脚本已剥离;
  - 全部 shape ``static=1, respondable=0``, 参与专用几何查询;
  - 关节 kinematic, 只由 ``setJointPosition`` 更新, 不参与动力学;
  - 可见层关闭, 不进入生产 sensor (生产 sensor 只检测指定物料句柄);
  - 真实机械臂的关节与位姿从不会被探针修改.

真实/影子 FK 一致性只在真实臂**当前实际构型**比较 (不为了检查而移动真机);
启动时比较一次, 执行到路径点时可再次比较 (06-09 §4.2/§4.6).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .. import config_base as base
from .. import physics as phys
from ..scene_base import (_chain_joints, _load_model, alias_of, all_objects,
                          subtree)
from .. import config_motion as cfg


class ShadowError(RuntimeError):
    """影子臂构建/查询异常"""


@dataclass
class ShadowArm:
    root: int
    joints: list[int]
    connection: int
    pad_root: int
    tcp: int
    links: list[int] = field(default_factory=list)
    link_aliases: list[str] = field(default_factory=list)
    pad_body: int = -1
    load: int = -1
    all_shapes: list[int] = field(default_factory=list)
    visibility_applied: bool = False
    aliases: dict = field(default_factory=dict)
    base_pose: Optional[list[float]] = None

    @property
    def alias_names(self) -> list[str]:
        """本影子臂的全部稳定别名 (用于选择性清理/校验)."""
        values = dict(self.aliases or {})
        if not values:
            values = {
                "arm": cfg.SHADOW_ARM_ALIAS,
                "pad": cfg.SHADOW_PAD_ALIAS,
                "tcp": cfg.SHADOW_TCP_ALIAS,
                "load": cfg.SHADOW_LOAD_ALIAS,
            }
        names = [values.get(key) for key in ("arm", "pad", "tcp", "load")]
        return [str(name) for name in names if name]

    def as_dict(self) -> dict:
        names = self.alias_names
        return {
            "root_alias": names[0] if names else cfg.SHADOW_ARM_ALIAS,
            "aliases": list(names),
            "joints": len(self.joints),
            "link_aliases": list(self.link_aliases),
            "pad_body": int(self.pad_body),
            "load_alias": (names[3] if len(names) > 3 and self.load >= 0
                           else None),
            "shape_count": len(self.all_shapes),
            "visibility_hidden": self.visibility_applied,
            "base_pose": (None if self.base_pose is None else
                          [round(float(v), 6) for v in self.base_pose]),
            "policy": "session_resource",
        }


# ═══════════════════════════════════════════════════════════════
# 构建 / 清理
# ═══════════════════════════════════════════════════════════════

def _is_shape(sim, handle: int) -> bool:
    try:
        return int(sim.getObjectType(handle)) == int(sim.object_shape_type)
    except Exception:
        return False


def _find_alias(sim, root: int, alias: str) -> int:
    for h in [root] + subtree(sim, root):
        if alias_of(sim, h) == alias:
            return int(h)
    return -1


def _hide(sim, handles: Iterable[int]) -> bool:
    applied = False
    param = getattr(sim, "objintparam_visibility_layer", None)
    for h in handles:
        if param is not None:
            try:
                sim.setObjectInt32Param(int(h), param, 0)
                applied = True
                continue
            except Exception:
                pass
        try:
            sim.setIntProperty(int(h), "visibilityLayers", 0)
            applied = True
        except Exception:
            continue
    return applied


def build_shadow_arm(sim, scene, *, arm_base=None, aliases=None,
                     base_pose=None) -> ShadowArm:
    """在当前场景中构建影子臂; 场景必须处于仿真停止态或尚未启动.

    ``aliases`` 用于阶段三多臂场景, 形如
    ``{"arm": "Shadow_UR5_02", "pad": ..., "tcp": ..., "load": ...}``;
    缺省保持阶段二的单臂别名. ``arm_base``/``base_pose`` 允许调用方
    指定该臂的基座, 而不是读取 ``scene.layout.arm_base``.
    """
    names = dict(aliases or {
        "arm": cfg.SHADOW_ARM_ALIAS,
        "pad": cfg.SHADOW_PAD_ALIAS,
        "tcp": cfg.SHADOW_TCP_ALIAS,
        "load": cfg.SHADOW_LOAD_ALIAS,
    })
    layout = scene.layout
    if arm_base is None:
        if layout is None:
            raise ShadowError("场景缺少 layout, 无法确定 UR5 基座")
        arm_base = layout.arm_base
    island_alias = getattr(scene, "root_alias", base.ALIASES["roots"][2])
    island_root = scene.handle(island_alias)
    if island_root < 0:
        raise ShadowError(f"找不到 Island 层级根: {island_alias}")

    arm = _load_model(sim, "ur5", names["arm"],
                      arm_base, island_root)
    if base_pose is not None:
        sim.setObjectPose(arm, list(base_pose), sim.handle_world)
    for h in [arm] + subtree(sim, arm):
        if _is_shape(sim, h):
            phys.set_static(sim, h, True)
            phys.set_respondable(sim, h, False)

    connection = -1
    for h in subtree(sim, arm):
        if alias_of(sim, h) == "connection":
            connection = int(h)
            if int(sim.getObjectType(h)) == int(sim.object_forcesensor_type):
                break
    if connection < 0:
        raise ShadowError("影子 UR5 找不到 connection 末端")

    joints = _chain_joints(sim, arm, connection)
    if len(joints) != 6:
        _remove_aliases(sim, names=names)
        raise ShadowError(f"影子 UR5 关节数不是 6: {len(joints)}")
    low, high = base.ARM_JOINT_LIMIT
    for joint, q in zip(joints, base.ARM_SEED_JOINTS):
        sim.setJointInterval(joint, False, [low, high - low])
        sim.setJointMode(joint, sim.jointmode_kinematic, 0)
        sim.setJointPosition(joint, float(q))

    pad = _load_model(sim, "suction_pad", names["pad"], None,
                      connection)
    sim.setObjectPose(pad, [0.0, 0.0, base.SUCTION_MOUNT_Z,
                            *base.SUCTION_MOUNT_QUATERNION], connection)
    for h in [pad] + subtree(sim, pad):
        if _is_shape(sim, h):
            phys.set_static(sim, h, True)
            phys.set_respondable(sim, h, False)

    tcp = int(sim.createDummy(0.01))
    sim.setObjectAlias(tcp, names["tcp"])
    sim.setObjectParent(tcp, pad, True)
    sim.setObjectPose(tcp, [*base.TCP_LOCAL_POS, *base.TCP_LOCAL_QUATERNION],
                      sim.handle_parent)

    load = int(sim.createPrimitiveShape(sim.primitiveshape_cuboid,
                                        list(base.MATERIAL_SIZE)))
    sim.setObjectAlias(load, names["load"])
    sim.setObjectParent(load, tcp, True)
    sim.setObjectPose(load, [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0], tcp)
    phys.set_static(sim, load, True)
    phys.set_kinematic(sim, load, True)
    phys.set_respondable(sim, load, False)
    # 未携带载荷时关闭查询参与性; 只在带载检查窗口打开
    sim.setBoolProperty(load, "collidable", False)
    sim.setBoolProperty(load, "measurable", False)

    links, link_aliases = discover_shadow_links(sim, arm)
    pad_body = _find_alias(sim, pad, "Body")
    if pad_body < 0 or not _is_shape(sim, pad_body):
        _remove_aliases(sim, names=names)
        raise ShadowError("影子吸盘找不到 Body shape")

    all_shapes = [h for h in [arm, *subtree(sim, arm)] if _is_shape(sim, h)]
    all_shapes += [h for h in [pad, *subtree(sim, pad)]
                   if _is_shape(sim, h) and h not in all_shapes]
    all_shapes.append(load)
    hidden = _hide(sim, all_shapes)
    return ShadowArm(root=int(arm), joints=[int(j) for j in joints],
                     connection=int(connection), pad_root=int(pad),
                     tcp=int(tcp), links=[int(h) for h in links],
                     link_aliases=list(link_aliases),
                     pad_body=int(pad_body), load=int(load),
                     all_shapes=[int(h) for h in all_shapes],
                     visibility_applied=bool(hidden),
                     aliases=names,
                     base_pose=(None if base_pose is None
                                else [float(v) for v in base_pose]))


def discover_shadow_links(sim, arm_root: int) -> tuple[list[int], list[str]]:
    by_alias = {}
    for h in subtree(sim, arm_root):
        if not _is_shape(sim, h):
            continue
        alias = alias_of(sim, h)
        if alias.startswith("link") and alias.endswith("_visible"):
            by_alias[alias] = int(h)
    aliases = [f"link{i}_visible" for i in range(1, cfg.UR5_LINK_COUNT + 1)]
    missing = [a for a in aliases if a not in by_alias]
    if missing:
        raise ShadowError(f"影子 UR5 可见连杆缺失: {missing}")
    return [by_alias[a] for a in aliases], aliases


def _remove_aliases(sim, names=None, prefix: Optional[str] = None
                    ) -> list[int]:
    """删除指定影子会话资源 (缺省删除全部 Shadow_*).

    ``names`` 用于阶段三多臂场景: 只删除本臂的 4 个根别名, 不误删另一台
    影子臂. 删除根对象会连同其子树一起移除; 失败时按前缀/别名再清理孤立
    对象.
    """
    wanted = {str(name) for name in (names or ()) if name}
    prefix = cfg.SESSION_RESOURCE_PREFIX if (not wanted and prefix is None) \
        else prefix

    def matches(alias: str) -> bool:
        if not alias:
            return False
        if wanted and alias in wanted:
            return True
        return bool(prefix) and alias.startswith(prefix)

    removed: list[int] = []
    for _ in range(3):
        pending = [int(h) for h in all_objects(sim) if matches(alias_of(sim, h))]
        if not pending:
            break
        for h in pending:
            try:
                sim.removeObjects([h])
                removed.append(h)
            except Exception:
                continue
    return removed


# ═══════════════════════════════════════════════════════════════
# 探针
# ═══════════════════════════════════════════════════════════════

class ShadowProbe:
    """在影子臂上设置任意关节构型; 不触碰真实机械臂."""

    def __init__(self, sim, shadow: ShadowArm) -> None:
        self.sim = sim
        self.shadow = shadow
        self._load_enabled = False

    # ── 构型 ────────────────────────────────────────────────

    def set_configuration(self, joints) -> None:
        values = [float(v) for v in joints]
        if len(values) != len(self.shadow.joints):
            raise ShadowError(
                f"影子臂关节数不匹配: {len(values)} != "
                f"{len(self.shadow.joints)}")
        if not all(math.isfinite(v) for v in values):
            raise ShadowError(f"构型含非有限关节角: {values}")
        for joint, q in zip(self.shadow.joints, values):
            self.sim.setJointPosition(joint, q)

    def tcp_pose(self) -> list[float]:
        return list(self.sim.getObjectPose(self.shadow.tcp,
                                           self.sim.handle_world))

    def link_poses(self, indices: Optional[list[int]] = None
                   ) -> list[list[float]]:
        targets = (self.shadow.links if indices is None
                   else [self.shadow.links[i] for i in indices])
        return [list(self.sim.getObjectPose(h, self.sim.handle_world))
                for h in targets]

    def key_link_poses(self) -> list[list[float]]:
        """肘部/腕部关键连杆位姿 (用于扫掠位移自适应)."""
        return self.link_poses([2, 3, 6])

    def pad_body_pose(self) -> list[float]:
        return list(self.sim.getObjectPose(self.shadow.pad_body,
                                           self.sim.handle_world))

    def load_pose(self) -> Optional[list[float]]:
        if self.shadow.load < 0:
            return None
        try:
            return list(self.sim.getObjectPose(self.shadow.load,
                                               self.sim.handle_world))
        except Exception:
            return None

    # ── 载荷副本 ────────────────────────────────────────────

    def set_load(self, transform: Optional[list[float]]) -> bool:
        """设置 TCP→物料相对变换; None 关闭载荷参与查询."""
        if transform is None:
            self._enabled(False)
            return True
        values = [float(v) for v in transform]
        if len(values) != 7:
            raise ShadowError("载荷相对变换必须是 7 元素")
        self.sim.setObjectPose(self.shadow.load, values,
                               self.shadow.tcp)
        self._enabled(True)
        return True

    def _enabled(self, enabled: bool) -> None:
        self.sim.setBoolProperty(self.shadow.load, "collidable", bool(enabled))
        self.sim.setBoolProperty(self.shadow.load, "measurable", bool(enabled))
        self._load_enabled = bool(enabled)

    @property
    def load_enabled(self) -> bool:
        return self._load_enabled

    # ── FK 一致性 ───────────────────────────────────────────

    def verify_fk(self, scene) -> dict:
        """在真实臂**当前**构型比较真实/影子连杆与 TCP 世界位姿."""
        errors = []
        ok = True
        for alias, shadow_link in zip(self.shadow.link_aliases,
                                      self.shadow.links):
            real = -1
            for h in [scene.robot_root] + subtree(self.sim, scene.robot_root):
                if alias_of(self.sim, h) == alias:
                    real = int(h)
                    break
            if real < 0:
                errors.append({"link": alias, "error": "real_missing"})
                ok = False
                continue
            real_pose = list(self.sim.getObjectPose(real,
                                                    self.sim.handle_world))
            shadow_pose = list(self.sim.getObjectPose(shadow_link,
                                                      self.sim.handle_world))
            dp = math.dist(real_pose[:3], shadow_pose[:3])
            da = _quat_angle(real_pose[3:7], shadow_pose[3:7])
            if dp > cfg.FK_POS_TOL or da > cfg.FK_ANG_TOL:
                ok = False
            errors.append({"link": alias, "pos_err": round(dp, 8),
                           "ang_err": round(da, 8)})
        try:
            real_tcp = list(self.sim.getObjectPose(scene.tcp,
                                                   self.sim.handle_world))
            shadow_tcp = self.tcp_pose()
            dp = math.dist(real_tcp[:3], shadow_tcp[:3])
            da = _quat_angle(real_tcp[3:7], shadow_tcp[3:7])
            if dp > cfg.FK_POS_TOL or da > cfg.FK_ANG_TOL:
                ok = False
            errors.append({"link": "tcp", "pos_err": round(dp, 8),
                           "ang_err": round(da, 8)})
        except Exception as exc:
            ok = False
            errors.append({"link": "tcp", "error": str(exc)})
        return {
            "ok": ok,
            "pos_tol": cfg.FK_POS_TOL,
            "ang_tol": cfg.FK_ANG_TOL,
            "joints": [round(float(self.sim.getJointPosition(j)), 6)
                       for j in self.shadow.joints],
            "links": errors,
        }

    # ── 清理 ────────────────────────────────────────────────

    def destroy(self, names=None) -> list[int]:
        """删除本影子臂的会话资源; 缺省按本臂别名精确清理."""
        return _remove_aliases(self.sim,
                               names=(self.shadow.alias_names if names is None
                                      else names))


def _quat_angle(a, b) -> float:
    dot = abs(sum(float(a[i]) * float(b[i]) for i in range(4)))
    return 2.0 * math.acos(max(-1.0, min(1.0, dot)))
