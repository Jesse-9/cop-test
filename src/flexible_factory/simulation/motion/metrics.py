"""metrics.py — v1 运动层逐段关节/腕部指标.

指标定义 (对所有段统一):

  - ``tv``: 每个关节的总变差 ``sum(abs(q_i[k+1]-q_i[k]))``;
  - ``tv_total``: 六关节 TV 之和;
  - ``max_step``: 相邻两个采样点的最大单关节增量;
  - ``points``: 采样点数 (含起点);
  - ``q_delta``: 段首到段尾的关节差.

关节读数按**限位内连续角**统计, 不做 ±pi 折叠: 4.10 当前模型关节区间
为 ±2pi, 折叠会把跨 0 的真实运动误算成小量, 也会掩盖 4.7 rad 与
-1.5 rad 这类同一模型中的表示跳变. 腕部分支归一化只用于分支判定,
不参与 TV/max step.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

from .. import config_motion as cfg
from .branch import branch_stats


def joint_stats(samples: Sequence[Sequence[float]]) -> dict:
    """计算一段关节序列的 TV / 最大单步 / 起止差.

    ``samples`` 按时间顺序, 含起点; 不足一个点时返回零值而不是失败.
    """
    values = [[float(v) for v in q] for q in samples]
    if not values:
        return {"q_start": None, "q_end": None, "q_delta": None,
                "tv": None, "tv_total": 0.0, "max_step": 0.0, "points": 0}
    dof = len(values[0])
    tv = [0.0] * dof
    max_step = 0.0
    for previous, current in zip(values, values[1:]):
        if len(current) != dof:
            raise ValueError("关节序列维度不一致")
        for index in range(dof):
            delta = abs(current[index] - previous[index])
            tv[index] += delta
            max_step = max(max_step, delta)
    return {
        "q_start": [_round(v) for v in values[0]],
        "q_end": [_round(v) for v in values[-1]],
        "q_delta": [_round(values[-1][i] - values[0][i])
                    for i in range(dof)],
        "tv": [_round(v) for v in tv],
        "tv_total": _round(sum(tv)),
        "max_step": _round(max_step),
        "points": len(values),
    }


def segment_metrics(samples: Sequence[Sequence[float]],
                    name: str = "", cartesian: bool = False,
                    target_pose: Optional[Sequence[float]] = None,
                    tcp_pose: Optional[Sequence[float]] = None,
                    lock_joint: Optional[int] = None,
                    lock_value: Optional[float] = None,
                    lock_tol: Optional[float] = None) -> dict:
    """组合关节统计、腕部统计、锁定偏差与段末 TCP 误差."""
    stats = joint_stats(samples)
    values = [[float(v) for v in q] for q in samples]
    out = {
        "name": name,
        "cartesian": bool(cartesian),
        "lock_joint": lock_joint,
        "lock_value": (None if lock_value is None else _round(lock_value)),
        "lock_tol": lock_tol,
        **stats,
    }
    if values:
        out["q0_delta"] = _round(values[-1][cfg.Q0_LOCK_JOINT]
                                 - values[0][cfg.Q0_LOCK_JOINT])
        if lock_joint is not None and lock_value is not None:
            out["lock_deviation_max"] = _round(max(
                abs(q[lock_joint] - float(lock_value)) for q in values))
            out["lock_ok"] = out["lock_deviation_max"] <= float(
                lock_tol if lock_tol is not None else cfg.Q0_LOCK_TOL)
    out.update(branch_stats(values))
    if tcp_pose is not None and target_pose is not None:
        pos, ang = pose_error(tcp_pose, target_pose)
        out["tcp_error_pos"] = _round(pos)
        out["tcp_error_ang"] = _round(ang)
    return out


def pose_error(actual: Sequence[float], target: Sequence[float],
               strict: bool = True) -> tuple[float, float]:
    """TCP 位置误差与姿态误差; 非 strict 只比较工具 Z 轴 (法向)."""
    pos = math.dist([float(v) for v in actual[:3]],
                    [float(v) for v in target[:3]])
    if strict:
        ang = quat_angle(actual[3:7], target[3:7])
    else:
        from .contact import quat_rotate
        axis_a = quat_rotate(actual[3:7], (0.0, 0.0, 1.0))
        axis_b = quat_rotate(target[3:7], (0.0, 0.0, 1.0))
        dot = max(-1.0, min(1.0, sum(a * b for a, b in zip(axis_a, axis_b))))
        ang = math.acos(dot)
    return pos, ang


def quat_angle(a: Sequence[float], b: Sequence[float]) -> float:
    from .contact import quat_angle_between
    return quat_angle_between(a, b)


def totals(segments: Sequence[dict]) -> dict:
    """汇总逐段指标."""
    tvs = [segment.get("tv_total") or 0.0 for segment in segments]
    steps = [segment.get("max_step") or 0.0 for segment in segments]
    points = [segment.get("points") or 0 for segment in segments]
    return {
        "segments": len(segments),
        "tv_total": _round(sum(tvs)),
        "max_step": _round(max(steps) if steps else 0.0),
        "points": sum(points),
    }


# ═══════════════════════════════════════════════════════════════
# 阶段 2.02: 整段候选代价与执行剖面 (06-10 §8.3.4–§8.3.5)
# ═══════════════════════════════════════════════════════════════

def chain_cost(samples: Sequence[Sequence[float]],
               dt: Optional[float] = None) -> dict:
    """整段分层代价 (06-10 §8.3.4).

    ``J = w_tv*TV_all + w_q0*TV(q[0]) + w_q3*TV(q[3])
          + w_b*B_all + w_a*A_all``

    - ``TV_all = Σ_i TV_i``, ``TV_i = Σ_k |q_i[k+1]-q_i[k]|``;
    - ``B_all = Σ_i Σ_k |q_i[k+1]-2q_i[k]+q_i[k-1]|``;
    - ``A_all = Σ_i Σ_k ((q_i[k+1]-2q_i[k]+q_i[k-1])/dt²)²``.

    关节按限位内连续角处理, 不做 ±pi 折叠. 规划采样使用
    :data:`flexible_factory.simulation.config_motion.CHAIN_COST_SAMPLE_DT` 作为单位时间; 执行剖面
    必须使用真实仿真时间另算, 不能复用该代价.
    """
    values = [[float(v) for v in q] for q in samples]
    dof = len(values[0]) if values else 0
    tv = [0.0] * dof
    second = [0.0] * dof
    for previous, current in zip(values, values[1:]):
        if len(current) != dof:
            raise ValueError("关节序列维度不一致")
        for index in range(dof):
            tv[index] += abs(current[index] - previous[index])
    for a, b, c in zip(values, values[1:], values[2:]):
        for index in range(dof):
            second[index] += abs(c[index] - 2.0 * b[index] + a[index])
    step = float(cfg.CHAIN_COST_SAMPLE_DT if dt is None else dt)
    if step <= 0.0:
        raise ValueError("chain_cost 的采样时间必须 > 0")
    accel_sq = 0.0
    for a, b, c in zip(values, values[1:], values[2:]):
        for index in range(dof):
            accel_sq += ((c[index] - 2.0 * b[index] + a[index])
                         / (step * step)) ** 2
    tv_total = sum(tv)
    tv_q0 = tv[cfg.Q0_LOCK_JOINT] if dof > cfg.Q0_LOCK_JOINT else 0.0
    q3 = 3 if dof > 3 else dof - 1
    tv_q3 = tv[q3] if q3 >= 0 else 0.0
    b_all = sum(second)
    cost = (cfg.CHAIN_COST_WEIGHT_TV * tv_total
            + cfg.CHAIN_COST_WEIGHT_Q0 * tv_q0
            + cfg.CHAIN_COST_WEIGHT_Q3 * tv_q3
            + cfg.CHAIN_COST_WEIGHT_B * b_all
            + cfg.CHAIN_COST_WEIGHT_A * accel_sq)
    return {
        "points": len(values),
        "dt": _round(step),
        "tv": [_round(v) for v in tv],
        "tv_total": _round(tv_total),
        "tv_q0": _round(tv_q0),
        "tv_q3": _round(tv_q3),
        "b_all": _round(b_all),
        "a_all": _round(accel_sq),
        "cost": _round(cost),
        "weights": {
            "tv": cfg.CHAIN_COST_WEIGHT_TV,
            "q0": cfg.CHAIN_COST_WEIGHT_Q0,
            "q3": cfg.CHAIN_COST_WEIGHT_Q3,
            "b": cfg.CHAIN_COST_WEIGHT_B,
            "a": cfg.CHAIN_COST_WEIGHT_A,
        },
    }


def chain_cost_increment(previous: Sequence[Sequence[float]],
                         current: Sequence[float],
                         dt: Optional[float] = None) -> dict:
    """向已选链追加一个构型时的代价增量 (beam 搜索用).

    ``previous`` 是当前链 (含最后一个构型), ``current`` 是待加入构型.
    增量与 :func:`chain_cost` 的完整式相加一致: TV 项按最后一对构型,
    B/A 项按最后三个构型. 链长不足时 B/A 为 0.
    """
    last = [float(v) for v in previous[-1]]
    value = [float(v) for v in current]
    dof = len(last)
    if len(value) != dof:
        raise ValueError("关节序列维度不一致")
    delta = [abs(value[i] - last[i]) for i in range(dof)]
    tv_total = sum(delta)
    tv_q0 = delta[cfg.Q0_LOCK_JOINT] if dof > cfg.Q0_LOCK_JOINT else 0.0
    q3 = 3 if dof > 3 else dof - 1
    tv_q3 = delta[q3] if q3 >= 0 else 0.0
    b_all = 0.0
    a_all = 0.0
    if len(previous) >= 2:
        first = [float(v) for v in previous[-2]]
        step = float(cfg.CHAIN_COST_SAMPLE_DT if dt is None else dt)
        if step <= 0.0:
            raise ValueError("chain_cost_increment 的采样时间必须 > 0")
        for i in range(dof):
            second = value[i] - 2.0 * last[i] + first[i]
            b_all += abs(second)
            a_all += (second / (step * step)) ** 2
    cost = (cfg.CHAIN_COST_WEIGHT_TV * tv_total
            + cfg.CHAIN_COST_WEIGHT_Q0 * tv_q0
            + cfg.CHAIN_COST_WEIGHT_Q3 * tv_q3
            + cfg.CHAIN_COST_WEIGHT_B * b_all
            + cfg.CHAIN_COST_WEIGHT_A * a_all)
    return {
        "cost": cost,
        "tv_total": tv_total,
        "tv_q0": tv_q0,
        "tv_q3": tv_q3,
        "b_all": b_all,
        "a_all": a_all,
    }


def direction_reversals(samples: Sequence[Sequence[float]]) -> list[int]:
    """每个关节的运动方向反转次数 (零增量不改变方向)."""
    values = [[float(v) for v in q] for q in samples]
    if len(values) < 2:
        return [0] * (len(values[0]) if values else 0)
    dof = len(values[0])
    signs: list[Optional[int]] = [None] * dof
    count = [0] * dof
    for previous, current in zip(values, values[1:]):
        for index in range(dof):
            delta = current[index] - previous[index]
            if abs(delta) <= 1e-12:
                continue
            sign = 1 if delta > 0.0 else -1
            if signs[index] is not None and sign != signs[index]:
                count[index] += 1
            signs[index] = sign
    return count


def motion_profile(samples: Sequence[dict],
                   limits: Optional[dict] = None) -> dict:
    """从带时间戳的实测/下发采样统计速度、加速度与 jerk 峰值/RMS.

    ``samples`` 为 ``{"t": 仿真时间, "q": [六关节]}`` 序列. 用非均匀时间
    差分计算; 采样不足时对应阶数返回空值而不是 0. 只做测量, 不改变轨迹.
    """
    rows = sorted(
        ({"t": float(s["t"]), "q": [float(v) for v in s["q"]]}
         for s in samples), key=lambda row: row["t"])
    if len(rows) < 2:
        return {"points": len(rows), "duration": 0.0, "joints": [],
                "max": {}, "within_limits": {}}
    dof = len(rows[0]["q"])
    for row in rows:
        if len(row["q"]) != dof:
            raise ValueError("关节序列维度不一致")
    vel: list[list[float]] = []
    acc: list[list[float]] = []
    jerk: list[list[float]] = []
    for index in range(1, len(rows)):
        dt = rows[index]["t"] - rows[index - 1]["t"]
        if dt <= 0.0:
            continue
        vel.append([(rows[index]["q"][j] - rows[index - 1]["q"][j]) / dt
                    for j in range(dof)])
    for index in range(1, len(vel)):
        dt = rows[index]["t"] - rows[index - 1]["t"]
        if dt <= 0.0:
            continue
        acc.append([(vel[index][j] - vel[index - 1][j]) / dt
                    for j in range(dof)])
    for index in range(1, len(acc)):
        dt = rows[index + 1]["t"] - rows[index]["t"]
        if dt <= 0.0:
            continue
        jerk.append([(acc[index][j] - acc[index - 1][j]) / dt
                     for j in range(dof)])

    def _stats(values):
        if not values:
            return {"peak": None, "rms": None}
        peak = max(abs(v) for row in values for v in row)
        squares = sum(v * v for row in values for v in row)
        count = sum(len(row) for row in values)
        return {"peak": _round(peak), "rms": _round(math.sqrt(
            squares / count if count else 0.0))}

    joints = []
    for joint in range(dof):
        joints.append({
            "joint": joint,
            "vel": _stats([[row[joint]] for row in vel]),
            "acc": _stats([[row[joint]] for row in acc]),
            "jerk": _stats([[row[joint]] for row in jerk]),
        })
    maximum = {
        "vel": max((j["vel"]["peak"] or 0.0 for j in joints), default=0.0),
        "acc": max((j["acc"]["peak"] or 0.0 for j in joints), default=0.0),
        "jerk": max((j["jerk"]["peak"] or 0.0 for j in joints), default=0.0),
    }
    bounds = {
        "vel": cfg.EXEC_JOINT_VEL,
        "acc": cfg.EXEC_JOINT_ACCEL,
        "jerk": cfg.EXEC_JOINT_JERK,
    }
    if limits:
        bounds.update({k: float(v) for k, v in limits.items() if k in bounds})
    return {
        "points": len(rows),
        "duration": _round(rows[-1]["t"] - rows[0]["t"]),
        "joints": joints,
        "max": {k: _round(v) for k, v in maximum.items()},
        "limits": bounds,
        "within_limits": {
            "vel": maximum["vel"] <= bounds["vel"] + 1e-9,
            "acc": maximum["acc"] <= bounds["acc"] + 1e-9,
            "jerk": maximum["jerk"] <= bounds["jerk"] + 1e-9,
        },
    }


def _round(value, digits: int = 6):
    return round(float(value), digits)
