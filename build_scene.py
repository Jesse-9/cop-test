"""
build_scene.py — 在 CoppeliaSim 中构建工厂仿真场景
=================================================
运行前请确保:
  1. CoppeliaSim 4.10+ 已启动
  2. ZMQ Remote API 服务端已激活（默认端口 23000）
     -> 检查菜单: Add-ons > ZMQ remote API server
  3. 场景尽量为空（可先 File > New Scene）

用法: uv run python build_scene.py [--host HOST] [--port PORT]
"""

import argparse
import math
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
        # 验证连接
        version = sim.getStringSignal("coppeliaSimVersion")
        print(f"[OK] 已连接 CoppeliaSim {version}")
        return client, sim
    except Exception as e:
        print(f"[FATAL] 无法连接 CoppeliaSim: {e}")
        print("  请确认 CoppeliaSim 已启动且 ZMQ Remote API 服务已激活。")
        sys.exit(1)


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
    sim.setObjectColor(handle, 0, sim.colorcomponent_ambient_diffuse, [0.25, 0.25, 0.28])
    sim.setObjectAlias(handle, "Floor")
    print(f"[OK] 地板 {sx}x{sy}  @ ({px}, {py})")
    return handle


def create_warehouse(sim, name: str, params: dict) -> int:
    """创建一个仓库（带底座 + 上层标识块）。"""
    px, py, pz = params["pos"]
    sx, sy, sz = params["size"]
    r, g, b = params["color"]

    handle = sim.createPrimitiveShape(
        sim.primitiveshape_cuboid, [sx, sy, sz]
    )
    sim.setObjectPosition(handle, [px, py, pz], sim.handle_world)
    sim.setObjectColor(handle, 0, sim.colorcomponent_ambient_diffuse, [r, g, b])
    sim.setObjectAlias(handle, f"Warehouse_{name}")
    print(f"[OK] 仓库 {name} @ ({px:.1f}, {py:.1f})")
    return handle


def create_work_island(sim, name: str, params: dict) -> int:
    """创建一个工作岛（底座 + 标签柱）。"""
    px, py, pz = params["pos"]
    sx, sy, sz = params["size"]
    r, g, b = params["color"]

    # 主体
    handle = sim.createPrimitiveShape(
        sim.primitiveshape_cuboid, [sx, sy, sz]
    )
    sim.setObjectPosition(handle, [px, py, pz], sim.handle_world)
    sim.setObjectColor(handle, 0, sim.colorcomponent_ambient_diffuse, [r, g, b])
    sim.setObjectAlias(handle, f"WorkIsland_{name}")

    # 标签柱（顶部圆柱，方便识别）
    pillar = sim.createPrimitiveShape(
        sim.primitiveshape_cylinder, [0.12, 0.12, 0.35]
    )
    sim.setObjectPosition(pillar, [px, py, pz + sz / 2 + 0.3], sim.handle_world)
    sim.setObjectColor(pillar, 0, sim.colorcomponent_ambient_diffuse, [r, g, b])
    sim.setObjectAlias(pillar, f"WorkIsland_{name}_Pillar")

    print(f"[OK] 工作岛 {name} @ ({px:.1f}, {py:.1f})")
    return handle


def create_conveyor(sim, name: str, params: dict) -> int:
    """创建传送带模型（扁长矩形 + 两端滚轮标记）。"""
    x1, y1 = params["start"]
    x2, y2 = params["end"]
    width = params["width"]
    r, g, b = params["color"]

    # 计算几何参数
    dx = x2 - x1
    dy = y2 - y1
    length = math.hypot(dx, dy)
    angle = math.atan2(dy, dx)          # 绕 Z 轴的旋转角 (rad)
    mx, my = (x1 + x2) / 2, (y1 + y2) / 2   # 中点

    # 传送带主体 — 扁平板
    belt = sim.createPrimitiveShape(
        sim.primitiveshape_cuboid,
        [length, width, 0.06],
    )
    sim.setObjectPosition(belt, [mx, my, cfg.Z_BASE], sim.handle_world)
    sim.setObjectOrientation(belt, [0, 0, angle], sim.handle_world)
    sim.setObjectColor(belt, 0, sim.colorcomponent_ambient_diffuse, [r, g, b])
    sim.setObjectAlias(belt, f"Conveyor_{name}")

    # 两端滚轮标记
    for idx, (rx, ry) in enumerate([(x1, y1), (x2, y2)]):
        suffix = "Start" if idx == 0 else "End"
        roller = sim.createPrimitiveShape(
            sim.primitiveshape_cylinder,
            [0.08, 0.08, width + 0.04],
        )
        sim.setObjectPosition(roller, [rx, ry, cfg.Z_BASE - 0.02], sim.handle_world)
        sim.setObjectOrientation(roller, [0, math.pi / 2, 0], sim.handle_world)
        sim.setObjectColor(roller, 0, sim.colorcomponent_ambient_diffuse, [0.35, 0.35, 0.35])
        sim.setObjectAlias(roller, f"Conveyor_{name}_Roller{suffix}")

    print(f"[OK] 传送带 {name}: ({x1:.1f},{y1:.1f}) → ({x2:.1f},{y2:.1f}), 长度={length:.2f}m")
    return belt


def create_agv_path(sim, agv_name: str, params: dict):
    """创建 AGV 小车 + 路径标记点。"""
    waypoints = params["waypoints"]
    r, g, b = params["color"]

    # AGV 车身（创建在第一个路径点）
    sx, sy, sz = cfg.AGV_SIZE
    start_x, start_y = waypoints[0]
    agv_handle = sim.createPrimitiveShape(
        sim.primitiveshape_cuboid, [sx, sy, sz]
    )
    sim.setObjectPosition(
        agv_handle, [start_x, start_y, cfg.Z_BASE + sz / 2], sim.handle_world
    )
    sim.setObjectColor(
        agv_handle, 0, sim.colorcomponent_ambient_diffuse, list(cfg.AGV_COLOR)
    )
    sim.setObjectAlias(agv_handle, f"AGV_{agv_name}")
    print(f"[OK] AGV {agv_name} @ ({start_x:.1f}, {start_y:.1f})")

    # 路径标记点
    for i, (wx, wy) in enumerate(waypoints):
        dot = sim.createPrimitiveShape(
            sim.primitiveshape_spheroid, [0.1, 0.1, 0.1]
        )
        sim.setObjectPosition(dot, [wx, wy, cfg.Z_BASE + 0.05], sim.handle_world)
        sim.setObjectColor(dot, 0, sim.colorcomponent_ambient_diffuse, [r, g, b])
        if i == 0:
            sim.setObjectAlias(dot, f"Waypoint_{agv_name}_Start")
        elif i == len(waypoints) - 1:
            sim.setObjectAlias(dot, f"Waypoint_{agv_name}_End")
        else:
            sim.setObjectAlias(dot, f"Waypoint_{agv_name}_{i}")

    return agv_handle


# ═══════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════

def build_scene(sim):
    """按拓扑顺序构建完整工厂场景。"""
    print("\n" + "=" * 55)
    print("  构建工厂仿真场景")
    print("=" * 55)

    # 1. 地板（先创建，后续对象都在其上）
    create_floor(sim)

    # 2. 仓库 S1, S2
    print("\n── 仓库 ──")
    for name, params in cfg.WAREHOUSES.items():
        create_warehouse(sim, name, params)

    # 3. 工作岛 A, B, C, D, E
    print("\n── 工作岛 ──")
    for name, params in cfg.WORK_ISLANDS.items():
        create_work_island(sim, name, params)

    # 4. 传送带
    print("\n── 传送带 ──")
    for name, params in cfg.CONVEYORS.items():
        create_conveyor(sim, name, params)

    # 5. AGV 小车 + 路径
    print("\n── AGV 路径 ──")
    for name, params in cfg.AGV_PATHS.items():
        create_agv_path(sim, name, params)

    # 提示用户
    print("\n" + "=" * 55)
    print("  场景构建完成！")
    print("  - 可在 CoppeliaSim 中保存为 factory.ttt")
    print("  - 运行 run_simulation.py 开始仿真控制")
    print("=" * 55)


def main():
    parser = argparse.ArgumentParser(description="构建工厂仿真场景")
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
