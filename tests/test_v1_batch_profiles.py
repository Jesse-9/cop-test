"""有限批次输入与命名配比 (WP1/WP2, 不需要 CoppeliaSim).

覆盖: 默认批次不变、profile 解析、缺省类型补 0、非法输入在仿真前拒绝、
批次输入版本与运行快照记录.
"""

from __future__ import annotations

import logging

import pytest

from flexible_factory import spec
from flexible_factory.application.batch import (
    BATCH_PROFILES, BatchError, DEFAULT_BATCH_PROFILE, SOURCE_ITEM_TYPES,
    batch_profile_names, expected_flow_counts, resolve_batch, validate_batch)
from flexible_factory.application.run import (run_scenario_v1,
                                               snapshot_config)
from flexible_factory.cli import build_parser, main
from flexible_factory.simulation import config as cfg
from flexible_factory.simulation.layout import default_layout


class _Scene:
    stage4_layout = default_layout()


# ═══════════════════════════════════════════════════════════════
# 默认与 profile 解析
# ═══════════════════════════════════════════════════════════════

def test_default_resolution_matches_scenario_batch():
    for scenario in cfg.STAGE4_SCENARIOS:
        assert resolve_batch(scenario) == cfg.SCENARIO_BATCH[scenario]
        assert (resolve_batch(scenario, profile=DEFAULT_BATCH_PROFILE)
                == cfg.SCENARIO_BATCH[scenario])


def test_named_profiles_cover_all_source_types():
    assert batch_profile_names()[0] == DEFAULT_BATCH_PROFILE
    for name in batch_profile_names()[1:]:
        resolved = validate_batch(BATCH_PROFILES[name])
        assert set(resolved) == set(SOURCE_ITEM_TYPES)
        assert all(type(value) is int and value >= 0
                   for value in resolved.values())


def test_profile_tables_are_immutable():
    with pytest.raises(TypeError):
        BATCH_PROFILES["other"] = {}  # type: ignore[index]
    with pytest.raises(TypeError):
        BATCH_PROFILES["direct_heavy"]["RAW_A"] = 0  # type: ignore[index]


def test_resolution_returns_independent_copy():
    first = resolve_batch(cfg.SCENARIO_FULL_FLOW)
    first["RAW_A"] = 99
    assert resolve_batch(cfg.SCENARIO_FULL_FLOW)["RAW_A"] == 1

    profile = resolve_batch(cfg.SCENARIO_FULL_FLOW, profile="direct_heavy")
    profile["RAW_A"] = 0
    assert resolve_batch(cfg.SCENARIO_FULL_FLOW,
                         profile="direct_heavy")["RAW_A"] == 2


def test_partial_batch_defaults_known_types_to_zero():
    assert validate_batch({"RAW_B": 3}) == {
        "RAW_A": 0, "RAW_B": 3, "RAW_D": 0}


def test_unknown_scenario_is_rejected():
    with pytest.raises(BatchError):
        resolve_batch("unknown_scenario")


def test_profile_requires_full_flow():
    with pytest.raises(BatchError):
        resolve_batch(cfg.SCENARIO_NO_PAIR, profile="direct_heavy")


def test_unknown_profile_is_rejected():
    with pytest.raises(BatchError):
        resolve_batch(cfg.SCENARIO_FULL_FLOW, profile="unknown_profile")


def test_batch_and_profile_are_mutually_exclusive():
    with pytest.raises(BatchError):
        resolve_batch(cfg.SCENARIO_FULL_FLOW, profile="direct_heavy",
                      batch={"RAW_A": 1})


def test_explicit_batch_with_default_profile_is_allowed():
    assert resolve_batch(cfg.SCENARIO_FULL_FLOW, profile="default",
                         batch={"RAW_D": 2}) == {
        "RAW_A": 0, "RAW_B": 0, "RAW_D": 2}


# ═══════════════════════════════════════════════════════════════
# 输入校验
# ═══════════════════════════════════════════════════════════════

@pytest.mark.parametrize("batch", [
    {"RAW_X": 1},
    {"RAW_A": -1},
    {"RAW_A": 1.0},
    {"RAW_A": "1"},
    {"RAW_A": True},
    {"RAW_A": False},
])
def test_invalid_batch_values_are_rejected(batch):
    with pytest.raises(BatchError):
        validate_batch(batch)


def test_non_mapping_batch_is_rejected():
    with pytest.raises(BatchError):
        validate_batch([("RAW_A", 1)])  # type: ignore[arg-type]


# ═══════════════════════════════════════════════════════════════
# 业务期望与证据字段
# ═══════════════════════════════════════════════════════════════

def test_expected_flow_counts_follow_one_to_one_join():
    assert expected_flow_counts({"RAW_A": 1, "RAW_B": 1, "RAW_D": 1}) == {
        "semi_ce": 2, "part_de": 1, "final": 1, "unpaired_semi_ce": 1}
    assert expected_flow_counts({"RAW_A": 2, "RAW_B": 0, "RAW_D": 2}) == {
        "semi_ce": 2, "part_de": 2, "final": 2, "unpaired_semi_ce": 0}
    assert expected_flow_counts({"RAW_A": 0, "RAW_B": 2, "RAW_D": 2}) == {
        "semi_ce": 2, "part_de": 2, "final": 2, "unpaired_semi_ce": 0}
    assert expected_flow_counts({"RAW_A": 2, "RAW_B": 1, "RAW_D": 2}) == {
        "semi_ce": 3, "part_de": 2, "final": 2, "unpaired_semi_ce": 1}


def test_snapshot_records_effective_batch():
    batch = resolve_batch(cfg.SCENARIO_FULL_FLOW, profile="direct_heavy")
    config = snapshot_config(
        cfg.SCENARIO_FULL_FLOW, max_sim_time=1200.0, scene=_Scene(),
        batch=batch, batch_profile="direct_heavy")
    assert config["batch"] == batch
    assert config["batch_profile"] == "direct_heavy"
    assert config["batch_input_version"] == spec.BATCH_INPUT_VERSION


def test_batch_input_version_is_declared():
    assert spec.BATCH_INPUT_VERSION.startswith("v1_batch_")


def test_run_scenario_rejects_mismatched_profile_label():
    with pytest.raises(BatchError):
        run_scenario_v1(
            None, None, None, scenario=cfg.SCENARIO_FULL_FLOW,
            max_sim_time=1.0, logger=logging.getLogger("test"),
            batch={"RAW_A": 1, "RAW_B": 1, "RAW_D": 1},
            batch_profile="direct_heavy")


# ═══════════════════════════════════════════════════════════════
# CLI 边界
# ═══════════════════════════════════════════════════════════════

def test_run_parser_accepts_batch_profile():
    ns = build_parser().parse_args(["run", "--batch-profile", "direct_heavy"])
    assert ns.batch_profile == "direct_heavy"


def test_run_parser_rejects_unknown_batch_profile():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["run", "--batch-profile", "unknown"])


def test_cli_rejects_profile_on_non_full_flow_before_connecting(capsys):
    assert main(["run", "--scenario", "no_pair",
                 "--batch-profile", "direct_heavy"]) == 2
    assert "BATCH_INPUT" in capsys.readouterr().err
