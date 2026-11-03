"""util.py — 与仿真无关的稳定序列化/摘要/文件工具."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def utc_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return jsonable(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(jsonable(item) for item in value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def stable_digest(value: Any) -> str:
    payload = json.dumps(jsonable(value), ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def read_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def write_json_atomic(path: Path, data: Any) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(jsonable(data), handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    return path


def append_jsonl(path: Path, event: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(jsonable(event), ensure_ascii=False,
                                sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def set_readonly(path: Path, readonly: bool = True) -> None:
    try:
        mode = path.stat().st_mode
        if readonly:
            path.chmod(mode & ~stat.S_IWRITE)
        else:
            path.chmod(mode | stat.S_IWRITE)
    except OSError:
        pass


def check(checks: list[dict], name: str, ok: bool,
          detail: str = "") -> None:
    checks.append({"name": name, "ok": bool(ok), "detail": detail})


def checks_ok(checks: Iterable[dict]) -> bool:
    return all(bool(item.get("ok")) for item in checks)


def diff_config(saved: dict, current: dict) -> list[dict]:
    diffs = []
    for key in sorted(set(saved) | set(current)):
        if saved.get(key) != current.get(key):
            diffs.append({"key": key, "saved": saved.get(key),
                          "current": current.get(key)})
    return diffs


def pick_upper(module, *, exclude: Optional[Iterable[str]] = None) -> dict:
    exclude = set(exclude or ())
    out: dict[str, Any] = {}
    for name, value in vars(module).items():
        if name.startswith("_") or name in exclude:
            continue
        if not name.isupper():
            continue
        if callable(value):
            continue
        out[name] = jsonable(value)
    return out
