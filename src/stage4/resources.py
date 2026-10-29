"""resources.py — 阶段四固定顺序资源表与全有或全无申请 (06-16 §F4-4).

在阶段三小型锁表基础上扩展:
  - 资源覆盖四条物流线、A→C 交接、E 两个缓冲、四个 dock、AGV 通道、
    三个臂间互锁区和五台臂;
  - **只做调度互斥**, 不代替碰撞检查, 不清除 `Stage2Safety` 锁存;
  - 任务必须一次性申请完整资源集 (`acquire_all`): 任一资源忙则只记录
    等待, 不持有部分资源, 从结构上消除循环等待;
  - 资源申请顺序由 `RESOURCE_ORDER` 固定, 仍校验顺序违规.

本模块是纯 Python, 不导入 sim.
"""

from __future__ import annotations

from typing import Iterable, Optional

from stage3.resources import ResourceTable as _BaseResourceTable

from . import config as cfg


class ResourceTable(_BaseResourceTable):
    """阶段四资源表."""

    def __init__(self, order: Optional[Iterable[str]] = None) -> None:
        super().__init__(order=tuple(order or cfg.RESOURCE_ORDER))
        # 基类 __post_init__ 按阶段三资源重建 waiting; 这里改为阶段四键.
        self.waiting = {name: [] for name in self.order}

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

    # ── 报告 ────────────────────────────────────────────────

    def as_dict(self) -> dict:
        return {
            "policy_version": cfg.STAGE4_RESOURCE_POLICY_VERSION,
            "order": list(self.order),
            "roles": {name: cfg.RESOURCE_ROLES.get(name, "")
                      for name in self.order},
            "snapshot": self.snapshot(),
            "events": [event.as_dict() for event in self.events],
        }
