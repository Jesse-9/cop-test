"""runtime.py — 阶段二会话资源装配 (影子臂/集合/探针/规划/联锁).

生命周期:
  - ``build_stage2_runtime`` 在场景**加载并校验之后、仿真启动之前**调用;
  - 影子臂/集合/载荷副本都是会话资源, 不写入 stage2 工件;
  - ``destroy`` 必须删除全部 ``Shadow_*`` 对象并销毁集合; runner 在停止时
    无条件调用, 保存工件前若仍有残留, stage2 契约校验会拒绝.

本模块不创建第二个 step 循环, 只把已有组件组合给 ``Stage2UnitController``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from stage1.scene import alias_of

from . import config as cfg
from .collisions import CollisionRuntime
from .path_planning import ConfigurationChecker, LocalPlanner
from .safety import Stage2Safety
from .shadow import ShadowProbe, build_shadow_arm


class RuntimeBuildError(RuntimeError):
    """阶段二运行时资源构建失败"""


@dataclass
class Stage2Runtime:
    sim: object
    scene: object
    shadow: object
    probe: ShadowProbe
    collisions: CollisionRuntime
    checker: ConfigurationChecker
    planner: LocalPlanner
    safety: Stage2Safety
    cup_radius: float = cfg.CUP_RADIUS_DESIGN
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
        # 非目标物料作为障碍; 目标在携带时完全退出场景障碍角色
        desired = current - ({target} if target is not None else set())
        for handle in sorted(desired - self.part_handles):
            self.collisions.add_part(handle)
        self.part_handles = desired

    def set_attach_transform(self, transform: Optional[list[float]]) -> None:
        self.probe.set_load(transform)

    def forget_material(self, handle: int) -> None:
        """任务间移除物料前清理集合引用, 避免下次更新引用已删除对象."""
        self.collisions.remove_part(int(handle))
        self.part_handles.discard(int(handle))

    # ── 运行期障碍 (验收用) ─────────────────────────────────

    def add_hazard(self, handle: int) -> None:
        self.collisions.add_hazard(int(handle))
        self.hazard_handles.append(int(handle))

    def remove_hazards(self) -> None:
        for handle in list(self.hazard_handles):
            self.collisions.remove_hazard(handle)
            try:
                self.sim.removeObjects([handle])
            except Exception:
                pass
        self.hazard_handles.clear()

    # ── 清理 ────────────────────────────────────────────────

    def destroy(self) -> dict:
        report = {}
        try:
            report["hazards_removed"] = len(self.hazard_handles)
            self.remove_hazards()
        finally:
            self.collisions.destroy()
            removed = self.probe.destroy()
            report["shadow_removed"] = len(removed)
            report["leftover"] = [alias_of(self.sim, h)
                                  for h in _shadow_aliases(self.sim)]
        return report

    def as_dict(self) -> dict:
        return {
            "controller_id": cfg.STAGE2_CONTROLLER_ID,
            "contract_version": cfg.STAGE2_CONTRACT_VERSION,
            "cup_radius": round(self.cup_radius, 6),
            "shadow": self.shadow.as_dict(),
            "collections": self.collisions.spec_dict(),
            "startup": self.startup,
        }


def _shadow_aliases(sim) -> list[int]:
    from stage1.scene import all_objects
    return [int(h) for h in all_objects(sim)
            if (alias_of(sim, h) or "").startswith(
                cfg.SESSION_RESOURCE_PREFIX)]


def build_stage2_runtime(sim, scene, ik, logger=None,
                         budget=None) -> Stage2Runtime:
    """构建影子臂、碰撞集合、检查器与规划器; 仿真必须处于停止态."""
    try:
        state = int(sim.getSimulationState())
    except Exception as exc:
        raise RuntimeBuildError(f"无法读取仿真状态: {exc}") from exc
    if state != int(sim.simulation_stopped):
        raise RuntimeBuildError(
            f"阶段二运行时必须在仿真停止态构建 (state={state})")

    shadow = build_shadow_arm(sim, scene)
    probe = ShadowProbe(sim, shadow)
    actual_q = [float(sim.getJointPosition(j)) for j in scene.robot_joints]
    probe.set_configuration(actual_q)
    fk = probe.verify_fk(scene)
    if not fk["ok"]:
        probe.destroy()
        raise RuntimeBuildError(f"真实/影子 FK 不一致: {fk}")

    cup_radius = _measure_cup_radius(sim, shadow.pad_body)
    if abs(cup_radius - cfg.CUP_RADIUS_DESIGN) > cfg.CUP_RADIUS_TOL:
        probe.destroy()
        raise RuntimeBuildError(
            f"杯口半径实测 {cup_radius:.6f} 与设计 "
            f"{cfg.CUP_RADIUS_DESIGN:.6f} 不一致")

    collisions = CollisionRuntime.build(
        sim, scene, link_handles=shadow.links,
        link_aliases=shadow.link_aliases, pad_body=shadow.pad_body,
        load_handle=shadow.load)
    semantics = collisions.query_semantics_probe()
    if not semantics.get("ok"):
        collisions.destroy()
        probe.destroy()
        raise RuntimeBuildError(f"碰撞查询语义自检失败: {semantics}")

    checker = ConfigurationChecker(sim, probe, collisions,
                                   cup_radius=cup_radius)
    planner = LocalPlanner(checker, ik, budget=budget)
    safety = Stage2Safety(logger=logger)
    startup = {
        "fk": fk,
        "cup_radius_measured": round(cup_radius, 6),
        "cup_radius_design": cfg.CUP_RADIUS_DESIGN,
        "query_semantics": semantics,
        "self_pairs": len(collisions._self_pairs),
        "collections": {name: len(spec.items)
                        for name, spec in zip(collisions.collections,
                                              collisions.specs)},
        "excluded": collisions.geometry.excluded,
        "workcell_aliases": collisions.geometry.workcell_aliases,
        "shadow_hidden": shadow.visibility_applied,
    }
    if logger:
        logger.info(
            "[STAGE2] 运行时构建完成: FK ok, cup_r=%.6f, self_pairs=%s, "
            "workcell=%s, excluded=%s",
            cup_radius, len(collisions._self_pairs),
            len(collisions.geometry.workcell),
            [e.get("alias") for e in collisions.geometry.excluded])
    return Stage2Runtime(sim=sim, scene=scene, shadow=shadow, probe=probe,
                         collisions=collisions, checker=checker,
                         planner=planner, safety=safety,
                         cup_radius=cup_radius, startup=startup)


def _measure_cup_radius(sim, pad_body: int) -> float:
    value = sim.getShapeBB(pad_body)
    size = (value[0] if isinstance(value, (tuple, list)) and len(value) == 2
            else value)
    return min(float(size[0]), float(size[1])) / 2.0
