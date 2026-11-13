"""runtime.py — v1 双站层/五岛层 UR5 会话资源装配.

本模块包含两个场景级入口:

  - :func:`build_stage3_runtime`: 双站层两台 UR5, 另一台真实臂作为障碍,
    错误类型为 :class:`Stage3RuntimeBuildError` (STAGE3_RUNTIME);
  - :func:`build_v1_runtime`: UFLOW 层五台 UR5, 其余四台真实臂和包络附近
    的物流线纳入工作区, 错误类型为 :class:`RuntimeBuildError`
    (STAGE4_RUNTIME).

每台工位拥有**独立**:
  - 影子 UR5 / suction pad / TCP / 载荷副本;
  - 碰撞集合与 `ConfigurationChecker` / `LocalPlanner`;
  - `Stage2Safety` 锁存;
  - simIK environment (由 runner 为每台臂各建一个 `Ur5IkController`).

共享的只有纯配置/算法和工作区几何: 物流线的承载 pad、置物台、仓库、
地面、AGV 车体/载台, 以及其余真实机械臂的可见连杆与吸盘 Body. 影子臂/
集合都是会话资源, 不写入 v1 工件; 清理时按本臂别名精确删除, 不误删
其他臂.

UFLOW 层相对双站层的变化:
  - 5 个工位而不是 2 个;
  - 工作区碰撞集合包含物流线 pad 和其余 4 台真实臂;
  - 其余真实臂的 `collidable`/`measurable` 只在会话期启用, 销毁时恢复.

单臂构建和共享运行时容器位于
:mod:`flexible_factory.simulation.runtime_common`; 本模块只保留场景级
障碍/物流线策略和公开入口. `runtime_dual.py` 仅保留旧导入路径兼容.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import config_base as base
from .runtime_common import (DiscoveryCache, Stage3ArmRuntime, Stage3Runtime,
                             UnitSceneView, build_arm_runtime)

from . import config as cfg
from . import config_dual as dualcfg
from .scene import Stage4Scene
from .scene_dual import Stage3Scene

__all__ = [
    "RuntimeBuildError",
    "Stage3RuntimeBuildError",
    "Stage4Runtime",
    "build_stage3_runtime",
    "build_v1_runtime",
]


class RuntimeBuildError(RuntimeError):
    code = "STAGE4_RUNTIME"


class Stage3RuntimeBuildError(RuntimeError):
    code = "STAGE3_RUNTIME"


def normalize_unit_ids(unit_ids=None) -> tuple[str, ...]:
    """规范化 v1 工位子集; None 表示全部 A–E.

    子集只用于订单运行, 不改变场景工件或配置摘要. 空集合和未知工位
    直接拒绝, 避免静默构建零台臂。
    """
    if unit_ids is None:
        return tuple(cfg.UNIT_IDS)
    values = tuple(str(item) for item in unit_ids)
    if not values:
        raise ValueError("unit_ids 不能为空; 至少构建一台臂")
    unknown = [item for item in values if item not in cfg.UNIT_IDS]
    if unknown:
        raise ValueError(
            f"未知工位 {unknown}; 允许 {list(cfg.UNIT_IDS)}")
    wanted = set(values)
    return tuple(unit_id for unit_id in cfg.UNIT_IDS if unit_id in wanted)


@dataclass
class Stage4Runtime(Stage3Runtime):
    """指定工位的会话资源集合 (字段语义与阶段三一致)."""

    def as_dict(self) -> dict:
        return {
            "controller_id": cfg.STAGE4_CONTROLLER_ID,
            "contract_version": cfg.STAGE4_CONTRACT_VERSION,
            "arms": {unit_id: arm.as_dict()
                     for unit_id, arm in self.arms.items()},
            "query_semantics": self.semantics,
            "startup": self.startup,
        }


def _other_unit_obstacles(sim, scene: Stage3Scene, unit_id: str,
                          discovery: DiscoveryCache
                          ) -> tuple[list[int], dict]:
    """另一台真实臂的可见连杆、吸盘 Body 与岛体 (作为本臂障碍)."""
    other_id = "B" if unit_id == "A" else "A"
    other = scene.unit(other_id)
    links, link_aliases, pad_body = discovery.ur5(
        sim, other.robot_root, other.suction_root)
    handles = [*links, pad_body, other.island]
    query = discovery.ensure_queryable(sim, [*links, pad_body])
    return handles, {
        "other_unit": other_id,
        "link_aliases": link_aliases,
        "pad_body": int(pad_body),
        "island": int(other.island),
        "query_properties": query,
    }


def build_stage3_runtime(sim, scene: Stage3Scene, ik_by_unit: dict,
                         logger=None) -> Stage3Runtime:
    """构建两台影子臂/碰撞集合/规划器/锁存; 仿真必须处于停止态."""
    try:
        state = int(sim.getSimulationState())
    except Exception as exc:
        raise Stage3RuntimeBuildError(f"无法读取仿真状态: {exc}") from exc
    if state != int(sim.simulation_stopped):
        raise Stage3RuntimeBuildError(
            f"阶段三运行时必须在仿真停止态构建 (state={state})")

    station_aliases = tuple(scene.unit(uid).aliases["station"]
                            for uid in ("A", "B"))
    arms: dict[str, Stage3ArmRuntime] = {}
    semantics: dict = {}
    discovery = DiscoveryCache()
    for unit_id in ("A", "B"):
        unit = scene.unit(unit_id)
        view = UnitSceneView(unit_id=unit_id, scene=scene, unit=unit)
        try:
            extra, other_info = _other_unit_obstacles(
                sim, scene, unit_id, discovery)
            arm, probed = build_arm_runtime(
                sim, view=view,
                shadow_aliases=dualcfg.SHADOW_ALIASES[unit_id],
                station_aliases=station_aliases,
                obstacles=extra, obstacle_info=other_info,
                obstacle_key="other_unit",
                ik=ik_by_unit[unit_id],
                probe_semantics=not semantics,
                error_cls=Stage3RuntimeBuildError,
                discovery=discovery,
                logger=logger)
        except Exception:
            # 清理已建臂, 避免影子对象/集合残留
            for built in arms.values():
                built.destroy()
            raise
        arms[unit_id] = arm
        if probed is not None:
            semantics = probed

    runtime = Stage3Runtime(
        sim=sim, scene=scene, arms=arms, semantics=semantics,
        startup={
            "units": [uid for uid in ("A", "B")],
            "shadow_aliases": dualcfg.SHADOW_ALIASES,
            "session_resource_policy": "not_persisted",
            "inter_arm_policy": "real_other_unit_in_workcell",
        },
        query_restore=discovery.query_restore,
        session_prefix=dualcfg.SESSION_RESOURCE_PREFIX,
        build_error=Stage3RuntimeBuildError)
    if logger:
        logger.info(
            "[STAGE3] 运行时构建完成: units=A/B, FK ok, cup_r=%.6f, "
            "inter-arm obstacles=%s",
            arms["A"].cup_radius,
            {uid: arms[uid].startup["other_unit"]["other_unit"]
             for uid in ("A", "B")})
    return runtime


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


def _unit_obstacles(sim, scene: Stage4Scene, unit_id: str,
                    discovery: DiscoveryCache) -> tuple[list[int], dict]:
    """其余 4 台真实臂的可见连杆、吸盘 Body 与岛体 (作为本臂障碍)."""
    handles: list[int] = []
    query: list[dict] = []
    others: list[dict] = []
    for other_id, other in scene.units.items():
        if other_id == unit_id:
            continue
        links, link_aliases, pad_body = discovery.ur5(
            sim, other.robot_root, other.suction_root)
        handles.extend([*links, pad_body, other.island])
        query.extend(discovery.ensure_queryable(sim, [*links, pad_body]))
        others.append({
            "unit_id": other_id,
            "link_aliases": link_aliases,
            "pad_body": int(pad_body),
            "island": int(other.island),
        })
    return handles, {"other_units": others, "query_properties": query}


def build_v1_runtime(sim, scene: Stage4Scene, ik_by_unit: dict,
                     logger=None, unit_ids=None) -> Stage4Runtime:
    """构建指定工位的影子臂/碰撞集合/规划器/锁存; 仿真必须处于停止态.

    ``unit_ids=None`` 构建全部 A–E; 订单运行可传入候选工位子集。
    """
    try:
        state = int(sim.getSimulationState())
    except Exception as exc:
        raise RuntimeBuildError(f"无法读取仿真状态: {exc}") from exc
    if state != int(sim.simulation_stopped):
        raise RuntimeBuildError(
            f"阶段四运行时必须在仿真停止态构建 (state={state})")

    unit_ids = normalize_unit_ids(unit_ids)
    station_aliases = tuple(
        unit.aliases["station"] for unit in scene.units.values()
        if unit.spec.has_station)
    arms: dict[str, Stage3ArmRuntime] = {}
    semantics: dict = {}
    discovery = DiscoveryCache()
    for unit_id in unit_ids:
        if logger:
            logger.info("[V1] 构建 %s 影子臂/碰撞集合 ...", unit_id)
        unit = scene.unit(unit_id)
        conveyor_aliases = _arm_conveyor_aliases(scene, unit_id)
        view = UnitSceneView(unit_id=unit_id, scene=scene, unit=unit)
        try:
            extra, other_info = _unit_obstacles(
                sim, scene, unit_id, discovery)
            arm, probed = build_arm_runtime(
                sim, view=view,
                shadow_aliases=cfg.SHADOW_ALIASES[unit_id],
                station_aliases=station_aliases,
                obstacles=extra, obstacle_info=other_info,
                obstacle_key="other_units",
                ik=ik_by_unit[unit_id],
                conveyor_aliases=conveyor_aliases,
                probe_semantics=not semantics,
                extra_startup={
                    "conveyor_aliases": list(conveyor_aliases),
                },
                error_cls=RuntimeBuildError,
                discovery=discovery,
                logger=logger)
        except Exception:
            for built in arms.values():
                built.destroy()
            raise
        arms[unit_id] = arm
        if probed is not None:
            semantics = probed
        if logger:
            logger.info("[V1] %s 运行时完成 (%d collections, "
                        "%d workcell)", unit_id,
                        len(arm.collisions.specs),
                        len(arm.collisions.geometry.workcell))

    runtime = Stage4Runtime(
        sim=sim, scene=scene, arms=arms, semantics=semantics,
        startup={
            "units": list(unit_ids),
            "shadow_aliases": {uid: cfg.SHADOW_ALIASES[uid]
                               for uid in unit_ids},
            "session_resource_policy": "not_persisted",
            "inter_arm_policy": "all_other_units_in_workcell",
            "adjacency_zones": cfg.ARM_ADJACENCY_ZONES,
        },
        query_restore=discovery.query_restore,
        session_prefix=cfg.SESSION_RESOURCE_PREFIX,
        build_error=RuntimeBuildError)
    if logger:
        logger.info(
            "[V1] 运行时构建完成: units=%s, cup_r=%.6f, "
            "adjacency=%s", list(unit_ids), arms[unit_ids[0]].cup_radius,
            cfg.ARM_ADJACENCY_ZONES)
    return runtime
