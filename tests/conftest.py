# tests/conftest.py
"""共享测试环境: 让 tests/ 能 import src/ 下的 v1 包.

测试只导入 `flexible_factory`; 旧阶段包已删除.
"""
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
