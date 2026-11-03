"""v1 运动层运行期联锁与锁存测试."""

from flexible_factory.simulation import config_motion as cfg
from flexible_factory.simulation.motion.safety import SafetyObservation, Stage2Safety


def obs(**overrides):
    values = dict(
        sim_time=1.0, phase="TRANSFERRING",
        actual_q=[0.0] * 6, commanded_q=[0.0] * 6,
        tcp_pose=[0.0, 0.0, 0.5, 0, 0, 0, 1],
        expected_tcp=[0.0, 0.0, 0.5, 0, 0, 0, 1],
        material_pose=None, material_expected=None, attached=False,
        details={"moving": False})
    values.update(overrides)
    return SafetyObservation(**values)


def test_no_violation_when_tracking():
    safety = Stage2Safety()
    assert safety.evaluate(obs()) == []


def test_joint_deviation_latches():
    safety = Stage2Safety()
    actual = [0.0] * 6
    actual[0] = cfg.EXEC_MAX_DEVIATION + 0.01
    violations = safety.evaluate(obs(actual_q=actual))
    assert any("JOINT_DEVIATION" in v for v in violations)
    safety.request_stop("; ".join(violations), sim_time=1.0)
    assert safety.engaged
    assert safety.latch.code == cfg.SAFETY_LATCH_CODE


def test_tcp_unreadable_is_not_safe():
    safety = Stage2Safety()
    violations = safety.evaluate(obs(expected_tcp=None))
    assert "TCP_UNREADABLE" in violations


def test_no_command_yet_does_not_require_tcp():
    safety = Stage2Safety()
    observation = obs(commanded_q=None, expected_tcp=None,
                      tcp_pose=None, details={"moving": False})
    assert safety.evaluate(observation) == []


def test_moving_skips_tcp_drift_but_checks_deviation():
    safety = Stage2Safety()
    far_tcp = [1.0, 1.0, 1.0, 0, 0, 0, 1]
    observation = obs(tcp_pose=far_tcp, expected_tcp=[0, 0, 0.5, 0, 0, 0, 1],
                      details={"moving": True})
    assert safety.evaluate(observation) == []


def test_load_drift_detected_when_attached_and_settled():
    safety = Stage2Safety()
    observation = obs(
        attached=True,
        material_pose=[0.0, 0.0, 0.6, 0, 0, 0, 1],
        material_expected=[0.0, 0.0, 0.5, 0, 0, 0, 1],
        details={"moving": False})
    violations = safety.evaluate(observation)
    assert any("LOAD_DRIFT" in v for v in violations)


def test_stop_confirmation_separates_command_from_readback():
    safety = Stage2Safety()
    safety.request_stop("test", sim_time=2.0)
    safety.note_stop_command(True, sim_time=2.0)
    assert safety.latch.stop_commanded is True
    assert safety.latch.stop_confirmed is False
    assert safety.confirm_stop(speed_known=False, speed=None) is False
    assert safety.latch.stop_confirmed is False
    assert safety.confirm_stop(speed_known=True, speed=0.001) is True
    assert safety.latch.stop_confirmed is True


def test_stop_from_position_requires_pad_still():
    safety = Stage2Safety()
    safety.request_stop("test")
    assert safety.confirm_stop_from_position(pad_still=False) is False
    assert safety.confirm_stop_from_position(pad_still=True) is True
    assert safety.latch.stop_source == "pad_displacement"


def test_latch_is_sticky():
    safety = Stage2Safety()
    safety.request_stop("first")
    safety.request_stop("second")
    assert safety.latch.reason == "first"
