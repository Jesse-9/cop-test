# tests/test_state_codes.py
"""状态码映射纯单测 — 无需 CoppeliaSim。"""
from reference.factory_mcp_server import _STATE_CODE_NAMES


def test_known_codes():
    assert _STATE_CODE_NAMES[0] == "stopped"
    assert 0 in _STATE_CODE_NAMES


def test_unknown_code_fallback():
    assert _STATE_CODE_NAMES.get(99999, "unknown") == "unknown"
