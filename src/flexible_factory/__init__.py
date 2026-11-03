"""flexible_factory — 柔性工业车间 v1 产品包.

阶段一至阶段四的基础能力在 v1 中按职责归并为本包的 domain /
simulation / artifacts / application 模块; 运行期不导入旧 stage 包.
"""

from .spec import PRODUCT_NAME, PRODUCT_VERSION, SCHEMA_VERSION

__all__ = ["PRODUCT_NAME", "PRODUCT_VERSION", "SCHEMA_VERSION"]
__version__ = PRODUCT_VERSION
