"""
build_scene.py — 在 CoppeliaSim 中构建岛式车间工厂仿真场景
=========================================================
遵循 /Warehouse, /Island, /Conveyor, /Mobile 分层命名规范。
优先使用 CoppeliaSim 内置模型，不存在时回退到基本几何体。

运行前请确保:
  1. CoppeliaSim 4.10+ 已启动
  2. ZMQ Remote API 服务端已激活（默认端口 23000）
  3. 场景尽量为空（可先 File > New Scene）

用法: uv run python build_scene.py [--host HOST] [--port PORT]
"""

import argparse
import math
import os
import sys

from coppeliasim_zmqremoteapi_client import RemoteAPIClient

import factory_config as cfg


# ═══════════════════════════════════════════════════════════════
# 连接
# ═══════════════════════════════════════════════════════════════

def connect(host: str = "localhost", port: int = 23000):
    """连接 CoppeliaSim ZMQ Remote API 服务端。"""
    print(f"[INFO] 连接 CoppeliaSim @ {host}:{port} ...")
    try:
        client = RemoteAPIClient(host=host, port=port)
        sim = client.require("sim")
        version = sim.getStringSignal("coppeliaSimVersion")
        print(f"[OK] 已连接 CoppeliaSim {version}")
        return client, sim
    except Exception as e:
        print(f"[FATAL] 无法连接 CoppeliaSim: {e}")
        print("  请确认 CoppeliaSim 已启动且 ZMQ Remote API 服务已激活。")
        sys.exit(1)


# ═══════════════════════════════════════════════════════════════
# 层级管理 — Hierarchy Management
# ═══════════════════════════════════════════════════════════════

def create_hierarchy_roots(sim) -> dict[str, int]:
    """创建场景层级根节点（不可见的"文件夹"对象）。

    返回 { "/Warehouse": handle, "/Conveyor": handle, ... } 的映射。
    所有设备作为这些根节点的子对象，在 CoppeliaSim 场景浏览器中
    形成清晰的树状结构。
    """
    roots = {}
    for path in cfg.HIERARCHY_ROOTS:
        # 使用 dummy 对象作为组容器
        handle = sim.createDummy(0.01)  # 极小不可见 dummy
        sim.setObjectAlias(handle, path)
        # 将 name 设为路径的叶子部分，使场景浏览器显示友好
        leaf = path.rsplit("/", 1)[-1]
        sim.setObjectName(handle, leaf)
        roots[path] = handle
        print(f"[OK] 层级根节点 {path}")
    return roots


# ═══════════════════════════════════════════════════════════════
# 内置模型加载 — Built-in Model Support
# ═══════════════════════════════════════════════════════════════

def model_exists(relative_path: str) -> bool:
    """检查内置模型文件是否存在。"""
    full = os.path.join(cfg.MODEL_BASE, relative_path)
    return os.path.exists(full)


def load_builtin_model(sim, relative_path: str, position: list,
                       alias: str, parent_handle: int = -1) -> int | None:
    """尝试加载 CoppeliaSim 内置模型。

    Returns:
        模型根句柄，若文件不存在则返回 None。
    """
    full_path = os.path.join(cfg.MODEL_BASE, relative_path)
    if not os.path.exists(full_path):
        return None
    try:
        handle = sim.loadModel(full_path)
        sim.setObjectPosition(handle, position, sim.handle_world)
        if parent_handle != -1:
            sim.setObjectParent(handle, parent_handle, True)
        sim.setObjectAlias(handle, alias)
        print(f"  [MODEL] 加载内置模型: {relative_path}")
        return handle
    except Exception as e:
        print(f"  [WARN] 模型加载失败 ({relative_path}): {e}")
        return None


# ═══════════════════════════════════════════════════════════════
# 场景元素构建
# ═══════════════════════════════════════════════════════════════

def create_floor(sim) -> int:
    """创建地板平面。"""
    sx, sy, sz = cfg.FLOOR_SIZE
    px, py, pz = cfg.FLOOR_POS
    handle = sim.createPrimitiveShape(
        sim.primitiveshape_cuboid, [sx, sy, sz]
    )
    sim.setObjectPosition(handle, [px, py, pz], sim.handle_world)
    sim.setObjectColor(handle, 0, sim.colorcomponent_ambient_diffuse,
                       [0.25, 0.25, 0.28])
    sim.setObjectAlias(handle, "Floor")
    print(f"[OK] 地板 {sx}x{sy} @ ({px}, {py})")
    return handle


def create_warehouse(sim, name: str, params: dict,
                     parent_handle: int) -> int:
    """创建一个仓库并挂载到 /Warehouse 父节点下。"""
    px, py, pz = params["pos"]
    sx, sy, sz = params["size"]
    r, g, b = params["color"]
    alias = params["alias"]

    handle = sim.createPrimitiveShape(
        sim.primitiveshape_cuboid, [sx, sy, sz]
    )
    sim.setObjectParent(handle, parent_handle, True)
    sim.setObjectPosition(handle, [px, py, pz], sim.handle_world)
    sim.setObjectColor(handle, 0, sim.colorcomponent_ambient_diffuse, [r, g, b])
    sim.setObjectAlias(handle, alias)
    print(f"[OK] 仓库 {name} → {alias} @ ({px:.1f}, {py:.1f})")
    return handle


def create_work_island(sim, name: str, params: dict,
                       parent_handle: int) -> int:
    """创建一个工作岛（底座 + 标签柱），挂载到 /Island 父节点下。"""
    px, py, pz = params["pos"]
    sx, sy, sz = params["size"]
    r, g, b = params["color"]
    alias = params["alias"]

    # 主体
    handle = sim.createPrimitiveShape(
        sim.primitiveshape_cuboid, [sx, sy, sz]
    )
    sim.setObjectParent(handle, parent_handle, True)
    sim.setObjectPosition(handle, [px, py, pz], sim.handle_world)
    sim.setObjectColor(handle, 0, sim.colorcomponent_ambient_diffuse, [r, g, b])
    sim.setObjectAlias(handle, alias)

    # 标签柱
    pillar = sim.createPrimitiveShape(
        sim.primitiveshape_cylinder, [0.12, 0.12, 0.35]
    )
    sim.setObjectParent(pillar, handle, True)
    sim.setObjectPosition(pillar, [0, 0, sz / 2 + 0.3], sim.handle_parent)
    sim.setObjectColor(pillar, 0, sim.colorcomponent_ambient_diffuse, [r, g, b])
    sim.setObjectAlias(pillar, f"{alias}/Pillar")

    print(f"[OK] 工作岛 {name} → {alias} @ ({px:.1f}, {py:.1f})")
    return handle


def create_conveyor(sim, name: str, params: dict,
                    parent_handle: int) -> int:
    """创建传送带模型（扁长矩形 + 两端滚轮标记），挂载到 /Conveyor 父节点下。"""
    x1, y1 = params["start"]
    x2, y2 = params["end"]
    width = params["width"]
    r, g, b = params["color"]
    alias = params["alias"]

    dx = x2 - x1
    dy = y2 - y1
    length = math.hypot(dx, dy)
    angle = math.atan2(dy, dx)
    mx, my = (x1 + x2) / 2, (y1 + y2) / 2

    # 传送带主体
    belt = sim.createPrimitiveShape(
        sim.primitiveshape_cuboid, [length, width, 0.06],
    )
    sim.setObjectParent(belt, parent_handle, True)
    sim.setObjectPosition(belt, [mx, my, cfg.Z_BASE], sim.handle_world)
    sim.setObjectOrientation(belt, [0, 0, angle], sim.handle_world)
    sim.setObjectColor(belt, 0, sim.colorcomponent_ambient_diffuse, [r, g, b])
    sim.setObjectAlias(belt, alias)

    # 两端滚轮标记
    roller_color = [0.35, 0.35, 0.35]
    for idx, (rx, ry) in enumerate([(x1, y1), (x2, y2)]):
        suffix = "RollerStart" if idx == 0 else "RollerEnd"
        roller = sim.createPrimitiveShape(
            sim.primitiveshape_cylinder, [0.08, 0.08, width + 0.04],
        )
        sim.setObjectParent(roller, belt, True)
        # 滚轮位置相对于传送带主体的局部坐标
        sim.setObjectPosition(roller, [-length / 2 + (0 if idx == 0 else length),
                                       0, -0.02], sim.handle_parent)
        sim.setObjectOrientation(roller, [0, math.pi / 2, 0], sim.handle_parent)
        sim.setObjectColor(roller, 0, sim.colorcomponent_ambient_diffuse, roller_color)
        sim.setObjectAlias(roller, f"{alias}/{suffix}")

    print(f"[OK] 传送带 {name} → {alias}: "
          f"({x1:.1f},{y1:.1f}) → ({x2:.1f},{y2:.1f}), L={length:.2f}m")
    return belt


def create_shared_agv(sim, parent_handle: int) -> int:
    """创建共享 AGV 小车 + 路径标记点，挂载到 /Mobile 父节点下。

    与旧版不同：只创建一个 AGV（而非三个），通过调度器在运行时
    动态分配任务（B→A 或 D→E）。
    """
    sx, sy, sz = cfg.AGV_SIZE
    alias = cfg.AGV_ALIAS

    # 尝试加载内置移动机器人模型
    model_handle = load_builtin_model(
        sim, "robots/mobile/pioneer p3dx.ttm",
        [-2.5, -1.0, cfg.Z_BASE + sz / 2],  # 初始停在 B 岛附近
        alias, parent_handle
    )

    if model_handle is not None:
        agv_handle = model_handle
    else:
        # 回退：基本几何体 AGV
        agv_handle = sim.createPrimitiveShape(
            sim.primitiveshape_cuboid, [sx, sy, sz]
        )
        sim.setObjectParent(agv_handle, parent_handle, True)
        sim.setObjectPosition(
            agv_handle,
            [-2.5, -1.0, cfg.Z_BASE + sz / 2],
            sim.handle_world,
        )
        sim.setObjectColor(
            agv_handle, 0, sim.colorcomponent_ambient_diffuse, list(cfg.AGV_COLOR)
        )
        sim.setObjectAlias(agv_handle, alias)

    # 为每条路径创建标记点
    route_colors = {
        "B_TO_A": (0.9, 0.9, 0.3),   # 黄色路点
        "D_TO_E": (0.6, 0.3, 0.9),   # 紫色路点
    }

    for route_key, waypoints in cfg.AGV_ROUTES.items():
        r, g, b = route_colors[route_key]
        for i, (wx, wy) in enumerate(waypoints):
            dot = sim.createPrimitiveShape(
                sim.primitiveshape_spheroid, [0.1, 0.1, 0.1]
            )
            sim.setObjectParent(dot, parent_handle, True)
            sim.setObjectPosition(dot, [wx, wy, cfg.Z_BASE + 0.05], sim.handle_world)
            sim.setObjectColor(dot, 0, sim.colorcomponent_ambient_diffuse, [r, g, b])
            if i == 0:
                dot_alias = f"{alias}/Waypoint_{route_key}_Start"
            elif i == len(waypoints) - 1:
                dot_alias = f"{alias}/Waypoint_{route_key}_End"
            else:
                dot_alias = f"{alias}/Waypoint_{route_key}_{i}"
            sim.setObjectAlias(dot, dot_alias)

    print(f"[OK] 共享 AGV → {alias} (B→A + D→E 双路径)")
    return agv_handle


# ═══════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════

def build_scene(sim):
    """按拓扑顺序构建完整岛式车间工厂场景。"""
    print("\n" + "=" * 55)
    print("  构建岛式车间工厂仿真场景")
    print("  (分层命名: /Warehouse, /Island, /Conveyor, /Mobile)")
    print("=" * 55)

    # 0. 创建层级根节点
    print("\n── 层级根节点 ──")
    roots = create_hierarchy_roots(sim)

    # 1. 地板
    print("\n── 地板 ──")
    create_floor(sim)

    # 2. 仓库 S1, S2
    print("\n── 仓库 ──")
    for name, params in cfg.WAREHOUSES.items():
        create_warehouse(sim, name, params, roots["/Warehouse"])

    # 3. 工作岛 A, B, C, D, E
    print("\n── 工作岛 ──")
    for name, params in cfg.WORK_ISLANDS.items():
        create_work_island(sim, name, params, roots["/Island"])

    # 4. 传送带
    print("\n── 传送带 ──")
    for name, params in cfg.CONVEYORS.items():
        create_conveyor(sim, name, params, roots["/Conveyor"])

    # 5. 共享 AGV（单一小车，双路径）
    print("\n── 共享 AGV ──")
    create_shared_agv(sim, roots["/Mobile"])

    # 提示用户
    print("\n" + "=" * 55)
    print("  场景构建完成！")
    print("  - 所有对象使用分层命名 (/Warehouse, /Island, ...)")
    print("  - 单一共享 AGV 覆盖 B→A 和 D→E 双路径")
    print("  - 可在 CoppeliaSim 中保存为 factory-sim.ttt")
    print("  - 运行 run_simulation.py 开始柔性仿真控制")
    print("=" * 55)


def main():
    parser = argparse.ArgumentParser(description="构建岛式车间工厂仿真场景")
    parser.add_argument("--host", default="localhost", help="CoppeliaSim 主机地址")
    parser.add_argument("--port", type=int, default=23000, help="ZMQ Remote API 端口")
    args = parser.parse_args()

    client, sim = connect(args.host, args.port)
    try:
        build_scene(sim)
    except Exception as e:
        print(f"\n[ERROR] 构建失败: {e}")
        raise
    finally:
        print("\n[DONE] 脚本结束 — 场景对象已持久化到 CoppeliaSim 中。")


if __name__ == "__main__":
    main()
