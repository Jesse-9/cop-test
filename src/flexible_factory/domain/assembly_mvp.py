"""assembly_mvp.py — 混线装配 MVP 的有限领域扩展 (WP0–WP3).

本模块只保存**有限、封闭**的 MVP 业务常量, 不导入仿真或持久化:

  - 两个产品配方各一种来料对与一个逻辑合流成品;
  - 6 个 MVP 物料类型 (压缩机/底板/压缩机装配体、前/后法兰/法兰组件);
  - 8 个 MVP 区域 (4 个来料槽、2 个逻辑装配位、2 个成品位);
  - 两个已登记配方/模板身份与固定端口名.

边界 (方案 §4/§7):
  - 不把 MVP 类型并入 :data:`flexible_factory.domain.rules.ITEM_TYPES`;
    该元组参与受保护 v1 工件的 ``material_model.types`` 契约校验,
    扩展它会使旧工件 ``validate``/``start`` 失败. MVP 类型只在
    profile=assembly_mvp 的新工件里声明.
  - 不读取图谱、不编译任意 recipe、不生成用户可编辑工艺.
"""

from __future__ import annotations

# 扩展契约版本: 只随 MVP 场景对象/类型/配方语义变化递增, 不改变
# v1 的 config_digest / SCENE_CONTRACT_VERSION.
EXTENSION_VERSION = "v1_assembly_mvp_20261114"
PROFILE = "assembly_mvp"
SCENE_PROFILE_FULL_FLOW = "full_flow"

# ── 物料类型 ────────────────────────────────────────────────

ITEM_TYPE_COMPRESSOR = "COMPRESSOR"
ITEM_TYPE_BASEPLATE = "BASEPLATE"
ITEM_TYPE_COMPRESSOR_ASSY = "COMPRESSOR_ASSY"
ITEM_TYPE_FRONT_FLANGE = "FRONT_FLANGE"
ITEM_TYPE_REAR_FLANGE = "REAR_FLANGE"
ITEM_TYPE_FLANGE_ASSY = "FLANGE_ASSY"

ITEM_TYPES = (
    ITEM_TYPE_COMPRESSOR, ITEM_TYPE_BASEPLATE, ITEM_TYPE_COMPRESSOR_ASSY,
    ITEM_TYPE_FRONT_FLANGE, ITEM_TYPE_REAR_FLANGE, ITEM_TYPE_FLANGE_ASSY,
)

PRODUCT_ITEM_TYPES = (
    ITEM_TYPE_COMPRESSOR_ASSY, ITEM_TYPE_FLANGE_ASSY,
)

ITEM_COLORS = {
    ITEM_TYPE_COMPRESSOR: (0.20, 0.45, 1.00),
    ITEM_TYPE_BASEPLATE: (0.20, 0.75, 0.65),
    ITEM_TYPE_COMPRESSOR_ASSY: (0.10, 0.55, 0.55),
    ITEM_TYPE_FRONT_FLANGE: (1.00, 0.55, 0.15),
    ITEM_TYPE_REAR_FLANGE: (0.95, 0.80, 0.20),
    ITEM_TYPE_FLANGE_ASSY: (0.60, 0.35, 0.85),
}

# ── 区域 ────────────────────────────────────────────────────

ZONE_SOURCE_COMPRESSOR = "mvp_source_compressor"
ZONE_SOURCE_BASEPLATE = "mvp_source_baseplate"
ZONE_SOURCE_FRONT_FLANGE = "mvp_source_front_flange"
ZONE_SOURCE_REAR_FLANGE = "mvp_source_rear_flange"
ZONE_COMPRESSOR_ASSEMBLY = "mvp_compressor_assembly"
ZONE_FLANGE_ASSEMBLY = "mvp_flange_assembly"
ZONE_OUTPUT_COMPRESSOR = "mvp_output_compressor_assy"
ZONE_OUTPUT_FLANGE = "mvp_output_flange_assy"

SOURCE_ZONES = (
    ZONE_SOURCE_COMPRESSOR, ZONE_SOURCE_BASEPLATE,
    ZONE_SOURCE_FRONT_FLANGE, ZONE_SOURCE_REAR_FLANGE,
)
ASSEMBLY_ZONES = (ZONE_COMPRESSOR_ASSEMBLY, ZONE_FLANGE_ASSEMBLY)
OUTPUT_ZONES = (ZONE_OUTPUT_COMPRESSOR, ZONE_OUTPUT_FLANGE)
ZONES = (*SOURCE_ZONES, *ASSEMBLY_ZONES, *OUTPUT_ZONES)

ZONE_CAPACITY = {zone: 1 for zone in ZONES}

ZONE_DESCRIPTIONS = {
    ZONE_SOURCE_COMPRESSOR: "压缩机独立岛压缩机来料槽",
    ZONE_SOURCE_BASEPLATE: "压缩机独立岛底板来料槽",
    ZONE_SOURCE_FRONT_FLANGE: "法兰独立岛前法兰来料槽",
    ZONE_SOURCE_REAR_FLANGE: "法兰独立岛后法兰来料槽",
    ZONE_COMPRESSOR_ASSEMBLY: "压缩机独立岛齐套/逻辑装配位",
    ZONE_FLANGE_ASSEMBLY: "法兰独立岛齐套/逻辑装配位",
    ZONE_OUTPUT_COMPRESSOR: "压缩机独立岛成品输出位",
    ZONE_OUTPUT_FLANGE: "法兰独立岛成品输出位",
}

# 来料槽 → 物料类型; 订单入口和场景生成位置必须与此一致.
SOURCE_ITEM_TYPE = {
    ZONE_SOURCE_COMPRESSOR: ITEM_TYPE_COMPRESSOR,
    ZONE_SOURCE_BASEPLATE: ITEM_TYPE_BASEPLATE,
    ZONE_SOURCE_FRONT_FLANGE: ITEM_TYPE_FRONT_FLANGE,
    ZONE_SOURCE_REAR_FLANGE: ITEM_TYPE_REAR_FLANGE,
}

# ── 配方 / 模板 / join handler ─────────────────────────────

RECIPE_COMPRESSOR = "mvp_compressor_v1"
RECIPE_FLANGE = "mvp_flange_v1"

TEMPLATE_COMPRESSOR = "mvp_compressor_assembly"
TEMPLATE_FLANGE = "mvp_flange_assembly"

JOIN_HANDLER = "mvp_pair_assembly"

PRODUCTS = {
    RECIPE_COMPRESSOR: {
        "template_id": TEMPLATE_COMPRESSOR,
        "product_type": ITEM_TYPE_COMPRESSOR_ASSY,
        "semantics": "MVP压缩机: COMPRESSOR + BASEPLATE → "
                     "VIRTUAL_SHAFT_HOLE_ASSEMBLY → COMPRESSOR_ASSY",
        "inputs": (
            (ZONE_SOURCE_COMPRESSOR, ITEM_TYPE_COMPRESSOR, "compressor-in"),
            (ZONE_SOURCE_BASEPLATE, ITEM_TYPE_BASEPLATE, "baseplate-in"),
        ),
        "output": (ZONE_OUTPUT_COMPRESSOR, ITEM_TYPE_COMPRESSOR_ASSY,
                   "output-0"),
    },
    RECIPE_FLANGE: {
        "template_id": TEMPLATE_FLANGE,
        "product_type": ITEM_TYPE_FLANGE_ASSY,
        "semantics": "MVP法兰: FRONT_FLANGE + REAR_FLANGE → "
                     "VIRTUAL_FLANGE_ASSEMBLY → FLANGE_ASSY",
        "inputs": (
            (ZONE_SOURCE_FRONT_FLANGE, ITEM_TYPE_FRONT_FLANGE, "front-in"),
            (ZONE_SOURCE_REAR_FLANGE, ITEM_TYPE_REAR_FLANGE, "rear-in"),
        ),
        "output": (ZONE_OUTPUT_FLANGE, ITEM_TYPE_FLANGE_ASSY, "output-0"),
    },
}

# 订单 fixture 的稳定目标类型; 只用于文档/测试断言, 不参与运行时派工.
ORDER_TARGETS = {item_type for item_type in PRODUCT_ITEM_TYPES}


__all__ = [
    "ASSEMBLY_ZONES", "EXTENSION_VERSION", "ITEM_COLORS", "ITEM_TYPES",
    "JOIN_HANDLER", "ORDER_TARGETS", "OUTPUT_ZONES", "PRODUCTS",
    "PRODUCT_ITEM_TYPES", "PROFILE", "RECIPE_COMPRESSOR", "RECIPE_FLANGE",
    "SCENE_PROFILE_FULL_FLOW", "SOURCE_ITEM_TYPE", "SOURCE_ZONES",
    "TEMPLATE_COMPRESSOR", "TEMPLATE_FLANGE", "ZONE_CAPACITY",
    "ZONE_DESCRIPTIONS", "ZONES",
]