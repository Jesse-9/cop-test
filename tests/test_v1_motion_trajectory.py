"""v1 运动层流式执行器: 逐步复核、保持与取消测试."""

from flexible_factory.simulation import config_motion as cfg
from flexible_factory.simulation.motion.path_planning import PlanStatus, SegmentCheck
from flexible_factory.simulation.motion.trajectory import ExecutorStatus, JointStreamExecutor


class Plan:
    def __init__(self, waypoints):
        self.waypoints = waypoints


class Checker:
    def __init__(self, collide=False):
        self.collide = collide
        self.calls = 0

    def check_segment(self, q0, q1, context, **kwargs):
        self.calls += 1
        if self.collide:
            return SegmentCheck(PlanStatus.PATH_COLLISION, list(q0),
                                list(q1), message="blocked")
        return SegmentCheck(PlanStatus.OK, list(q0), list(q1), samples=2)


def make_executor(collide=False):
    plan = Plan([[0.0] * 6, [0.5] * 6])
    return JointStreamExecutor(plan, Checker(collide), [object()],
                               max_joint_step=0.2)


def test_stepwise_command_and_done():
    ex = make_executor()
    actual = [0.0] * 6
    step = ex.next_command(actual, 0.0)
    assert step.status is ExecutorStatus.RUNNING
    assert max(step.q) == cfg.EXEC_MAX_JOINT_STEP / (6 ** 0.5)
    # 模拟关节达到命令
    actual = list(step.q)
    for i in range(1, 20):
        step = ex.next_command(actual, i * 0.05)
        if step.done:
            break
        actual = list(step.q)
    assert step.done
    assert actual == [0.5] * 6


def test_holding_does_not_push_target_further():
    ex = make_executor()
    first = ex.next_command([0.0] * 6, 0.0)
    # 实际关节没有动: 应保持上一条命令, 而不是继续叠加
    held = ex.next_command([0.0] * 6, 0.05)
    assert held.status is ExecutorStatus.RUNNING
    assert held.q == first.q
    assert held.check.get("holding") is True


def test_cancel_stops_future_commands():
    ex = make_executor()
    ex.next_command([0.0] * 6, 0.0)
    ex.cancel()
    step = ex.next_command([0.0] * 6, 0.05)
    assert step.status is ExecutorStatus.CANCELLED
    assert step.q == [0.0] * 6


def test_collision_blocks_before_command():
    ex = make_executor(collide=True)
    step = ex.next_command([0.0] * 6, 0.0)
    assert step.status is ExecutorStatus.COLLISION
    assert step.q == [0.0] * 6


def test_deviation_beyond_limit():
    ex = make_executor()
    ex.next_command([0.0] * 6, 0.0)
    actual = [cfg.EXEC_MAX_DEVIATION + 0.05] * 6
    step = ex.next_command(actual, 0.05)
    assert step.status is ExecutorStatus.DEVIATION
