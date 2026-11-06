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

单臂构建、共享运行时容器和查询属性辅助函数位于
:mod:`flexible_factory.simulation.runtime_common`; 本模块只保留双站层
的障碍策略和公开入口.
"""

from __future__ import annotations

from .motion.collisions import discover_pad_body, discover_ur5_links
from .runtime_common import (Stage3ArmRuntime, Stage3Runtime, UnitSceneView,
                             _ensure_queryable, build_arm_runtime)

from . import config_dual as cfg
from .scene_dual import Stage3Scene

__all__ = [
    "RuntimeBuildError",
    "Stage3ArmRuntime",
    "Stage3Runtime",
    "UnitSceneView",
    "build_stage3_runtime",
]


class RuntimeBuildError(RuntimeError):
    code = "STAGE3_RUNTIME"


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
        try:
            extra, other_info = _other_unit_obstacles(sim, scene, unit_id)
            query_restore.extend(other_info.get("query_properties") or [])
            arm, probed = build_arm_runtime(
                sim, view=view,
                shadow_aliases=cfg.SHADOW_ALIASES[unit_id],
                station_aliases=station_aliases,
                obstacles=extra, obstacle_info=other_info,
                obstacle_key="other_unit",
                ik=ik_by_unit[unit_id],
                probe_semantics=not semantics,
                error_cls=RuntimeBuildError,
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
            "shadow_aliases": cfg.SHADOW_ALIASES,
            "session_resource_policy": "not_persisted",
            "inter_arm_policy": "real_other_unit_in_workcell",
        },
        query_restore=query_restore,
        session_prefix=cfg.SESSION_RESOURCE_PREFIX,
        build_error=RuntimeBuildError)
    if logger:
        logger.info(
            "[STAGE3] 运行时构建完成: units=A/B, FK ok, cup_r=%.6f, "
            "inter-arm obstacles=%s",
            arms["A"].cup_radius,
            {uid: arms[uid].startup["other_unit"]["other_unit"]
             for uid in ("A", "B")})
    return runtime
