"""阶段四任务优先级/去重/状态与资源表测试."""

from __future__ import annotations

import pytest

from stage4 import config as cfg
from stage4.resources import ResourceTable
from stage4.tasks import (TaskError, TaskQueue, TaskStatus, make_task)


def test_dedupe_active_same_kind_uid():
    queue = TaskQueue()
    first = make_task(cfg.TASK_KIND_A_CONVEYOR, 1, source="line_s1a",
                      destination="handoff_ac")
    duplicate = make_task(cfg.TASK_KIND_A_CONVEYOR, 1, source="line_s1a",
                          destination="handoff_ac")
    assert queue.submit(first)[0]
    ok, reason = queue.submit(duplicate)
    assert not ok
    assert "重复请求" in reason
    assert len(queue.rejections) == 1


def test_different_kind_same_uid_allowed():
    queue = TaskQueue()
    assert queue.submit(make_task(cfg.TASK_KIND_B, 1, source="line_s1b",
                                  destination="agv"))[0]
    assert queue.submit(make_task(cfg.TASK_KIND_TRANSPORT_BA, 1,
                                  source="dock_B", destination="dock_A"))[0]


def test_priority_order_and_aging():
    queue = TaskQueue()
    low = make_task(cfg.TASK_KIND_TRANSPORT_DE, 2, source="dock_D",
                    destination="dock_E", sim_time=0.0)
    high = make_task(cfg.TASK_KIND_TRANSPORT_BA, 1, source="dock_B",
                     destination="dock_A", sim_time=0.5)
    queue.submit(low)
    queue.submit(high)
    assert [task.kind for task in queue.pending_transports(1.0)] == [
        cfg.TASK_KIND_TRANSPORT_BA, cfg.TASK_KIND_TRANSPORT_DE]
    # 老化后 D→E 与 B→A 同优先级, 先创建者优先
    assert [task.kind for task in queue.pending_transports(
        cfg.D_E_STARVATION_UPGRADE_S + 1.0)] == [
        cfg.TASK_KIND_TRANSPORT_DE, cfg.TASK_KIND_TRANSPORT_BA]
    assert low.effective_priority(20.0) == 0


def test_failed_cannot_become_done_and_done_cannot_fail():
    queue = TaskQueue()
    task = make_task(cfg.TASK_KIND_B, 1, source="line_s1b",
                     destination="agv")
    queue.submit(task)
    queue.start(task.task_id, 0.0)
    queue.fail(task.task_id, 1.0, "boom")
    with pytest.raises(TaskError):
        queue.done(task.task_id, 2.0)
    other = make_task(cfg.TASK_KIND_D, 2, source="line_s2d",
                      destination="agv")
    queue.submit(other)
    queue.start(other.task_id, 0.0)
    queue.done(other.task_id, 1.0)
    with pytest.raises(TaskError):
        queue.fail(other.task_id, 2.0, "late")


def test_wait_block_unblock_evidence():
    queue = TaskQueue()
    task = make_task(cfg.TASK_KIND_C, 3, source="handoff_ac",
                     destination="line_ce")
    queue.submit(task)
    queue.wait(task.task_id, 1.0, "handoff busy")
    assert task.status is TaskStatus.WAITING
    assert task.waiting_since == 1.0
    queue.start(task.task_id, 2.0)
    queue.block(task.task_id, 3.0, "agv blocked")
    assert task.status is TaskStatus.BLOCKED
    queue.unblock(task.task_id, 4.0, "clear")
    assert task.status is TaskStatus.RUNNING
    assert task.evidence["resumes"][0]["sim_time"] == 4.0


def test_resource_acquire_all_is_all_or_nothing():
    table = ResourceTable()
    resources = ("line_ce", "handoff_ac", "zone_ac", "zone_ce", "arm_C")
    ok, busy = table.acquire_all(resources, "unit_C/task_c", "c_uid1", 0.0)
    assert ok and busy == ""
    assert all(table.owner(name) == "unit_C/task_c" for name in resources)
    ok, busy = table.acquire_all(("line_ce", "arm_A"), "unit_A/task_a",
                                 "a_uid1", 1.0)
    assert not ok
    assert busy == "line_ce"
    assert table.owner("arm_A") is None          # 未持有部分资源
    assert table.owner("line_ce") == "unit_C/task_c"


def test_resource_release_all_and_order_validation():
    table = ResourceTable()
    ok, _ = table.acquire_all(("handoff_ac", "dock_A", "arm_A"),
                              "unit_A/task", "task", 0.0)
    assert ok
    table.release_all("unit_A/task", 1.0, "done")
    assert all(table.owner(name) is None for name in table.order)
    checks = table.validate()
    assert all(check["ok"] for check in checks)


def test_resource_order_violation_rejected():
    table = ResourceTable()
    assert table.acquire("arm_A", "owner", "task", 0.0)
    ok, busy = table.acquire_all(("line_s1a", "arm_A"), "owner", "task", 1.0)
    assert not ok                      # 已持有更靠后的资源
    assert table.owner("line_s1a") is None


def test_queue_capacity_and_rejection_record():
    queue = TaskQueue(capacity=1)
    assert queue.submit(make_task(cfg.TASK_KIND_B, 1, source="",
                                  destination=""))[0]
    ok, reason = queue.submit(make_task(cfg.TASK_KIND_D, 2, source="",
                                        destination=""))
    assert not ok
    assert "队列已满" in reason
    assert len(queue.rejections) == 1
