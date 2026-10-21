"""stage2 — 阶段二: 物理约束、碰撞检查、运动规划与运行期联锁.

模块边界 (06-09 §9 实施包):

    config.py            阶段二全部新参数 (阶段一基线仍来自 stage1.config)
    contact.py           物料局部顶面接触几何 (纯几何)
    collisions.py        对象角色 / 碰撞集合 / 4.10 查询适配
    shadow.py            同基座影子 UR5 与构型探针 (会话资源)
    path_planning.py     构型/路径检查与有限局部规划
    trajectory.py        Cartesian 法向段与流式执行器
    safety.py            运行期联锁与现场锁存
    conveyor_safety.py   停带判据、受限补偿与物料稳定
    suction.py           外表面接触吸附与无瞬移释放
    unit_controller.py   阶段二单步控制器 (继承阶段一状态机)
    runtime.py           会话资源装配/清理
    scene_contract.py    stage2 manifest extra 契约与只读校验
    run.py               阶段二 runner 与 CLI

阶段一默认调用行为不由本包改变; 进入阶段二必须通过 stage2 工件显式路由。
"""

__version__ = "0.1.0"
