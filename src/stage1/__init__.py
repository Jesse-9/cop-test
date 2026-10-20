"""阶段一单元闭环包 — stage 1 unit-cell simulation.

阶段一只包含一条传送带、一个 UR5、一个 suction pad、一个 pioneer p3dx
和指定物料。子模块:

    config                  参数与别名
    layout                  LayoutSpec / 布局几何校验
    physics                 物理属性工具
    scene                   单工位场景构建
    device_handle_map       启动期句柄表
    material_manager        物料生命周期
    conveyor_controller     传送带驱动与受限补偿
    ur5_ik_controller       simIK 与关节目标
    suction_controller      逻辑吸附/释放
    pioneer_controller      差速轮控制
    unit_controller         §2.11 单步状态机
    run                     CLI 入口

入口: ``python src/run_stage1.py`` 或 ``uv run run-stage1``。
"""

__all__ = [
    "config", "layout", "physics", "scene", "device_handle_map",
    "material_manager", "conveyor_controller", "ur5_ik_controller",
    "suction_controller", "pioneer_controller", "unit_controller", "run",
]
