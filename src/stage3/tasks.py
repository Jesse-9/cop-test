"""tasks.py — 阶段三 UID 任务流、去重与状态 (06-12 §3.4).

任务只按物料 UID 和工位编号追踪; 不使用对象 alias 或屏幕位置推断完成.
每个任务携带:
    task_id / kind / uid / source / destination / dock / phase / attempts

`TaskQueue.submit` 对同一 `(kind, uid)` 的活动任务去重 (06-12 §5 竞争与
重复请求用例). 失败/阻塞任务不会被改写成 DONE; 阶段三不自动重试/续作.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from . import config as cfg


class TaskKind(str, Enum):
    LOAD_A = "load_A"              # conveyor → AGV (dock A)
    TRANSPORT = "transport"        # AGV dock A ↔ dock B
    UNLOAD_B = "unload_B"          # AGV (dock B) → Station B
    RETURN = "return"              # AGV dock B → dock A (空载基线)


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    WAITING = "WAITING"            # 等待前序任务/资源/AGV 到站
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
    code = "TASK_ERROR"


@dataclass
class Stage3Task:
    task_id: str
    kind: TaskKind
    uid: int
    source: str
    destination: str
    dock: str
    status: TaskStatus = TaskStatus.PENDING
    phase: str = ""
    attempts: int = 0
    created_sim_time: float = 0.0
    started_sim_time: Optional[float] = None
    finished_sim_time: Optional[float] = None
    failure_reason: str = ""
    evidence: dict = field(default_factory=dict)
    resources: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "kind": self.kind.value,
            "uid": int(self.uid),
            "source": self.source,
            "destination": self.destination,
            "dock": self.dock,
            "status": self.status.value,
            "phase": self.phase,
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


def make_task(kind: TaskKind, uid: int, *, source: str, destination: str,
              dock: str, sim_time: float = 0.0) -> Stage3Task:
    task_id = f"{kind.value}_uid{int(uid)}"
    return Stage3Task(task_id=task_id, kind=kind, uid=int(uid),
                      source=source, destination=destination, dock=dock,
                      created_sim_time=float(sim_time))


class TaskQueue:
    """小型任务表; 只做去重/状态, 不调度设备."""

    def __init__(self, capacity: int = cfg.QUEUE_CAPACITY) -> None:
        self.capacity = int(capacity)
        self.tasks: dict[str, Stage3Task] = {}
        self.order: list[str] = []
        self.rejections: list[dict] = []

    # ── 提交 ────────────────────────────────────────────────

    def submit(self, task: Stage3Task, sim_time: float = 0.0) -> tuple[bool, str]:
        if len(self.tasks) >= self.capacity:
            reason = f"队列已满 ({self.capacity})"
            self._reject(task, sim_time, reason)
            return False, reason
        for existing in self.tasks.values():
            if (existing.kind is task.kind and existing.uid == task.uid
                    and existing.status.active):
                reason = (f"重复请求: {task.kind.value} uid={task.uid} "
                          f"已由 {existing.task_id} "
                          f"({existing.status.value}) 持有")
                self._reject(task, sim_time, reason)
                return False, reason
        if task.task_id in self.tasks:
            reason = f"task_id 重复: {task.task_id}"
            self._reject(task, sim_time, reason)
            return False, reason
        self.tasks[task.task_id] = task
        self.order.append(task.task_id)
        return True, ""

    def _reject(self, task: Stage3Task, sim_time: float,
                reason: str) -> None:
        self.rejections.append({
            "sim_time": round(float(sim_time), 4),
            "task_id": task.task_id,
            "kind": task.kind.value,
            "uid": int(task.uid),
            "reason": reason,
        })

    # ── 查询 ────────────────────────────────────────────────

    def get(self, task_id: str) -> Optional[Stage3Task]:
        return self.tasks.get(task_id)

    def by_kind(self, kind: TaskKind) -> list[Stage3Task]:
        return [self.tasks[tid] for tid in self.order
                if self.tasks[tid].kind is kind]

    def all(self) -> list[Stage3Task]:
        return [self.tasks[tid] for tid in self.order]

    def active(self) -> list[Stage3Task]:
        return [task for task in self.tasks.values() if task.status.active]

    def finished(self) -> bool:
        return all(task.status.terminal for task in self.tasks.values())

    def all_done(self) -> bool:
        return bool(self.tasks) and all(
            task.status is TaskStatus.DONE for task in self.tasks.values())

    def any_failed(self) -> bool:
        return any(task.status is TaskStatus.FAILED
                   for task in self.tasks.values())

    # ── 状态转换 ────────────────────────────────────────────

    def start(self, task_id: str, sim_time: float) -> Stage3Task:
        task = self._require(task_id)
        if task.status not in (TaskStatus.PENDING, TaskStatus.WAITING):
            raise TaskError(
                f"{task_id} 状态 {task.status.value} 不能启动")
        task.status = TaskStatus.RUNNING
        task.attempts += 1
        task.started_sim_time = float(sim_time)
        return task

    def wait(self, task_id: str, sim_time: float, reason: str) -> Stage3Task:
        task = self._require(task_id)
        if task.status not in (TaskStatus.PENDING, TaskStatus.WAITING):
            raise TaskError(
                f"{task_id} 状态 {task.status.value} 不能进入 WAITING")
        task.status = TaskStatus.WAITING
        task.phase = f"WAITING: {reason}"
        task.evidence.setdefault("waits", []).append({
            "sim_time": round(float(sim_time), 4), "reason": reason})
        return task

    def set_phase(self, task_id: str, phase: str) -> Stage3Task:
        task = self._require(task_id)
        task.phase = str(phase)
        return task

    def block(self, task_id: str, sim_time: float,
              reason: str) -> Stage3Task:
        task = self._require(task_id)
        if task.status.terminal:
            raise TaskError(f"{task_id} 已结束, 不能阻塞")
        task.status = TaskStatus.BLOCKED
        task.phase = f"BLOCKED: {reason}"
        task.evidence.setdefault("blocks", []).append({
            "sim_time": round(float(sim_time), 4), "reason": reason})
        return task

    def unblock(self, task_id: str, sim_time: float,
                reason: str) -> Stage3Task:
        task = self._require(task_id)
        if task.status is not TaskStatus.BLOCKED:
            raise TaskError(f"{task_id} 不在 BLOCKED")
        task.status = TaskStatus.RUNNING
        task.phase = f"RESUMED: {reason}"
        task.evidence.setdefault("resumes", []).append({
            "sim_time": round(float(sim_time), 4), "reason": reason})
        return task

    def done(self, task_id: str, sim_time: float,
             evidence: Optional[dict] = None) -> Stage3Task:
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
             evidence: Optional[dict] = None) -> Stage3Task:
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

    def _require(self, task_id: str) -> Stage3Task:
        task = self.tasks.get(task_id)
        if task is None:
            raise TaskError(f"未知任务: {task_id}")
        return task

    # ── 报告 ────────────────────────────────────────────────

    def as_dict(self) -> dict:
        return {
            "flow_version": cfg.STAGE3_TASK_FLOW_VERSION,
            "flow": list(cfg.TASK_FLOW),
            "duplicate_policy": cfg.DUPLICATE_REQUEST_POLICY,
            "capacity": self.capacity,
            "tasks": [task.as_dict() for task in self.all()],
            "rejections": list(self.rejections),
            "all_done": self.all_done(),
            "any_failed": self.any_failed(),
        }
