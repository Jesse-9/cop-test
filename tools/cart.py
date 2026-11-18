#!/usr/bin/env python3
"""tools/cart.py — 给车间加一台手推小车（附加件）.

这个脚本是**纯加法**: 只往"当前已加载的场景"里增删 ``WorkshopCart*`` 对象，
不修改任何现有对象、资源表、工艺目录、工件、manifest 或代码，
因此 ``config_digest`` 与受保护工件都不变，``full_flow`` 的行为也不变。

用法（在仓库根目录）:

    # 离线：只算默认停放点的几何间隙（不需要 CoppeliaSim）
    python tools/cart.py spot

    # 连接仿真器，把小车摆进当前场景（需要停止态 + 已加载场景）
    python tools/cart.py add
    python tools/cart.py add --x 3.40 --y 2.40 --yaw 1.57

    # 查看 / 移除
    python tools/cart.py status
    python tools/cart.py remove

    # 独立演示：让小车沿 +X 前进 0.7 m 再退回（自己 stepping，不用协调器）
    python tools/cart.py drive --distance 0.7 --speed 0.12

注意:

  - ``add`` 只作用于内存中的场景；重新 ``flexible-factory load`` 或
    重启 CoppeliaSim 后小车消失（这是刻意的：不写工件）；
  - 小车是 static + respondable 的道具，不占资源、不建任务；
    ``flexible-factory validate`` 会把它记进 ``extra_aliases``，
    但不会因此失败（manifest 校验不看多余对象）；
  - ``drive`` 会短暂启动/停止仿真（setStepping + startSimulation），
    若当前有运行在跑会直接拒绝。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from flexible_factory.simulation import cart as cart_mod  # noqa: E402


def _print(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _connect(ns):
    from flexible_factory.simulation.component_builder import connect

    _, sim, _ = connect(ns.host, ns.port)
    return sim


def cmd_spot(ns) -> int:
    from flexible_factory.simulation.layout import default_layout

    layout = default_layout()
    spec = cart_mod.default_cart_spec(layout)
    report = cart_mod.clearance_report(spec, layout)
    report["chosen_fallback"] = (
        abs(spec.x - cart_mod.DEFAULT_SPOT[0]) > 1e-9
        or abs(spec.y - cart_mod.DEFAULT_SPOT[1]) > 1e-9)
    report["parts"] = [part["alias"] for part in cart_mod.cart_parts(spec)]
    _print(report)
    return 0 if report["ok"] else 1


def cmd_add(ns) -> int:
    from flexible_factory.simulation.layout import default_layout

    layout = default_layout()
    spec = cart_mod.CartSpec(
        x=float(ns.x) if ns.x is not None else cart_mod.DEFAULT_SPOT[0],
        y=float(ns.y) if ns.y is not None else cart_mod.DEFAULT_SPOT[1],
        yaw=float(ns.yaw) if ns.yaw is not None else cart_mod.DEFAULT_YAW)
    if ns.x is None and ns.y is None:
        spec = cart_mod.default_cart_spec(layout)
    sim = _connect(ns)
    result = cart_mod.build_cart(sim, spec, replace=ns.replace, layout=layout,
                                 require_clear=not ns.force)
    _print(result)
    return 0


def cmd_remove(ns) -> int:
    sim = _connect(ns)
    _print(cart_mod.remove_cart(sim))
    return 0


def cmd_status(ns) -> int:
    sim = _connect(ns)
    _print(cart_mod.cart_status(sim))
    return 0


def cmd_drive(ns) -> int:
    sim = _connect(ns)
    status = cart_mod.cart_status(sim)
    if not status["present"]:
        print("[ERROR] 场景里没有小车，先执行 add", file=sys.stderr)
        return 2
    pose = status.get("pose") or [0.0, 0.0, 0.0]
    spec = cart_mod.CartSpec(x=float(pose[0]), y=float(pose[1]),
                             yaw=float(pose[5]))
    _print(cart_mod.drive_cart(sim, spec, distance=ns.distance,
                               speed=ns.speed))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cart.py",
        description="车间手推小车（附加件）：只新增对象，不改任何既有内容")
    sub = parser.add_subparsers(dest="command", required=True)

    def _net(child: argparse.ArgumentParser) -> None:
        child.add_argument("--host", default="localhost")
        child.add_argument("--port", type=int, default=23000)

    p = sub.add_parser("spot", help="离线计算默认停放点与间隙（不需要仿真器）")
    p.set_defaults(func=cmd_spot)

    p = sub.add_parser("add", help="把小车摆进当前已加载场景")
    _net(p)
    p.add_argument("--x", type=float, default=None)
    p.add_argument("--y", type=float, default=None)
    p.add_argument("--yaw", type=float, default=None)
    p.add_argument("--replace", action="store_true",
                   help="场景已有小车时先删再建")
    p.add_argument("--force", action="store_true",
                   help="跳过间隙检查（不推荐）")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("remove", help="删除全部 WorkshopCart* 对象")
    _net(p)
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser("status", help="查询小车是否存在及其位姿")
    _net(p)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("drive", help="独立演示: 直行再退回（自己 stepping）")
    _net(p)
    p.add_argument("--distance", type=float, default=0.7)
    p.add_argument("--speed", type=float, default=0.12)
    p.set_defaults(func=cmd_drive)

    return parser


def main(argv=None) -> int:
    ns = build_parser().parse_args(argv)
    try:
        return int(ns.func(ns))
    except cart_mod.CartError as exc:
        print(f"[ERROR] {exc.code}: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # 连接失败等
        print(f"[ERROR] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
