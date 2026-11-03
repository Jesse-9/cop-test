"""physics.py — v1 基础层物理属性工具.

4.10 实测:
  - `static` / `respondable` / `kinematic` / `respondable_mask` 走
    `setObjectInt32Param` (properties 路径对 static 不可靠)。
  - 摩擦走引擎属性 (`bullet.friction` / `ode.friction`), 全部失败时
    保留引擎默认值并告警。
  - 动态形状改 static=1 前必须记录原值, 释放时恢复。
"""

from __future__ import annotations

from typing import Optional

from . import config_base as cfg

FRICTION_PROPS = ("bullet.friction", "ode.friction")


def set_static(sim, handle: int, static: bool) -> bool:
    try:
        sim.setObjectInt32Param(handle, sim.shapeintparam_static,
                                1 if static else 0)
        return True
    except Exception:
        return False


def set_respondable(sim, handle: int, respondable: bool = True) -> bool:
    try:
        sim.setObjectInt32Param(handle, sim.shapeintparam_respondable,
                                1 if respondable else 0)
        return True
    except Exception:
        return False


def set_kinematic(sim, handle: int, kinematic: bool) -> bool:
    try:
        sim.setObjectInt32Param(handle, sim.shapeintparam_kinematic,
                                1 if kinematic else 0)
        return True
    except Exception:
        return False


def set_respondable_mask(sim, handle: int, mask: int) -> bool:
    try:
        sim.setObjectInt32Param(handle, sim.shapeintparam_respondable_mask,
                                int(mask))
        return True
    except Exception:
        return False


def set_mass(sim, handle: int, mass: float) -> bool:
    try:
        sim.setObjectFloatParam(handle, sim.shapefloatparam_mass, float(mass))
        return True
    except Exception:
        return False


def get_mass(sim, handle: int) -> Optional[float]:
    try:
        return float(sim.getObjectFloatParam(handle,
                                             sim.shapefloatparam_mass))
    except Exception:
        return None


def set_friction(sim, handle: int, friction: float) -> Optional[str]:
    for prop in FRICTION_PROPS:
        try:
            sim.setFloatProperty(handle, prop, float(friction))
            return prop
        except Exception:
            continue
    return None


def get_friction(sim, handle: int) -> Optional[float]:
    for prop in FRICTION_PROPS:
        try:
            return float(sim.getFloatProperty(handle, prop))
        except Exception:
            continue
    return None


def reset_dynamics(sim, handle: int) -> None:
    try:
        sim.resetDynamicObject(handle)
    except Exception:
        pass


def apply_static_shape(sim, handle: int, respondable: bool = True) -> None:
    """静态平台/挡边: static + respondable, 非 kinematic (不需要被驱动)."""
    set_static(sim, handle, True)
    set_kinematic(sim, handle, False)
    set_respondable(sim, handle, respondable)


def apply_kinematic_shape(sim, handle: int, respondable: bool = True) -> None:
    """挂在动态车体上的载台/挡边: kinematic + respondable (非 static)。

    4.10 会对"静态 shape 挂在非静态树上"给出警告; kinematic 子对象随父
    变换移动且不参与重力, 是车体附件更合适的配置。
    """
    set_static(sim, handle, False)
    set_kinematic(sim, handle, True)
    set_respondable(sim, handle, respondable)
    reset_dynamics(sim, handle)


def apply_material_physics(sim, handle: int, mass: float | None = None,
                           friction: float | None = None) -> dict:
    """物料: dynamic + respondable + 质量/摩擦/碰撞掩码。"""
    mass = cfg.MATERIAL_MASS if mass is None else mass
    friction = cfg.MATERIAL_FRICTION if friction is None else friction

    set_static(sim, handle, False)
    set_kinematic(sim, handle, False)
    set_respondable(sim, handle, True)
    set_respondable_mask(sim, handle, cfg.MATERIAL_RESPONDABLE_MASK)
    set_mass(sim, handle, mass)
    friction_prop = set_friction(sim, handle, friction)
    reset_dynamics(sim, handle)
    return {
        "mass": get_mass(sim, handle),
        "friction_prop": friction_prop,
        "friction": get_friction(sim, handle),
        "static": _int_param(sim, handle, sim.shapeintparam_static),
        "respondable": _int_param(sim, handle, sim.shapeintparam_respondable),
        "mask": _int_param(sim, handle, sim.shapeintparam_respondable_mask),
    }


def snapshot_physics(sim, handle: int) -> dict:
    return {
        "static": _int_param(sim, handle, sim.shapeintparam_static),
        "kinematic": _int_param(sim, handle, sim.shapeintparam_kinematic),
        "respondable": _int_param(sim, handle, sim.shapeintparam_respondable),
        "mask": _int_param(sim, handle, sim.shapeintparam_respondable_mask),
        "mass": get_mass(sim, handle),
        "friction": get_friction(sim, handle),
    }


def _int_param(sim, handle: int, param) -> Optional[int]:
    try:
        return int(sim.getObjectInt32Param(handle, param))
    except Exception:
        return None
