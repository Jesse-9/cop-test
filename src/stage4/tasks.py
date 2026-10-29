"""tasks.py — 阶段四多岛任务状态、优先级与去重 (06-16 §F4-4).

任务只按物料 UID、工位和物流边追踪; 不使用对象 alias 或屏幕位置推断完成.
每个任务携带 task_id / kind / uid / source / destination / dock / phase /
priority / 资源 / 等待与阻塞记录.

去重: 同一 ``(kind, uid)`` 的活动任务必须拒绝; 空载调机与 E 配对使用
独立序号, 不与物料任务冲突. FAILED/BLOCKED 不得改写成 DONE; 阶段四不
自动重试.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from . import config as cfg


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    WAITING = "WAITING"
    RUNNING = "RUNNING"
    BLOCKED = "BLOCKED"
    DONE = "DONE"
    FAILED = "FAILED"
    REJECTED = "REJECTED"

    @property
    def terminal(self) -> bool:
        return self in (TaskStatus.DONE, TaskStatus.FAILED,
                        TaskStatus.REJECTED)

    @property
    def active(self) -> bool:
        return self in (TaskStatus.PENDING, TaskStatus.WAITING,
                        TaskStatus.RUNNING, TaskStatus.BLOCKED)


class TaskError(RuntimeError):
    code = "STAGE4_TASK"


@dataclass
class Stage4Task:
    task_id: str
    kind: str
    uid: int
    source: str
    destination: str
    dock: str = ""
    device: str = ""
    status: TaskStatus = TaskStatus.PENDING
    phase: str = ""
    priority: int = 1
    attempts: int = 0
    created_sim_time: float = 0.0
    started_sim_time: Optional[float] = None
    finished_sim_time: Optional[float] = None
    waiting_since: Optional[float] = None
    blocked_since: Optional[float] = None
    failure_reason: str = ""
    evidence: dict = field(default_factory=dict)
    resources: list[str] = field(default_factory=list)
    upgraded: bool = False

    # ── 派生 ────────────────────────────────────────────────

    def is_transport(self) -> bool:
        return self.kind in (cfg.TASK_KIND_TRANSPORT_BA,
                             cfg.TASK_KIND_TRANSPORT_DE)

    def effective_priority(self, sim_time: float) -> int:
        """D→E 等待升级: 只影响下一次派车, 不抢占运行中任务."""
        base = int(self.priority)
        if self.kind != cfg.TASK_KIND_TRANSPORT_DE:
            return base
        if self.status not in (TaskStatus.PENDING, TaskStatus.WAITING,
                               TaskStatus.BLOCKED):
            return base
        waited = max(0.0, float(sim_time) - float(self.created_sim_time))
        if waited >= cfg.D_E_STARVATION_UPGRADE_S:
            return max(0, base - 1)
        return base

    def wait_duration(self, sim_time: float) -> float:
        if self.waiting_since is None:
            return 0.0
        return max(0.0, float(sim_time) - float(self.waiting_since))

    def as_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "kind": self.kind,
            "uid": int(self.uid),
            "source": self.source,
            "destination": self.destination,
            "dock": self.dock,
            "device": self.device,
            "status": self.status.value,
            "phase": self.phase,
            "priority": int(self.priority),
            "upgraded": bool(self.upgraded),
            "attempts": int(self.attempts),
            "created_sim_time": round(float(self.created_sim_time), 4),
            "started_sim_time": (None if self.started_sim_time is None
                                 else round(float(self.started_sim_time), 4)),
            "finished_sim_time": (None if self.finished_sim_time is None
                                  else round(float(self.finished_sim_time), 4)),
            "failure_reason": self.failure_reason,
            "resources": list(self.resources),
            "evidence": self.evidence,
        }


def make_task(kind: str, uid: int, *, source: str, destination: str,
              dock: str = "", device: str = "",
              sim_time: float = 0.0) -> Stage4Task:
    if kind not in cfg.TASK_KINDS:
        raise TaskError(f"未知任务类型: {kind!r}")
    task_id = f"{kind}_uid{int(uid)}"
    return Stage4Task(
        task_id=task_id, kind=kind, uid=int(uid), source=str(source),
        destination=str(destination), dock=str(dock), device=str(device),
        priority=int(cfg.TASK_PRIORITY.get(kind, 1)),
        created_sim_time=float(sim_time))


class TaskQueue:
    """阶段四任务表; 只做去重/状态/优先级, 不调度设备."""

    def __init__(self, capacity: int = cfg.QUEUE_CAPACITY) -> None:
        self.capacity = int(capacity)
        self.tasks: dict[str, Stage4Task] = {}
        self.order: list[str] = []
        self.rejections: list[dict] = []
        self._serial = 0

    # ── 提交 ────────────────────────────────────────────────

    def submit(self, task: Stage4Task, sim_time: float = 0.0
               ) -> tuple[bool, str]:
        if len(self.tasks) >= self.capacity:
            reason = f"队列已满 ({self.capacity})"
            self._reject(task, sim_time, reason)
            return False, reason
        if task.task_id in self.tasks:
            existing = self.tasks[task.task_id]
            if existing.status.active:
                reason = (f"重复请求: {task.kind} uid={task.uid} 已由 "
                          f"{existing.task_id} ({existing.status.value}) 持有")
                self._reject(task, sim_time, reason)
                return False, reason
            reason = f"task_id 重复: {task.task_id}"
            self._reject(task, sim_time, reason)
            return False, reason
        self.tasks[task.task_id] = task
        self.order.append(task.task_id)
        return True, ""

    def next_serial(self) -> int:
        self._serial += 1
        return self._serial

    def _reject(self, task: Stage4Task, sim_time: float,
                reason: str) -> None:
        self.rejections.append({
            "sim_time": round(float(sim_time), 4),
            "task_id": task.task_id,
            "kind": task.kind,
            "uid": int(task.uid),
            "reason": reason,
        })

    # ── 查询 ────────────────────────────────────────────────

    def get(self, task_id: str) -> Optional[Stage4Task]:
        return self.tasks.get(task_id)

    def by_kind(self, kind: str) -> list[Stage4Task]:
        return [self.tasks[tid] for tid in self.order
                if self.tasks[tid].kind == kind]

    def pending_transports(self, sim_time: float) -> list[Stage4Task]:
        rows = [task for task in self.tasks.values()
                if task.is_transport()
                and task.status in (TaskStatus.PENDING, TaskStatus.WAITING)]
        rows.sort(key=lambda task: (
            task.effective_priority(sim_time), task.created_sim_time,
            task.task_id))
        return rows

    def all(self) -> list[Stage4Task]:
        return [self.tasks[tid] for tid in self.order]

    def active(self) -> list[Stage4Task]:
        return [task for task in self.tasks.values() if task.status.active]

    def finished(self) -> bool:
        return all(task.status.terminal for task in self.tasks.values())

    def all_done(self) -> bool:
        return bool(self.tasks) and all(
            task.status is TaskStatus.DONE for task in self.tasks.values())

    def any_failed(self) -> bool:
        return any(task.status is TaskStatus.FAILED
                   for task in self.tasks.values())

    def any_blocked(self) -> bool:
        return any(task.status is TaskStatus.BLOCKED
                   for task in self.tasks.values())

    # ── 状态转换 ────────────────────────────────────────────

    def start(self, task_id: str, sim_time: float) -> Stage4Task:
        task = self._require(task_id)
        if task.status not in (TaskStatus.PENDING, TaskStatus.WAITING):
            raise TaskError(f"{task_id} 状态 {task.status.value} 不能启动")
        task.status = TaskStatus.RUNNING
        task.phase = "RUNNING"
        task.attempts += 1
        task.started_sim_time = float(sim_time)
        task.waiting_since = None
        return task

    def wait(self, task_id: str, sim_time: float, reason: str) -> Stage4Task:
        task = self._require(task_id)
        if task.status not in (TaskStatus.PENDING, TaskStatus.WAITING):
            raise TaskError(f"{task_id} 状态 {task.status.value} 不能等待")
        if task.status is TaskStatus.PENDING:
            task.waiting_since = float(sim_time)
        task.status = TaskStatus.WAITING
        task.phase = f"WAITING: {reason}"
        task.evidence.setdefault("waits", []).append({
            "sim_time": round(float(sim_time), 4), "reason": reason})
        return task

    def set_phase(self, task_id: str, phase: str) -> Stage4Task:
        task = self._require(task_id)
        task.phase = str(phase)
        return task

    def block(self, task_id: str, sim_time: float,
              reason: str) -> Stage4Task:
        task = self._require(task_id)
        if task.status.terminal:
            raise TaskError(f"{task_id} 已结束, 不能阻塞")
        task.status = TaskStatus.BLOCKED
        task.blocked_since = float(sim_time)
        task.phase = f"BLOCKED: {reason}"
        task.evidence.setdefault("blocks", []).append({
            "sim_time": round(float(sim_time), 4), "reason": reason})
        return task

    def unblock(self, task_id: str, sim_time: float,
                reason: str) -> Stage4Task:
        task = self._require(task_id)
        if task.status is not TaskStatus.BLOCKED:
            raise TaskError(f"{task_id} 不在 BLOCKED")
        task.status = TaskStatus.RUNNING
        task.blocked_since = None
        task.phase = f"RESUMED: {reason}"
        task.evidence.setdefault("resumes", []).append({
            "sim_time": round(float(sim_time), 4), "reason": reason})
        return task

    def done(self, task_id: str, sim_time: float,
             evidence: Optional[dict] = None) -> Stage4Task:
        task = self._require(task_id)
        if task.status is TaskStatus.FAILED:
            raise TaskError(f"{task_id} 已 FAILED, 不能改写成 DONE")
        task.status = TaskStatus.DONE
        task.phase = "DONE"
        task.finished_sim_time = float(sim_time)
        if evidence:
            task.evidence.update(evidence)
        return task

    def fail(self, task_id: str, sim_time: float, reason: str,
             evidence: Optional[dict] = None) -> Stage4Task:
        task = self._require(task_id)
        if task.status is TaskStatus.DONE:
            raise TaskError(f"{task_id} 已 DONE, 不能改写成 FAILED")
        task.status = TaskStatus.FAILED
        task.phase = "FAILED"
        task.failure_reason = str(reason)
        task.finished_sim_time = float(sim_time)
        if evidence:
            task.evidence.update(evidence)
        return task

    def _require(self, task_id: str) -> Stage4Task:
        task = self.tasks.get(task_id)
        if task is None:
            raise TaskError(f"未知任务: {task_id}")
        return task

    # ── 报告 ────────────────────────────────────────────────

    def as_dict(self) -> dict:
        return {
            "flow_version": cfg.STAGE4_TASK_FLOW_VERSION,
            "flow": list(cfg.TASK_FLOW),
            "duplicate_policy": cfg.DUPLICATE_REQUEST_POLICY,
            "capacity": self.capacity,
            "tasks": [task.as_dict() for task in self.all()],
            "rejections": list(self.rejections),
            "all_done": self.all_done(),
            "any_failed": self.any_failed(),
            "any_blocked": self.any_blocked(),
        }
