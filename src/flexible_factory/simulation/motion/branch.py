"""branch.py — 腕部分支判定与奇异裕量.

腕部分支由 :data:`flexible_factory.simulation.config_motion.WRIST_BRANCH_JOINT`
(q[4], UR5 wrist2) 的归一化角判定:

  - 归一化到 ``(-pi, pi]`` 后, 符号给出 ``positive`` / ``negative``;
  - 奇异边界是 ``0`` 与 ``±pi`` (wrist1 与 wrist3 轴共线);
  - 裕量 ``min(|a|, pi-|a|)``; 小于 :data:`WRIST_BRANCH_MARGIN` 的构型
    视为近奇异, 不允许作为锚点、候选或执行中间构型.

判定只使用连续关节读数, 不按 q6 符号或单个阈值推断; 分支锚点由规划
起始实测构型给出, 后续候选必须同号且远离边界. 本模块是纯函数, 不访问
CoppeliaSim, 便于单元测试与证据复算.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

from .. import config_motion as cfg

POSITIVE = "positive"
NEGATIVE = "negative"
SINGULAR = "singular"


def wrap_angle(angle: float) -> float:
    """归一化到 ``(-pi, pi]``, 不改变物理关节读数本身."""
    value = math.remainder(float(angle), 2.0 * math.pi)
    if value <= -math.pi:
        value += 2.0 * math.pi
    return value


def wrist_angle(q: Sequence[float]) -> float:
    """返回腕部判定关节的归一化角 (rad)."""
    return wrap_angle(float(q[cfg.WRIST_BRANCH_JOINT]))


def wrist_branch(q: Sequence[float]) -> tuple[str, float]:
    """返回 ``(分支标签, 距最近奇异边界的裕量)``.

    标签为 ``positive`` / ``negative``; 恰好落在边界时返回 ``singular``.
    裕量单位 rad, 范围 ``[0, pi/2]``.
    """
    angle = wrist_angle(q)
    margin = min(abs(angle), math.pi - abs(angle))
    if margin <= 1e-9:
        return SINGULAR, 0.0
    return (POSITIVE if angle > 0.0 else NEGATIVE), margin


def branch_ok(q: Sequence[float], anchor: Optional[str],
              margin: float = cfg.WRIST_BRANCH_MARGIN
              ) -> tuple[bool, str, float]:
    """检查构型是否与 ``anchor`` 同分支且离奇异边界不小于 ``margin``."""
    label, value = wrist_branch(q)
    if anchor is None:
        return True, label, value
    if label != anchor:
        return False, label, value
    return value >= float(margin), label, value


def branch_stats(samples: Sequence[Sequence[float]],
                 margin: float = cfg.WRIST_BRANCH_MARGIN) -> dict:
    """汇总一段关节序列的分支标签、最小裕量与近奇异步数."""
    if not samples:
        return {"branch_start": None, "branch_end": None,
                "wrist_margin_min": None, "singular_steps": 0}
    labels = []
    margins = []
    for q in samples:
        label, value = wrist_branch(q)
        labels.append(label)
        margins.append(value)
    return {
        "branch_start": labels[0],
        "branch_end": labels[-1],
        "wrist_margin_min": round(min(margins), 6),
        "singular_steps": sum(1 for label in labels if label == SINGULAR),
        "near_singular_steps": sum(1 for value in margins
                                   if value < float(margin)),
    }
