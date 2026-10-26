"""CLI 与 MCP 生命周期动作语义一致性 (离线)。"""

import asyncio
import inspect
import json

from stage1 import lifecycle


def _cli_actions():
    parser = lifecycle.build_parser()
    for action in parser._actions:
        if action.__class__.__name__ == "_SubParsersAction":
            return set(action.choices)
    return set()


def _cli_subparser(name):
    parser = lifecycle.build_parser()
    for action in parser._actions:
        if action.__class__.__name__ == "_SubParsersAction":
            return action.choices[name]
    raise AssertionError("CLI 缺少子命令解析器")


def test_cli_exposes_all_lifecycle_actions():
    assert _cli_actions() >= {
        "build", "save", "load", "validate", "start", "stop", "status",
        "list",
    }


def test_cli_help_documents_startup_validation():
    assert "启动点" in _cli_subparser("start").format_help()
    assert "可选诊断" in _cli_subparser("validate").format_help()
    assert "可直接 start" in _cli_subparser("load").format_help()


def test_mcp_exposes_matching_lifecycle_tools():
    import reference.factory_mcp_server as mcp_server

    tools = asyncio.run(mcp_server.mcp.list_tools())
    names = {t.name for t in tools}
    assert {
        "build_scene", "save_scene", "load_scene", "validate_scene",
        "start_simulation", "stop_simulation", "lifecycle_status",
    } <= names


def test_mcp_tool_count_is_24():
    import reference.factory_mcp_server as mcp_server

    tools = asyncio.run(mcp_server.mcp.list_tools())
    assert len(tools) == 24


def test_start_simulation_doc_says_no_implicit_build():
    import reference.factory_mcp_server as mcp_server

    doc = inspect.getdoc(mcp_server.start_simulation) or ""
    assert "不重建场景" in doc or "不执行 build" in doc


def test_start_simulation_doc_allows_loaded_start_with_startup_validation():
    import reference.factory_mcp_server as mcp_server

    doc = inspect.getdoc(mcp_server.start_simulation) or ""
    assert "已加载" in doc
    assert "启动点" in doc
    assert "validate_scene" in doc


def test_load_scene_doc_no_longer_requires_manual_validate():
    import reference.factory_mcp_server as mcp_server

    doc = inspect.getdoc(mcp_server.load_scene) or ""
    assert "可直接 start_simulation" in doc
    assert "validate_scene 仅用于可选" in doc
