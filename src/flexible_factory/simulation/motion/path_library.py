"""path_library.py — 固定动作关节路径记录的精确匹配与 JSON 存取.

首版只保存已通过最终复核的成功 ``MotionPlan``。记录只含静态数值与标识:
关节 waypoint、语义段名称/点数、起终点、场景工件 ID、机器人/工具/载荷
标识和运动配置版本; **不保存** CoppeliaSim 句柄、运行期 ``MotionContext``
或任何会话对象。路径文件独立于 ``.ttt`` 场景工件, 默认布局示例::

    <root>/<scene-version>/<motion-phase>/<path-id>.json

本模块只做精确键匹配, 不做最近邻、相似路径搜索、平移变换或局部修复。
文件缺失、JSON 损坏、版本不匹配或键不一致都返回 ``None``, 由调用方决定
失败或回退到现有 ``LocalPlanner``。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

from .. import config_motion as cfg
from .plan_types import Goal, MotionPlan, PlanStatus, _OK_STATUSES

PATH_RECORD_VERSION = "v1_path_record_20261121"
PATH_REUSE_MODES = ("PLAN_ONLY", "CACHE_ONLY", "CACHE_THEN_PLAN")

# 复用阶段名称 (不是业务运动状态); 只写入计划回放证据.
PR_LOOKUP = "PR_LOOKUP"
PR_REPLAY = "PR_REPLAY"
PR_PLAN_FALLBACK = "PR_PLAN_FALLBACK"
PR_DONE = "PR_DONE"
PR_PHASES = (PR_LOOKUP, PR_REPLAY, PR_PLAN_FALLBACK, PR_DONE)

_REPLAYABLE_STATUSES = frozenset(
    status.value for status in _OK_STATUSES)

# 形成运动配置版本的既有参数; 不新增 config*.py 常量, 不改变
# config_digest()/layout_digest() 契约.
_MOTION_VERSION_FIELDS = (
    "MOTION_OPT_VERSION",
    "TRANSFER_TRAJECTORY_VERSION",
    "JOINT_SAMPLE_MAX_DELTA",
    "SCREEN_SAMPLE_MAX_DELTA",
    "CARTESIAN_SAMPLE_MAX_DELTA",
    "CARTESIAN_IK_STEP",
    "CARTESIAN_PATH_POS_TOL",
    "CARTESIAN_PATH_ANG_TOL",
    "COLLISION_MARGIN",
    "COLLISION_NEAR_MARGIN",
    "IK_MAX_SEEDS",
    "IK_MAX_CANDIDATES",
    "IK_DEDUPE_TOL",
    "IK_WRIST_PENALTY_WEIGHT",
    "IK_SEED_VARIANTS",
    "PLAN_MAX_WAYPOINT_CANDIDATES",
    "PLAN_MAX_DFS_NODES",
    "PLAN_LOOKAHEAD_WEIGHT",
    "PLAN_LOOKAHEAD_FAIL_PENALTY",
    "Q0_LOCK_JOINT",
    "Q0_LOCK_IK_TOL",
    "Q0_LOCK_TOL",
    "Q0_LOCK_SEGMENTS",
    "TRANSFER_HEIGHT",
    "TRANSFER_HORIZONTAL_DETOUR_OFFSETS",
    "CARTESIAN_CHAIN_SEARCH",
    "CARTESIAN_BEAM_WIDTH",
    "CARTESIAN_LAYER_CANDIDATES",
    "CARTESIAN_CHAIN_MAX_NODES",
    "TRAJECTORY_COMPACTION_ENABLED",
    "COMPACTION_TCP_POS_TOL",
    "COMPACTION_TCP_ANG_TOL",
    "TRAJECTORY_SMOOTHING_ENABLED",
    "TRAJECTORY_SMOOTHING_PASSES",
    "TRAJECTORY_SMOOTHING_WEIGHT",
    "EXEC_JOINT_VEL",
    "EXEC_JOINT_ACCEL",
    "EXEC_JOINT_JERK",
    "EXEC_MAX_JOINT_STEP",
    "EXEC_JOINT_TOL",
    "EXEC_COMMAND_SETTLE_TOL",
    "EXEC_TCP_POS_TOL",
    "EXEC_TCP_ANG_TOL",
    "EXEC_MAX_DEVIATION",
    "WRIST_BRANCH_MARGIN",
)


class PathRecordError(ValueError):
    """路径记录字段或版本不合法."""


def _canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _digest(value) -> str:
    return hashlib.sha256(
        _canonical_json(value).encode("utf-8")).hexdigest()


def _slug(value: str) -> str:
    """把场景/阶段标识转成扁平目录名, 避免路径穿越和平台差异."""
    text = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(value)).strip("._-")
    return text[:80] or "value"


def _version_value(value):
    """把配置值归一成稳定 JSON 值; set 顺序不影响摘要."""
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, (int, float)):
        return round(float(value), 9)
    if isinstance(value, str):
        return value
    if isinstance(value, frozenset):
        return sorted((_version_value(item) for item in value), key=repr)
    if isinstance(value, (tuple, list)):
        return [_version_value(item) for item in value]
    return repr(value)


def motion_config_version() -> str:
    """由既有运动参数生成稳定版本串; 不写回 ``config_motion``."""
    payload = {name: _version_value(getattr(cfg, name))
               for name in _MOTION_VERSION_FIELDS}
    return f"motion_{_digest(payload)[:16]}"


def start_digest(start_q: Sequence[float],
                 tol: float = cfg.EXEC_JOINT_TOL) -> tuple[int, ...]:
    """按执行容差量化起始关节; 只用于键匹配, 回放前仍做距离复核."""
    values = [float(v) for v in start_q]
    if not values:
        raise ValueError("起始关节不能为空")
    if float(tol) <= 0.0:
        raise ValueError("起始关节量化容差必须大于 0")
    return tuple(int(round(value / float(tol))) for value in values)


def goals_digest(goals: Sequence[Goal]) -> str:
    """目标摘要: 名称、位姿、接触/姿态/锁轴与 forced_q 任一变化即不匹配."""
    rows = []
    for goal in goals:
        rows.append({
            "name": str(goal.name),
            "pose": [round(float(v), 6) for v in goal.pose],
            "cartesian": bool(goal.cartesian),
            "allow_target_contact": bool(goal.allow_target_contact),
            "final_contact": bool(goal.final_contact),
            "strict_orientation": bool(goal.strict_orientation),
            "allow_final_load_contact": bool(
                goal.allow_final_load_contact),
            "lock_joint": (None if goal.lock_joint is None
                           else int(goal.lock_joint)),
            "lock_tol": round(float(goal.lock_tol), 9),
            "forced_q": (None if goal.forced_q is None else
                         [round(float(v), 6) for v in goal.forced_q]),
        })
    return _digest(rows)[:16]


def _text(data: dict, name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str) or not value:
        raise PathRecordError(f"路径键字段 {name!r} 缺失或不是非空字符串")
    return value


def _numbers(values, name: str) -> list[float]:
    if not isinstance(values, (list, tuple)) or not values:
        raise PathRecordError(f"{name} 必须是非空数值序列")
    try:
        result = [float(value) for value in values]
    except (TypeError, ValueError) as exc:
        raise PathRecordError(f"{name} 含非数值元素") from exc
    if not all(math.isfinite(value) for value in result):
        raise PathRecordError(f"{name} 含非有限数值")
    return result


@dataclass(frozen=True)
class PathKey:
    """精确匹配键; 任何字段不同都视为未命中."""

    scene: str
    phase: str
    unit: str
    robot: str
    tool: str
    payload: str
    start_digest: tuple[int, ...]
    targets_digest: str
    motion_version: str

    def as_dict(self) -> dict:
        return {
            "scene": self.scene,
            "phase": self.phase,
            "unit": self.unit,
            "robot": self.robot,
            "tool": self.tool,
            "payload": self.payload,
            "start_digest": list(self.start_digest),
            "targets_digest": self.targets_digest,
            "motion_version": self.motion_version,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PathKey":
        if not isinstance(data, dict):
            raise PathRecordError("路径键必须是对象")
        try:
            digest = tuple(int(value) for value in data["start_digest"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PathRecordError(f"start_digest 无效: {exc}") from exc
        if not digest:
            raise PathRecordError("start_digest 不能为空")
        return cls(
            scene=_text(data, "scene"),
            phase=_text(data, "phase"),
            unit=_text(data, "unit"),
            robot=_text(data, "robot"),
            tool=_text(data, "tool"),
            payload=_text(data, "payload"),
            start_digest=digest,
            targets_digest=_text(data, "targets_digest"),
            motion_version=_text(data, "motion_version"),
        )

    def path_id(self) -> str:
        return _digest(self.as_dict())[:16]


@dataclass
class PathRecord:
    """一条固定动作路径; 只含静态数值和标识."""

    key: PathKey
    waypoints: list[list[float]]
    segments: list[dict]
    start_q: list[float]
    end_q: list[float]
    goal_pose: Optional[list[float]] = None
    final_contact: bool = False
    strict_orientation: bool = False
    allow_final_load_contact: bool = False
    status: str = PlanStatus.OK.value
    strategy: str = ""
    lock_policy: str = ""
    locked_segments: list[str] = field(default_factory=list)
    branch_anchor: Optional[str] = None
    format_version: str = PATH_RECORD_VERSION

    def as_dict(self) -> dict:
        return {
            "format_version": self.format_version,
            "key": self.key.as_dict(),
            "start_q": [round(float(v), 6) for v in self.start_q],
            "end_q": [round(float(v), 6) for v in self.end_q],
            "waypoints": [[round(float(v), 6) for v in q]
                          for q in self.waypoints],
            "segments": [dict(row) for row in self.segments],
            "goal_pose": (None if self.goal_pose is None else
                          [round(float(v), 6) for v in self.goal_pose]),
            "final_contact": bool(self.final_contact),
            "strict_orientation": bool(self.strict_orientation),
            "allow_final_load_contact": bool(self.allow_final_load_contact),
            "status": self.status,
            "strategy": self.strategy,
            "lock_policy": self.lock_policy,
            "locked_segments": list(self.locked_segments),
            "branch_anchor": self.branch_anchor,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PathRecord":
        if not isinstance(data, dict):
            raise PathRecordError("路径记录必须是对象")
        version = data.get("format_version")
        if version != PATH_RECORD_VERSION:
            raise PathRecordError(
                f"路径记录版本不匹配: {version!r} != "
                f"{PATH_RECORD_VERSION!r}")
        key = PathKey.from_dict(data.get("key") or {})
        waypoints = [_numbers(row, "waypoint")
                     for row in data.get("waypoints") or []]
        if len(waypoints) < 2:
            raise PathRecordError("路径至少需要 2 个 waypoint")
        width = len(waypoints[0])
        if any(len(row) != width for row in waypoints):
            raise PathRecordError("waypoint 维度不一致")
        segments = data.get("segments")
        if not isinstance(segments, list) or not segments:
            raise PathRecordError("segments 必须是非空数组")
        normalized_segments = []
        total = 0
        for row in segments:
            if not isinstance(row, dict):
                raise PathRecordError("segment 必须是对象")
            name = row.get("name")
            if not isinstance(name, str) or not name:
                raise PathRecordError("segment.name 缺失")
            count = row.get("waypoint_count")
            if isinstance(count, bool) or not isinstance(count, int) \
                    or count < 1:
                raise PathRecordError(
                    f"segment {name!r} 的 waypoint_count 无效")
            target_pose = (None if row.get("target_pose") is None
                           else _numbers(row["target_pose"],
                                         "segment.target_pose"))
            if target_pose is not None and len(target_pose) < 7:
                raise PathRecordError(
                    f"segment {name!r} 的 target_pose 少于 7 维")
            lock_joint = row.get("lock_joint")
            if lock_joint is not None:
                if isinstance(lock_joint, bool) or \
                        not isinstance(lock_joint, int):
                    raise PathRecordError(
                        f"segment {name!r} 的 lock_joint 不是整数")
            lock_value = row.get("lock_value")
            if lock_value is not None:
                try:
                    lock_value = float(lock_value)
                except (TypeError, ValueError) as exc:
                    raise PathRecordError(
                        f"segment {name!r} 的 lock_value 无效") from exc
            lock_tol = row.get("lock_tol")
            if lock_tol is not None:
                try:
                    lock_tol = float(lock_tol)
                except (TypeError, ValueError) as exc:
                    raise PathRecordError(
                        f"segment {name!r} 的 lock_tol 无效") from exc
            normalized_segments.append({
                "name": name,
                "cartesian": bool(row.get("cartesian", False)),
                "waypoint_count": count,
                "target_pose": target_pose,
                "lock_joint": lock_joint,
                "lock_value": lock_value,
                "lock_tol": lock_tol,
            })
            total += count
        if total != len(waypoints) - 1:
            raise PathRecordError(
                f"segment 点数 {total} 与 waypoint 数 "
                f"{len(waypoints) - 1} 不一致")
        status = str(data.get("status") or "")
        if status not in _REPLAYABLE_STATUSES:
            raise PathRecordError(f"路径状态不可回放: {status!r}")
        locked = data.get("locked_segments") or []
        if not isinstance(locked, list):
            raise PathRecordError("locked_segments 必须是数组")
        goal_pose = data.get("goal_pose")
        start_q = _numbers(data.get("start_q"), "start_q")
        end_q = _numbers(data.get("end_q"), "end_q")
        if len(start_q) != width or len(end_q) != width:
            raise PathRecordError("start_q/end_q 与 waypoint 维度不一致")
        if any(abs(start_q[i] - waypoints[0][i]) > 1e-6 for i in range(width)) \
                or any(abs(end_q[i] - waypoints[-1][i]) > 1e-6
                       for i in range(width)):
            raise PathRecordError("start_q/end_q 与路径端点不一致")
        if goal_pose is not None:
            goal_pose = _numbers(goal_pose, "goal_pose")
            if len(goal_pose) < 7:
                raise PathRecordError("goal_pose 少于 7 维")
        return cls(
            key=key,
            waypoints=waypoints,
            segments=normalized_segments,
            start_q=start_q,
            end_q=end_q,
            goal_pose=goal_pose,
            final_contact=bool(data.get("final_contact", False)),
            strict_orientation=bool(data.get("strict_orientation", False)),
            allow_final_load_contact=bool(
                data.get("allow_final_load_contact", False)),
            status=status,
            strategy=str(data.get("strategy") or ""),
            lock_policy=str(data.get("lock_policy") or ""),
            locked_segments=[str(value) for value in locked],
            branch_anchor=(None if data.get("branch_anchor") is None
                           else str(data.get("branch_anchor"))),
            format_version=version,
        )


def record_from_plan(plan: MotionPlan, key: PathKey) -> Optional[PathRecord]:
    """把已成功且通过最终复核的计划转成记录; 不一致时返回 ``None``."""
    if not plan.ok or len(plan.waypoints) < 2 or not plan.segments:
        return None
    if plan.plan_source == "cache":
        return None
    if start_digest(plan.waypoints[0]) != key.start_digest:
        return None
    counts = [int(segment.waypoint_count) for segment in plan.segments]
    if any(count < 1 for count in counts) or \
            sum(counts) != len(plan.waypoints) - 1:
        return None
    segments = []
    for segment in plan.segments:
        segments.append({
            "name": str(segment.name),
            "cartesian": bool(segment.cartesian),
            "waypoint_count": int(segment.waypoint_count),
            "target_pose": (None if segment.target_pose is None else
                            [round(float(v), 6)
                             for v in segment.target_pose]),
            "lock_joint": (None if segment.lock_joint is None
                           else int(segment.lock_joint)),
            "lock_value": (None if segment.lock_value is None
                           else round(float(segment.lock_value), 6)),
            "lock_tol": (None if segment.lock_tol is None
                         else round(float(segment.lock_tol), 6)),
        })
    return PathRecord(
        key=key,
        waypoints=[[round(float(v), 6) for v in q] for q in plan.waypoints],
        segments=segments,
        start_q=[round(float(v), 6) for v in plan.waypoints[0]],
        end_q=[round(float(v), 6) for v in plan.waypoints[-1]],
        goal_pose=(None if plan.goal_pose is None else
                   [round(float(v), 6) for v in plan.goal_pose]),
        final_contact=bool(plan.final_contact),
        strict_orientation=bool(plan.strict_orientation),
        allow_final_load_contact=bool(plan.allow_final_load_contact),
        status=plan.status.value,
        strategy=str(plan.strategy),
        lock_policy=str(plan.lock_policy),
        locked_segments=[str(value) for value in plan.locked_segments],
        branch_anchor=plan.branch_anchor,
    )


class PathLibrary:
    """目录型路径库; 精确读取/保存, 不扫描相似路径."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.last_error = ""

    def scene_dir(self, scene_id: str) -> Path:
        """场景工件对应的路径库子目录 (扁平 slug, 不含阶段)."""
        return self.root / _slug(scene_id)

    def count_records(self, scene_id: str) -> int:
        """场景目录下现有 JSON 记录数; 只用于运行前预检, 不校验键."""
        directory = self.scene_dir(scene_id)
        if not directory.is_dir():
            return 0
        return sum(1 for path in directory.glob("*/*.json")
                   if path.is_file())

    def path_for(self, key: PathKey) -> Path:
        return (self.scene_dir(key.scene) / _slug(key.phase) /
                f"{key.path_id()}.json")

    def get_exact(self, key: PathKey) -> Optional[PathRecord]:
        """返回键完全一致的记录; 缺文件/损坏/版本不符/键不符均返回 None."""
        self.last_error = ""
        path = self.path_for(key)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError) as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None
        try:
            record = PathRecord.from_dict(data)
        except (PathRecordError, TypeError, ValueError) as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None
        if record.key != key:
            self.last_error = "路径键与请求不一致"
            return None
        return record

    def explain_miss(self, key: PathKey) -> str:
        """只读诊断: 比较同场景/阶段记录的键差异, 不改变匹配规则.

        只在未命中路径调用; 返回空字符串表示没有可比记录. 诊断不读取
        句柄, 只读取记录文件中的静态键字段.
        """
        directory = self.path_for(key).parent
        if not directory.is_dir():
            return "场景/阶段目录无记录"
        best: Optional[tuple[str, list[str]]] = None
        for path in sorted(directory.glob("*.json")):
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    record = PathRecord.from_dict(json.load(handle))
            except (OSError, TypeError, ValueError):
                continue
            differences = [
                field for field in ("scene", "phase", "unit", "robot",
                                    "tool", "payload", "motion_version")
                if getattr(record.key, field) != getattr(key, field)]
            if record.key.start_digest != key.start_digest:
                differences.append("start_digest")
            if record.key.targets_digest != key.targets_digest:
                differences.append("targets_digest")
            if not differences:
                return f"同键记录 {path.stem} 无法读取"
            if best is None or len(differences) < len(best[1]):
                best = (path.stem, differences)
        if best is None:
            return "场景/阶段目录无可用记录"
        return f"最近记录 {best[0]} 差异: {','.join(best[1])}"

    def put(self, record: PathRecord) -> Path:
        """原子写入一条已通过最终复核的记录; 同键记录会被替换."""
        if record.format_version != PATH_RECORD_VERSION:
            raise PathRecordError(
                f"拒绝写入版本 {record.format_version!r}; 当前 "
                f"{PATH_RECORD_VERSION!r}")
        if record.status not in _REPLAYABLE_STATUSES:
            raise PathRecordError(
                f"拒绝写入非成功计划: {record.status!r}")
        path = self.path_for(record.key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(record.as_dict(), handle, ensure_ascii=False,
                      indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temp, path)
        return path


__all__ = [
    "PATH_RECORD_VERSION",
    "PATH_REUSE_MODES",
    "PR_DONE",
    "PR_LOOKUP",
    "PR_PHASES",
    "PR_PLAN_FALLBACK",
    "PR_REPLAY",
    "PathKey",
    "PathLibrary",
    "PathRecord",
    "PathRecordError",
    "goals_digest",
    "motion_config_version",
    "record_from_plan",
    "start_digest",
]