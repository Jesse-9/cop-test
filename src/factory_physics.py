"""
factory_physics.py — 物理属性应用工具 (4.10 properties + legacy int-param 兼容)
==============================================================================
CoppeliaSim 4.10 推荐 properties 体系 (sim.setBoolProperty/setFloatProperty/...)。
旧 setObjectInt32Param + shapeintparam_* 常量仍可用, 但官方建议迁移
(docs/wiki/01-API参考/01-03-常量与参数ID.md §六/§七)。
本模块所有 setter 都先尝试 properties, 失败时回退到 int-param — 这样 4.10
不同版本 (properties 字段可能仍在演进) 都能跑。

⚠ 实测发现 (2026-07-31):
  1. 'static' property 失败, 必须用 setObjectInt32Param(shapeintparam_static)
  2. 'kinematic' / 'respondable' / 'startInDynSleepMode' 走 properties 可用
  3. **surface velocity 在 4.10 不可用** — setVector3Property 对
     bullet.surfaceVelocity / ode.surfaceVelocity 等全部抛 "property is unknown"
     (与官方手册 propertiesReference.htm 描述不符, 可能需 4.11+)
  4. 故当前物理传送带 = static+kinematic+respondable 表面, 由 Python 端
     在 _move_conveyors_physics 中每步对所有 pad 调 setObjectPosition 推进
     (physicsEngineDifferences.htm:38: 此类表面所有引擎均通过摩擦带动上方 dynamic 物料)

公开函数:
  apply_material_physics_props(sim, handle, logger=None)
  apply_conveyor_physics_props(sim, handle, speed, engine, logger=None)
  get_physics_engine_name(sim)   → "bullet"/"ode"/"newton"/"vortex"/"mujoco"/"unknown"
  is_physics_engine_safe(engine) → False 仅当 mujoco (走 kinematic 路径仍 OK)
  find_belt_pad_shape(sim, h)    → 单个 pad (旧 API, 保留)
  find_all_belt_pad_shapes(sim, h) → 所有 pad 列表 (新 API)
  apply_conveyor_physics_props_all_pads(sim, h, ...) → 对所有 pad 一次设属性
"""

from __future__ import annotations

import logging
import math
from typing import Optional


# ═══════════════════════════════════════════════════════════════
# 内部通用 setter — properties 优先, int-param 兜底
# ═══════════════════════════════════════════════════════════════

def _try_set_bool_property(sim, handle: int, name: str, value: bool) -> bool:
    """优先 sim.setBoolProperty, 失败时回退 setObjectInt32Param。
    ⚠ 'static' 实测 properties 路径失败, 必须走 int-param; 这里强制走 int-param。
    """
    # static 必须 int-param
    if name == "static":
        return _legacy_set_int_param(sim, handle,
                                     getattr(sim, "shapeintparam_static", None),
                                     1 if value else 0)
    try:
        sim.setBoolProperty(handle, name, value)
        return True
    except Exception:
        pass
    # 旧 int-param 回退
    legacy_map = {
        "kinematic":            getattr(sim, "shapeintparam_kinematic", None),
        "respondable":          getattr(sim, "shapeintparam_respondable", None),
        "startInDynSleepMode":  getattr(sim, "shapeintparam_sleepmodestart", None),
    }
    int_param = legacy_map.get(name)
    if int_param is None:
        return False
    return _legacy_set_int_param(sim, handle, int_param, 1 if value else 0)


def _try_set_float_property(sim, handle: int, name: str, value: float) -> bool:
    """优先 sim.setFloatProperty, 失败时回退 setObjectFloatParam。"""
    try:
        sim.setFloatProperty(handle, name, value)
        return True
    except Exception:
        pass
    legacy_map = {
        "mass": getattr(sim, "shapefloatparam_mass", None),
    }
    int_param = legacy_map.get(name)
    if int_param is None:
        return False
    try:
        sim.setObjectFloatParam(handle, int_param, value)
        return True
    except Exception:
        return False


def _try_set_engine_friction(sim, handle: int, value: float,
                             logger: Optional[logging.Logger] = None) -> str | None:
    """依次尝试 cfg.FRICTION_PROPS 中的引擎特定 friction 属性。
    返回成功生效的属性名, 全部失败返回 None (不致命, 引擎用默认摩擦)。"""
    from factory_config import FRICTION_PROPS
    for prop in FRICTION_PROPS:
        try:
            sim.setFloatProperty(handle, prop, value)
            if logger:
                logger.debug(f"  friction: {prop} = {value} ✓")
            return prop
        except Exception:
            continue
    if logger:
        logger.debug(f"  friction: 全部引擎属性写入失败, 使用引擎默认")
    return None


def _try_set_respondable_mask(sim, handle: int, packed: int) -> bool:
    """设置 respondable_mask (int32 = global<<8 | local)。"""
    # 4.10 properties 路径 — 实测 setIntProperty('respondableMask', packed) 可用
    try:
        sim.setIntProperty(handle, "respondableMask", packed)
        return True
    except Exception:
        pass
    # 旧 int-param 回退
    int_param = getattr(sim, "shapeintparam_respondable_mask", None)
    if int_param is not None:
        return _legacy_set_int_param(sim, handle, int_param, packed)
    return False


def _legacy_set_int_param(sim, handle: int, param_id, value: int) -> bool:
    """包装 setObjectInt32Param, 失败时返回 False。"""
    if param_id is None or handle is None or handle < 0:
        return False
    try:
        sim.setObjectInt32Param(handle, param_id, int(value))
        return True
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════
# 公开 API
# ═══════════════════════════════════════════════════════════════

def apply_material_physics_props(sim, handle: int,
                                 logger: Optional[logging.Logger] = None) -> bool:
    """把物料 shape 设为 dynamic+respondable, 设 mass / friction / mask。

    兼容内置模型 (UR5/AGV/传送带) 的子 shape, 也兼容 createPrimitiveShape
    创建的物料。4.10 实测: 'static'/'respondable'/'startInDynSleepMode' 可
    走 properties; 'static' 必须 int-param (这里内部已处理)。

    Returns:
        True 表示核心属性 (dynamic via static=0 / respondable / mass)
        至少有一半以上生效; False 表示全部失败 (4.10 API 异常, 走 kinematic 模式).
    """
    if handle is None or handle < 0:
        return False
    from factory_config import (
        MATERIAL_MASS, MATERIAL_FRICTION, MATERIAL_START_IN_SLEEP,
        MATERIAL_RESPONDABLE_MASK_PACKED,
    )
    ok = []
    # 1) dynamic → 内部映射为 static=0 (Bool property 不可用, int-param 走)
    if _try_set_bool_property(sim, handle, "static", False):
        ok.append("dynamic(=static=0)")
    # 2) respondable=True
    if _try_set_bool_property(sim, handle, "respondable", True):
        ok.append("respondable")
    # 3) kinematic=False (与 dynamic 不冲突)
    _try_set_bool_property(sim, handle, "kinematic", False)
    # 4) startInDynSleepMode
    _try_set_bool_property(sim, handle, "startInDynSleepMode",
                           bool(MATERIAL_START_IN_SLEEP))
    # 5) mass
    if _try_set_float_property(sim, handle, "mass", float(MATERIAL_MASS)):
        ok.append(f"mass={MATERIAL_MASS}")
    # 6) friction (按引擎; 失败不致命, 引擎用默认)
    _try_set_engine_friction(sim, handle, float(MATERIAL_FRICTION), logger)
    # 7) respondable_mask
    if _try_set_respondable_mask(sim, handle, MATERIAL_RESPONDABLE_MASK_PACKED):
        ok.append("mask")
    # 8) 计算惯量 (按密度; 4.10 推荐 computeMassAndInertia 自动算)
    try:
        sim.computeMassAndInertia(handle, 1000.0)  # 1000 kg/m³ 近似塑料/轻金属
    except Exception:
        pass
    # 9) 通知引擎重置该 shape 的动力学表示
    try:
        sim.resetDynamicObject(handle)
    except Exception:
        pass

    if logger and len(ok) >= 2:
        logger.debug(f"  物料物理属性 OK: {ok}")
    return len(ok) >= 2


def apply_conveyor_physics_props(sim, handle: int,
                                 speed: float = 0.0,
                                 engine: str = "unknown",
                                 logger: Optional[logging.Logger] = None) -> bool:
    """把传送带面 shape 设为 static+kinematic+respondable+mask。

    关键设计 (2026-07-31 第三次修正):
      CoppeliaSim 4.10 实测 **surface velocity 属性未实现** (所有
      bullet.surfaceVelocity / ode.surfaceVelocity 等 setVector3Property
      均抛 "property is unknown" — 仓库 docs/raw/manual/en/propertiesReference.htm
      也未列出此字段)。所以回到 kinematic + setObjectPosition 路径:
      - static=True (int-param)
      - kinematic=True (properties)
      - respondable=True (properties)
      - mask 与物料一致
      然后 Python 端在 _move_conveyors_physics 中每步对所有 pad 调
      setObjectPosition 沿局部 +X 推进 CONVEYOR_SPEED*dt, 超过带长回卷 —
      这种 "static+kinematic+respondable 程序化移动表面" 在所有引擎下
      都会通过摩擦推动上方 dynamic 物料
      (physicsEngineDifferences.htm:38, Bullet/ODE/Newton/Vortex/MuJoCo 均支持)。

    Args:
        sim:    CoppeliaSim Remote API
        handle: 带面 shape 句柄 (单个 pad)
        speed:  保留参数 (未使用, 速度在 controller 控制)
        engine: 保留参数
        logger: 可选日志

    Returns:
        True 表示核心属性 (static/kinematic/respondable) 至少 2/3 生效.
    """
    if handle is None or handle < 0:
        return False
    from factory_config import MATERIAL_RESPONDABLE_MASK_PACKED
    ok = []
    # 1) static=True (走 int-param)
    if _try_set_bool_property(sim, handle, "static", True):
        ok.append("static")
    # 2) kinematic=True (走 properties; 核心: 让 setObjectPosition 触发摩擦)
    if _try_set_bool_property(sim, handle, "kinematic", True):
        ok.append("kinematic")
    # 3) respondable=True
    if _try_set_bool_property(sim, handle, "respondable", True):
        ok.append("respondable")
    # 4) respondable_mask
    if _try_set_respondable_mask(sim, handle, MATERIAL_RESPONDABLE_MASK_PACKED):
        ok.append("mask")
    # 5) 通知引擎重置
    try:
        sim.resetDynamicObject(handle)
    except Exception:
        pass

    if logger and len(ok) >= 2:
        logger.debug(f"  传送带物理属性 OK: {ok}")
    return len(ok) >= 2


# ═══════════════════════════════════════════════════════════════
# 表面速度 (Surface Velocity) — 各引擎差异
# ═══════════════════════════════════════════════════════════════

# 文档参考: docs/raw/manual/en/physicsEngineDifferences.htm:39
# "most engines support body surface velocities, which are often used to
#  simulate a simple conveyor modeled as a cuboid with a surface velocity"
_SURFACE_VEL_PROPS = (
    "bullet.surfaceVelocity",   # Bullet — 优先
    "ode.surfaceVelocity",      # ODE
    "newton.surfaceVelocity",   # Newton
    "vortex.surfaceVelocity",   # Vortex
    "mujoco.surfaceVelocity",   # MuJoCo — 实测不支持
)


def set_conveyor_surface_velocity(sim, handle: int, speed: float,
                                  engine: str = "unknown",
                                  logger: Optional[logging.Logger] = None) -> bool:
    """设置传送带 surface velocity (沿局部 +X 方向)。

    依次尝试各引擎的 surface velocity 属性, 第一个成功的生效。
    速度向量是 [vx, vy, vz] (m/s), 本函数固定写 [speed, 0, 0]
    (沿带面局部 +X — 假定 build_scene 已按 start→end 旋转带面)。

    Args:
        sim:    CoppeliaSim Remote API
        handle: 带面 shape 句柄
        speed:  速度大小 (m/s)
        engine: 物理引擎名 (仅作日志提示, 实际按 _SURFACE_VEL_PROPS 顺序尝试)
        logger: 可选日志

    Returns:
        True 表示至少一个引擎属性写入成功; False 表示全部失败
        (典型情形: MuJoCo 引擎 / properties 未实现)。
    """
    if handle is None or handle < 0:
        return False
    vel_vec = [float(speed), 0.0, 0.0]
    # surface velocity 是 vector3, 用 setVector3Property; 4.10 推荐
    for prop in _SURFACE_VEL_PROPS:
        try:
            sim.setVector3Property(handle, prop, vel_vec)
            if logger:
                logger.debug(f"  surfaceVelocity: {prop} = {vel_vec} ✓")
            return True
        except Exception:
            continue
    # 旧 vector3-param 回退 (sim.setObjectVector3Param 需 prop id, 通常无 surface vel 专属)
    if logger:
        logger.debug(f"  surfaceVelocity: 全部 {len(_SURFACE_VEL_PROPS)} 个属性写入失败")
    return False


# ═══════════════════════════════════════════════════════════════
# 引擎探测
# ═══════════════════════════════════════════════════════════════

def get_physics_engine_name(sim) -> str:
    """读取当前物理引擎名, 用于 log warning。优先 properties, 回退常量。"""
    try:
        eng = sim.getStringProperty(sim.handle_scene, "dynamicsEngine")
        if isinstance(eng, str) and eng:
            return eng
    except Exception:
        pass
    # 旧 enum 常量回退 (int → 名字)
    const_map = {
        0: "bullet", 1: "ode", 2: "vortex", 3: "newton", 4: "mujoco",
    }
    try:
        eng = sim.getIntProperty(sim.handle_scene, "dynamicsEngine")
        if isinstance(eng, int):
            return const_map.get(eng, f"engine_{eng}")
    except Exception:
        pass
    return "unknown"


def is_physics_engine_safe(engine: str) -> bool:
    """MuJoCo 在 kinematic+static+respondable 路径下用 mocap + weld 表达,
    surface velocity 不可用 (physicsEngineDifferences.htm:39)。
    本项目不强制切引擎, 但 log warning 提示。"""
    return engine.lower() != "mujoco"


# ═══════════════════════════════════════════════════════════════
# 内置传送带子 shape 查找
# ═══════════════════════════════════════════════════════════════

def find_belt_pad_shape(sim, conveyor_handle: int) -> Optional[int]:
    """在内置传送带模型中找到名为 'pad' 的形状子对象 (真正的接触面)。

    内置 `generic conveyor (belt).ttm` 的结构: 模型根 (dummy) 之下有多个
    'pad' 形状, 它们才是物理上承载物料的面; 直接设模型根 dummy 没有意义。
    返回第一个 'pad' 形状句柄; 找不到返回 None。
    """
    if conveyor_handle is None or conveyor_handle < 0:
        return None
    try:
        tree = sim.getObjectsInTree(conveyor_handle, sim.handle_all, 0)
    except Exception:
        return None
    for obj in tree:
        try:
            if (sim.getObjectType(obj) == sim.object_shape_type
                    and sim.getObjectAlias(obj) == "pad"):
                return int(obj)
        except Exception:
            continue
    return None


def find_all_belt_pad_shapes(sim, conveyor_handle: int) -> list[int]:
    """返回内置传送带模型下所有名为 'pad' 的形状句柄 (全部 200 个)。

    与 find_belt_pad_shape 不同: 内置传送带模型每个 pad 是一个独立 shape,
    设 surface velocity 必须在每个 pad 上都设一遍 (单个 pad 上的物料
    移动, 但其他 pad 不动 — 物料会停在 pad 边界处抖动)。

    Returns:
        pad 句柄列表; 找不到返回空列表.
    """
    if conveyor_handle is None or conveyor_handle < 0:
        return []
    try:
        tree = sim.getObjectsInTree(conveyor_handle, sim.handle_all, 0)
    except Exception:
        return []
    pads = []
    for obj in tree:
        try:
            if (sim.getObjectType(obj) == sim.object_shape_type
                    and sim.getObjectAlias(obj) == "pad"):
                pads.append(int(obj))
        except Exception:
            continue
    return pads


def capture_belt_pad_loop(sim, conveyor_handle: int) -> Optional[dict]:
    """采集内置传送带 pad 的闭环局部位姿。"""
    pads = find_all_belt_pad_shapes(sim, conveyor_handle)
    if len(pads) < 2:
        return None

    entries = []
    for handle in pads:
        try:
            pose = list(sim.getObjectPose(handle, sim.handle_parent))
        except Exception:
            return None
        entries.append((int(handle), pose))

    # 内置模型子树顺序即 pad 的闭环顺序。验证相邻采样间距，避免将
    # 结构不同的模型误当作 generic conveyor (belt) 处理。
    distances = []
    for index, (_, pose) in enumerate(entries):
        next_pose = entries[(index + 1) % len(entries)][1]
        distances.append(math.dist(pose[:3], next_pose[:3]))
    positive = [distance for distance in distances if distance > 1e-6]
    if not positive:
        return None
    spacing = sum(positive) / len(positive)
    if max(positive) > spacing * 3.0:
        return None

    return {
        "pads": [entry[0] for entry in entries],
        "poses": [entry[1] for entry in entries],
        "spacing": spacing,
        "phase": 0.0,
    }


def advance_belt_pad_loop(sim, loop: dict, distance: float) -> bool:
    """将 pad 沿采样闭环推进指定距离，并连续插值位置和四元数。"""
    pads = loop.get("pads", [])
    poses = loop.get("poses", [])
    spacing = float(loop.get("spacing", 0.0))
    if len(pads) < 2 or len(pads) != len(poses) or spacing <= 0.0:
        return False

    count = len(pads)
    phase = (float(loop.get("phase", 0.0)) + distance / spacing) % count
    loop["phase"] = phase
    base_shift = int(math.floor(phase))
    fraction = phase - base_shift

    for index, handle in enumerate(pads):
        pose_index = (index + base_shift) % count
        next_index = (pose_index + 1) % count
        try:
            pose = sim.interpolatePoses(
                poses[pose_index], poses[next_index], fraction
            )
            sim.setObjectPose(handle, pose, sim.handle_parent)
        except Exception:
            return False
    return True


def apply_conveyor_physics_props_all_pads(
    sim, conveyor_handle: int, speed: float, engine: str,
    logger: Optional[logging.Logger] = None
) -> bool:
    """对内置传送带模型下所有 pad shape 一次性设物理属性 + surface velocity。

    Args:
        sim:             CoppeliaSim Remote API
        conveyor_handle: 传送带根句柄 (dummy)
        speed:           表面速度 (m/s, 沿带面局部 +X)
        engine:          物理引擎名
        logger:          可选日志

    Returns:
        True 表示至少一个 pad 成功设上 surface velocity; 否则 False
    """
    if conveyor_handle is None or conveyor_handle < 0:
        return False
    pads = find_all_belt_pad_shapes(sim, conveyor_handle)
    if not pads:
        if logger:
            logger.warning(f"[PHYSICS] 传送带 {conveyor_handle} 下未找到 pad shape")
        return False
    ok_count = 0
    for pad_h in pads:
        try:
            if apply_conveyor_physics_props(sim, pad_h, speed, engine, logger):
                ok_count += 1
        except Exception as e:
            if logger:
                logger.debug(f"  pad={pad_h} 属性应用失败: {e}")
    if logger:
        logger.info(f"  [PHYSICS] 传送带 {conveyor_handle}: "
                    f"{ok_count}/{len(pads)} 个 pad 设上 surface velocity")
    return ok_count > 0
