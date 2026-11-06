"""batch.py — 运行边界的有限批次输入与命名配比 profile.

批次只改变一次 ``full_flow`` 运行中各原料的数量, 不改变 scenario 名称、
默认批次、工艺路线、设备候选、物料提交或资源互锁. 它只存在于本次运行的
内存中, 不写入场景工件/manifest, 也不参与冻结的 ``config_digest``.

``batch`` 与 ``profile`` 的区别:
  - ``batch``: 应用层显式映射, 供测试或嵌入调用注入; 缺省的已知原料类型
    按 0 处理;
  - ``profile``: CLI 可选的命名配比, 只是现有 ``RAW_A/RAW_B/RAW_D`` 数量
    的有限集合; 除 ``default`` 外只允许用于用户入口 ``full_flow``.

两条壳体路线按入口静态绑定 (``RAW_A`` 直达 / ``RAW_B`` AGV 绕行), 因此
调整数量只展示批次组合灵活性, 不表示同一 UID 可在运行中动态选路.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping, Optional

from ..domain.materials import ItemType
from ..simulation import config as cfg

DEFAULT_BATCH_PROFILE = "default"

# 合法批次键来自现有来源物料类型; 不从 scenario 名称推断.
SOURCE_ITEM_TYPES: tuple[str, ...] = tuple(sorted(
    {str(item["item_type"]) for item in cfg.SOURCE_ITEMS.values()}))


class BatchError(ValueError):
    """批次输入非法; 必须在任何仿真动作前拒绝."""

    code = "BATCH_INPUT"


def _freeze(batch: Mapping[str, int]) -> Mapping[str, int]:
    return MappingProxyType(dict(batch))


# 命名配比. 每个 profile 显式列出全部原料数量, 避免依赖 scenario 默认值的
# 隐式差异. direct_heavy 用 2 个壳体 + 2 个支架产生 2 个 FINAL;
# detour_heavy 用 1 个壳体 + 1 个支架把绕行占比提高到 100%, 同时不把 AGV
# 空载调机等待推到 300 s 背压上限 (0/2/2 的失败尝试见实施报告).
BATCH_PROFILES: Mapping[str, Mapping[str, int]] = MappingProxyType({
    "direct_heavy": _freeze({"RAW_A": 2, "RAW_B": 0, "RAW_D": 2}),
    "detour_heavy": _freeze({"RAW_A": 0, "RAW_B": 1, "RAW_D": 1}),
})


def batch_profile_names() -> tuple[str, ...]:
    """CLI 可选值: 默认配比在前, 其余按声明顺序."""
    return (DEFAULT_BATCH_PROFILE, *BATCH_PROFILES)


def validate_batch(batch: Mapping[str, int]) -> dict[str, int]:
    """校验并归一化批次映射; 未知键、负数、非整数和布尔值都拒绝.

    返回值总是包含全部已知原料类型, 缺省类型补 0, 且键顺序稳定.
    """
    if not isinstance(batch, Mapping):
        raise BatchError(
            f"batch 必须是映射, 实际 {type(batch).__name__}: {batch!r}")
    unknown = [key for key in batch if key not in SOURCE_ITEM_TYPES]
    if unknown:
        raise BatchError(
            f"未知原料类型: {unknown!r}; 允许: {list(SOURCE_ITEM_TYPES)}")
    normalized: dict[str, int] = {key: 0 for key in SOURCE_ITEM_TYPES}
    for key in SOURCE_ITEM_TYPES:
        if key not in batch:
            continue
        value = batch[key]
        # bool 是 int 的子类, 必须显式拒绝, 避免 True/False 被当成 1/0.
        if isinstance(value, bool) or not isinstance(value, int):
            raise BatchError(
                f"{key} 的数量必须是整数, 实际 "
                f"{type(value).__name__}: {value!r}")
        if value < 0:
            raise BatchError(f"{key} 的数量不能为负: {value}")
        normalized[key] = int(value)
    return normalized


def resolve_batch(scenario: str, *, profile: str = "",
                  batch: Optional[Mapping[str, int]] = None) -> dict[str, int]:
    """解析本次运行实际生效的批次.

    ``batch`` 缺省且 ``profile`` 为 ``default`` 时严格使用
    ``SCENARIO_BATCH[scenario]``; 非默认 profile 只用于 ``full_flow``.
    """
    if scenario not in cfg.STAGE4_SCENARIOS:
        raise BatchError(f"未知 scenario: {scenario!r}")
    profile_name = str(profile or DEFAULT_BATCH_PROFILE)
    if batch is not None:
        if profile_name != DEFAULT_BATCH_PROFILE:
            raise BatchError(
                f"batch 与 batch profile 不能同时指定: {profile_name!r}")
        return validate_batch(batch)
    if profile_name == DEFAULT_BATCH_PROFILE:
        return validate_batch(cfg.SCENARIO_BATCH[scenario])
    if profile_name not in BATCH_PROFILES:
        raise BatchError(
            f"未知 batch profile: {profile_name!r}; "
            f"允许: {list(batch_profile_names())}")
    if scenario != cfg.SCENARIO_FULL_FLOW:
        raise BatchError(
            f"batch profile {profile_name!r} 只用于 "
            f"{cfg.SCENARIO_FULL_FLOW!r}, 当前 scenario={scenario!r}")
    return validate_batch(BATCH_PROFILES[profile_name])


def expected_flow_counts(batch: Mapping[str, int]) -> dict[str, int]:
    """由有限批次推导物料数量; E 岛按 1:1 消耗 SEMI_CE 与 PART_DE.

    仅用于验收期望值和报告, 不驱动派工或物料提交.
    """
    housing = (int(batch.get(ItemType.RAW_A.value, 0))
               + int(batch.get(ItemType.RAW_B.value, 0)))
    parts = int(batch.get(ItemType.RAW_D.value, 0))
    return {
        "semi_ce": housing,
        "part_de": parts,
        "final": min(housing, parts),
        "unpaired_semi_ce": max(0, housing - parts),
    }
