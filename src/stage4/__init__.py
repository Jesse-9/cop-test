"""stage4 — 阶段四多岛柔性车间.

模块边界:
  config.py          阶段四全部新增参数 (布局/物流边/物料/调度/验收)
  layout.py          L5_UFLOW 五岛布局、物流线/AGV 路网、静态筛选与序列化
  scene.py           从 stage3 工件派生 C/D/E 单元、三条物流线与 E 缓冲
  materials.py       多类型物料谱系、区域占用、E 岛配对契约
  tasks.py           多岛任务状态、优先级、老化与去重
  resources.py       阶段四固定顺序资源表 (线/交接/停靠/直道/互锁区/臂)
  agv_map.py         AGV 分段路网、直道+原地转向、装/卸载航线与空载调机
  runtime.py         五台影子 UR5、独立碰撞集合/规划器/联锁
  unit_controller.py 单岛控制器 (传送带/AGV/固定交接三种取料源)
  coordinator.py     多岛任务编排、AGV 调度、配对、背压与证据
  metrics.py         事件级节拍/吞吐/WIP/利用率指标
  scene_contract.py  extra.stage4 契约、摘要与只读校验
  run.py             阶段四 runner、验收场景与 CLI
"""

from . import config  # noqa: F401

__all__ = ["config"]
