"""operation_runtime.py — 阶段四工序实例的最小运行时状态 (06-19 §1B).

阶段 0-1 只建立**单一运行时状态所有者**: 每个 :class:`~stage4.tasks.Stage4Task`
持有一个 :class:`OperationInstance`, 任务对象上的旧字段 (status/phase/时间/
证据/资源) 都是该实例的只读或受控代理, 不再维护两份可独立修改的状态.

本模块不实现工序 DAG 推进、ready 集合或依赖解锁; 这些属于阶段三
`operation runtime` 的扩展. 当前 coordinator 仍按旧任务队列逐 tick 推进,
但任务字段的来源已统一为工序实例.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


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


@dataclass
class OperationInstance:
    """一个已释放工序实例的运行时记录.

    稳定字段 (`template_id`/`kind`/`uid`/`source`/`destination`/`dock`/
    `device`) 创建后只读; 生命周期和时间字段由任务队列通过
    :class:`~stage4.tasks.Stage4Task` 的受控属性写入.
    """

    instance_id: str
    template_id: str
    kind: str
    uid: int
    source: str
    destination: str
    dock: str = ""
    device: str = ""
    transport: bool = False
    state: TaskStatus = TaskStatus.PENDING
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

    def as_dict(self) -> dict:
        return {
            "instance_id": self.instance_id,
            "template_id": self.template_id,
            "kind": self.kind,
            "uid": int(self.uid),
            "source": self.source,
            "destination": self.destination,
            "dock": self.dock,
            "device": self.device,
            "state": self.state.value,
            "phase": self.phase,
            "priority": int(self.priority),
            "attempts": int(self.attempts),
            "created_sim_time": round(float(self.created_sim_time), 4),
            "started_sim_time": (None if self.started_sim_time is None
                                 else round(float(self.started_sim_time), 4)),
            "finished_sim_time": (None if self.finished_sim_time is None
                                  else round(float(self.finished_sim_time), 4)),
            "failure_reason": self.failure_reason,
            "resources": list(self.resources),
            "transport": bool(self.transport),
        }
