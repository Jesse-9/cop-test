"""runtime_common.py — 双站层与 v1 共用的单臂会话运行时.

本模块不按工位或场景类型枚举实例, 只提供:
  - 单臂视图/运行时容器 (:class:`UnitSceneView`,
    :class:`Stage3ArmRuntime`, :class:`Stage3Runtime`);
  - 共享的查询属性与杯口半径辅助函数;
  - 单臂构建函数 :func:`build_arm_runtime`.

场景级差异仍由调用方决定: Stage3 只把另一台真实臂作为本臂障碍, v1 把
其余四台真实臂与包络附近的物流线纳入工作区. 两个公开入口
``build_stage3_runtime`` / ``build_v1_runtime`` 保留各自的障碍策略.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from . import config_base as base
from . import config_motion as s2cfg
from .config_dual import (SESSION_RESOURCE_PREFIX, STAGE3_CONTRACT_VERSION,
                          STAGE3_CONTROLLER_ID)
from .motion.collisions import CollisionRuntime
from .motion.path_planning import ConfigurationChecker, LocalPlanner
from .motion.safety import Stage2Safety
from .motion.shadow import ShadowProbe, build_shadow_arm
from .scene_base import alias_of, all_objects

if TYPE_CHECKING:  # pragma: no cover - 仅用于类型检查
    import logging

    from .layout_dual import UnitLayoutView
    from .scene_dual import Stage3Scene, Stage3Unit


@dataclass
class UnitSceneView:
    """单工位视图: 只暴露该臂的句柄与目标, 不共享可变任务状态."""

    unit_id: str
    scene: "Stage3Scene"
    unit: "Stage3Unit"
    root_alias: str = base.ALIASES["roots"][2]

    @property
    def layout(self) -> "UnitLayoutView":
        return self.unit.layout

    @property
    def robot_root(self) -> int:
        return self.unit.robot_root

    @property
    def robot_connection(self) -> int:
        return self.unit.robot_connection

    @property
    def robot_joints(self) -> list[int]:
        return list(self.unit.robot_joints)

    @property
    def suction_root(self) -> int:
        return self.unit.suction_root

    @property
    def tcp(self) -> int:
        return self.unit.tcp

    @property
    def suction_sensor(self) -> int:
        return self.unit.suction_sensor

    @property
    def suction_model_sensor(self) -> int:
        return self.unit.suction_model_sensor

    @property
    def suction_link(self) -> int:
        return self.unit.suction_link

    @property
    def agv_root(self) -> int:
        return self.scene.agv_root

    def handle(self, alias: str) -> int:
        return self.scene.handle(alias)

    def check_sensor(self, alias: str, entity: int):
        return self.scene.check_sensor(alias, entity)

    def pick_sensor_hits(self, handles):
        return self.scene.pick_sensor_hits(handles)

    def suction_sensor_hits(self, handles):
        return self.scene.suction_sensor_hits(handles)

    def agv_dock_hit(self, agv_handle: int) -> bool:
        return self.scene.agv_dock_hit_at(self.unit_id, agv_handle)


@dataclass
class Stage3ArmRuntime:
    unit_id: str
    view: UnitSceneView
    shadow: object
    probe: ShadowProbe
    collisions: CollisionRuntime
    checker: ConfigurationChecker
    planner: LocalPlanner
    safety: Stage2Safety
    cup_radius: float = s2cfg.CUP_RADIUS_DESIGN
    startup: dict = field(default_factory=dict)
    part_handles: set = field(default_factory=set)
    hazard_handles: list[int] = field(default_factory=list)

    # ── 物料角色 ────────────────────────────────────────────

    def update_materials(self, manager, target_uid: Optional[int],
                         carrying: bool = False) -> None:
        current = {int(h) for h in manager.handles() if h is not None}
        target = None
        if target_uid is not None:
            rec = manager.by_uid(target_uid)
            if rec is not None and rec.sim_handle is not None:
                target = int(rec.sim_handle)
        for handle in sorted(self.part_handles - current):
            self.collisions.remove_part(handle)
        self.collisions.set_target_carried(target, carrying)
        desired = current - ({target} if target is not None else set())
        for handle in sorted(desired - self.part_handles):
            self.collisions.add_part(handle)
        self.part_handles = desired

    def set_attach_transform(self, transform) -> None:
        self.probe.set_load(transform)

    def forget_material(self, handle: int) -> None:
        self.collisions.remove_part(int(handle))
        self.part_handles.discard(int(handle))

    # ── 运行期障碍 ──────────────────────────────────────────

    def add_hazard(self, handle: int) -> None:
        self.collisions.add_hazard(int(handle))
        self.hazard_handles.append(int(handle))

    def remove_hazards(self) -> None:
        for handle in list(self.hazard_handles):
            self.collisions.remove_hazard(handle)
        self.hazard_handles.clear()

    # ── 报告/清理 ────────────────────────────────────────────

    def as_dict(self) -> dict:
        return {
            "unit_id": self.unit_id,
            "controller_id": STAGE3_CONTROLLER_ID,
            "cup_radius": round(float(self.cup_radius), 6),
            "shadow": self.shadow.as_dict(),
            "collections": self.collisions.spec_dict(),
            "startup": self.startup,
        }

    def destroy(self) -> dict:
        report = {"hazards_removed": len(self.hazard_handles)}
        self.remove_hazards()
        self.collisions.destroy()
        report["shadow_removed"] = len(self.probe.destroy())
        report["aliases"] = list(self.shadow.alias_names)
        return report


@dataclass
class Stage3Runtime:
    sim: object
    scene: "Stage3Scene"
    arms: dict[str, Stage3ArmRuntime]
    semantics: dict = field(default_factory=dict)
    startup: dict = field(default_factory=dict)
    query_restore: list[dict] = field(default_factory=list)
    session_prefix: str = SESSION_RESOURCE_PREFIX
    build_error: type[Exception] = RuntimeError

    def arm(self, unit_id: str) -> Stage3ArmRuntime:
        try:
            return self.arms[str(unit_id)]
        except KeyError as exc:
            raise self.build_error(f"未知工位运行时: {unit_id!r}") from exc

    def update_materials(self, manager, targets: dict,
                         carrying: Optional[dict] = None) -> None:
        carrying = carrying or {}
        for unit_id, arm in self.arms.items():
            arm.update_materials(manager, targets.get(unit_id),
                                 bool(carrying.get(unit_id, False)))

    def set_attach_transform(self, unit_id: str, transform) -> None:
        self.arm(unit_id).set_attach_transform(transform)

    def forget_material(self, handle: int) -> None:
        for arm in self.arms.values():
            arm.forget_material(handle)

    def add_hazard(self, handle: int) -> None:
        for arm in self.arms.values():
            arm.add_hazard(handle)

    def remove_hazards(self) -> None:
        for arm in self.arms.values():
            arm.remove_hazards()

    def as_dict(self) -> dict:
        return {
            "controller_id": STAGE3_CONTROLLER_ID,
            "contract_version": STAGE3_CONTRACT_VERSION,
            "arms": {unit_id: arm.as_dict()
                     for unit_id, arm in self.arms.items()},
            "query_semantics": self.semantics,
            "startup": self.startup,
        }

    def destroy(self) -> dict:
        report = {"arms": {}}
        for unit_id, arm in self.arms.items():
            report["arms"][unit_id] = arm.destroy()
        report["query_restore"] = self._restore_query_properties()
        report["leftover"] = sorted(
            alias_of(self.sim, h)
            for h in _shadow_aliases(self.sim, self.session_prefix))
        return report

    def _restore_query_properties(self) -> dict:
        """恢复会话期启用的 collidable/measurable 原值 (不触碰物理属性)."""
        restored = 0
        failed = []
        for row in self.query_restore:
            before = row.get("before") or {}
            handle = row.get("handle")
            if handle is None:
                continue
            for prop, value in before.items():
                try:
                    self.sim.setBoolProperty(int(handle), prop, bool(value))
                    restored += 1
                except Exception as exc:
                    failed.append({"handle": handle, "prop": prop,
                                   "error": f"{type(exc).__name__}: {exc}"})
        return {"restored": restored, "failed": failed}


def _shadow_aliases(sim, prefix: str) -> list[int]:
    return [int(h) for h in all_objects(sim)
            if (alias_of(sim, h) or "").startswith(prefix)]


def _ensure_queryable(sim, handles) -> list[dict]:
    """真实对方机械臂的连杆/pad 必须参与碰撞查询.

    只改 `collidable`/`measurable` 查询属性, 不改 static/respondable/质量/
    摩擦/掩码; 记录原值, 由 :meth:`Stage3Runtime.destroy` 在会话结束时恢复.
    """
    applied = []
    for h in handles:
        if h is None or int(h) < 0:
            continue
        row = {"alias": alias_of(sim, int(h)), "handle": int(h)}
        try:
            before_collidable = bool(sim.getBoolProperty(
                int(h), "collidable"))
            before_measurable = bool(sim.getBoolProperty(
                int(h), "measurable"))
            row["before"] = {"collidable": before_collidable,
                             "measurable": before_measurable}
            sim.setBoolProperty(int(h), "collidable", True)
            sim.setBoolProperty(int(h), "measurable", True)
            row["ok"] = True
        except Exception as exc:
            row["ok"] = False
            row["error"] = f"{type(exc).__name__}: {exc}"
        applied.append(row)
    return applied


def _measure_cup_radius(sim, pad_body: int) -> float:
    value = sim.getShapeBB(pad_body)
    size = (value[0] if isinstance(value, (tuple, list)) and len(value) == 2
            else value)
    return min(float(size[0]), float(size[1])) / 2.0


def build_arm_runtime(sim, *, view: UnitSceneView, shadow_aliases,
                      station_aliases, obstacles, obstacle_info: dict,
                      obstacle_key: str, ik, conveyor_aliases=(),
                      probe_semantics: bool = False,
                      extra_startup: Optional[dict] = None,
                      error_cls: type[Exception] = RuntimeError,
                      logger: Optional["logging.Logger"] = None
                      ) -> tuple[Stage3ArmRuntime, Optional[dict]]:
    """构建单臂影子副本/碰撞集合/规划器/锁存.

    调用方提供本臂的工位视图、影子别名、场景级障碍句柄与 station/
    conveyor aliases; 本函数不枚举工位, 也不合并不同场景的障碍策略.
    返回运行时臂和 (仅在 ``probe_semantics`` 时) 碰撞查询语义自检结果.

    构建中途失败时只清理本臂已创建的资源; 已建臂由调用方清理.
    """
    unit = view.unit
    current_probe = None
    current_collisions = None
    try:
        shadow = build_shadow_arm(
            sim, view, arm_base=unit.spec.arm_base,
            aliases=shadow_aliases)
        probe = ShadowProbe(sim, shadow)
        current_probe = probe
        actual_q = [float(sim.getJointPosition(j))
                    for j in unit.robot_joints]
        probe.set_configuration(actual_q)
        fk = probe.verify_fk(view)
        if not fk["ok"]:
            raise error_cls(
                f"{view.unit_id} 真实/影子 FK 不一致: {fk}")

        cup_radius = _measure_cup_radius(sim, shadow.pad_body)
        if abs(cup_radius - s2cfg.CUP_RADIUS_DESIGN) > \
                s2cfg.CUP_RADIUS_TOL:
            raise error_cls(
                f"{view.unit_id} 杯口半径实测 {cup_radius:.6f} 与设计 "
                f"{s2cfg.CUP_RADIUS_DESIGN:.6f} 不一致")

        collisions = CollisionRuntime.build(
            sim, view, link_handles=shadow.links,
            link_aliases=shadow.link_aliases,
            pad_body=shadow.pad_body, load_handle=shadow.load,
            mount_alias=unit.aliases["island"],
            station_aliases=station_aliases,
            conveyor_aliases=tuple(conveyor_aliases),
            extra_obstacles=obstacles)
        current_collisions = collisions
        semantics = None
        if probe_semantics:
            semantics = collisions.query_semantics_probe()
            if not semantics.get("ok"):
                raise error_cls(
                    f"碰撞查询语义自检失败: {semantics}")

        checker = ConfigurationChecker(sim, probe, collisions,
                                       cup_radius=cup_radius)
        planner = LocalPlanner(checker, ik)
        safety = Stage2Safety(logger=logger)
        startup = {
            "fk": fk,
            "cup_radius_measured": round(cup_radius, 6),
            "cup_radius_design": s2cfg.CUP_RADIUS_DESIGN,
            obstacle_key: obstacle_info,
            "self_pairs": len(collisions._self_pairs),
            "collections": {
                name: len(spec.items)
                for name, spec in zip(collisions.collections,
                                      collisions.specs)},
            "excluded": collisions.geometry.excluded,
            "workcell_aliases": collisions.geometry.workcell_aliases,
            "shadow_hidden": shadow.visibility_applied,
            "station_aliases": list(station_aliases),
        }
        if extra_startup:
            startup.update(extra_startup)
        arm = Stage3ArmRuntime(
            unit_id=view.unit_id, view=view, shadow=shadow, probe=probe,
            collisions=collisions, checker=checker, planner=planner,
            safety=safety, cup_radius=cup_radius, startup=startup)
        return arm, semantics
    except Exception:
        # 清理本臂半成品; 已建臂与 query 属性由调用方/正常销毁处理.
        if current_collisions is not None:
            current_collisions.destroy()
        if current_probe is not None:
            current_probe.destroy()
        raise
