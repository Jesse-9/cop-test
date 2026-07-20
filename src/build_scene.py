"""
build_scene.py — 在 CoppeliaSim 中构建岛式车间工厂仿真场景
=========================================================
遵循 Warehouse, Island, Conveyor, Mobile 分层命名规范（下划线连接, 不用 '/'）。
优先使用 CoppeliaSim 内置模型，不存在时回退到基本几何体。

运行前请确保:
  1. CoppeliaSim 4.10+ 已启动
  2. ZMQ Remote API 服务端已激活（默认端口 23000）
  3. 场景尽量为空（可先 File > New Scene）

用法: uv run python src/build_scene.py [--host HOST] [--port PORT]
     或: uv run build-scene [--host HOST] [--port PORT]
"""

import argparse
import math
import os
import sys

from coppeliasim_zmqremoteapi_client import RemoteAPIClient

import factory_config as cfg


def clear_scene(sim):
    """清空当前场景的所有对象 — Clear all scene objects.

    build_scene.py 应是幂等的: 重复运行前先清空，避免对象累积污染。
    每次 build 前必须清场，否则重复构建会因别名冲突报错。

    策略: 遍历所有主类型枚举对象，收集句柄后用 removeObjects 批量删除。
    污染严重时部分句柄可能失效（被父对象删除时连带），忽略错误继续。

    保留: CoppeliaSim 默认场景自带的 DefaultCamera (别名/对象名相同)。
    它是视角相机, 删掉后用户在 CoppeliaSim GUI 里会失去默认视角, 故保留。
    识别方式: 别名或对象名等于 "DefaultCamera"。

    注意: CoppeliaSim 的 sim.getObjects(i, type) 按对象在该类型中的序号索引,
    删除对象后剩余对象的序号会被压缩重排为连续 0..N-1, 故循环遇到 None/<0
    即可安全 break, 不会因"索引空洞"漏删对象。
    """
    type_list = [
        sim.object_shape_type, sim.object_dummy_type,
        sim.object_joint_type, sim.object_proximitysensor_type,
        sim.object_visionsensor_type, sim.object_graph_type,
        sim.object_light_type, sim.object_camera_type,
        sim.object_mirror_type, sim.object_octree_type,
        sim.object_pointcloud_type,
    ]
    # 需保留的对象别名/对象名集合 (CoppeliaSim 默认场景自带)
    keep_names = {"DefaultCamera",
                  "XYZCameraProxy", "XViewCamera", "YViewCamera", "ZViewCamera", "NXViewCamera", "NYViewCamera", "NZViewCamera",
                  "DefaultLights", "LightA", "LightB", "LightC", "LightD"}

    all_handles: set[int] = set()
    kept: set[str] = set()
    for tval in type_list:
        i = 0
        while i < 100000:
            try:
                h = sim.getObjects(i, tval)
                if h is None or h < 0:
                    break
                # 读取别名与对象名, 任一命中保留集即跳过删除
                try:
                    alias = sim.getObjectAlias(h)
                except Exception:
                    alias = ""
                try:
                    name = sim.getObjectName(h)
                except Exception:
                    name = ""
                if alias in keep_names or name in keep_names:
                    kept.add(alias or name)
                    print(f"[INFO] 保留默认对象: {alias or name} (handle={h})", i, tval)
                else:
                    all_handles.add(int(h))
                i += 1
            except Exception:
                break

    if kept:
        print(f"[INFO] 保留 {len(kept)} 个默认对象: {', '.join(sorted(kept))}")

    if not all_handles:
        print("[INFO] 场景已为空，无需清理")
        return 0

    removed = 0
    # 分批删除，避免单次 ZMQ 调用负载过大
    handle_list = list(all_handles)
    batch_size = 500
    for start in range(0, len(handle_list), batch_size):
        batch = handle_list[start:start + batch_size]
        try:
            sim.removeObjects(batch)
            removed += len(batch)
        except Exception:
            # 失败时逐个删
            for h in batch:
                try:
                    sim.removeObjects([h])
                    removed += 1
                except Exception:
                    continue
    print(f"[INFO] 已清理 {removed} 个对象")
    return removed


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

    返回 { "Warehouse": handle, "Conveyor": handle, ... } 的映射。
    所有设备作为这些根节点的子对象，在 CoppeliaSim 场景浏览器中
    形成清晰的树状结构。

    注意: CoppeliaSim 命名不支持字符 '/', 别名一律用下划线连接,
    不带前导斜杠 (详见 CLAUDE.md「已知问题与坑」第 1 条)。
    """
    roots = {}
    for path in cfg.HIERARCHY_ROOTS:
        # 使用 dummy 对象作为组容器
        handle = sim.createDummy(0.01)  # 极小不可见 dummy
        sim.setObjectAlias(handle, path)
        # name 与 alias 一致 (已是无斜杠的简单名)
        sim.setObjectName(handle, path)
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
                       alias: str, parent_handle: int = -1,
                       orientation: list = None) -> int | None:
    """尝试加载 CoppeliaSim 内置模型。

    Args:
        relative_path: 相对 MODEL_BASE 的模型路径。
        position:      世界坐标 [x, y, z]。
        alias:         规范别名 (如 Island_A_Assembly_Robot_A)。
        parent_handle: 父对象句柄, -1 表示挂到场景根。
        orientation:   世界姿态欧拉角 [alpha, beta, gamma] (弧度), None 为默认。

    Returns:
        模型根句柄，若文件不存在则返回 None。
    """
    full_path = os.path.join(cfg.MODEL_BASE, relative_path)
    if not os.path.exists(full_path):
        print(f"  [WARN] 内置模型不存在，将回退到基本几何体: {relative_path}")
        return None
    try:
        handle = sim.loadModel(full_path)
        sim.setObjectPosition(handle, position, sim.handle_world)
        if orientation is not None:
            sim.setObjectOrientation(handle, orientation, sim.handle_world)
        if parent_handle != -1:
            sim.setObjectParent(handle, parent_handle, True)
        sim.setObjectAlias(handle, alias)
        print(f"  [MODEL] 加载内置模型: {relative_path} → {alias}")
        return handle
    except Exception as e:
        print(f"  [WARN] 模型加载失败 ({relative_path}): {e}")
        return None


def _resolve_model_path(model_key: str) -> str:
    """从 cfg.BUILTIN_MODELS 解析模型键到相对路径。"""
    print(f"  [INFO] 解析模型键: {model_key}")
    return cfg.BUILTIN_MODELS.get(model_key, "")


def create_robot_arm(sim, island_alias: str, island_handle: int,
                     island_pos: list, robot_cfg: dict) -> int | None:
    """为工作岛加载一只机械臂并挂载夹爪 — Load robot arm + gripper.

    机械臂作为岛底座的子对象，位于岛上方。加载失败时回退为
    圆柱体立柱（标识性占位），不阻断场景构建。

    Args:
        island_alias: 岛别名 (如 Island_A_Assembly).
        island_handle: 岛底座句柄.
        island_pos:   岛世界坐标 [x, y, z].
        robot_cfg:    cfg.ISLAND_ROBOTS 中的配置项.

    Returns:
        机械臂根句柄, 失败返回 None.
    """
    alias = robot_cfg["alias"]
    model_rel = _resolve_model_path(robot_cfg["model_key"])
    ox, oy, oz = robot_cfg["offset"]
    base_x = island_pos[0] + ox
    base_y = island_pos[1] + oy
    base_z = island_pos[2] + oz

    arm_handle = load_builtin_model(
        sim, model_rel,
        [base_x, base_y, base_z], alias,
        parent_handle=island_handle,
        orientation=robot_cfg.get("euler"),
    )

    if arm_handle is None:
        # 回退: 圆柱体立柱占位
        arm_handle = sim.createPrimitiveShape(
            sim.primitiveshape_cylinder, [0.1, 0.1, 0.6]
        )
        sim.setObjectParent(arm_handle, island_handle, True)
        sim.setObjectPosition(
            arm_handle, [ox, oy, oz + 0.3], sim.handle_parent
        )
        sim.setObjectColor(
            arm_handle, 0, sim.colorcomponent_ambient_diffuse, [0.3, 0.3, 0.3]
        )
        sim.setObjectAlias(arm_handle, alias)
        print(f"  [FALLBACK] {alias} 使用立柱占位")

    # 挂载夹爪 — Attach gripper to arm tip
    gripper_key = robot_cfg.get("gripper")
    if gripper_key:
        gripper_rel = _resolve_model_path(gripper_key)
        gripper_alias = f"{alias}_Gripper"
        try:
            # 用 getObjectsInTree 找机械臂末端的 tip dummy/shape。
            # 内置机械臂末端常见命名: tip / connection / flange / linkTip
            tip_handle = None
            try:
                tree = sim.getObjectsInTree(arm_handle, sim.handle_all, 0)
            except Exception:
                tree = []
            candidate_names = (
                "tip", "connection", "flange", "linkTip",
                "UR5_connection", "iiwa_tip", "Franka_tip",
                "tcp", "endpoint", "gripperConnect",
            )
            for obj in tree:
                try:
                    obj_alias = sim.getObjectAlias(obj)
                except Exception:
                    continue
                # 叶子名匹配 (别名已统一为下划线, 取末段)
                leaf = obj_alias.rsplit("_", 1)[-1]
                if obj_alias in candidate_names or leaf in candidate_names:
                    # 偏好 dummy 类型作为挂载点
                    if sim.getObjectType(obj) == sim.object_dummy_type:
                        tip_handle = obj
                        break
                    if tip_handle is None:
                        tip_handle = obj
            if tip_handle is not None:
                grip_handle = sim.loadModel(
                    os.path.join(cfg.MODEL_BASE, gripper_rel)
                )
                sim.setObjectParent(grip_handle, tip_handle, True)
                sim.setObjectAlias(grip_handle, "Gripper")
                sim.setObjectPosition(
                    grip_handle, [0, 0, 0], sim.handle_parent
                )
                print(f"  [GRIPPER] {gripper_rel} 挂载到末端")
            else:
                print(f"  [WARN] 未找到 {alias} 末端 link，跳过夹爪挂载")
        except Exception as e:
            print(f"  [WARN] 夹爪挂载失败 ({gripper_rel}): {e}")

    return arm_handle


# ═══════════════════════════════════════════════════════════════
# 场景元素构建
# ═══════════════════════════════════════════════════════════════

def create_floor(sim) -> int:
    """创建地板平面 — 优先使用内置工业地板模型，回退到基本几何体。"""
    sx, sy, sz = cfg.FLOOR_SIZE
    px, py, pz = cfg.FLOOR_POS

    # 尝试加载内置工业地板（5m×5m 单元，需铺设多块覆盖整个区域）
    model_rel = _resolve_model_path("floor_concrete")
    handle = None
    if model_rel and os.path.exists(os.path.join(cfg.MODEL_BASE, model_rel)):
        try:
            # 内置地板为 5×5m，按区域铺设多块拼成大地面
            tile = 5.0
            nx = max(1, int(math.ceil(sx / tile)))
            ny = max(1, int(math.ceil(sy / tile)))
            for ix in range(nx):
                for iy in range(ny):
                    fx = px - sx / 2 + tile / 2 + ix * tile
                    fy = py - sy / 2 + tile / 2 + iy * tile
                    fh = sim.loadModel(
                        os.path.join(cfg.MODEL_BASE, model_rel)
                    )
                    sim.setObjectPosition(fh, [fx, fy, pz], sim.handle_world)
                    if ix == 0 and iy == 0:
                        sim.setObjectAlias(fh, "Floor")
                        handle = fh
                    else:
                        # 后续铺设的地板不重命名，避免别名冲突
                        pass
            print(f"[OK] 工业地板 {nx}×{ny} 块已铺设 ({sx}x{sy})")
            return handle
        except Exception as e:
            print(f"  [WARN] 内置地板加载失败，回退到几何体: {e}")

    # 回退: 基本几何体地板
    handle = sim.createPrimitiveShape(
        sim.primitiveshape_cuboid, [sx, sy, sz]
    )
    sim.setObjectPosition(handle, [px, py, pz], sim.handle_world)
    sim.setObjectColor(handle, 0, sim.colorcomponent_ambient_diffuse,
                       [0.25, 0.25, 0.28])
    sim.setObjectAlias(handle, "Floor")
    print(f"[OK] 地板(几何体) {sx}x{sy} @ ({px}, {py})")
    return handle


def create_warehouse(sim, name: str, params: dict,
                     parent_handle: int) -> int:
    """创建一个仓库并挂载到 Warehouse 父节点下。"""
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
    """创建一个工作岛（底座 + 标签柱），挂载到 Island 父节点下。"""
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
    sim.setObjectAlias(pillar, f"{alias}_Pillar")

    print(f"[OK] 工作岛 {name} → {alias} @ ({px:.1f}, {py:.1f})")
    return handle


def _strip_conveyor_scripts(sim, conveyor_handle: int):
    """移除内置传送带自带的 Lua 脚本子对象。

    CoppeliaSim 内置传送带模型含一个 Script 子对象，依赖
    conveyor_customization 模块；该模块在 4.10 仍用已弃用的 matrix
    模块，会在控制台刷 matrix/simEigen 警告。本项目的物料移动由
    Python 端按 start→end 插值定位，不需要传送带脚本，故移除以
    消除警告。移除的是脚本对象，传送带几何体保留不受影响。

    Args:
        conveyor_handle: 传送带模型根句柄.
    """
    try:
        tree = sim.getObjectsInTree(conveyor_handle, sim.handle_all, 0)
    except Exception:
        return
    for obj in tree:
        try:
            if sim.getObjectAlias(obj) == "Script":
                sim.removeObjects([obj])
        except Exception:
            continue


def create_conveyor(sim, name: str, params: dict,
                    parent_handle: int) -> int:
    """创建传送带 — 优先内置皮带传送带模型，回退到几何体。

    内置传送带默认沿 +X 方向。按 start→end 的角度旋转后放置于中点。
    若内置模型长度与实际距离不符，仍按中点对齐（视觉接近即可），
    物料移动仍由 Python 端按 start→end 线性插值定位。
    """
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

    # 尝试加载内置传送带模型
    model_rel = _resolve_model_path("conveyor_belt")
    belt_handle = None
    if model_rel and os.path.exists(os.path.join(cfg.MODEL_BASE, model_rel)):
        try:
            belt_handle = sim.loadModel(
                os.path.join(cfg.MODEL_BASE, model_rel)
            )
            sim.setObjectParent(belt_handle, parent_handle, True)
            # 重要: 先缩放后移动。scaleObjects 围绕世界原点缩放，
            # 若先移动到目标位置再缩放，位置会被等比放大漂移 (pos × scale)，
            # 导致传送带飞到远离工厂的位置无法可视化。
            try:
                # generic conveyor 默认长度约 1.0m，按实际长度缩放 X 轴
                default_len = 1.0
                scale_x = length / default_len
                sim.scaleObjects([belt_handle], scale_x, 1.0, 1.0, False)
            except Exception:
                pass
            sim.setObjectPosition(
                belt_handle, [mx, my, cfg.Z_BASE], sim.handle_world
            )
            sim.setObjectOrientation(
                belt_handle, [0, 0, angle], sim.handle_world
            )
            sim.setObjectAlias(belt_handle, alias)
            # 移除传送带自带 Lua 脚本: 物料移动由 Python 端控制，
            # 内置脚本依赖的 conveyor_customization 模块在 4.10 会
            # 发出 matrix/simEigen 弃用警告，且脚本无用，移除以消除警告。
            _strip_conveyor_scripts(sim, belt_handle)
            print(f"[OK] 传送带(内置) {name} → {alias}: "
                  f"({x1:.1f},{y1:.1f}) → ({x2:.1f},{y2:.1f}), L={length:.2f}m")
            return belt_handle
        except Exception as e:
            print(f"  [WARN] 内置传送带加载失败，回退到几何体: {e}")

    # 回退: 基本几何体传送带
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
        sim.setObjectPosition(roller, [-length / 2 + (0 if idx == 0 else length),
                                       0, -0.02], sim.handle_parent)
        sim.setObjectOrientation(roller, [0, math.pi / 2, 0], sim.handle_parent)
        sim.setObjectColor(roller, 0, sim.colorcomponent_ambient_diffuse, roller_color)
        sim.setObjectAlias(roller, f"{alias}_{suffix}")

    print(f"[OK] 传送带(几何体) {name} → {alias}: "
          f"({x1:.1f},{y1:.1f}) → ({x2:.1f},{y2:.1f}), L={length:.2f}m")
    return belt


def create_shared_agv(sim, parent_handle: int) -> int:
    """创建共享 AGV 小车 + 路径标记点，挂载到 Mobile 父节点下。

    与旧版不同：只创建一个 AGV（而非三个），通过调度器在运行时
    动态分配任务（B→A 或 D→E）。
    """
    sx, sy, sz = cfg.AGV_SIZE
    alias = cfg.AGV_ALIAS

    # 尝试加载内置移动机器人模型
    model_rel = _resolve_model_path("agv_pioneer")
    model_handle = load_builtin_model(
        sim, model_rel,
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

    # 为每条路径创建路点标记 (使用不可见 dummy)
    for route_key, waypoints in cfg.AGV_ROUTES.items():
        for i, (wx, wy) in enumerate(waypoints):
            # 路点使用 dummy 作为不可见的逻辑标记，而非可见几何体
            dot = sim.createDummy(0.05)
            sim.setObjectParent(dot, parent_handle, True)
            sim.setObjectPosition(dot, [wx, wy, cfg.Z_BASE + 0.05], sim.handle_world)
            if i == 0:
                dot_alias = f"{alias}_Waypoint_{route_key}_Start"
            elif i == len(waypoints) - 1:
                dot_alias = f"{alias}_Waypoint_{route_key}_End"
            else:
                dot_alias = f"{alias}_Waypoint_{route_key}_{i}"
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
    print("  (分层命名: Warehouse, Island, Conveyor, Mobile)")
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
        create_warehouse(sim, name, params, roots["Warehouse"])

    # 3. 工作岛 A, B, C, D, E + 各岛机械臂
    print("\n── 工作岛 ──")
    island_handles: dict[str, int] = {}
    for name, params in cfg.WORK_ISLANDS.items():
        island_handles[name] = create_work_island(sim, name, params, roots["Island"])

    print("\n── 工作岛机械臂 ──")
    for key, robot_cfg in cfg.ISLAND_ROBOTS.items():
        # E_C / E_D 都挂在 E 岛下
        island_name = key[0]  # 取首字符作为岛名
        island_handle = island_handles.get(island_name)
        island_pos = cfg.WORK_ISLANDS[island_name]["pos"]
        if island_handle is not None:
            create_robot_arm(
                sim, cfg.WORK_ISLANDS[island_name]["alias"],
                island_handle, list(island_pos), robot_cfg
            )

    # 4. 传送带
    print("\n── 传送带 ──")
    for name, params in cfg.CONVEYORS.items():
        create_conveyor(sim, name, params, roots["Conveyor"])

    # 5. 共享 AGV（单一小车，双路径）
    print("\n── 共享 AGV ──")
    create_shared_agv(sim, roots["Mobile"])

    # 提示用户
    print("\n" + "=" * 55)
    print("  场景构建完成！")
    print("  - 所有对象使用分层命名 (Warehouse, Island, ...)")
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
        clear_scene(sim)   # 幂等: 构建前清空，避免重复运行累积对象
        build_scene(sim)
    except Exception as e:
        print(f"\n[ERROR] 构建失败: {e}")
        raise
    finally:
        print("\n[DONE] 脚本结束 — 场景对象已持久化到 CoppeliaSim 中。")


if __name__ == "__main__":
    main()
