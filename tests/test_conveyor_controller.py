"""tests/test_conveyor_controller.py — 传送带控制器纯逻辑测试 (阶段一 §2.4)

不需要 CoppeliaSim 连接。使用轻量伪造对象模拟 sim。
"""

from types import SimpleNamespace

from conveyor_controller import (
    ConveyorController,
    ConveyorHandle,
    ConveyorState,
    ConveyorError,
)


def _make_sim():
    """FakeSim 模拟 4.10 内置 conveyor 脚本的 buffer 语义:
    __ctrl__ 写 JSON 文本, __state__ 回读同一份 vel。
    """
    buf = {"vel": 0.0}

    class FakeSim:
        def setBufferProperty(self, h, prop, value):
            if prop == "customData.__ctrl__":
                import json
                buf["vel"] = float(json.loads(value)["vel"])

        def readCustomTableData(self, h, name):
            if name == "__state__":
                return {"vel": buf["vel"], "pos": 0.0}
            return None

    return FakeSim(), buf


def _make_handle(sim, alias="Conveyor_Test", vel_mode="SCRIPT_DRIVEN"):
    return ConveyorHandle(
        alias=alias,
        sim_handle=7,
        native_length=1.216,
        actual_length=2.0,
        width=0.5,
        surface_z=0.592,
        pad_range_x=(-0.497, 0.500),
        sim=sim,
        state=ConveyorState.IDLE,
        velocity=0.0,
        conveyor_mode=vel_mode,
    )


def test_register_and_by_alias():
    sim, _ = _make_sim()
    ctl = ConveyorController(sim)
    h = _make_handle(sim)
    ctl.register(h)
    assert ctl.by_alias(h.alias) is h
    assert ctl.aliases() == [h.alias]


def test_unknown_alias_is_none_not_error():
    """by_alias 对未知别名返回 None (查询接口); 仅 set_speed 等操作接口报错"""
    sim, _ = _make_sim()
    ctl = ConveyorController(sim)
    assert ctl.by_alias("NoSuch") is None
    try:
        ctl.set_speed("NoSuch", 0.5)
    except ConveyorError:
        pass
    else:
        raise AssertionError("set_speed 应对未知别名抛出 ConveyorError")


def test_set_speed_running_and_idle():
    sim, buf = _make_sim()
    ctl = ConveyorController(sim)
    h = _make_handle(sim)
    ctl.register(h)

    ctl.set_speed(h.alias, 0.5)
    assert h.state is ConveyorState.RUNNING
    assert h.velocity == 0.5
    # SCRIPT_DRIVEN 应把 vel 写入内置脚本的 buffer
    assert buf["vel"] == 0.5

    ctl.set_speed(h.alias, 0.0)
    assert h.state is ConveyorState.IDLE
    assert buf["vel"] == 0.0


def test_is_item_in_valid_zone():
    sim, _ = _make_sim()
    ctl = ConveyorController(sim)
    h = _make_handle(sim)
    ctl.register(h)
    # pad_range_x = (-0.497, 0.500), inset=0.08 -> [-0.417, 0.420]
    assert ctl.is_item_in_valid_zone(h.alias, -0.4) is True
    assert ctl.is_item_in_valid_zone(h.alias, 0.4) is True
    assert ctl.is_item_in_valid_zone(h.alias, -0.5) is False  # 超出滚筒
    assert ctl.is_item_in_valid_zone(h.alias, 0.49) is False  # 超出滚筒


def test_read_actual_velocity_script_driven():
    sim, state = _make_sim()
    ctl = ConveyorController(sim)
    h = _make_handle(sim)
    ctl.register(h)
    ctl.set_speed(h.alias, 0.5)
    assert abs(ctl.read_actual_velocity(h.alias) - 0.5) < 1e-9


def test_fallback_mode_no_buffer_write():
    sim, buf = _make_sim()
    ctl = ConveyorController(sim)
    h = _make_handle(sim, vel_mode="FALLBACK_GEOMETRY")
    ctl.register(h)
    ctl.set_speed(h.alias, 0.5)
    # 不应通过内置脚本的 buffer 驱动 (由调用层写 pad 位姿)
    assert buf["vel"] == 0.0
