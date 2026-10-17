# 测试文件：tests/test_factory_config_values.py
# 用于验证 factory_config.py 中的关键参数

import factory_config as cfg


def test_sim_step():
    """验证 SIM_STEP 是否为 0.05 秒"""
    assert cfg.SIM_STEP == 0.05, f"SIM_STEP 应为 0.05，实际为 {cfg.SIM_STEP}"


def test_conveyor_speed():
    """验证 CONVEYOR_SPEED 是否为 0.5 m/s"""
    assert cfg.CONVEYOR_SPEED == 0.5, f"CONVEYOR_SPEED 应为 0.5，实际为 {cfg.CONVEYOR_SPEED}"


def test_agv_speed():
    """验证 AGV_SPEED 是否为 0.8 m/s"""
    assert cfg.AGV_SPEED == 0.8, f"AGV_SPEED 应为 0.8，实际为 {cfg.AGV_SPEED}"


def test_takt_time_a():
    """验证 TAKT_TIME['A'] 是否为 5.0 秒"""
    assert cfg.TAKT_TIME['A'] == 5.0, f"TAKT_TIME['A'] 应为 5.0，实际为 {cfg.TAKT_TIME['A']}"


def test_zmq_timeout():
    """验证 ZMQ_TIMEOUT 是否为 3.0 秒"""
    assert cfg.ZMQ_TIMEOUT == 3.0, f"ZMQ_TIMEOUT 应为 3.0，实际为 {cfg.ZMQ_TIMEOUT}"


if __name__ == "__main__":
    print("Running factory_config parameter tests...")
    test_sim_step()
    test_conveyor_speed()
    test_agv_speed()
    test_takt_time_a()
    test_zmq_timeout()
    print("All tests passed!")