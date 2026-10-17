# tests/test_mcp_console_tools.py
"""tail_coppelia_console 工具测试 (纯文件读写, 不依赖 CoppeliaSim)。"""
import json

import pytest


class TestReadConsoleSince:
    def test_reads_from_offset(self, tmp_path):
        from factory_mcp_server import _read_console_since
        p = tmp_path / "coppelia_console.log"
        p.write_bytes(b"AAA\nBBB\n")
        r = _read_console_since(p, 4)
        assert r["content"] == "BBB\n"
        assert r["next_offset"] == p.stat().st_size
        assert r["file_size"] == p.stat().st_size
        assert r["reset"] is False

    def test_offset_beyond_eof_resets(self, tmp_path):
        from factory_mcp_server import _read_console_since
        p = tmp_path / "coppelia_console.log"
        p.write_text("NEW\n", encoding="utf-8")
        r = _read_console_since(p, 999)
        assert r["content"] == "NEW\n"
        assert r["reset"] is True

    def test_negative_offset_resets(self, tmp_path):
        from factory_mcp_server import _read_console_since
        p = tmp_path / "coppelia_console.log"
        p.write_text("NEW\n", encoding="utf-8")
        r = _read_console_since(p, -1)
        assert r["content"] == "NEW\n"
        assert r["reset"] is True


class TestTailCoppeliaConsoleTool:
    def test_missing_file(self, tmp_path, monkeypatch):
        import factory_mcp_server as srv
        monkeypatch.setattr(srv, "_console_log_path",
                            lambda: tmp_path / "nope.log")
        r = srv.tail_coppelia_console()
        assert "不存在" in r
        assert "ConPTY" in r

    def test_tail_lines(self, tmp_path, monkeypatch):
        import factory_mcp_server as srv
        p = tmp_path / "coppelia_console.log"
        p.write_text("l1\nl2\nl3\n", encoding="utf-8")
        monkeypatch.setattr(srv, "_console_log_path", lambda: p)
        assert srv.tail_coppelia_console(lines=2) == "l2\nl3"

    def test_incremental_follow(self, tmp_path, monkeypatch):
        import factory_mcp_server as srv
        p = tmp_path / "coppelia_console.log"
        p.write_text("one\n", encoding="utf-8")
        monkeypatch.setattr(srv, "_console_log_path", lambda: p)

        first = json.loads(srv.tail_coppelia_console(offset=0))
        assert first["content"] == "one\n"

        with p.open("a", encoding="utf-8") as f:
            f.write("two\n")
        second = json.loads(
            srv.tail_coppelia_console(offset=first["next_offset"]))
        assert second["content"] == "two\n"
        assert second["reset"] is False
        assert second["next_offset"] == p.stat().st_size

    def test_default_offset_returns_json(self, tmp_path, monkeypatch):
        """offset=0 (而非 None) 走 JSON 增量分支。"""
        import factory_mcp_server as srv
        p = tmp_path / "coppelia_console.log"
        p.write_text("", encoding="utf-8")
        monkeypatch.setattr(srv, "_console_log_path", lambda: p)
        d = json.loads(srv.tail_coppelia_console(offset=0))
        assert d["ok"] is True
        assert d["content"] == ""
        assert d["next_offset"] == 0
