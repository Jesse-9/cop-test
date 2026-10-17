"""tests/test_scene_contract.py — 场景契约测试 (阶段一 §2.2 步骤4 / §2.3)

验证 DeviceHandleMap 与别名规则:
  - 全部别名扁平下划线, 禁止 '/' (CLAUDE.md §1, 方案 §3)
  - 句柄表不依赖对象创建顺序 (§3.2 步骤5)
  - 唯一性检查: 重复 alias / 重复 handle 必须被拒绝 (§3 唯一性检查)
  - 未登记别名查询有明确错误 (§2.2: 模块输入输出与异常类型)

纯逻辑, 不需要 CoppeliaSim。
"""

import pytest

from device_handle_map import (
    DeviceHandleMap,
    DuplicateAliasError,
    DuplicateHandleError,
    UnknownAliasError,
    InvalidAliasError,
)


# ═══════════════════════════════════════════════════════════════
# 别名规则 (§3: 扁平下划线, 禁止 '/' 路径)
# ═══════════════════════════════════════════════════════════════

def test_alias_must_not_contain_slash():
    m = DeviceHandleMap()
    with pytest.raises(InvalidAliasError):
        m.register("/Island/A", 10)


def test_alias_must_not_be_empty():
    m = DeviceHandleMap()
    with pytest.raises(InvalidAliasError):
        m.register("", 10)


def test_flat_underscore_alias_accepted():
    m = DeviceHandleMap()
    m.register("Island_A_Assembly", 10)
    assert m.get("Island_A_Assembly") == 10


# ═══════════════════════════════════════════════════════════════
# 唯一性 (§3: 启动时唯一性检查)
# ═══════════════════════════════════════════════════════════════

def test_duplicate_alias_rejected():
    m = DeviceHandleMap()
    m.register("Sensor_PickStation", 1)
    with pytest.raises(DuplicateAliasError):
        m.register("Sensor_PickStation", 2)


def test_duplicate_handle_rejected():
    m = DeviceHandleMap()
    m.register("Robot_UR5_01", 5)
    with pytest.raises(DuplicateHandleError):
        m.register("Robot_UR5_02", 5)


def test_unknown_alias_raises_with_alias_in_message():
    m = DeviceHandleMap()
    m.register("A", 1)
    with pytest.raises(UnknownAliasError) as ei:
        m.require("NoSuch")
    assert "NoSuch" in str(ei.value)


# ═══════════════════════════════════════════════════════════════
# 不依赖创建顺序 (§3.2 步骤5)
# ═══════════════════════════════════════════════════════════════

def test_lookup_independent_of_registration_order():
    m1 = DeviceHandleMap()
    m1.register("B_Second", 22)
    m1.register("A_First", 11)

    m2 = DeviceHandleMap()
    m2.register("A_First", 11)
    m2.register("B_Second", 22)

    assert m1.get("A_First") == m2.get("A_First") == 11
    assert m1.get("B_Second") == m2.get("B_Second") == 22


def test_bulk_uniqueness_report():
    """批量注册后能一次性取出全部别名 (供启动期唯一性报告)"""
    m = DeviceHandleMap()
    m.register("X", 1)
    m.register("Y", 2)
    assert sorted(m.aliases()) == ["X", "Y"]

    # 冲突检测: 报告重复项
    conflicts = m.detect_duplicates({"X": 9, "Z": 3})
    assert conflicts == ["X"]


def test_kind_tagging_for_diagnostics():
    """按设备类别登记 (conveyor / robot / sensor / agv), 便于 §2.3 输出清单"""
    m = DeviceHandleMap()
    m.register("Conveyor_S1_to_A", 30, kind="conveyor")
    m.register("Robot_UR5_01", 31, kind="robot")
    assert m.kinds()["conveyor"] == ["Conveyor_S1_to_A"]
    assert m.kinds()["robot"] == ["Robot_UR5_01"]


def test_require_all_reports_missing():
    """启动期批量校验: 缺哪个报哪个 (不抛第一个就停)"""
    m = DeviceHandleMap()
    m.register("A", 1)
    missing = m.missing(["A", "B", "C"])
    assert missing == ["B", "C"]