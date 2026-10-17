"""阶段一 CLI 兼容入口 — 实际实现在 :mod:`stage1.run`.

保留该薄包装是为了让现有命令 ``python src/run_stage1.py ...`` 继续可用；
新的调用方式也可以使用 ``uv run run-stage1``。
"""

from stage1.run import main

if __name__ == "__main__":
    raise SystemExit(main())
