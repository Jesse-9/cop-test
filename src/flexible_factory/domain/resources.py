"""resources.py — v1 固定顺序资源表 (领域层, 不导入 sim).

在阶段三小型锁表基础上扩展为 v1 五岛资源集:
  - 资源覆盖四条物流线、A→C 交接、E 两个缓冲、四个 dock、三条 AGV
    直道、两个臂间互锁区和五台臂;
  - 只做调度互斥, 不代替碰撞检查, 也不清除运动层安全锁存;
  - 任务必须一次性申请完整资源集 (`acquire_all`): 任一资源忙则只记录
    等待, 不持有部分资源, 从结构上消除循环等待;
  - 申请顺序由 ``RESOURCE_ORDER`` 固定, 并校验顺序违规.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from . import rules as cfg


class ResourceError(RuntimeError):
    code = "RESOURCE_ERROR"


class UnknownResourceError(ResourceError):
    code = "UNKNOWN_RESOURCE"


@dataclass(frozen=True)
class ResourceEvent:
    sim_time: float
    action: str
    resource: str
    owner: str
    task_id: str
    reason: str = ""
    holder: str = ""

    def as_dict(self) -> dict:
        return {
            "sim_time": round(float(self.sim_time), 4),
            "action": self.action,
            "resource": self.resource,
            "owner": self.owner,
            "task_id": self.task_id,
            "reason": self.reason,
            "holder": self.holder,
        }


@dataclass
class ResourceTable:
    """固定顺序的独占资源表.

    ``owner`` 是稳定字符串 (如 ``unit_A/task_load_A_uid1``), ``task_id``
    用于证据追踪. 资源获取失败只返回 False 并记录等待, 由调用方决定
    稍后重试, 不阻塞仿真步进.
    """

    order: tuple[str, ...] = tuple(cfg.RESOURCE_ORDER)
    events: list[ResourceEvent] = field(default_factory=list)
    owners: dict[str, str] = field(default_factory=dict)
    task_of: dict[str, str] = field(default_factory=dict)
    waiting: dict[str, list[str]] = field(
        default_factory=lambda: {name: [] for name in cfg.RESOURCE_ORDER})

    def __post_init__(self) -> None:
        if not self.order:
            raise ResourceError("资源顺序不能为空")
        if len(set(self.order)) != len(self.order):
            raise ResourceError(f"资源顺序含重复项: {self.order}")
        self.waiting = {name: [] for name in self.order}

    # ── 查询 ────────────────────────────────────────────────

    def _index(self, resource: str) -> int:
        try:
            return self.order.index(str(resource))
        except ValueError as exc:
            raise UnknownResourceError(
                f"未知资源 {resource!r}; 可用 {list(self.order)}") from exc

    def owner(self, resource: str) -> Optional[str]:
        self._index(resource)
        return self.owners.get(resource)

    def held_by(self, owner: str) -> list[str]:
        return [name for name in self.order
                if self.owners.get(name) == owner]

    def is_free(self, resource: str) -> bool:
        return self.owner(resource) is None

    # ── 申请/释放 ───────────────────────────────────────────

    def acquire(self, resource: str, owner: str, task_id: str,
                sim_time: float, reason: str = "") -> bool:
        index = self._index(resource)
        owner = str(owner)
        if self.owners.get(resource) == owner:
            return True
        current = self.owners.get(resource)
        if current is not None:
            self._wait(resource, owner, task_id, sim_time, current, reason)
            return False

        held = self.held_by(owner)
        violations = [name for name in held if self._index(name) > index]
        if violations:
            self._event(sim_time, "ORDER_VIOLATION", resource, owner,
                        task_id, f"已持有更靠后的资源 {violations}; {reason}")
            return False

        self.owners[resource] = owner
        self.task_of[resource] = str(task_id)
        if owner in self.waiting.get(resource, []):
            self.waiting[resource].remove(owner)
        self._event(sim_time, "ACQUIRE", resource, owner, task_id, reason,
                    holder=owner)
        return True

    def release(self, resource: str, owner: str, sim_time: float,
                reason: str = "") -> bool:
        self._index(resource)
        current = self.owners.get(resource)
        if current != str(owner):
            self._event(sim_time, "RELEASE_DENIED", resource, str(owner),
                        self.task_of.get(resource, ""),
                        f"当前 owner={current!r}; {reason}",
                        holder=current or "")
            return False
        task_id = self.task_of.pop(resource, "")
        self.owners.pop(resource, None)
        self._event(sim_time, "RELEASE", resource, str(owner), task_id,
                    reason, holder=str(owner))
        return True

    def release_all(self, owner: str, sim_time: float,
                    reason: str = "") -> list[str]:
        released: list[str] = []
        for resource in reversed(self.order):
            if self.owners.get(resource) == str(owner):
                if self.release(resource, owner, sim_time, reason):
                    released.append(resource)
        return released

    def release_any(self, resources: Iterable[str], sim_time: float,
                    reason: str = "") -> list[str]:
        released: list[str] = []
        wanted = set(resources)
        for resource in reversed(self.order):
            if resource not in wanted:
                continue
            owner = self.owners.get(resource)
            if owner is not None and self.release(resource, owner, sim_time,
                                                  reason):
                released.append(resource)
        return released

    # ── 等待/报告 ───────────────────────────────────────────

    def note_wait(self, resource: str, owner: str, task_id: str,
                  sim_time: float, reason: str = "") -> None:
        """记录“已申请但暂不获取”的等待, 不改变所有权."""
        self._index(resource)
        current = self.owners.get(resource)
        self._wait(resource, owner, task_id, sim_time,
                   current or "<free>", reason)

    def _wait(self, resource: str, owner: str, task_id: str,
              sim_time: float, current: str, reason: str) -> None:
        queue = self.waiting.setdefault(resource, [])
        if owner in queue:
            return          # 同一等待方只记录一次, 避免每步刷屏
        queue.append(owner)
        self._event(sim_time, "WAIT", resource, owner, task_id,
                    f"holder={current}; {reason}", holder=current)

    def _event(self, sim_time: float, action: str, resource: str,
               owner: str, task_id: str, reason: str = "",
               holder: str = "") -> None:
        self.events.append(ResourceEvent(
            sim_time=float(sim_time), action=action, resource=resource,
            owner=owner, task_id=task_id, reason=reason, holder=holder))

    def snapshot(self) -> dict:
        return {
            "order": list(self.order),
            "owners": {name: self.owners.get(name) for name in self.order},
            "tasks": {name: self.task_of.get(name) for name in self.order},
            "waiting": {name: list(self.waiting.get(name, []))
                        for name in self.order},
        }

    def as_dict(self) -> dict:
        return {
            "policy_version": cfg.RESOURCE_POLICY_VERSION,
            "order": list(self.order),
            "roles": {name: cfg.RESOURCE_ROLES.get(name, "")
                      for name in self.order},
            "snapshot": self.snapshot(),
            "events": [event.as_dict() for event in self.events],
        }

    def validate(self) -> list[dict]:
        """自检: owner 持有的资源顺序必须合法, 无重复 owner 冲突."""
        checks: list[dict] = []
        for owner in sorted(set(self.owners.values())):
            held = [self._index(name) for name in self.held_by(owner)]
            checks.append({
                "check": f"order:{owner}",
                "ok": held == sorted(held),
                "detail": f"held={self.held_by(owner)}",
            })
        checks.append({
            "check": "event_owner_consistency",
            "ok": all(event.owner for event in self.events),
            "detail": f"events={len(self.events)}",
        })
        return checks

    # ── 全有或全无 ──────────────────────────────────────────

    def acquire_all(self, resources: Iterable[str], owner: str,
                    task_id: str, sim_time: float,
                    reason: str = "") -> tuple[bool, str]:
        """一次性申请完整资源集; 任一忙则不持有任何资源."""
        wanted = sorted({str(name) for name in resources},
                        key=self._index)
        owner = str(owner)
        for name in wanted:
            self._index(name)
        # 已由本 owner 持有的资源不重复计算
        busy = [name for name in wanted
                if (self.owners.get(name) is not None
                    and self.owners.get(name) != owner)]
        if busy:
            name = busy[0]
            holder = self.owners.get(name)
            self._wait(name, owner, task_id, sim_time, holder,
                       f"资源集等待; {reason}")
            return False, name
        violations = []
        held = self.held_by(owner)
        if held:
            last = max(self._index(name) for name in held)
            violations = [name for name in wanted
                          if self._index(name) < last]
        if violations:
            self._event(sim_time, "ORDER_VIOLATION", violations[0], owner,
                        task_id, f"已持有更靠后的资源; {reason}")
            return False, violations[0]
        for name in wanted:
            self.acquire(name, owner, task_id, sim_time, reason)
        return True, ""
