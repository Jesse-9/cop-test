"""v1 依赖边界测试: 产品入口不导入旧 stage/reference 包."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"


def _run(code: str) -> str:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                            capture_output=True, text=True, env=env,
                            timeout=120)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_cli_does_not_import_legacy_packages():
    out = _run(
        "import flexible_factory.cli; import sys; "
        "banned=sorted({m.split('.')[0] for m in sys.modules} & "
        "{'stage1','stage2','stage3','stage4','reference','stage1_1'}); "
        "print(','.join(banned))")
    assert out == "", f"legacy imports: {out}"


def test_domain_does_not_import_simulation():
    out = _run(
        "import flexible_factory.domain.materials, "
        "flexible_factory.domain.tasks, flexible_factory.domain.resources, "
        "flexible_factory.domain.metrics; import sys; "
        "banned=sorted(m for m in sys.modules "
        "if m.startswith('flexible_factory.simulation')); "
        "print(','.join(banned))")
    assert out == "", f"domain imported simulation: {out}"


def test_simulation_does_not_import_application_or_cli():
    out = _run(
        "import flexible_factory.simulation.scene, "
        "flexible_factory.simulation.runtime, "
        "flexible_factory.simulation.unit_controller; import sys; "
        "banned=sorted(m for m in sys.modules "
        "if m.startswith('flexible_factory.application') "
        "or m == 'flexible_factory.cli'); print(','.join(banned))")
    assert out == "", f"simulation imported application: {out}"


def test_scheduler_policy_does_not_import_simulation_or_application():
    out = _run(
        "import flexible_factory.domain.scheduling; import sys; "
        "banned=sorted(m for m in sys.modules "
        "if m.startswith('flexible_factory.simulation') "
        "or m.startswith('flexible_factory.application')); "
        "print(','.join(banned))")
    assert out == "", f"scheduler imported runtime layer: {out}"


def test_mcp_registration_is_removed_for_v1():
    assert not (ROOT / ".mcp.json").exists(), "v1 已移除旧 factory-sim MCP 注册"
