"""v1 运动层停带判据、受限补偿与稳定窗口测试."""

import pytest

from flexible_factory.simulation.conveyor import BeltGeometry
from flexible_factory.simulation import config_motion as cfg
from flexible_factory.simulation.motion.conveyor_safety import (ConveyorStopMonitor, SpeedReading,
                                    StopStatus, MaterialStabilityTracker,
                                    bounded_transport_step)

GEOMETRY = BeltGeometry((0.0, 0.0, 0.45), (1.0, 0.0, 0.45))


def valid(value):
    return SpeedReading(value=value, valid=True, source="model_state")


def invalid():
    return SpeedReading.unavailable("state_missing")


def test_stop_confirmed_from_valid_velocity():
    monitor = ConveyorStopMonitor(GEOMETRY)
    monitor.on_stop_command(0.0, [0.5, 0.0, 0.45], 0.2)
    assert monitor.observe(0.05, valid(0.0), [0.5, 0.0, 0.45],
                           [0.0, 0.0, 0.0]) is StopStatus.PENDING
    status = monitor.observe(0.10, valid(0.0), [0.5, 0.0, 0.45],
                             [0.0, 0.0, 0.0])
    assert status is StopStatus.OK
    assert monitor.measurement.latency == pytest.approx(0.10)


def test_stop_fallback_requires_pad_still():
    monitor = ConveyorStopMonitor(GEOMETRY)
    monitor.on_stop_command(0.0, [0.5, 0.0, 0.45], 0.2)
    monitor.observe(0.05, invalid(), [0.5, 0.0, 0.45], [0.1, 0, 0])
    monitor.observe(0.10, invalid(), [0.5, 0.0, 0.45], [0.1, 0, 0])
    status = monitor.observe(0.15, invalid(), [0.5, 0.0, 0.45],
                             [0.1, 0, 0])
    assert status is StopStatus.OK
    assert monitor.measurement.speed_source == "pad_displacement_fallback"


def test_unknown_speed_and_missing_pad_is_unconfirmed():
    monitor = ConveyorStopMonitor(GEOMETRY, max_steps=3)
    monitor.on_stop_command(0.0, [0.5, 0.0, 0.45], 0.2)
    status = StopStatus.PENDING
    for i in range(3):
        status = monitor.observe(0.05 * (i + 1), invalid(),
                                 [0.5, 0.0, 0.45], None)
    assert status is StopStatus.STOP_UNCONFIRMED


def test_overrun_fails_immediately():
    monitor = ConveyorStopMonitor(GEOMETRY)
    monitor.on_stop_command(0.0, [0.5, 0.0, 0.45], 0.2)
    status = monitor.observe(0.05, valid(0.1), [0.54, 0.0, 0.45],
                             [0.0, 0, 0])
    assert status is StopStatus.OVERRUN_EXCEEDED


def test_latency_exceeded():
    monitor = ConveyorStopMonitor(GEOMETRY, max_latency=0.20)
    monitor.on_stop_command(0.0, [0.5, 0.0, 0.45], 0.2)
    monitor.observe(0.30, valid(0.0), [0.5, 0.0, 0.45], [0, 0, 0])
    status = monitor.observe(0.35, valid(0.0), [0.5, 0.0, 0.45], [0, 0, 0])
    assert status is StopStatus.LATENCY_EXCEEDED


def test_compensation_only_for_lag():
    step = bounded_transport_step([0.50, 0.0, 0.45], [0.49, 0.0, 0.45],
                                  GEOMETRY, 0.2, 0.05)
    assert step.physical_delta == pytest.approx(0.01)
    assert step.delta == pytest.approx(0.0)

    step = bounded_transport_step([0.50, 0.0, 0.45], [0.495, 0.0, 0.45],
                                  GEOMETRY, 0.2, 0.05)
    assert step.shortfall == pytest.approx(0.005)
    assert step.delta == pytest.approx(0.005)
    assert step.new_position[0] == pytest.approx(0.505)


def test_compensation_not_applied_when_ahead():
    step = bounded_transport_step([0.52, 0.0, 0.45], [0.49, 0.0, 0.45],
                                  GEOMETRY, 0.2, 0.05)
    assert step.overspeed is True
    assert step.delta == pytest.approx(0.0)


def test_compensation_capped_by_max_step():
    step = bounded_transport_step([0.40, 0.0, 0.45], [0.40, 0.0, 0.45],
                                  GEOMETRY, 0.2, 0.05,
                                  max_step=cfg.COMPENSATION_MAX_STEP)
    assert step.delta == pytest.approx(cfg.COMPENSATION_MAX_STEP)


def test_stability_tracker():
    tracker = MaterialStabilityTracker(0.0, [0.5, 0.0, 0.45])
    state = None
    for i in range(1, cfg.CONVEYOR_MATERIAL_STABLE_STEPS + 1):
        state = tracker.update(i * 0.05, [0.5, 0.0, 0.45], 0.0)
    assert state.stable is True
    assert state.count >= cfg.CONVEYOR_MATERIAL_STABLE_STEPS


def test_stability_timeout():
    tracker = MaterialStabilityTracker(0.0, [0.5, 0.0, 0.45],
                                       timeout=0.1)
    state = tracker.update(0.2, [0.6, 0.0, 0.45], 0.5)
    assert state.stable is False
    assert state.timed_out is True
