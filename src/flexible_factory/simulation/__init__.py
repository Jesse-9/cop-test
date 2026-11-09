"""simulation — v1 五岛场景、布局、物理、运动与仿真适配.

文件名中的 ``_base`` / ``_motion`` / ``_dual`` 表示能力来源和继承层级,
不表示废弃, 也不能仅因名称带阶段编号而删除:

  - ``*_base``: 基础层 (单站闭环);
  - ``*_motion``: 运动层 (碰撞、规划、轨迹、安全、吸附);
  - ``*_dual``: 双站层 (L2_MIRROR);
  - ``config.py`` / ``layout.py`` / ``scene.py`` / ``unit_controller.py`` /
    ``runtime.py``: v1 五岛 (L5_UFLOW) 生产路径.

兼容层:
  - ``unit_controller_dual.py`` / ``runtime_dual.py`` 只 re-export 已并入
    ``unit_controller.py`` / ``runtime.py`` 的实现;
  - ``motion/path_planning.py`` 是 ``motion/plan_*`` 模块的兼容导出层.

模块职责、消费者和遗留代码处置见
``docs/implementation/reference/simulation/simulation-module-inventory.md``.
"""
