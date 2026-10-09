"""build_scene 机械臂构建范围选择的纯单元测试 (§9.7 第 5 条)。

阶段一带动验证只启用 A 岛机械臂, 确认后再逐台启用 B/C/D/E_C/E_D。
"""

import build_scene as bs
import factory_config as cfg


ROBOTS = {
    "A": {"alias": "Island_A_Assembly_Robot_A"},
    "B": {"alias": "Island_B_Preprocess_Robot_B"},
    "E_C": {"alias": "Island_E_FinalAssembly_Robot_E_C"},
    "E_D": {"alias": "Island_E_FinalAssembly_Robot_E_D"},
}


def test_arm_keys_defaults_to_every_configured_arm():
    assert bs.arm_keys_to_build(None, ROBOTS) == list(ROBOTS)


def test_arm_keys_restricts_to_requested_islands():
    assert bs.arm_keys_to_build(["A"], ROBOTS) == ["A"]


def test_arm_keys_ignores_undefined_islands():
    assert bs.arm_keys_to_build(["A", "Z"], ROBOTS) == ["A"]


def test_arm_keys_returns_config_order_and_dedupes():
    assert bs.arm_keys_to_build(["E_D", "A", "E_D"], ROBOTS) == ["A", "E_D"]


def test_arm_keys_of_real_config_covers_all_six_arms():
    assert bs.arm_keys_to_build(None) == list(cfg.ISLAND_ROBOTS)
    assert bs.arm_keys_to_build(["A"]) == ["A"]
