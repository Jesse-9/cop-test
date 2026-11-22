"""运行级路径复用选项: CLI 解析、策略构建与路径库预检 (不需要 CoppeliaSim)."""

from __future__ import annotations

import pytest

from flexible_factory import spec
from flexible_factory.application.run import (build_path_reuse_policy,
                                              path_reuse_info)
from flexible_factory.cli import build_parser
from flexible_factory.simulation.motion.path_library import PathLibrary

SCENE = "v1/L5_UFLOW/v001_test"


def test_off_mode_builds_no_policy(tmp_path):
    assert build_path_reuse_policy("off", tmp_path, SCENE) is None


def test_invalid_mode_or_missing_artifact_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        build_path_reuse_policy("UNKNOWN", tmp_path, SCENE)
    with pytest.raises(ValueError):
        build_path_reuse_policy("PLAN_ONLY", tmp_path, "")


def test_path_library_root_inside_scene_artifacts_is_rejected(tmp_path):
    bad = spec.ARTIFACT_ROOT / "v1" / "L5_UFLOW" / "v999_test"
    with pytest.raises(ValueError):
        build_path_reuse_policy("PLAN_ONLY", bad, SCENE)


def test_plan_only_enables_export_for_all_units(tmp_path):
    policy = build_path_reuse_policy("PLAN_ONLY", tmp_path, SCENE)
    assert policy is not None
    assert policy.mode == "PLAN_ONLY"
    assert policy.export is True
    assert policy.enabled_units is None
    assert policy.enabled_phases == ("PLACING",)
    info = path_reuse_info(policy, tmp_path)
    assert info["scene"] == SCENE
    assert info["record_count"] == 0
    assert info["enabled_units"] is None


def test_cache_only_requires_existing_scene_records(tmp_path):
    with pytest.raises(ValueError):
        build_path_reuse_policy("CACHE_ONLY", tmp_path, SCENE)

    record_dir = PathLibrary(tmp_path).scene_dir(SCENE) / "PLACING_place"
    record_dir.mkdir(parents=True)
    (record_dir / "placeholder.json").write_text("{}", encoding="utf-8")
    policy = build_path_reuse_policy("CACHE_ONLY", tmp_path, SCENE)
    assert policy is not None
    assert policy.export is False
    assert PathLibrary(tmp_path).count_records(SCENE) == 1


def test_cache_then_plan_allows_empty_library(tmp_path):
    policy = build_path_reuse_policy("CACHE_THEN_PLAN", tmp_path, SCENE)
    assert policy is not None
    assert policy.mode == "CACHE_THEN_PLAN"
    assert policy.export is False


def test_cli_parses_path_reuse_options(tmp_path):
    ns = build_parser().parse_args([
        "run", "--path-reuse", "CACHE_THEN_PLAN",
        "--path-library-root", str(tmp_path)])
    assert ns.path_reuse == "CACHE_THEN_PLAN"
    assert ns.path_library_root == str(tmp_path)


def test_cli_default_path_reuse_is_off():
    ns = build_parser().parse_args(["run"])
    assert ns.path_reuse == "off"
    assert ns.path_library_root == str(spec.PATH_LIBRARY_ROOT)


def test_cli_rejects_unknown_path_reuse_mode():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["run", "--path-reuse", "unknown"])