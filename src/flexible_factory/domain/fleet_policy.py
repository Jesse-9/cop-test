"""fleet_policy.py — 纯车队编制契约 (single/dual、固定分工与已验证组合).

本模块只依赖 ``spec``，不导入 CoppeliaSim、执行器、资源表或应用层，供
planner、订单服务与 coordinator 共用同一份车队策略:

  - :class:`FleetConfig`：`single` / `dual` 两种编制；
  - dual 下按工序 kind 固定分工（壳体回程 → `agv_1`，支架运输 → `cart_2`）；
  - :data:`DUAL_VERIFIED_PAIRS`：已验收的非基线 (设备, 模板) 组合；
  - :func:`normalize_fleet_mode`：请求值规范化与启动前校验。

固定分工是**运行/规划假设**，不写入订单版本或 DAG digest。
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import spec

FLEET_SINGLE = "single"
FLEET_DUAL = "dual"
FLEET_MODES = (FLEET_SINGLE, FLEET_DUAL)
DEFAULT_FLEET_MODE = FLEET_SINGLE

#: 固定分工策略版本; 唯一来源在 ``spec.py``.
FLEET_POLICY_VERSION = spec.FLEET_POLICY_VERSION

#: 固定分工：工序类型 → 车辆。未列出的类型走 `default_vehicle`。
_DUAL_ASSIGNMENT = {
    "b_conveyor": "agv_1",          # B 装载（AGV 空载到位）
    "a_agv": "agv_1",               # A 从 AGV 卸货
    "transport_ba": "agv_1",        # 壳体回程 B→A
    "d_conveyor": "cart_2",         # D 装载
    "e_agv": "cart_2",              # E 从车卸货
    "transport_de": "cart_2",       # 支架运输 D→E
}

#: 双车固定分工下**已验收**的非基线 (device_id, template_id) 组合.
#: 调度策略只允许基线设备或此集合内的组合被选中; `cart_2` 承运
#: `transport_DE` 由双车合并提交 `bb54cf5` 的仿真验收覆盖。新增组合必须
#: 先完成 focused 仿真实机验收再登记。
DUAL_VERIFIED_PAIRS = frozenset({
    ("cart_2", "transport_DE"),
})


def normalize_fleet_mode(value: object) -> str:
    """把请求值规范化为合法车队模式; 空值回退 ``single``.

    未知值抛 :class:`ValueError`; 调用方必须在创建 run 记录/启动 worker
    或生成 planner 候选之前完成校验.
    """
    text = str(value or "").strip().lower()
    if not text:
        return DEFAULT_FLEET_MODE
    if text not in FLEET_MODES:
        raise ValueError(
            f"未知车队模式 {value!r}; 允许值: {list(FLEET_MODES)}")
    return text


@dataclass(frozen=True)
class FleetConfig:
    """车队编制。`single` 保持 v1 现状（所有运输走 agv_1）。"""

    mode: str = FLEET_SINGLE
    default_vehicle: str = "agv_1"

    def __post_init__(self) -> None:
        if self.mode not in FLEET_MODES:
            raise ValueError(f"未知车队模式: {self.mode!r}")

    @property
    def dual(self) -> bool:
        return self.mode == FLEET_DUAL

    @property
    def policy_version(self) -> str:
        return FLEET_POLICY_VERSION

    def vehicle_for(self, kind: str) -> str:
        if not self.dual:
            return self.default_vehicle
        return _DUAL_ASSIGNMENT.get(kind, self.default_vehicle)

    def vehicles(self) -> tuple[str, ...]:
        """本编制实际使用的车辆集合; single 只含默认车辆."""
        if not self.dual:
            return (self.default_vehicle,)
        values = [self.default_vehicle]
        for vehicle in _DUAL_ASSIGNMENT.values():
            if vehicle not in values:
                values.append(vehicle)
        return tuple(values)

    def verified_pairs(self) -> frozenset[tuple[str, str]]:
        """本编制允许的非基线 (设备, 模板) 组合; single 下为空集."""
        if not self.dual:
            return frozenset()
        return DUAL_VERIFIED_PAIRS

    def as_dict(self) -> dict:
        return {"mode": self.mode,
                "policy_version": FLEET_POLICY_VERSION,
                "default_vehicle": self.default_vehicle,
                "vehicles": list(self.vehicles()),
                "assignment": ({k: self.vehicle_for(k) for k in _DUAL_ASSIGNMENT}
                               if self.dual else {}),
                "verified_pairs": [list(pair) for pair
                                   in sorted(self.verified_pairs())]}


__all__ = [
    "DEFAULT_FLEET_MODE",
    "DUAL_VERIFIED_PAIRS",
    "FLEET_DUAL",
    "FLEET_MODES",
    "FLEET_POLICY_VERSION",
    "FLEET_SINGLE",
    "FleetConfig",
    "normalize_fleet_mode",
]