"""
main.py — 工厂仿真入口菜单
===========================
uv run python main.py

选择 (1) 构建场景 或 (2) 运行仿真控制。
"""

import sys


def main():
    print("=" * 50)
    print("  岛式车间柔性生产仿真系统")
    print("  Island-Style Flexible Factory Simulation")
    print("=" * 50)
    print()
    print("  请先启动 CoppeliaSim 4.10+")
    print("  确保 ZMQ Remote API 服务端已激活")
    print("  (菜单: Add-ons > ZMQ remote API server)")
    print()
    print("  1) 构建场景 (分层命名 + 共享AGV)")
    print("  2) 运行仿真 (柔性调度 + 节拍追踪 + 异常处理)")
    print("  3) 退出")
    print()

    try:
        choice = input("  请选择 [1-3]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n退出。")
        return

    if choice == "1":
        from build_scene import main as build_main
        build_main()
    elif choice == "2":
        from run_simulation import main as run_main
        run_main()
    elif choice == "3":
        print("退出。")
    else:
        print(f"无效选择: {choice}")


if __name__ == "__main__":
    main()
