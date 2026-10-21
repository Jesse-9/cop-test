"""阶段二 CLI 兼容入口 — 实际实现在 :mod:`stage2.run`.

用法:
    .venv/Scripts/python src/run_stage2.py --target station
    .venv/Scripts/python src/run_stage2.py --target both --time 180
    .venv/Scripts/python src/run_stage2.py --target station \
        --fault runtime_obstacle

运行前必须已有 stage2 工件:
    python src/run_scene_lifecycle.py load stage1/L1_S1/v002_...
    python src/run_scene_lifecycle.py validate
    python src/run_scene_lifecycle.py save --phase stage2 \
        --parent stage1/L1_S1/v002_...
"""

from stage2.run import main

if __name__ == "__main__":
    raise SystemExit(main())
