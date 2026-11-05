"""persistence.clock — 持久化时间戳.

数据库时间戳统一保存为 UTC ISO-8601 文本 (``2026-11-05T04:00:00+00:00``),
不依赖进程本地时区; 仿真时间不属于本模块.
"""

from __future__ import annotations

from datetime import datetime, timezone


def utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_utc_iso(value: str) -> datetime:
    """解析数据库时间戳; 无时区信息视为非法."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"时间戳缺少时区: {value!r}")
    return parsed.astimezone(timezone.utc)
