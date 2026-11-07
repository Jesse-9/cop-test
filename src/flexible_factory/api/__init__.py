"""api — 订单服务 HTTP adapter (阶段 3).

包内只包含传输层; 业务规则在 ``application.orders``. 默认本地绑定, 不导入
CoppeliaSim.
"""

from .server import ApiError, OrderApiServer

__all__ = ["ApiError", "OrderApiServer"]
