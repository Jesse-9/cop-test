"""已提交路径库记录: 版本、自洽性和五臂覆盖 (不需要 CoppeliaSim)."""

from __future__ import annotations

import json

import pytest

from flexible_factory import spec
from flexible_factory.simulation import config as cfg
from flexible_factory.simulation.motion.path_library import (
    PATH_RECORD_VERSION, PathLibrary, PathRecord)


def _records():
    root = spec.PATH_LIBRARY_ROOT
    if not root.is_dir():
        return []
    records = []
    for path in sorted(root.rglob("*.json")):
        records.append((path, PathRecord.from_dict(
            json.loads(path.read_text(encoding="utf-8")))))
    return records


def test_committed_path_library_covers_all_arms():
    records = _records()
    if not records:
        pytest.skip("尚未生成 docs/path_library 记录")
    library = PathLibrary(spec.PATH_LIBRARY_ROOT)
    units = set()
    for path, record in records:
        assert record.format_version == PATH_RECORD_VERSION, path
        assert record.key.phase == "PLACING:place", path
        assert len(record.waypoints) >= 2, path
        assert sum(int(row["waypoint_count"])
                   for row in record.segments) == len(record.waypoints) - 1
        assert library.get_exact(record.key) is not None, path
        units.add(record.key.unit)
    assert units == set(cfg.UNIT_IDS)