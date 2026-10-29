"""run_stage4.py — 阶段四多岛车间 CLI 薄包装 (兼容 `python src/run_stage4.py`)."""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from stage4.run import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
