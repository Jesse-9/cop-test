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

# ═══════════════════════════════════════════════════════════════
# 几何测量 — Geometry Measurement Helpers (重构方案 §3.1 / §4.1)
# ═══════════════════════════════════════════════════════════════
# build_scene (构建期) 与 factory_controller (运行期) 都必须能"量"出传送带
# 实际几何。构建期把入口/停止区 dummy 摆好; 运行期按别名把这些对象重新找
# 回来 —— 构建期的元数据只活在构建进程的内存里, 换个进程就没了。
def model_local_bbox(sim, handle: int) -> list[float] | None:
    """模型**本地**包围盒 — Local model bbox via objfloatparam_modelbbox_*.

    4.10 实测可用 (docs/wiki/01-API参考/01-03-常量与参数ID.md 的
    objfloatparam_modelbbox_min/max_*)。返回 [min_x, min_y, min_z,
    max_x, max_y, max_z]; 任一参数读取失败返回 None, 调用方必须能处理。
    """
    names = ("min_x", "min_y", "min_z", "max_x", "max_y", "max_z")
    values = []
    for name in names:
        param = getattr(sim, f"objfloatparam_modelbbox_{name}", None)
        if param is None:
            return None
        try:
            values.append(float(sim.getObjectFloatParam(handle, param)))
        except Exception:
            return None
    return values


def shape_size(sim, handle: int) -> list[float] | None:
    """shape 的**全尺寸** [sx, sy, sz] — Shape full size.

    ⚠ 4.10 的 `sim.getShapeBB` 返回 `(size, transform)`, 而不是官方手册里
    的 6 元素局部包围盒 (实测 2026-10-13)。这里只取 size, 且 size 是
    **全尺寸**而非半尺寸 (岛屿 1.4 m 的 cuboid → size[0] == 1.4)。
    """
    try:
        bb = sim.getShapeBB(handle)
    except Exception:
        return None
    if isinstance(bb, (list, tuple)) and len(bb) == 2 and \
            isinstance(bb[0], (list, tuple)):
        return [float(v) for v in bb[0][:3]]
    if isinstance(bb, (list, tuple)) and len(bb) == 6:
        return [float(bb[3] - bb[0]), float(bb[4] - bb[1]), float(bb[5] - bb[2])]
    return None


def find_shapes_by_alias(sim, root: int, alias: str) -> list[int]:
    """在 root 子树中找出所有别名为 alias 的 shape 句柄。"""
    try:
        tree = sim.getObjectsInTree(root, sim.handle_all, 0)
    except Exception:
        return []
    found = []
    for obj in tree:
        try:
            if (sim.getObjectType(obj) == sim.object_shape_type
                    and sim.getObjectAlias(obj) == alias):
                found.append(int(obj))
        except Exception:
            continue
    return found


def find_script_child(sim, root: int) -> int:
    """找出 root 子树中的脚本对象句柄; 没有返回 -1。"""
    script_type = getattr(sim, "object_script_type", None)
    if script_type is None:
        return -1
    try:
        tree = sim.getObjectsInTree(root, sim.handle_all, 0)
    except Exception:
        return -1
    for obj in tree:
        try:
            if sim.getObjectType(obj) == script_type:
                return int(obj)
        except Exception:
            continue
    return -1


def measure_belt_surface_offset(sim, root: int, pads: list[int]) -> float | None:
    """带面上表面相对**模型根**的 Z 偏移 (m) — Belt top surface local Z.

    模型根位于原点时, 上表面 = 最高的 pad 中心 + 半个 pad 厚度。返回 None
    表示测不出来 (调用方必须回退而不是猜一个数)。
    """
    if not pads:
        return None
    try:
        root_z = sim.getObjectPosition(root, sim.handle_world)[2]
    except Exception:
        return None
    best = None
    for pad in pads:
        try:
            pos_z = sim.getObjectPosition(pad, sim.handle_world)[2]
        except Exception:
            continue
        size = shape_size(sim, pad)
        half = (size[2] / 2.0) if size else 0.0
        top = pos_z + half
        best = top if best is None else max(best, top)
    if best is None:
        return None
    return best - root_z


def measure_belt_top_run(sim, root: int, pads: list) -> dict | None:
    """实测履带**上表面行程** — Measure the actual top run of a belt.

    ⚠ 关键 (2026-10-13 实测): 内置传送带模型的包围盒**不等于**可承载区。
    `generic conveyor (belt).ttm` 的 native 包围盒 X 跨度 1.216 m, 但上表面
    pad 只覆盖 [-0.497, 0.500] (约 82%), 两端是回转滚筒。若按包围盒半长放
    入口 dummy, 物料会生成在滚筒外侧直接掉到地面 —— 实测确实如此。

    故入口/停止区 dummy 必须由**上表面 pad 的实际行程**推出:

    Returns:
        {"min_x": 上表面最小局部 X, "max_x": 上表面最大局部 X,
         "surface_off": 带面上表面相对模型根的 Z 偏移,
         "pad_thickness": 单个 pad 厚度}
        没有可用的上表面 pad 时返回 None。
    """
    if not pads:
        return None
    top_pads = []
    best_top = None
    geometry = {}
    for pad in pads:
        try:
            local = sim.getObjectPosition(pad, root)
        except Exception:
            continue
        size = shape_size(sim, pad)
        half = (size[2] / 2.0) if size else 0.0
        geometry[pad] = (local, half)
        top = local[2] + half
        if best_top is None or top > best_top:
            best_top = top
    if best_top is None:
        return None
    # 容差取 pad 厚度的一半, 避免把滚筒上过渡的 pad 也算成上表面
    thicknesses = [half * 2.0 for _, half in geometry.values() if half > 0]
    tol = (min(thicknesses) if thicknesses else 0.01)
    for pad, (local, half) in geometry.items():
        if abs(local[2] + half - best_top) <= max(tol, 1e-4):
            top_pads.append(local)
    if not top_pads:
        return None
    return {
        "min_x": min(p[0] for p in top_pads),
        "max_x": max(p[0] for p in top_pads),
        "surface_off": best_top,
        "pad_thickness": (min(thicknesses) if thicknesses else 0.0),
        "top_pad_count": len(top_pads),
    }
