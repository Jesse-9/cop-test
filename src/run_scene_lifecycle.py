"""步骤 1.2 场景生命周期 CLI 兼容入口.

用法:
    .venv/Scripts/python src/run_scene_lifecycle.py build
    .venv/Scripts/python src/run_scene_lifecycle.py save --phase stage1
    .venv/Scripts/python src/run_scene_lifecycle.py load current
    .venv/Scripts/python src/run_scene_lifecycle.py validate
    .venv/Scripts/python src/run_scene_lifecycle.py start --target station
    .venv/Scripts/python src/run_scene_lifecycle.py stop
    .venv/Scripts/python src/run_scene_lifecycle.py status

实际实现在 :mod:`stage1.lifecycle`.
"""

from stage1.lifecycle import main

if __name__ == "__main__":
    raise SystemExit(main())
