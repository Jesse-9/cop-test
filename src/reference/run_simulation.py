"""
run_simulation.py — 岛式车间柔性生产仿真入口
================================================
基于 CoppeliaSim 4.10+ ZeroMQ Remote API 的工厂仿真启动器。

职责:
  - 解析 CLI 参数 (--host, --port, --time, --log)
  - 初始化日志系统
  - 建立 ZMQ 连接
  - 创建 FactoryController 并启动主循环

工厂仿真的所有子系统分布在独立模块中:
  factory_types.py       — 类型定义 (枚举, 数据类, 物料工具)
  factory_core.py        — ZMQ 通信 + 步进循环 + 设备句柄 + 日志
  factory_agv.py         — 共享 AGV 柔性调度器
  factory_island.py      — 工作岛控制 + 节拍追踪 + 供料门控
  factory_exceptions.py  — 统一异常处理器
  factory_controller.py  — 主控制器 (编排层)
  factory_config.py      — 集中配置 (场景参数, 节拍, 阈值)

运行前请确保:
  1. CoppeliaSim 已启动且已加载工厂场景 (先运行 build_scene.py)
  2. ZMQ Remote API 服务已激活

用法:
  uv run python src/run_simulation.py [--host HOST] [--port PORT] [--time TOTAL_TIME]
  或: uv run run-simulation [--host HOST] [--port PORT] [--time TOTAL_TIME]
"""

import argparse
import sys

from reference.factory_core import setup_logger, CoppeliaConnector
from reference.factory_controller import FactoryController
import reference.factory_config as cfg


def main():
    """仿真入口 — CLI argument parsing, connection, and launch."""
    parser = argparse.ArgumentParser(
        description="岛式车间柔性生产仿真控制器"
    )
    parser.add_argument("--host", default="localhost",
                        help="CoppeliaSim 主机地址")
    parser.add_argument("--port", type=int, default=23000,
                        help="ZMQ Remote API 端口")
    parser.add_argument("--time", type=float, default=cfg.TOTAL_SIM_TIME,
                        help=f"仿真总时长 (秒), 默认 {cfg.TOTAL_SIM_TIME}s")
    parser.add_argument("--log", default="factory_sim.log",
                        help="日志文件路径")
    args = parser.parse_args()

    # 1. 初始化日志 — Initialize structured logger
    logger = setup_logger(log_file=args.log)

    # 2. 建立 ZMQ 连接 — Connect to CoppeliaSim
    connector = CoppeliaConnector(host=args.host, port=args.port)
    if not connector.connect():
        sys.exit(1)

    # 3. 创建主控制器 — Build the factory controller
    try:
        sim_ik = connector.client.require("simIK")
    except Exception:
        sim_ik = None
    controller = FactoryController(connector.sim, total_time=args.time,
                                   logger=logger, sim_ik=sim_ik)

    # 4. 将实际连接器注入到 ZMQ 监控中 — Wire up the real connector
    controller.connector = connector
    controller.zmq_monitor.connector = connector
    controller.zmq_monitor.last_response = 0.0

    # 5. 启动仿真主循环 — Launch the main simulation loop
    controller.run()


if __name__ == "__main__":
    main()
