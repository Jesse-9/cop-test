#!/usr/bin/env python3
"""tools/cart2.py — 小车二的物理车体（会沿路网行驶的运输车）.

与 tools/cart.py 的区别:
  cart.py   静态手推车, 只摆位置, 用 drive 走一小段
  cart2.py  运输车, 按 transport_schedule 的路线/速度/停留行驶

用法（需要 CoppeliaSim 已启动、并已 flexible-factory load）:

    python tools/cart2.py build            # 在 D 节点造车
    python tools/cart2.py status
    python tools/cart2.py demo             # D→E: 装货停留 2s → 行驶 → 卸货停留 2s
    python tools/cart2.py demo --from D --to A
    python tools/cart2.py remove

注意: 车体是运动学驱动（位姿直给），不代表轮速/碰撞动力学；只在仿真停止态建造。
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

from flexible_factory.simulation import cart_vehicle as cv  # noqa: E402


def _print(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _connect(ns):
    from flexible_factory.simulation.component_builder import connect

    _, sim, _ = connect(ns.host, ns.port)
    return sim


def cmd_build(ns) -> int:
    sim = _connect(ns)
    spec = cv.Cart2Spec(node=ns.node)
    _print(cv.build_cart2(sim, spec, replace=ns.replace))
    return 0


def cmd_remove(ns) -> int:
    _print(cv.remove_cart2(_connect(ns)))
    return 0


def cmd_status(ns) -> int:
    _print(cv.cart2_status(_connect(ns)))
    return 0


def cmd_demo(ns) -> int:
    sim = _connect(ns)
    if not cv.cart2_objects(sim):
        print("[ERROR] 场景里没有小车二, 先 build", file=sys.stderr)
        return 2
    _print(cv.demo_route(sim, ns.source, ns.destination))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cart2.py", description="小车二物理车体")
    sub = parser.add_subparsers(dest="command", required=True)

    def _net(child):
        child.add_argument("--host", default="localhost")
        child.add_argument("--port", type=int, default=23000)

    p = sub.add_parser("build", help="在指定节点建造车体")
    _net(p)
    p.add_argument("--node", default=cv.CART2_HOME, choices=list(cv.NODES))
    p.add_argument("--replace", action="store_true")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("remove", help="删除车体")
    _net(p)
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser("status", help="查询车体与位姿")
    _net(p)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("demo", help="按路线行驶一遍（含装卸停留）")
    _net(p)
    p.add_argument("--from", dest="source", default=cv.CART2_HOME,
                   choices=list(cv.NODES))
    p.add_argument("--to", dest="destination", default="E",
                   choices=list(cv.NODES))
    p.set_defaults(func=cmd_demo)
    return parser


def main(argv=None) -> int:
    ns = build_parser().parse_args(argv)
    try:
        return int(ns.func(ns))
    except cv.Cart2Error as exc:
        print(f"[ERROR] {exc.code}: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"[ERROR] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
