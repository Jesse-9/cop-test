"""
build_scene.py — 在 CoppeliaSim 中构建岛式车间工厂仿真场景
=========================================================
遵循 Warehouse, Island, Conveyor, Mobile 分层命名规范（下划线连接, 不用 '/'）。
优先使用 CoppeliaSim 内置模型，不存在时回退到基本几何体。

运行前请确保:
  1. CoppeliaSim 4.10+ 已启动
  2. ZMQ Remote API 服务端已激活（默认端口 23000）
  3. 场景尽量为空（可先 File > New Scene）

用法: uv run python src/build_scene.py [--host HOST] [--port PORT] [--arms A]
     或: uv run build-scene [--host HOST] [--port PORT] [--arms A]

--arms 用于逐台带动验收 (重构方案 §9.7 第 5 条): 只构建指定岛的机械臂,
如 `--arms A`, 确认后再逐台启用 B/C/D/E_C/E_D; 默认 all 构建全部六台。
"""

import argparse
import math
import os
import sys

from coppeliasim_zmqremoteapi_client import RemoteAPIClient

import factory_config as cfg
from factory_scene import (
    iter_by_type, model_local_bbox, shape_size, find_shapes_by_alias,
    find_script_child, measure_belt_surface_offset, measure_belt_top_run,
)
from factory_physics import (
    apply_conveyor_physics_props, apply_conveyor_physics_props_all_pads,
    apply_material_physics_props,
    find_belt_pad_shape, find_all_belt_pad_shapes,
    get_physics_engine_name, is_physics_engine_safe,
)


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
        sim.object_pointcloud_type, sim.object_forcesensor_type,
        sim.object_script_type
    ]
    # 需保留的对象别名/对象名集合 (CoppeliaSim 默认场景自带)
    keep_names = {"DefaultCamera",
                  "XYZCameraProxy", "XViewCamera", "YViewCamera", "ZViewCamera", "NXViewCamera", "NYViewCamera", "NZViewCamera",
                  "DefaultLights", "LightA", "LightB", "LightC", "LightD"}

    all_handles: set[int] = set()
    kept: set[str] = set()
    # 使用 factory_scene.iter_by_type 统一遍历逻辑 (消除 3 处重复之一)
    for h, alias in iter_by_type(sim, *type_list):
        # 读取对象名 (iter_by_type 不读 name, 因为部分对象无 name)
        try:
            name = sim.getObjectName(h)
        except Exception:
            name = ""
        if alias in keep_names or name in keep_names:
            kept.add(alias or name)
            print(f"[INFO] 保留默认对象: {alias or name} (handle={h})")
        else:
            all_handles.add(int(h))

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
        # 4.10 推荐 properties: getStringProperty(handle_app, 'productVersion')
        # 旧 getStringSignal("coppeliaSimVersion") 在部分实例返回 None, 仅作回退
        version = ""
        try:
            version = sim.getStringProperty(sim.handle_app, "productVersion") or ""
        except Exception:
            pass
        if not version:
            try:
                version = sim.getStringSignal("coppeliaSimVersion") or ""
            except Exception:
                version = ""
        if version:
            print(f"[OK] 已连接 CoppeliaSim {version}")
        else:
            print(f"[OK] 已连接 CoppeliaSim @ {host}:{port} (版本信号未设置)")
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

    ⚠ 4.10 实测: `setObjectParent` 会清掉被挂对象**自身**的 `static` 标志
    (见 CLAUDE.md「已知问题与坑」第 7 条)。UR5.ttm 的模型根是
    static+kinematic 的固定基座, 被挂到工作岛下后 static 变 0 → 整条臂成为
    动力学体, 仿真开始即自由落体。故这里挂父前记录根 shape 的 static 标志、
    挂父后写回。对本来就是 dynamic 的模型 (如 pioneer p3dx) 是空操作。

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
        # 挂父前记录根的 static 标志 (setObjectParent 会清掉它)。
        # 'static' 的 properties 路径在 4.10 不可用, 只能走 int-param
        # (见 factory_physics 实测记录)。
        root_static = None
        try:
            root_static = sim.getObjectInt32Param(
                handle, sim.shapeintparam_static
            )
        except Exception:
            pass
        sim.setObjectPosition(handle, position, sim.handle_world)
        if orientation is not None:
            sim.setObjectOrientation(handle, orientation, sim.handle_world)
        if parent_handle != -1:
            sim.setObjectParent(handle, parent_handle, True)
            if root_static:
                try:
                    sim.setObjectInt32Param(
                        handle, sim.shapeintparam_static, root_static
                    )
                except Exception as e:
                    print(f"  [WARN] {alias} 挂父后写回 static 失败: {e}")
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


# ═══════════════════════════════════════════════════════════════
# 几何测量 — Geometry Measurement Helpers (方案 §3.1 / §4.1)
# ═══════════════════════════════════════════════════════════════
# 这些 helper 是"从包围盒推导布局"的唯一入口。任何位置/高度都不允许
# 用常量假设计算 — 必须先量, 再放。

def arm_keys_to_build(only=None, robots: dict = None) -> list:
    """列出本次要构建的机械臂键 — Which island arms to build.

    重构方案 §9.7 第 5 条: 首次验收只启用 A 岛机械臂, 确认后再逐台启用
    B/C/D/E_C/E_D。故构建范围可收窄, 默认仍是全部。

    Args:
        only:   要构建的岛键序列 (如 ["A"] / ["A", "E_C"]); None 或空 → 全部。
        robots: 机械臂配置字典, 默认取 cfg.ISLAND_ROBOTS。

    Returns:
        按配置顺序排列的键列表; only 里的未定义键被忽略并告警, 不抛异常。
    """
    robots = cfg.ISLAND_ROBOTS if robots is None else robots
    if not only:
        return list(robots)
    wanted = list(dict.fromkeys(only))          # 去重保序
    unknown = [k for k in wanted if k not in robots]
    if unknown:
        print(f"  [WARN] 未定义的机械臂键 {unknown}, 已忽略 "
              f"(可用: {list(robots)})")
    return [k for k in robots if k in wanted]


def make_arm_kinematic(sim, arm_handle: int) -> dict:
    """把机械臂整条子树设为非动态 — Make the arm a pure kinematic mechanism.

    4.10 实测 (CLAUDE.md 坑 9): 本版 UR5.ttm 的连杆与夹爪碰撞体是
    `static=0 respondable=1`(动态启用)。关节转到 kinematic 后引擎不再为它们
    建立约束, 连杆失去支撑直接自由落体 (TCP 世界 Z 由 2.04 m 掉到 0.13 m),
    而**关节角读数仍等于设定值**。改成非动态后 kinematic 关节才能真正驱动它,
    且构建期/运行期两种切换时序都不再塌陷 (实测对照见计划文档 §9.2)。

    必须覆盖**整棵子树**: 只改 6 个连杆的话, 夹爪(根/手指/配重块)会作为
    无支撑的自由动力学体掉落。只改 `static`, `respondable` 保持不动
    (实测与非动态改造前一致, 不影响与动态物料的碰撞行为)。

    Args:
        sim:        CoppeliaSim Remote API "sim" 模块.
        arm_handle: 机械臂根对象句柄.

    Returns:
        {"total": 子树内 shape 总数, "changed": 本次改为非动态的个数}.
    """
    report = {"total": 0, "changed": 0}
    try:
        tree = sim.getObjectsInTree(arm_handle, sim.handle_all, 0)
    except Exception as e:
        print(f"  [WARN] 机械臂子树枚举失败, 跳过纯运动学改造: {e}")
        return report
    for h in tree:
        try:
            if sim.getObjectType(h) != sim.object_shape_type:
                continue
            report["total"] += 1
            if sim.getObjectInt32Param(h, sim.shapeintparam_static) == 0:
                sim.setObjectInt32Param(h, sim.shapeintparam_static, 1)
                report["changed"] += 1
        except Exception:
            continue
    return report


def create_robot_arm(sim, island_alias: str, island_handle: int,
                     island_pos: list, robot_cfg: dict) -> int | None:
    """为工作岛加载一只机械臂并挂载吸盘 — Load robot arm + suction tool.

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
    else:
        # 内置 UR5 自带 IK/控制脚本 → 剥离, 避免多臂脚本争用 ZMQ addOn
        # 导致 "abort execution Connectivity >> ZMQ remote API server@addOnScript"
        # 整场冻结 (CLAUDE.md §3)。动作由 Python IK 驱动, 无需模型脚本。
        _strip_model_scripts(sim, arm_handle)

    # 挂载吸盘 — Attach suction tool to arm tip
    suction_key = robot_cfg.get("suction") or robot_cfg.get("tool") \
        or robot_cfg.get("gripper")
    if suction_key:
        gripper_rel = _resolve_model_path(suction_key)
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
                sim.setObjectAlias(
                    grip_handle, getattr(cfg, "ARM_SUCTION_ALIAS", "Suction")
                )
                sim.setObjectPosition(
                    grip_handle, [0, 0, 0], sim.handle_parent
                )
                # 方案 §6.2/§6.3: 只有内置脚本提供公开 activate/deactivate 时
                # 才保留它; 实测 `suction pad.ttm` 没有公开函数, 且其
                # sysCall_sensing 每步枚举全场景并吸附**任意** respondable
                # 物体 (含传送带 pad) → 必须剥离, 改由 Python 显式管理吸附。
                if not getattr(cfg, "SUCTION_USE_BUILTIN_SCRIPT", False):
                    _strip_model_scripts(sim, grip_handle)
                    print("  [SUCTION] 剥离吸盘自带脚本 (无公开函数, 走 §6.3 回退)")
                else:
                    print("  [SUCTION] 保留吸盘内置脚本 (use_builtin_suction_script)")
                print(f"  [SUCTION] {gripper_rel} 挂载到末端")
            else:
                print(f"  [WARN] 未找到 {alias} 末端 link，跳过夹爪挂载")
        except Exception as e:
            print(f"  [WARN] 吸盘挂载失败 ({gripper_rel}): {e}")

    # 纯运动学改造 — 必须在挂好夹爪**之后**做: 夹爪的根/手指/配重块同样是
    # static=0 的动态体, 漏掉它们整只夹爪会掉落 (见 make_arm_kinematic 注释)。
    if cfg.ARM_CONTROL_MODE == "kinematic":
        rep = make_arm_kinematic(sim, arm_handle)
        print(f"  [KINEMATIC] {alias} 纯运动学化: "
              f"{rep['changed']}/{rep['total']} 个 shape 改为非动态")

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
    """创建一个仓库并挂载到 Warehouse 父节点下。

    `params["top_z"]` 是仓库**顶面**高度; 立方体中心由它减去高度推出
    (方案 §2.1: 构建函数统一接收顶面高度, 不假定模型原点在底面)。
    """
    px, py = params["pos"][0], params["pos"][1]
    sx, sy, sz = params["size"]
    r, g, b = params["color"]
    alias = params["alias"]
    center_z = params["top_z"] - sz / 2.0

    handle = sim.createPrimitiveShape(
        sim.primitiveshape_cuboid, [sx, sy, sz]
    )
    sim.setObjectParent(handle, parent_handle, True)
    sim.setObjectPosition(handle, [px, py, center_z], sim.handle_world)
    sim.setObjectColor(handle, 0, sim.colorcomponent_ambient_diffuse, [r, g, b])
    sim.setObjectAlias(handle, alias)
    print(f"[OK] 仓库 {name} → {alias} @ ({px:.1f}, {py:.1f}), "
          f"顶面 Z={params['top_z']:.3f}")
    return handle


def create_work_island(sim, name: str, params: dict,
                       parent_handle: int) -> int:
    """创建一个低矮工作岛平台，挂载到 Island 父节点下。

    `params["top_z"]` 是平台**顶面**高度 (方案 §2.1); 平台立方体的中心由
    top_z - size_z/2 推出, 使顶面正好落在台面高度上。不再使用高模块和
    Pillar 标签柱 (方案 §2.2)。
    """
    px, py = params["pos"][0], params["pos"][1]
    sx, sy, sz = params["size"]
    r, g, b = params["color"]
    alias = params["alias"]
    center_z = params["top_z"] - sz / 2.0

    # 主体
    handle = sim.createPrimitiveShape(
        sim.primitiveshape_cuboid, [sx, sy, sz]
    )
    sim.setObjectParent(handle, parent_handle, True)
    sim.setObjectPosition(handle, [px, py, center_z], sim.handle_world)
    sim.setObjectColor(handle, 0, sim.colorcomponent_ambient_diffuse, [r, g, b])
    sim.setObjectAlias(handle, alias)

    print(f"[OK] 低矮工作岛 {name} → {alias} @ ({px:.1f}, {py:.1f}), "
          f"台面 Z={params['top_z']:.3f}")
    return handle


def _strip_model_scripts(sim, model_handle: int):
    """移除内置模型自带的 Lua 脚本子对象 — Strip embedded scripts from a loaded model.

    CoppeliaSim 内置机械臂 (UR5/KUKA/...)、AGV (pioneer p3dx) 等模型自带
    定制脚本 (customization/child script), 它们在仿真启动时执行 IK/逆运动学
    或差速控制逻辑。本项目机械臂由 Python IK 驱动 (factory_robot.py),
    不需要这些脚本; 且 6 只机械臂 + 1 个 AGV 的脚本组合在 4.10 会互相争用
    ZMQ addOn, 导致启动约 3 帧处整场冻结并报
    "abort execution Connectivity >> ZMQ remote API server@addOnScript"
    (见 CLAUDE.md §3)。统一剥离脚本后, 仅保留几何体 + 关节, 仿真由 Python 步进。

    Args:
        model_handle: 模型根句柄.
    """
    try:
        tree = sim.getObjectsInTree(model_handle, sim.handle_all, 0)
    except Exception:
        return
    # 收集脚本句柄后再删 (边遍历边删会扰动 tree 迭代器)
    to_remove = []
    script_type = getattr(sim, "object_script_type", None)
    for obj in tree:
        try:
            ot = sim.getObjectType(obj)
            if script_type is not None and ot == script_type:
                to_remove.append(obj)
                continue
            alias = sim.getObjectAlias(obj)
            # 内置模型脚本别名常见: Script / customizationScript /
            # UR5 反向运动学脚本等; 兜底匹配包含 "script" 的别名 (不区分大小写)。
            if alias and alias.lower().find("script") >= 0:
                to_remove.append(obj)
        except Exception:
            continue
    for obj in to_remove:
        try:
            sim.removeObjects([obj])
        except Exception:
            continue
    if to_remove:
        print(f"    [STRIP] 移除 {len(to_remove)} 个脚本子对象")


def _create_belt_marker(sim, root: int, alias: str, local_x: float,
                        surface_off: float) -> int:
    """在传送带上创建一个不可见 dummy 标记 — Belt entry / pickup-stop marker.

    位置用**传送带局部坐标**: local_x 沿带长方向, Z 取实测带面高度。
    局部坐标而非世界坐标是关键 —— 传送带带转角时, 世界端点必须由模型的
    世界变换推出, 不能靠给局部 X 加增量 (方案 §4.2)。
    """
    handle = sim.createDummy(0.02)
    sim.setObjectParent(handle, root, True)
    sim.setObjectPosition(handle, [local_x, 0.0, surface_off], sim.handle_parent)
    sim.setObjectAlias(handle, alias)
    return int(handle)


def _create_pickup_stopper(sim, root: int, alias: str, local_x: float,
                           surface_off: float,
                           belt_width: float) -> int:
    """在带尾创建 static+respondable 挡停体 — PickupStopper (方案 §5.3).

    它的作用是让物料"自然停住"而不是靠 Python 伪造。挡停体做成 static 且
    respondable, 只挡不推, 因此不会干扰 pad 的摩擦输送。
    """
    thk, _, height = cfg.PICKUP_STOPPER_SIZE
    handle = sim.createPrimitiveShape(
        sim.primitiveshape_cuboid, [thk, belt_width, height]
    )
    sim.setObjectParent(handle, root, True)
    sim.setObjectPosition(
        handle, [local_x, 0.0, surface_off + height / 2.0], sim.handle_parent
    )
    try:
        sim.setObjectColor(handle, 0, sim.colorcomponent_ambient_diffuse,
                           list(cfg.PICKUP_STOPPER_COLOR))
    except Exception:
        pass
    try:
        sim.setObjectInt32Param(handle, sim.shapeintparam_static, 1)
        sim.setObjectInt32Param(handle, sim.shapeintparam_respondable, 1)
    except Exception:
        pass
    try:
        sim.setObjectAlias(handle, f"{alias}_PickupStopper")
    except Exception:
        pass
    return int(handle)


def create_conveyor(sim, name: str, params: dict,
                    parent_handle: int) -> int:
    """创建传送带 — 优先内置皮带传送带模型，回退到几何体。

    几何 (方案 §4.1/§4.2):
      1. 加载内置模型, 读**实测** native 包围盒 → native_length
      2. scale = 设计长度 / native_length, 缩放后**重读**包围盒确认实际长度
      3. 按实测带面高度反推模型根 Z, 使带面 == cfg.CONVEYOR_SURFACE_Z
      4. 由实测几何创建 entry / pickup_stop dummy 并写回运行期元数据

    ⚠ 4.10 的 `sim.scaleObjects(handles, float, scalePositionsToo)` **只能等比
    缩放** (实测传 (2,1,1) 结果三轴同时 ×2, 与官方手册签名一致)。故传送带只能
    整体等比放大到设计长度 — 宽度和高度会随之等比变化, 这是 4.10 的 API 约束,
    运行时一律以实测值 (actual_length / width) 为准, 不再假设配置宽度。

    驱动 (方案 §5.1): 保留内置 conveyor 脚本作为**唯一** pad 写入者;
    Python 只通过 `customData.__ctrl__` 调速, 见 factory_physics。
    是否真的由脚本驱动, 由 factory_controller 的启动探测最终判定。
    """
    x1, y1 = params["start"]
    x2, y2 = params["end"]
    r, g, b = params["color"]
    alias = params["alias"]

    dx = x2 - x1
    dy = y2 - y1
    length = math.hypot(dx, dy)
    angle = math.atan2(dy, dx)
    mx, my = (x1 + x2) / 2, (y1 + y2) / 2
    dir_x = (dx / length) if length > 0 else 1.0
    dir_y = (dy / length) if length > 0 else 0.0

    # ── 运行期元数据骨架 (方案 §4.2) —— 全部先置空, 由实测填充 ──
    params.update({
        "dir": [dir_x, dir_y],
        "length": length,             # 设计长度
        "actual_length": None,        # 实测长度
        "actual_start": None,         # 入口世界坐标
        "actual_end": None,           # 末端世界坐标
        "surface_z": None,            # 带面世界 Z
        "width": None,                # 实测带宽
        "entry_handle": -1,
        "stop_handle": -1,
        "stopper_handle": -1,
        "script_handle": -1,
        "drive_mode": cfg.CONVEYOR_DRIVE_FALLBACK,
        "handle": -1,
    })

    belt_handle: int = -1
    model_rel = _resolve_model_path("conveyor_belt")
    if model_rel and os.path.exists(os.path.join(cfg.MODEL_BASE, model_rel)):
        try:
            belt_handle = sim.loadModel(
                os.path.join(cfg.MODEL_BASE, model_rel)
            )
            sim.setObjectParent(belt_handle, parent_handle, True)

            # ── 1. 实测 native 尺寸, 等比缩放到设计长度 (方案 §4.1) ──
            native = model_local_bbox(sim, belt_handle)
            if native is None:
                raise RuntimeError("无法读取模型包围盒 (objfloatparam_modelbbox_*)")
            native_len = native[3] - native[0]
            if native_len <= 1e-6:
                raise RuntimeError(f"模型 native 长度异常: {native_len}")
            scale = length / native_len
            # scalePositionsToo=True: 子对象的相对位置随之缩放, 带体才真正变长。
            # 模型此时在原点附近, 缩放位置后再显式 setObjectPosition 定位。
            sim.scaleObjects([belt_handle], scale, True)
            sim.setObjectPosition(belt_handle, [0.0, 0.0, 0.0], sim.handle_world)
            sim.setObjectOrientation(belt_handle, [0.0, 0.0, 0.0], sim.handle_world)

            # ── 2. 缩放后重读包围盒 (方案 §4.1 第 2 段) ──
            scaled = model_local_bbox(sim, belt_handle)
            actual_len = (scaled[3] - scaled[0]) if scaled else length
            actual_width = (scaled[4] - scaled[1]) if scaled else params["width"]

            # ── 3. 实测带面高度 + **上表面行程** (方案 §3.1 第 2 条 / §4.2) ──
            # 入口/停止区必须落在真实的上表面行程内, 不能用模型包围盒半长 ——
            # 包围盒两端是回转滚筒, 物料落在那里会直接掉到地面 (实测)。
            pads = find_shapes_by_alias(sim, belt_handle, "pad")
            top_run = measure_belt_top_run(sim, belt_handle, pads)
            surface_off = measure_belt_surface_offset(sim, belt_handle, pads)
            if surface_off is None:
                surface_off = 0.0
                print(f"  [WARN] {alias}: 带面高度实测失败, 按模型根高度定位")
            root_z = cfg.CONVEYOR_SURFACE_Z - surface_off
            sim.setObjectPosition(belt_handle, [mx, my, root_z], sim.handle_world)
            sim.setObjectOrientation(belt_handle, [0.0, 0.0, angle], sim.handle_world)
            sim.setObjectAlias(belt_handle, alias)

            params["actual_length"] = actual_len
            params["width"] = actual_width
            params["surface_z"] = cfg.CONVEYOR_SURFACE_Z
            params["handle"] = belt_handle
            params["script_handle"] = find_script_child(sim, belt_handle)

            # ── 4. 由实测上表面行程创建入口 / 停止区 dummy (方案 §4.2) ──
            if top_run is None:
                # 没有可识别的上表面 pad → 退回包围盒推算, 并明确告警
                print(f"  [WARN] {alias}: 上表面 pad 实测失败, "
                      f"入口/停止区按包围盒估算 (物料可能落在滚筒外)")
                run_min, run_max = -actual_len / 2.0, actual_len / 2.0
            else:
                run_min, run_max = top_run["min_x"], top_run["max_x"]
                params["top_run"] = [run_min, run_max]
            entry_local_x = min(run_min + cfg.CONVEYOR_ENTRY_INSET,
                                run_max - 0.02)
            stop_local_x = max(run_max - cfg.CONVEYOR_STOP_INSET,
                               entry_local_x + 0.02)
            params["entry_handle"] = _create_belt_marker(
                sim, belt_handle, f"{alias}_Entry",
                entry_local_x, surface_off
            )
            params["stop_handle"] = _create_belt_marker(
                sim, belt_handle, f"{alias}_PickupStop",
                stop_local_x, surface_off
            )
            params["actual_start"] = list(
                sim.getObjectPosition(params["entry_handle"], sim.handle_world)
            )
            params["actual_end"] = list(
                sim.getObjectPosition(params["stop_handle"], sim.handle_world)
            )
            params["travel_length"] = stop_local_x - entry_local_x

            # ── 5. 挡停体 (方案 §5.3 第 2 条) ──
            if cfg.PICKUP_USE_STOPPER:
                params["stopper_handle"] = _create_pickup_stopper(
                    sim, belt_handle, alias,
                    run_max + cfg.PICKUP_STOPPER_SIZE[0] / 2.0,
                    surface_off, actual_width
                )

            # ── 6. 驱动模式初判: 有脚本 + 有 pad ⇒ 候选脚本驱动 ──
            # 真正结论由 factory_controller 的启动探测给出 (§5.1 PROBING)。
            if params["script_handle"] >= 0 and pads:
                params["drive_mode"] = cfg.CONVEYOR_DRIVE_SCRIPT
            elif pads:
                params["drive_mode"] = cfg.CONVEYOR_DRIVE_PYTHON
            else:
                params["drive_mode"] = cfg.CONVEYOR_DRIVE_FALLBACK

            print(f"[OK] 传送带(内置) {name} → {alias}: "
                  f"设计 L={length:.3f}m / 实测 L={actual_len:.3f}m "
                  f"(native {native_len:.3f}m, scale {scale:.3f}), "
                  f"宽 {actual_width:.3f}m, 带面 Z={cfg.CONVEYOR_SURFACE_Z:.3f}, "
                  f"pad={len(pads)}, 驱动={params['drive_mode']}")
        except Exception as e:
            print(f"  [WARN] 内置传送带加载失败，回退到几何体: {e}")
            if belt_handle != -1:
                try:
                    sim.removeObjects([belt_handle])
                except Exception:
                    pass
            belt_handle = -1

    # 回退: 基本几何体传送带
    if belt_handle == -1:
        params["drive_mode"] = cfg.CONVEYOR_DRIVE_FALLBACK
        thickness = 0.06
        belt_handle = sim.createPrimitiveShape(
            sim.primitiveshape_cuboid, [length, params["width"], thickness],
        )
        sim.setObjectParent(belt_handle, parent_handle, True)
        # 几何体传送带: 让**上表面**落在 CONVEYOR_SURFACE_Z
        sim.setObjectPosition(
            belt_handle, [mx, my, cfg.CONVEYOR_SURFACE_Z - thickness / 2.0],
            sim.handle_world
        )
        sim.setObjectOrientation(belt_handle, [0.0, 0.0, angle], sim.handle_world)
        sim.setObjectColor(belt_handle, 0, sim.colorcomponent_ambient_diffuse, [r, g, b])
        sim.setObjectAlias(belt_handle, alias)

        # 两端滚轮标记
        roller_color = [0.35, 0.35, 0.35]
        for idx, (rx, ry) in enumerate([(x1, y1), (x2, y2)]):
            suffix = "RollerStart" if idx == 0 else "RollerEnd"
            roller = sim.createPrimitiveShape(
                sim.primitiveshape_cylinder, [0.08, 0.08, params["width"] + 0.04],
            )
            sim.setObjectParent(roller, belt_handle, True)
            sim.setObjectPosition(roller, [-length / 2 + (0 if idx == 0 else length),
                                           0, -0.02], sim.handle_parent)
            sim.setObjectOrientation(roller, [0, math.pi / 2, 0], sim.handle_parent)
            sim.setObjectColor(roller, 0, sim.colorcomponent_ambient_diffuse, roller_color)
            sim.setObjectAlias(roller, f"{alias}_{suffix}")

        params["actual_length"] = length
        params["width"] = params["width"]
        params["surface_z"] = cfg.CONVEYOR_SURFACE_Z
        params["handle"] = belt_handle
        params["entry_handle"] = _create_belt_marker(
            sim, belt_handle, f"{alias}_Entry",
            -length / 2.0 + cfg.CONVEYOR_ENTRY_INSET, -thickness / 2.0
        )
        params["stop_handle"] = _create_belt_marker(
            sim, belt_handle, f"{alias}_PickupStop",
            length / 2.0 - cfg.CONVEYOR_STOP_INSET, -thickness / 2.0
        )
        params["actual_start"] = list(
            sim.getObjectPosition(params["entry_handle"], sim.handle_world)
        )
        params["actual_end"] = list(
            sim.getObjectPosition(params["stop_handle"], sim.handle_world)
        )
        if cfg.PICKUP_USE_STOPPER:
            params["stopper_handle"] = _create_pickup_stopper(
                sim, belt_handle, alias,
                length / 2.0 - cfg.PICKUP_STOPPER_SIZE[0] / 2.0,
                -thickness / 2.0, params["width"]
            )

        print(f"[OK] 传送带(几何体) {name} → {alias}: "
              f"L={length:.2f}m, 带面 Z={cfg.CONVEYOR_SURFACE_Z:.3f}")

    # 物理模式: 让带面能承载 dynamic 物料 (脚本驱动时同样需要)
    if cfg.PHYSICS_CONVEYOR and belt_handle > 0:
        try:
            engine = get_physics_engine_name(sim)
        except Exception:
            engine = "unknown"
        pads = find_all_belt_pad_shapes(sim, belt_handle)
        if pads:
            ok = apply_conveyor_physics_props_all_pads(
                sim, belt_handle, cfg.CONVEYOR_SPEED, engine, None
            )
            print(f"  [PHYSICS] {alias}: {len(pads)} 个 pad "
                  f"static+kinematic+respondable "
                  f"({'OK' if ok else '部分失败'}, engine={engine})")
        else:
            ok = apply_conveyor_physics_props(
                sim, belt_handle, cfg.CONVEYOR_SPEED, engine, None
            )
            if not ok:
                print(f"  [WARN] {alias} 物理属性设置失败")

    return belt_handle


def create_shared_agv(sim, parent_handle: int) -> int:
    """创建共享 AGV 小车 + 路径标记点，挂载到 Mobile 父节点下。

    Z 基准 = 地面 (方案 §2.2: AGV 以地面和自身承载面为基准, 不复用平台或
    传送带的 Z 偏移)。模型根高度由实测包围盒推出, 使车体最低点贴地。
    """
    sx, sy, sz = cfg.AGV_SIZE
    alias = cfg.AGV_ALIAS
    start_xy = (-2.5, -1.0)      # 初始停在 B 岛附近

    # 尝试加载内置移动机器人模型
    model_rel = _resolve_model_path("agv_pioneer")
    model_handle = load_builtin_model(
        sim, model_rel,
        [start_xy[0], start_xy[1], cfg.AGV_DECK_Z], alias, parent_handle
    )

    if model_handle is not None:
        agv_handle = model_handle
        # pioneer p3dx 自带差速驱动脚本 → 剥离, AGV 位置/朝向由 Python 端
        # factory_controller._update_scene_positions 直接 setObjectPosition 控制,
        # 无需脚本; 同样规避 addOn 脚本争用导致的冻结。
        _strip_model_scripts(sim, agv_handle)
        # 按实测包围盒把车体贴到地面 (方案 §3.1 第 6 条)
        bbox = model_local_bbox(sim, agv_handle)
        if bbox is not None:
            root_z = cfg.FLOOR_TOP_Z - bbox[2]
            sim.setObjectPosition(
                agv_handle, [start_xy[0], start_xy[1], root_z], sim.handle_world
            )
            print(f"  [AGV] 依包围盒贴地: min_z={bbox[2]:.3f} → 根 Z={root_z:.3f}")
    else:
        # 回退：基本几何体 AGV
        agv_handle = sim.createPrimitiveShape(
            sim.primitiveshape_cuboid, [sx, sy, sz]
        )
        sim.setObjectParent(agv_handle, parent_handle, True)
        sim.setObjectPosition(
            agv_handle,
            [start_xy[0], start_xy[1], cfg.FLOOR_TOP_Z + sz / 2.0],
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
            sim.setObjectPosition(dot, [wx, wy, cfg.AGV_DECK_Z], sim.handle_world)
            if i == 0:
                dot_alias = f"{alias}_Waypoint_{route_key}_Start"
            elif i == len(waypoints) - 1:
                dot_alias = f"{alias}_Waypoint_{route_key}_End"
            else:
                dot_alias = f"{alias}_Waypoint_{route_key}_{i}"
            sim.setObjectAlias(dot, dot_alias)

    print(f"[OK] 共享 AGV → {alias} (B→A + D→E 双路径), 承载面 Z={cfg.AGV_DECK_Z:.3f}")
    return agv_handle


# ═══════════════════════════════════════════════════════════════
# 构建期几何断言 — Build-time Layout Assertions (方案 §3.2)
# ═══════════════════════════════════════════════════════════════

def verify_layout(sim) -> dict:
    """构建后只读校验几何关系 — Read-only layout verification.

    只检查, **不修改**任何对象 (方案 §3.2: "断言只验证几何关系, 不在构建期
    偷偷移动对象")。失败时打印对象别名、实测值和期望基准, 返回断言结果字典。

    校验项:
      abs(conveyor_surface_z - configured_surface_z) < z_tolerance
      abs(material_center_z - conveyor_surface_z - material_height/2) < tol
      island_top_z >= configured_top_z - tolerance
      arm_base_z    >= island_top_z - base_tolerance
    """
    tol = cfg.Z_TOLERANCE
    base_tolerance = 0.05  # 5 cm 机械臂基座高度容差
    results: dict = {"ok": True, "checks": []}

    def check(name, actual, expected, ok, detail=""):
        results["checks"].append(
            {"name": name, "actual": actual, "expected": expected, "ok": bool(ok)}
        )
        if not ok:
            results["ok"] = False
            print(f"  [ASSERT-FAIL] {name}: 实测={actual} 期望={expected} {detail}")
        return ok

    # ── 1. 传送带带面高度 & 实际长度 ──
    for cname, c in cfg.CONVEYORS.items():
        surf = c.get("surface_z")
        if surf is None:
            check(f"conveyor[{cname}].surface_z", None, cfg.CONVEYOR_SURFACE_Z,
                  False, "元数据缺失 — 未用内置模型?")
            continue
        check(f"conveyor[{cname}].surface_z", round(surf, 4),
              cfg.CONVEYOR_SURFACE_Z,
              abs(surf - cfg.CONVEYOR_SURFACE_Z) < tol, f"alias={c['alias']}")
        alen = c.get("actual_length")
        check(f"conveyor[{cname}].actual_length", round(alen, 4) if alen else None,
              round(c["length"], 4),
              alen is not None and abs(alen - c["length"]) < max(tol, 0.05),
              f"alias={c['alias']} 设计长度={c['length']:.3f}")

    # ── 2. 物料几何中心 ──
    mat_center = cfg.CONVEYOR_SURFACE_Z + cfg.MATERIAL_SIZE[2] / 2.0
    check("material_center_z", round(cfg.MATERIAL_CENTER_Z, 4),
          round(mat_center, 4), abs(cfg.MATERIAL_CENTER_Z - mat_center) < tol)

    # ── 3. 工作岛台面 ──
    island_tops: dict[str, float] = {}
    for iname, i in cfg.WORK_ISLANDS.items():
        h = _find_scene_object(sim, i["alias"])
        if h < 0:
            check(f"island[{iname}].top_z", None, i["top_z"], False,
                  f"场景中找不到 {i['alias']}")
            continue
        size = shape_size(sim, h)
        pos = sim.getObjectPosition(h, sim.handle_world)
        top = pos[2] + (size[2] / 2.0 if size else 0.0)
        island_tops[iname] = top
        check(f"island[{iname}].top_z", round(top, 4), round(i["top_z"], 4),
              top >= i["top_z"] - tol, f"alias={i['alias']}")

    # ── 4. 机械臂基座不低于台面 ──
    for key, rc in cfg.ISLAND_ROBOTS.items():
        h = _find_scene_object(sim, rc["alias"])
        if h < 0:
            # 句柄找不到 → 直接标记失败，避免继续用脏数据
            check(f"arm[{key}].base_z", float("nan"), rc["alias"],
                  False, f"未找到句柄 alias={rc['alias']}")
            continue
        pos = sim.getObjectPosition(h, sim.handle_world)
        iname = key[0]
        top = island_tops.get(iname)
        if top is None:
            continue
        check(f"arm[{key}].base_z", round(pos[2], 4), f">= {round(top, 4)}",
              pos[2] >= top - base_tolerance, f"alias={rc['alias']}")

    # ── 5. 取料停止区不低于带面 ──
    for cname, c in cfg.CONVEYORS.items():
        stop = c.get("actual_end")
        if stop is None:
            continue
        check(f"conveyor[{cname}].pickup_stop_z", round(stop[2], 4),
              f">= {cfg.CONVEYOR_SURFACE_Z}", stop[2] >= cfg.CONVEYOR_SURFACE_Z - tol)

    # ── 6. 机械臂能否够到本岛来料的取料点 (方案 §2.2) ──
    # 实测教训: offset=[0,0.35,0.45] 时 A/B/D 的底座到取料点 0.89~0.97 m,
    # 超出 UR5 工作半径 → simIK 主组与阻尼组必然失败, 吸盘永远取不到料。
    # 这条断言让"够不着"在构建期就暴露, 而不是等仿真里刷 IK 失败日志。
    arm_reach_limit = cfg.ARM_REACH_RADIUS - cfg.ARM_REACH_MARGIN
    inbound = {"S1_to_A": "A", "S1_to_B": "B", "S2_to_D": "D", "C_to_E": "E"}
    for cname, island_name in inbound.items():
        stop = cfg.CONVEYORS[cname].get("actual_end")
        if stop is None or island_name not in cfg.WORK_ISLANDS:
            continue
        arm_key = island_name if island_name in cfg.ISLAND_ROBOTS else "E_C"
        rc = cfg.ISLAND_ROBOTS.get(arm_key)
        if rc is None:
            continue
        ox, oy, oz = rc["offset"]
        base = [cfg.WORK_ISLANDS[island_name]["pos"][0] + ox,
                cfg.WORK_ISLANDS[island_name]["pos"][1] + oy,
                cfg.island_center_z(island_name) + oz]
        dist = math.dist(base, stop[:3])
        check(f"arm[{arm_key}].reach_to({cname})", round(dist, 4),
              f"<= {arm_reach_limit:.3f}", dist <= arm_reach_limit,
              f"底座={[round(v, 3) for v in base]} 取料点={[round(v, 3) for v in stop[:3]]}")

    print(f"\n[LAYOUT] 断言 {'全部通过 ✓' if results['ok'] else '存在失败 ✗'} "
          f"({sum(1 for c in results['checks'] if c['ok'])}/"
          f"{len(results['checks'])} 项)")
    return results


def _find_scene_object(sim, alias: str) -> int:
    """按别名取句柄 (带 '/' 前缀优先, 兼容别名路径规则)。"""
    for candidate in ("/" + alias, alias):
        try:
            return int(sim.getObject(candidate))
        except Exception:
            continue
    return -1


# ═══════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════

def build_scene(sim, arm_islands=None):
    """按拓扑顺序构建完整岛式车间工厂场景。

    Args:
        sim:          CoppeliaSim Remote API "sim" 模块 (已连接)。
        arm_islands:  只构建这些岛的机械臂 (如 ["A"]); None = 全部 (默认)。
                      用于重构方案 §9.7 第 5 条的逐台带动验收。
    """
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
    arm_keys = arm_keys_to_build(arm_islands)
    if len(arm_keys) != len(cfg.ISLAND_ROBOTS):
        print(f"  [SCOPE] 本次只构建机械臂: {arm_keys} "
              f"(共 {len(cfg.ISLAND_ROBOTS)} 台, 其余跳过)")
    for key in arm_keys:
        robot_cfg = cfg.ISLAND_ROBOTS[key]
        # E_C / E_D 都挂在 E 岛下
        island_name = key[0]  # 取首字符作为岛名
        island_handle = island_handles.get(island_name)
        island_pos = cfg.island_pos3(island_name)
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

    # 6. 物理引擎提示 — 警告 MuJoCo (physicsEngineDifferences.htm:39)
    if cfg.PHYSICS_CONVEYOR:
        try:
            engine = get_physics_engine_name(sim)
            if not is_physics_engine_safe(engine):
                print(f"  [WARN] 当前物理引擎 = {engine}; "
                      "kinematic+static 表面在 MuJoCo 下走 mocap+weld 路径, "
                      "如出现性能问题请切换 Bullet/ODE/Newton")
            else:
                print(f"  [PHYSICS] 当前物理引擎 = {engine} OK (适合 kinematic+static 表面)")
        except Exception as e:
            print(f"  [WARN] 读取物理引擎失败: {e}")

    # 7. 构建期几何断言 (方案 §3.2) — 只读校验, 失败只报告不修正
    print("\n── 布局断言 ──")
    verify_layout(sim)

    # 提示用户
    print("\n" + "=" * 55)
    print("  场景构建完成！")
    print("  - 所有对象使用分层命名 (Warehouse, Island, ...)")
    print("  - 单一共享 AGV 覆盖 B→A 和 D→E 双路径")
    print("  - 布局基准: 地面 %.3f / 台面 %.3f / 带面 %.3f"
          % (cfg.FLOOR_TOP_Z, cfg.ISLAND_TOP_Z, cfg.CONVEYOR_SURFACE_Z))
    print("  - 可在 CoppeliaSim 中保存为 factory-sim.ttt")
    print("  - 运行 run_simulation.py 开始柔性仿真控制")
    print("=" * 55)


def main():
    parser = argparse.ArgumentParser(description="构建岛式车间工厂仿真场景")
    parser.add_argument("--host", default="localhost", help="CoppeliaSim 主机地址")
    parser.add_argument("--port", type=int, default=23000, help="ZMQ Remote API 端口")
    parser.add_argument(
        "--arms", default="all",
        help="只构建指定岛的机械臂 (逗号分隔, 如 A 或 A,E_C); all=全部 (默认)",
    )
    args = parser.parse_args()

    arm_islands = None
    if args.arms.strip().lower() != "all":
        arm_islands = [k.strip() for k in args.arms.split(",") if k.strip()]

    client, sim = connect(args.host, args.port)
    try:
        clear_scene(sim)   # 幂等: 构建前清空，避免重复运行累积对象
        build_scene(sim, arm_islands=arm_islands)
    except Exception as e:
        print(f"\n[ERROR] 构建失败: {e}")
        raise
    finally:
        print("\n[DONE] 脚本结束 — 场景对象已持久化到 CoppeliaSim 中。")


if __name__ == "__main__":
    main()
