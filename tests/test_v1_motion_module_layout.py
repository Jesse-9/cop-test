"""规划模块拆分后的旧路径兼容测试."""

from __future__ import annotations


def test_path_planning_reexports_split_modules():
    from flexible_factory.simulation.motion import (path_planning,
                                                    plan_checks,
                                                    plan_postprocess,
                                                    plan_search,
                                                    plan_types)

    assert path_planning.PlanStatus is plan_types.PlanStatus
    assert path_planning.MotionPlan is plan_types.MotionPlan
    assert (path_planning.ConfigurationChecker
            is plan_checks.ConfigurationChecker)
    assert path_planning.LocalPlanner is plan_search.LocalPlanner
    assert (path_planning.smooth_plan_waypoints
            is plan_postprocess.smooth_plan_waypoints)
    assert (path_planning.compact_plan_waypoints
            is plan_postprocess.compact_plan_waypoints)
