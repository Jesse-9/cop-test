"""spec.py — v1 产品级、与仿真无关的稳定标识和版本.

产品版本、工件 schema 版本和场景契约版本分别编号 (方案 §5.2):
  - ``PRODUCT_VERSION``: 安装包/CLI 版本;
  - ``SCHEMA_VERSION``: manifest 文件结构版本;
  - ``SCENE_CONTRACT_VERSION``: 场景内容/控制契约版本.

本模块只包含常量, 不导入 CoppeliaSim、文件系统实现或 CLI.
"""

from __future__ import annotations

from pathlib import Path

PRODUCT_NAME = "flexible-factory"
PRODUCT_VERSION = "1.0.0"

# manifest 结构版本, 独立于产品版本.
SCHEMA_VERSION = "2.0"

# 场景契约与各稳定策略版本.
SCENE_CONTRACT_VERSION = "v1_contract_20261103"
SCENE_BUILDER_VERSION = "v1_scene_20261103"
TASK_FLOW_VERSION = "v1_taskflow_20261103"
RESOURCE_POLICY_VERSION = "v1_resources_20261103"
AGV_ROUTE_VERSION = "v1_agv_route_20261103"
MATERIAL_MODEL_VERSION = "v1_materials_20261103"
METRICS_VERSION = "v1_metrics_20261103"

CONTROLLER_ID = ("flexible_factory.simulation.unit_controller."
                 "Stage4UnitController")
COORDINATOR_ID = ("flexible_factory.application.coordinator."
                  "Stage4Coordinator")
RUNNER_ID = "flexible_factory.application.run.run_v1"

LAYOUT_NAME = "L5_UFLOW"

# 工件命名空间与默认输出位置.
ARTIFACT_ROOT = Path("docs") / "scene_artifacts"
REPORT_DIR = Path("docs") / "implementation" / "runs"
DEFAULT_LOG_FILE = "flexible_factory.log"

# CoppeliaSim 内置模型 (与已有实测基线一致; 不随 v1 改名).
MODEL_BASE = r"C:\Program Files\CoppeliaRobotics\CoppeliaSimEdu\models"
MODEL_PATHS = {
    "conveyor": "equipment/conveyors/generic conveyor (belt).ttm",
    "ur5": "robots/non-mobile/UR5.ttm",
    "suction_pad": "components/grippers/suction pad.ttm",
    "pioneer_p3dx": "robots/mobile/pioneer p3dx.ttm",
}
