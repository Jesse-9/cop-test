"""runtime_dual.py — v1 双站层两台 UR5 的会话资源装配.

每台工位拥有**独立**:
  - 影子 UR5 / suction pad / TCP / 载荷副本;
  - 碰撞集合与 `ConfigurationChecker` / `LocalPlanner`;
  - `Stage2Safety` 锁存;
  - simIK environment (由调用方为每台臂各建一个 `Ur5IkController`).

共享的只有纯配置/算法和共享工作区几何:
  - 传送带 pads、两个置物台、仓库、地面、AGV 车体/载台;
  - 另一台**真实**机械臂的可见连杆与吸盘 Body (作为本臂的障碍).

影子臂/集合都是会话资源, 不写入 v1 工件. 两台臂的别名分别使用
`Shadow_UR5_01/02` 等, 清理时精确定位, 不误删另一台.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from . import config_base as base
from .scene_base import alias_of
from . import config_motion as s2cfg
from .motion.collisions import (CollisionRuntime, CollisionError,
                               discover_pad_body, discover_ur5_links)
from .motion.path_planning import ConfigurationChecker, LocalPlanner
from .motion.safety import Stage2Safety
from .motion.shadow import ShadowProbe, build_shadow_arm

from . import config_dual as cfg
from .layout_dual import UnitLayoutView
from .scene_dual import Stage3Scene, Stage3Unit


class RuntimeBuildError(RuntimeError):
    code = "STAGE3_RUNTIME"


@dataclass
class UnitSceneView:
    """单工位视图: 只暴露该臂的句柄与目标, 不共享可变任务状态."""

    unit_id: str
    scene: Stage3Scene
    unit: Stage3Unit
    root_alias: str = base.ALIASES["roots"][2]

    @property
    def layout(self) -> UnitLayoutView:
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
            "controller_id": cfg.STAGE3_CONTROLLER_ID,
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
    scene: Stage3Scene
    arms: dict[str, Stage3ArmRuntime]
    semantics: dict = field(default_factory=dict)
    startup: dict = field(default_factory=dict)
    query_restore: list[dict] = field(default_factory=list)

    def arm(self, unit_id: str) -> Stage3ArmRuntime:
        try:
            return self.arms[str(unit_id)]
        except KeyError as exc:
            raise RuntimeBuildError(f"未知工位运行时: {unit_id!r}") from exc

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
            "controller_id": cfg.STAGE3_CONTROLLER_ID,
            "contract_version": cfg.STAGE3_CONTRACT_VERSION,
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
            alias_of(self.sim, h) for h in _shadow_aliases(self.sim))
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


def _shadow_aliases(sim) -> list[int]:
    from .scene_base import all_objects
    return [int(h) for h in all_objects(sim)
            if (alias_of(sim, h) or "").startswith(
                cfg.SESSION_RESOURCE_PREFIX)]


def _ensure_queryable(sim, handles) -> list[dict]:
    """真实对方机械臂的连杆/pad 必须参与碰撞查询.

    只改 `collidable`/`measurable` 查询属性, 不改 static/respondable/质量/
    摩擦/掩码; 记录原值, 由 `Stage3Runtime.destroy` 在会话结束时恢复.
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


def _other_unit_obstacles(sim, scene: Stage3Scene,
                          unit_id: str) -> tuple[list[int], dict]:
    """另一台真实臂的可见连杆、吸盘 Body 与岛体 (作为本臂障碍)."""
    other_id = "B" if unit_id == "A" else "A"
    other = scene.unit(other_id)
    links, link_aliases = discover_ur5_links(sim, other.robot_root)
    pad_body = discover_pad_body(sim, other.suction_root)
    handles = [*links, pad_body, other.island]
    query = _ensure_queryable(sim, [*links, pad_body])
    return handles, {
        "other_unit": other_id,
        "link_aliases": link_aliases,
        "pad_body": int(pad_body),
        "island": int(other.island),
        "query_properties": query,
    }


def _measure_cup_radius(sim, pad_body: int) -> float:
    value = sim.getShapeBB(pad_body)
    size = (value[0] if isinstance(value, (tuple, list)) and len(value) == 2
            else value)
    return min(float(size[0]), float(size[1])) / 2.0


def build_stage3_runtime(sim, scene: Stage3Scene, ik_by_unit: dict,
                         logger=None) -> Stage3Runtime:
    """构建两台影子臂/碰撞集合/规划器/锁存; 仿真必须处于停止态."""
    try:
        state = int(sim.getSimulationState())
    except Exception as exc:
        raise RuntimeBuildError(f"无法读取仿真状态: {exc}") from exc
    if state != int(sim.simulation_stopped):
        raise RuntimeBuildError(
            f"阶段三运行时必须在仿真停止态构建 (state={state})")

    station_aliases = tuple(scene.unit(uid).aliases["station"]
                            for uid in ("A", "B"))
    arms: dict[str, Stage3ArmRuntime] = {}
    semantics: dict = {}
    query_restore: list[dict] = []
    for unit_id in ("A", "B"):
        unit = scene.unit(unit_id)
        view = UnitSceneView(unit_id=unit_id, scene=scene, unit=unit)
        current_probe = None
        current_collisions = None
        try:
            shadow = build_shadow_arm(
                sim, view, arm_base=unit.spec.arm_base,
                aliases=cfg.SHADOW_ALIASES[unit_id])
            probe = ShadowProbe(sim, shadow)
            current_probe = probe
            actual_q = [float(sim.getJointPosition(j))
                        for j in unit.robot_joints]
            probe.set_configuration(actual_q)
            fk = probe.verify_fk(view)
            if not fk["ok"]:
                raise RuntimeBuildError(
                    f"{unit_id} 真实/影子 FK 不一致: {fk}")

            cup_radius = _measure_cup_radius(sim, shadow.pad_body)
            if abs(cup_radius - s2cfg.CUP_RADIUS_DESIGN) > s2cfg.CUP_RADIUS_TOL:
                raise RuntimeBuildError(
                    f"{unit_id} 杯口半径实测 {cup_radius:.6f} 与设计 "
                    f"{s2cfg.CUP_RADIUS_DESIGN:.6f} 不一致")

            extra, other_info = _other_unit_obstacles(sim, scene, unit_id)
            query_restore.extend(other_info.get("query_properties") or [])
            collisions = CollisionRuntime.build(
                sim, view, link_handles=shadow.links,
                link_aliases=shadow.link_aliases,
                pad_body=shadow.pad_body, load_handle=shadow.load,
                mount_alias=unit.aliases["island"],
                station_aliases=station_aliases,
                extra_obstacles=extra)
            current_collisions = collisions
            if not semantics:
                semantics = collisions.query_semantics_probe()
                if not semantics.get("ok"):
                    raise RuntimeBuildError(
                        f"碰撞查询语义自检失败: {semantics}")

            checker = ConfigurationChecker(sim, probe, collisions,
                                           cup_radius=cup_radius)
            planner = LocalPlanner(checker, ik_by_unit[unit_id])
            safety = Stage2Safety(logger=logger)
            startup = {
                "fk": fk,
                "cup_radius_measured": round(cup_radius, 6),
                "cup_radius_design": s2cfg.CUP_RADIUS_DESIGN,
                "other_unit": other_info,
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
            arms[unit_id] = Stage3ArmRuntime(
                unit_id=unit_id, view=view, shadow=shadow, probe=probe,
                collisions=collisions, checker=checker, planner=planner,
                safety=safety, cup_radius=cup_radius, startup=startup)
        except Exception:
            # 清理本臂半成品与已建臂, 避免影子对象/集合残留
            if current_collisions is not None:
                current_collisions.destroy()
            if current_probe is not None:
                current_probe.destroy()
            for built in arms.values():
                built.destroy()
            raise

    runtime = Stage3Runtime(
        sim=sim, scene=scene, arms=arms, semantics=semantics,
        startup={
            "units": [uid for uid in ("A", "B")],
            "shadow_aliases": cfg.SHADOW_ALIASES,
            "session_resource_policy": "not_persisted",
            "inter_arm_policy": "real_other_unit_in_workcell",
        },
        query_restore=query_restore)
    if logger:
        logger.info(
            "[STAGE3] 运行时构建完成: units=A/B, FK ok, cup_r=%.6f, "
            "inter-arm obstacles=%s",
            arms["A"].cup_radius,
            {uid: arms[uid].startup["other_unit"]["other_unit"]
             for uid in ("A", "B")})
    return runtime
