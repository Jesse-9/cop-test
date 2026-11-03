"""阶段 2 步骤 6: 执行适配层的静态边界 (不需要 CoppeliaSim).

三条约束来自 06-20 与实施方案 §7:
  1. adapter 不调用 ``sim.step()`` (唯一 stepping owner 仍是协调器);
  2. ``simulation/execution/`` 不导入 ``application`` / ``cli``;
  3. 适配层不输出句柄 (证据只写 device/zone/alias/execution_id).
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
EXECUTION_DIR = SRC / "flexible_factory" / "simulation" / "execution"
FORBIDDEN_HANDLE_KEYS = {"handle", "sim_handle", "handles"}


def _modules() -> list[Path]:
    return sorted(path for path in EXECUTION_DIR.glob("*.py"))


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_execution_modules_exist():
    names = {path.name for path in _modules()}
    assert {"__init__.py", "base.py", "arm.py", "agv.py"} <= names


def test_execution_modules_do_not_step_the_simulation():
    offenders: list[str] = []
    for path in _modules():
        for node in ast.walk(_parse(path)):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr != "step":
                continue
            target = func.value
            is_sim = (
                (isinstance(target, ast.Name) and target.id == "sim")
                or (isinstance(target, ast.Attribute)
                    and target.attr == "sim"))
            if is_sim:
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, f"adapter 不得调用 sim.step(): {offenders}"


def test_execution_modules_do_not_serialize_handles():
    offenders: list[str] = []
    for path in _modules():
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and node.value in FORBIDDEN_HANDLE_KEYS:
                offenders.append(f"{path.name}:{node.lineno}:{node.value}")
    assert not offenders, f"证据字段不得包含句柄: {offenders}"


def test_execution_package_does_not_import_application():
    code = (
        "import flexible_factory.simulation.execution; import sys; "
        "banned=sorted(m for m in sys.modules "
        "if m.startswith('flexible_factory.application') "
        "or m == 'flexible_factory.cli'); print(','.join(banned))")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                            capture_output=True, text=True, env=env,
                            timeout=120)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", \
        f"execution 导入了应用层: {result.stdout.strip()}"


def test_device_binding_does_not_hold_handles():
    path = SRC / "flexible_factory" / "simulation" / "device_binding.py"
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and node.value in FORBIDDEN_HANDLE_KEYS:
            raise AssertionError(
                f"绑定表不得持有句柄字段: line {node.lineno}")
