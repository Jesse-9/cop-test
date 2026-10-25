"""run_stage3.py — 阶段三双工位 CLI 薄包装 (兼容 `python src/run_stage3.py`)."""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from stage3.run import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
