"""订单运行器工位子集辅助函数测试 (纯逻辑, 不连接 CoppeliaSim)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from flexible_factory.application.order_runner import (
    OrderRunnerConfig, build_order_path_reuse, required_arm_units)


def _command(mode: str, artifact: str = "v1/L5_UFLOW/test-artifact"):
    return SimpleNamespace(path_reuse_mode=mode, artifact_id=artifact)


def _compiled(*rows):
    return SimpleNamespace(instances=list(rows))


def _instance(candidates=(), selected=""):
    return SimpleNamespace(candidate_device_ids=list(candidates),
                           selected_device_id=selected)


def test_required_arm_units_uses_candidate_union():
    compiled = _compiled(
        _instance(["arm_C", "arm_A"]),
        _instance(["arm_C", "arm_D"]),
        _instance(["agv"]),
    )
    assert required_arm_units(compiled) == ("A", "C", "D")


def test_required_arm_units_includes_selected_device():
    compiled = _compiled(_instance([], selected="arm_B"))
    assert required_arm_units(compiled) == ("B",)


def test_required_arm_units_empty_for_logic_only_order():
    compiled = _compiled(_instance(["agv"]))
    assert required_arm_units(compiled) == ()


def test_required_arm_units_stable_unit_order():
    compiled = _compiled(_instance(["arm_E", "arm_B", "arm_D"]))
    assert required_arm_units(compiled) == ("B", "D", "E")


def test_build_order_path_reuse_off_returns_none(tmp_path):
    config = OrderRunnerConfig(path_library_root=tmp_path)
    assert build_order_path_reuse(config, _command("off")) is None


def test_build_order_path_reuse_builds_scoped_policy(tmp_path):
    config = OrderRunnerConfig(path_library_root=tmp_path)
    policy = build_order_path_reuse(config, _command("CACHE_THEN_PLAN"))
    assert policy is not None
    assert policy.mode == "CACHE_THEN_PLAN"
    assert policy.scene_artifact_id == "v1/L5_UFLOW/test-artifact"
    assert policy.enabled_phases == ("PLACING",)


def test_build_order_path_reuse_rejects_unknown_mode(tmp_path):
    config = OrderRunnerConfig(path_library_root=tmp_path)
    with pytest.raises(ValueError, match="未知路径复用模式"):
        build_order_path_reuse(config, _command("BOGUS"))


def test_build_order_path_reuse_cache_only_requires_records(tmp_path):
    config = OrderRunnerConfig(path_library_root=tmp_path)
    with pytest.raises(ValueError, match="CACHE_ONLY"):
        build_order_path_reuse(config, _command("CACHE_ONLY"))