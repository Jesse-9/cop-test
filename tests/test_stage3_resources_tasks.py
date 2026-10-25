"""阶段三资源锁表与任务队列测试 (06-12 §3.2/§3.4)."""

import pytest

from stage3 import config as cfg
from stage3.resources import ResourceTable, UnknownResourceError
from stage3.tasks import (Stage3Task, TaskError, TaskKind, TaskQueue,
                          TaskStatus, make_task)


def test_resource_acquire_release_and_snapshot():
    table = ResourceTable()
    assert table.acquire("pick_zone", "unit_A", "t1", 1.0)
    assert table.owner("pick_zone") == "unit_A"
    assert table.acquire("pick_zone", "unit_A", "t1", 1.1)  # 幂等
    assert not table.acquire("pick_zone", "unit_B", "t2", 1.2)
    assert "unit_B" in table.waiting["pick_zone"]
    assert table.release("pick_zone", "unit_A", 2.0)
    assert table.is_free("pick_zone")
    assert not table.release("pick_zone", "unit_A", 2.1)
    snap = table.snapshot()
    assert snap["owners"]["pick_zone"] is None
    assert any(e["action"] == "WAIT" for e in table.as_dict()["events"])


def test_fixed_order_rejects_reverse_acquisition():
    table = ResourceTable()
    assert table.acquire("dock_A", "unit_A", "t1", 1.0)
    assert not table.acquire("pick_zone", "unit_A", "t1", 1.1)
    assert any(e["action"] == "ORDER_VIOLATION"
               for e in table.as_dict()["events"])


def test_release_all_and_release_any():
    table = ResourceTable()
    for name in ("pick_zone", "dock_A", "arm_A"):
        assert table.acquire(name, "unit_A", "t1", 1.0)
    released = table.release_all("unit_A", 2.0, "done")
    assert set(released) == {"pick_zone", "dock_A", "arm_A"}
    assert table.validate() and all(c["ok"] for c in table.validate())


def test_unknown_resource_rejected():
    with pytest.raises(UnknownResourceError):
        ResourceTable().acquire("not_a_resource", "x", "t", 0.0)


def test_task_queue_deduplicates_active_same_kind_uid():
    queue = TaskQueue()
    task = make_task(TaskKind.LOAD_A, 1, source="conveyor",
                     destination="AGV", dock="A")
    assert queue.submit(task)[0]
    dup = make_task(TaskKind.LOAD_A, 1, source="conveyor",
                    destination="AGV", dock="A")
    ok, reason = queue.submit(dup)
    assert not ok and "重复请求" in reason
    assert len(queue.rejections) == 1
    # 不同 UID 可以提交
    assert queue.submit(make_task(TaskKind.LOAD_A, 2, source="conveyor",
                                  destination="AGV", dock="A"))[0]


def test_task_terminal_states_cannot_be_rewritten():
    queue = TaskQueue()
    task = make_task(TaskKind.TRANSPORT, 1, source="dock_A",
                     destination="dock_B", dock="B")
    queue.submit(task)
    queue.start(task.task_id, 1.0)
    queue.fail(task.task_id, 2.0, "blocked forever")
    with pytest.raises(TaskError):
        queue.done(task.task_id, 3.0)
    assert queue.get(task.task_id).status is TaskStatus.FAILED
    assert queue.any_failed()


def test_task_done_requires_evidence_and_all_done():
    queue = TaskQueue()
    task = make_task(TaskKind.UNLOAD_B, 1, source="AGV",
                     destination="Station_B", dock="B")
    queue.submit(task)
    queue.start(task.task_id, 1.0)
    queue.done(task.task_id, 2.0, {"uid": 1, "placed": True})
    assert queue.all_done()
    assert queue.get(task.task_id).evidence["placed"] is True


def test_block_and_resume_are_recorded():
    queue = TaskQueue()
    task = make_task(TaskKind.TRANSPORT, 1, source="dock_A",
                     destination="dock_B", dock="B")
    queue.submit(task)
    queue.start(task.task_id, 1.0)
    queue.block(task.task_id, 2.0, "obstacle")
    assert queue.get(task.task_id).status is TaskStatus.BLOCKED
    queue.unblock(task.task_id, 3.0, "cleared")
    assert queue.get(task.task_id).status is TaskStatus.RUNNING
    record = queue.get(task.task_id)
    assert record.evidence["blocks"][0]["reason"] == "obstacle"
    assert record.evidence["resumes"][0]["reason"] == "cleared"


def test_task_flow_metadata_matches_config():
    data = TaskQueue().as_dict()
    assert data["flow"] == list(cfg.TASK_FLOW)
    assert data["duplicate_policy"] == cfg.DUPLICATE_REQUEST_POLICY
