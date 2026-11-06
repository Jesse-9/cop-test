"""path_planning.py — 规划模块兼容导出层.

实现已按职责拆分为:

  - :mod:`plan_types`: ``PlanStatus``、预算、上下文与计划结果类型;
  - :mod:`plan_checks`: ``ConfigurationChecker``（构型/路径段检查）;
  - :mod:`plan_search`: ``LocalPlanner``（有限局部候选搜索）;
  - :mod:`plan_postprocess`: 成功计划的平滑与压缩.

本模块保留原有公共导入路径, 不复制实现. 新代码可直接从上述 ``plan_*``
模块导入; 旧调用方无需一次性迁移. 拆分不改变检查顺序、容差、预算或
任何规划算法.
"""

from __future__ import annotations

from .plan_checks import ConfigurationChecker
from .plan_postprocess import compact_plan_waypoints, smooth_plan_waypoints
from .plan_search import CandidateSolver, LocalPlanner
from .plan_types import (ConfigCheck, Goal, MotionContext, MotionPlan,
                         MotionSegment, PlanBudget, PlanBudgetExceeded,
                         PlanStatus, SegmentCheck, failure_priority)

__all__ = [
    "CandidateSolver",
    "ConfigCheck",
    "ConfigurationChecker",
    "Goal",
    "LocalPlanner",
    "MotionContext",
    "MotionPlan",
    "MotionSegment",
    "PlanBudget",
    "PlanBudgetExceeded",
    "PlanStatus",
    "SegmentCheck",
    "compact_plan_waypoints",
    "failure_priority",
    "smooth_plan_waypoints",
]
