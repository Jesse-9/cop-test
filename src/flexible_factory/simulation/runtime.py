"""runtime.py — v1 UFLOW 五台 UR5 的会话资源装配.

每台工位拥有**独立**:
  - 影子 UR5 / suction pad / TCP / 载荷副本;
  - 碰撞集合与 `ConfigurationChecker` / `LocalPlanner`;
  - `Stage2Safety` 锁存;
  - simIK environment (由 runner 为每台臂各建一个 `Ur5IkController`).

共享的只有纯配置/算法和工作区几何: 四条物流线的承载 pad、两个置物台、
仓库、地面、AGV 车体/载台, 以及**其余真实机械臂**的可见连杆与吸盘 Body
(作为本臂障碍). 影子臂/集合都是会话资源, 不写入 v1 工件; 清理时按
本臂别名精确删除, 不误删其他臂.

UFLOW 层相对双站层的变化:
  - 5 个工位而不是 2 个;
  - 工作区碰撞集合包含四条线的 pad 和其余 4 台真实臂;
  - 其余真实臂的 `collidable`/`measurable` 只在会话期启用, 销毁时恢复.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from . import config_base as base
from .scene_base import alias_of, all_objects
from . import config_motion as s2cfg
from .motion.collisions import (CollisionRuntime, CollisionError,
                               discover_pad_body, discover_ur5_links)
from .motion.path_planning import ConfigurationChecker, LocalPlanner
from .motion.safety import Stage2Safety
from .motion.shadow import ShadowProbe, build_shadow_arm
from .runtime_dual import (Stage3ArmRuntime, Stage3Runtime, UnitSceneView,
                            _ensure_queryable, _measure_cup_radius)

from . import config as cfg
from .scene import Stage4Scene, Stage4Unit


class RuntimeBuildError(RuntimeError):
    code = "STAGE4_RUNTIME"


@dataclass
class Stage4Runtime(Stage3Runtime):
    """五台臂的会话资源集合 (字段语义与阶段三一致)."""

    def as_dict(self) -> dict:
        return {
            "controller_id": cfg.STAGE4_CONTROLLER_ID,
            "contract_version": cfg.STAGE4_CONTRACT_VERSION,
            "arms": {unit_id: arm.as_dict()
                     for unit_id, arm in self.arms.items()},
            "query_semantics": self.semantics,
            "startup": self.startup,
        }


_UNIT_LINE_IDS = {
    "A": "line_s1a",
    "B": "line_s1b",
    "C": "line_ce",
    "D": "line_s2d",
    "E": "line_ce",
}


def _rect_distance_2d(point, rect) -> float:
    dx = max(rect[0] - point[0], 0.0, point[0] - rect[2])
    dy = max(rect[1] - point[1], 0.0, point[1] - rect[3])
    return (dx * dx + dy * dy) ** 0.5


def _arm_conveyor_aliases(scene: Stage4Scene, unit_id: str) -> list[str]:
    """本臂工作区需要纳入的物流线: 本岛线与包络附近的线.

    四条线全部纳入会让每次碰撞查询对 200 个 pad 求交; 这里只保留与
    本臂基座距离不超过 reach + 余量的线, 并始终包含本岛线. 过滤规则
    是保守的静态包络, 不改变线的物理属性.
    """
    from .layout import line_specs
    unit = scene.unit(unit_id)
    own = _UNIT_LINE_IDS.get(unit_id)
    aliases: list[str] = []
    limit = base.REACH_LIMIT + 0.35
    for line_id, line in line_specs().items():
        if line_id == own:
            aliases.append(line.conveyor)
            continue
        if _rect_distance_2d(unit.spec.arm_base, line.top_run_rect) <= limit:
            aliases.append(line.conveyor)
    return aliases


def _unit_obstacles(sim, scene: Stage4Scene,
                    unit_id: str) -> tuple[list[int], dict]:
    """其余 4 台真实臂的可见连杆、吸盘 Body 与岛体 (作为本臂障碍)."""
    handles: list[int] = []
    query: list[dict] = []
    others: list[dict] = []
    for other_id, other in scene.units.items():
        if other_id == unit_id:
            continue
        links, link_aliases = discover_ur5_links(sim, other.robot_root)
        pad_body = discover_pad_body(sim, other.suction_root)
        handles.extend([*links, pad_body, other.island])
        query.extend(_ensure_queryable(sim, [*links, pad_body]))
        others.append({
            "unit_id": other_id,
            "link_aliases": link_aliases,
            "pad_body": int(pad_body),
            "island": int(other.island),
        })
    return handles, {"other_units": others, "query_properties": query}


def build_v1_runtime(sim, scene: Stage4Scene, ik_by_unit: dict,
                         logger=None) -> Stage4Runtime:
    """构建五台影子臂/碰撞集合/规划器/锁存; 仿真必须处于停止态."""
    try:
        state = int(sim.getSimulationState())
    except Exception as exc:
        raise RuntimeBuildError(f"无法读取仿真状态: {exc}") from exc
    if state != int(sim.simulation_stopped):
        raise RuntimeBuildError(
            f"阶段四运行时必须在仿真停止态构建 (state={state})")

    station_aliases = tuple(
        unit.aliases["station"] for unit in scene.units.values()
        if unit.spec.has_station)
    arms: dict[str, Stage3ArmRuntime] = {}
    semantics: dict = {}
    query_restore: list[dict] = []
    for unit_id in cfg.UNIT_IDS:
        if logger:
            logger.info("[V1] 构建 %s 影子臂/碰撞集合 ...", unit_id)
        unit = scene.unit(unit_id)
        conveyor_aliases = _arm_conveyor_aliases(scene, unit_id)
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
            if abs(cup_radius - s2cfg.CUP_RADIUS_DESIGN) > \
                    s2cfg.CUP_RADIUS_TOL:
                raise RuntimeBuildError(
                    f"{unit_id} 杯口半径实测 {cup_radius:.6f} 与设计 "
                    f"{s2cfg.CUP_RADIUS_DESIGN:.6f} 不一致")

            extra, other_info = _unit_obstacles(sim, scene, unit_id)
            query_restore.extend(other_info.get("query_properties") or [])
            collisions = CollisionRuntime.build(
                sim, view, link_handles=shadow.links,
                link_aliases=shadow.link_aliases,
                pad_body=shadow.pad_body, load_handle=shadow.load,
                mount_alias=unit.aliases["island"],
                station_aliases=station_aliases,
                conveyor_aliases=conveyor_aliases,
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
                "other_units": other_info,
                "self_pairs": len(collisions._self_pairs),
                "collections": {
                    name: len(spec.items)
                    for name, spec in zip(collisions.collections,
                                          collisions.specs)},
                "excluded": collisions.geometry.excluded,
                "workcell_aliases": collisions.geometry.workcell_aliases,
                "shadow_hidden": shadow.visibility_applied,
                "station_aliases": list(station_aliases),
                "conveyor_aliases": list(conveyor_aliases),
            }
            arms[unit_id] = Stage3ArmRuntime(
                unit_id=unit_id, view=view, shadow=shadow, probe=probe,
                collisions=collisions, checker=checker, planner=planner,
                safety=safety, cup_radius=cup_radius, startup=startup)
            if logger:
                logger.info("[V1] %s 运行时完成 (%d collections, "
                            "%d workcell)", unit_id,
                            len(collisions.specs),
                            len(collisions.geometry.workcell))
        except Exception:
            if current_collisions is not None:
                current_collisions.destroy()
            if current_probe is not None:
                current_probe.destroy()
            for built in arms.values():
                built.destroy()
            raise

    runtime = Stage4Runtime(
        sim=sim, scene=scene, arms=arms, semantics=semantics,
        startup={
            "units": list(cfg.UNIT_IDS),
            "shadow_aliases": cfg.SHADOW_ALIASES,
            "session_resource_policy": "not_persisted",
            "inter_arm_policy": "all_other_units_in_workcell",
            "adjacency_zones": cfg.ARM_ADJACENCY_ZONES,
        },
        query_restore=query_restore)
    if logger:
        logger.info(
            "[V1] 运行时构建完成: units=%s, cup_r=%.6f, "
            "adjacency=%s", list(cfg.UNIT_IDS), arms["A"].cup_radius,
            cfg.ARM_ADJACENCY_ZONES)
    return runtime


def _shadow_aliases(sim) -> list[int]:
    return [int(h) for h in all_objects(sim)
            if (alias_of(sim, h) or "").startswith(
                cfg.SESSION_RESOURCE_PREFIX)]
