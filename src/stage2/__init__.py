"""stage2 — 阶段二: 物理约束、碰撞检查、运动规划与运行期联锁.

阶段 2.01 (动作轨迹优化) 在阶段二之上增加:

    branch.py            腕部分支判定、奇异边界裕量与跨支拒绝
    metrics.py           逐段关节 TV / 最大单步 / 分支 / TCP 误差指标

阶段 2.02 (AGV 转运路径优化, 默认关闭) 增加:

    diagnostics.py       逐 waypoint q/TCP/载荷/分支/净空证据
    (path_planning.py)   有界整链 beam 选链、分层代价、段内平滑

模块边界 (06-09 §9 实施包):

    config.py            阶段二全部新参数 (阶段一基线仍来自 stage1.config)
    contact.py           物料局部顶面接触几何 (纯几何)
    collisions.py        对象角色 / 碰撞集合 / 4.10 查询适配
    shadow.py            同基座影子 UR5 与构型探针 (会话资源)
    path_planning.py     构型/路径检查与有限局部规划 (分支/锁轴/压缩)
    trajectory.py        Cartesian 法向段、流式执行器与执行指标
    safety.py            运行期联锁与现场锁存
    conveyor_safety.py   停带判据、受限补偿与物料稳定
    suction.py           外表面接触吸附与无瞬移释放
    unit_controller.py   阶段二单步控制器 (继承阶段一状态机)
    runtime.py           会话资源装配/清理
    scene_contract.py    stage2 manifest extra 契约与只读校验
    run.py               阶段二 runner 与 CLI

阶段一默认调用行为不由本包改变; 进入阶段二必须通过 stage2 工件显式路由。
"""

__version__ = "0.3.0"   # 0.3.0 — 阶段 2.02 AGV 转运路径优化 (实验能力, 默认关闭)
