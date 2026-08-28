"""
factory_scene.py — 场景对象公共 helper (消除 3 处重复的"按叶子别名匹配对象")
================================================================================
原 wiki 02-01-分层命名场景构建.md 显式指出: 项目里
  - build_scene.clear_scene
  - factory_controller._setup_agv_visual
  - factory_robot._collect_arm_joints
三处各自实现了"按叶子别名/末两段匹配"逻辑。本模块统一抽到一处。

公开 API:
  find_by_alias(sim, full_alias, *types)   → int (句柄或 -1)
  iter_by_type(sim, *types)                → 生成器 (handle, alias)
  add_object(sim, handle, alias, parent)   → 统一 setObjectAlias + setObjectParent
  parse_leaf(full_alias, n_last=2)         → 从完整别名取末 n 段作为叶子
"""

from __future__ import annotations

from typing import Iterator


def parse_leaf(full_alias: str, n_last: int = 2) -> str:
    """从完整别名取末 n_last 段作为叶子名, 兼容 `_Mobile_AGV_Shared` → `AGV_Shared`。

    Args:
        full_alias: 完整别名 (e.g. "Island_A_Assembly_Robot_A" / "_Mobile_AGV_Shared").
        n_last:     取末几段。默认 2 (e.g. `AGV_Shared`、`Robot_A`).
    """
    if not full_alias:
        return ""
    # 兼容首字符是 '_' (alias path quirk, 见 CLAUDE.md §1)
    if full_alias.startswith("_"):
        full_alias = full_alias[1:]
    parts = full_alias.split("_")
    if len(parts) <= n_last:
        return full_alias
    return "_".join(parts[-n_last:])


def find_by_alias(sim, full_alias: str, *types: int) -> int:
    """按别名在给定对象类型中查找场景对象 — Find scene object by alias (with leaf fallback).

    匹配规则 (按顺序):
      1) 完整别名完全相等 (e.g. "Mobile_AGV_Shared")
      2) 别名的末两段 (叶子) 与 full_alias 的叶子相等
         (兼容内置 pioneer p3dx 模型残留的原生别名 "Pioneer_p3dx" 等)
      3) 别名以 "_<leaf>" 结尾 (兼容 setObjectAlias 把 '/' 转 '_' 的 _前缀别名)

    Args:
        sim:        CoppeliaSim Remote API "sim" 模块.
        full_alias: 期望的完整别名 (不含斜杠).
        *types:     sim.object_shape_type / _dummy_type / _joint_type 等.
                    不传时默认查 shape+dummy.

    Returns:
        对象句柄; 找不到返回 -1.
    """
    if not full_alias:
        return -1
    if not types:
        types = (sim.object_shape_type, sim.object_dummy_type)
    leaf = parse_leaf(full_alias, 2)
    for tval in types:
        i = 0
        # 5000 上限 (与原实现一致) — 场景对象超过此值在仓库中无先例
        while i < 5000:
            try:
                h = sim.getObjects(i, tval)
            except Exception:
                break
            if h is None or h < 0:
                break
            try:
                a = sim.getObjectAlias(h)
            except Exception:
                a = ""
            if a and (a == full_alias or a == leaf or a.endswith("_" + leaf)):
                return int(h)
            i += 1
    return -1


def iter_by_type(sim, *types: int) -> Iterator[tuple[int, str]]:
    """按类型遍历场景对象 — Iterate scene objects of given types.

    处理 getObjects -1 哨兵问题 (CLAUDE.md/MCP fix: -1 不表示结束而是无效句柄,
    错误循环到 5000 会污染数据)。遇 None 或负值句柄立即结束当前类型遍历。

    Yields:
        (handle, alias) 元组; alias 读取失败时为空串.
    """
    for tval in types:
        i = 0
        while True:
            try:
                h = sim.getObjects(i, tval)
            except Exception:
                break
            if h is None or h < 0:
                break
            try:
                a = sim.getObjectAlias(h)
            except Exception:
                a = ""
            yield int(h), a
            i += 1


def add_object(sim, handle: int, full_alias: str,
               parent_handle: int = -1) -> int:
    """统一 setObjectAlias + 可选 setObjectParent — Wrap alias/parent setup.

    Returns:
        原 handle, 便于链式调用.
    """
    sim.setObjectAlias(handle, full_alias)
    if parent_handle != -1:
        sim.setObjectParent(handle, parent_handle, True)
    return handle
