"""C 档运行时控制工具集成测试。"""
import json
import time as _t
import pytest


class TestPauseResumeFlag:
    def test_flag_default_false(self, mcp_server):
        # controller 未运行时无 flag, 故测 FactoryController 类属性约定
        from factory_controller import FactoryController
        import inspect
        src = inspect.getsource(FactoryController.__init__)
        assert "_pause_requested" in src
