"""stage3 — 阶段三双工位柔性车间.

模块边界:
  config.py         阶段三全部新增参数
  layout.py         L2_MIRROR 双工位布局、单工位适配器、候选静态筛选
  scene.py          从 stage2 工件派生第二单元、Stage3Scene 与契约断言
  resources.py      小型固定顺序资源锁表
  tasks.py          UID 任务流、去重与状态
  agv_route.py      AGV 两站直线路线与停靠/阻塞/续行
  runtime.py        两台影子 UR5、独立碰撞集合、规划器与联锁
  unit_controller.py 单工位控制器 (传送带取料 / AGV 托盘取料)
  coordinator.py    任务编排、资源互斥、AGV 调度与故障注入
  scene_contract.py extra.stage3 契约、manifest 与只读校验
  run.py            阶段三 runner 与 CLI
"""

from . import config  # noqa: F401

__all__ = ["config"]
