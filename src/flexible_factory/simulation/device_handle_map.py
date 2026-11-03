"""device_handle_map.py — 启动期集中句柄表 (§2.2 步骤4 / §2.3).

规则:
  - 别名必须是扁平下划线形式, 禁止 '/' (CLAUDE.md §3)
  - 每个别名唯一, 每个句柄只属于一个别名
  - 运行循环只读查询, 不重复枚举场景 (§3)
  - 句柄表不依赖对象创建顺序
"""

from __future__ import annotations

from typing import Optional


class HandleMapError(Exception):
    """句柄表异常基类"""


class InvalidAliasError(HandleMapError, ValueError):
    """别名不合法 (空串或含 '/')"""


class DuplicateAliasError(HandleMapError, ValueError):
    """别名已登记"""


class DuplicateHandleError(HandleMapError, ValueError):
    """句柄已属于另一个别名"""


class UnknownAliasError(HandleMapError, KeyError):
    """查询未登记的别名"""


class DeviceHandleMap:
    def __init__(self) -> None:
        self._by_alias: dict[str, int] = {}
        self._kind: dict[str, str] = {}
        self._owner: dict[int, str] = {}

    def register(self, alias: str, handle: int, kind: str = "device") -> None:
        if not alias or "/" in alias:
            raise InvalidAliasError(
                f"别名必须是非空扁平下划线形式 (禁止 '/'): {alias!r}")
        if alias in self._by_alias:
            raise DuplicateAliasError(f"别名重复: {alias!r}")
        if handle in self._owner:
            raise DuplicateHandleError(
                f"句柄 {handle} 已登记为 {self._owner[handle]!r}, "
                f"不能重复登记为 {alias!r}")
        self._by_alias[alias] = int(handle)
        self._kind[alias] = kind
        self._owner[int(handle)] = alias

    def replace(self, alias: str, handle: int, kind: Optional[str] = None) -> None:
        old = self._by_alias.get(alias)
        if old is not None:
            self._owner.pop(old, None)
        if handle in self._owner and self._owner[handle] != alias:
            raise DuplicateHandleError(
                f"句柄 {handle} 已属于 {self._owner[handle]!r}")
        self._by_alias[alias] = int(handle)
        self._owner[int(handle)] = alias
        if kind is not None:
            self._kind[alias] = kind

    def get(self, alias: str, default: Optional[int] = None) -> Optional[int]:
        return self._by_alias.get(alias, default)

    def require(self, alias: str) -> int:
        h = self._by_alias.get(alias)
        if h is None:
            raise UnknownAliasError(f"未登记的别名: {alias!r}")
        return h

    def aliases(self) -> list[str]:
        return list(self._by_alias)

    def kinds(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for alias, kind in self._kind.items():
            out.setdefault(kind, []).append(alias)
        return out

    def handle_owner(self, handle: int) -> Optional[str]:
        return self._owner.get(int(handle))

    def missing(self, required: list[str]) -> list[str]:
        return [a for a in required if a not in self._by_alias]

    def conflicts(self, candidate: dict[str, int]) -> list[str]:
        return [a for a in candidate if a in self._by_alias]

    # 兼容旧场景契约测试的命名
    def detect_duplicates(self, candidate: dict[str, int]) -> list[str]:
        return self.conflicts(candidate)
