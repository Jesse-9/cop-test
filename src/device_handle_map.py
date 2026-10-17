"""device_handle_map.py — 集中设备句柄表 (阶段一 §2.2 步骤4 / §2.3 / §3)

方案约束:
  - 设备句柄在启动时一次性建立并缓存, 运行循环不得重复枚举全场景 (§3)
  - 别名必须是扁平下划线形式; 禁止把 '/' 路径传给 setObjectAlias (§3)
  - 传感器、目标 dummy、TCP、物料和工位都必须有唯一 alias,
    并在启动时做唯一性检查 (§3)
  - 句柄表不得依赖对象创建顺序 (§3.2 步骤5)

Exports:
  DeviceHandleMap, DuplicateAliasError, DuplicateHandleError,
  UnknownAliasError, InvalidAliasError
"""

from __future__ import annotations

from typing import Optional


# ═══════════════════════════════════════════════════════════════
# 异常
# ═══════════════════════════════════════════════════════════════

class HandleMapError(Exception):
    """句柄表异常基类"""


class InvalidAliasError(HandleMapError, ValueError):
    """别名不合法 (空串或含 '/')"""


class DuplicateAliasError(HandleMapError, ValueError):
    """别名重复"""


class DuplicateHandleError(HandleMapError, ValueError):
    """同一句柄被登记到两个别名"""


class UnknownAliasError(HandleMapError, KeyError):
    """查询未登记的别名"""


# ═══════════════════════════════════════════════════════════════
# 句柄表
# ═══════════════════════════════════════════════════════════════

class DeviceHandleMap:
    """集中句柄表 — 启动期一次性登记, 运行期只读查询。

    每个别名必须唯一; 每个句柄也只能属于一个别名 (句柄复用通常是
    构建期错误的信号)。`kind` 用于诊断分组 (conveyor/robot/sensor/agv/...)。
    """

    def __init__(self) -> None:
        self._by_alias: dict[str, int] = {}
        self._kind: dict[str, str] = {}
        self._handle_owner: dict[int, str] = {}

    # ── 登记 ────────────────────────────────────────────────────

    def register(self, alias: str, handle: int, kind: str = "device") -> None:
        """登记设备句柄。

        Raises:
            InvalidAliasError:   别名空串或含 '/'.
            DuplicateAliasError: 别名已存在.
            DuplicateHandleError: 句柄已被其它别名占用.
        """
        if not alias or "/" in alias:
            raise InvalidAliasError(
                f"别名必须是非空扁平下划线形式 (禁止 '/'): {alias!r}")
        if alias in self._by_alias:
            raise DuplicateAliasError(f"别名重复: {alias!r}")
        if handle in self._handle_owner:
            raise DuplicateHandleError(
                f"句柄 {handle} 已登记为 {self._handle_owner[handle]!r}, "
                f"不能重复登记为 {alias!r}")
        self._by_alias[alias] = int(handle)
        self._kind[alias] = kind
        self._handle_owner[int(handle)] = alias

    def replace(self, alias: str, handle: int, kind: Optional[str] = None) -> None:
        """重登记 (同一 alias 覆盖句柄, 用于重建场景后刷新)"""
        old = self._by_alias.get(alias)
        if old is not None:
            self._handle_owner.pop(old, None)
        self._by_alias[alias] = int(handle)
        if kind is not None:
            self._kind[alias] = kind
        self._handle_owner[int(handle)] = alias

    # ── 查询 ────────────────────────────────────────────────────

    def get(self, alias: str, default: Optional[int] = None) -> Optional[int]:
        return self._by_alias.get(alias, default)

    def require(self, alias: str) -> int:
        """必须存在的别名; 缺失抛 UnknownAliasError (启动期校验用)"""
        h = self._by_alias.get(alias)
        if h is None:
            raise UnknownAliasError(f"未登记的别名: {alias!r}")
        return h

    def aliases(self) -> list[str]:
        return list(self._by_alias.keys())

    def kinds(self) -> dict[str, list[str]]:
        """按类别分组返回别名 (供 §2.3 场景清单输出)"""
        out: dict[str, list[str]] = {}
        for alias, kind in self._kind.items():
            out.setdefault(kind, []).append(alias)
        return out

    def handle_owner(self, handle: int) -> Optional[str]:
        return self._handle_owner.get(int(handle))

    # ── 启动期批量校验 ───────────────────────────────────────────

    def missing(self, required: list[str]) -> list[str]:
        """返回 required 中未登记的别名列表 (不抛异常, 一次报全)"""
        return [a for a in required if a not in self._by_alias]

    def detect_duplicates(self, candidate: dict[str, int]) -> list[str]:
        """检测 candidate 中与本表冲突的别名 (用于二次登记前的唯一性检查)"""
        return [a for a in candidate if a in self._by_alias]
