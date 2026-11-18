#!/usr/bin/env python3
"""tools/cart2_plan.py — 双车运输调度的离线推演（不需要 CoppeliaSim）.

把「AGV + 小车二」的路线、单趟时间与三种调度策略的指标打印出来，用来
回答"要不要第二台车、怎么派工、会不会互相堵"。

用法:

    python tools/cart2_plan.py                        # 默认 1 壳体 + 1 支架
    python tools/cart2_plan.py --housing 3 --bracket 3
    python tools/cart2_plan.py --housing 6 --bracket 0
    python tools/cart2_plan.py --stagger 20           # 每 20 s 到一单
    python tools/cart2_plan.py --json                 # 机器可读

策略:
    single   只用 AGV（现状基线）
    fixed    固定分工：壳体回程→AGV，支架→小车二
    eta      资源感知的配对派工（推荐）

注意: 这是**离散事件推演**，不含加速/减速细节，也不含 CoppeliaSim 物理；
它只验证调度逻辑与数量级，不替代实机验收。
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

from flexible_factory.simulation import transport_schedule as ts  # noqa: E402

_POLICY_LABEL = {
    "single": "单 AGV（基线）",
    "fixed": "固定分工",
    "eta": "配对派工（推荐）",
}


def _routes() -> list[tuple[str, str]]:
    return [("B", "A"), ("D", "E"), ("A", "D"), ("A", "E"), ("D", "A"),
            ("B", "E")]


def render_routes() -> list[str]:
    lines = ["路线（沿用 v1 的 6 节点 / 3 直道，不新增节点）:"]
    for origin, destination in _routes():
        route = ts.plan_route(origin, destination)
        lines.append(
            f"  {origin:>2s}→{destination:<2s} {route.length:5.2f} m  "
            f"{route.turns} 次转向  {'-'.join(route.nodes)}")
    return lines


def render_trips() -> list[str]:
    lines = ["单趟时间（空载不占时间，含装 1.5/2.0 s + 卸 1.5/2.0 s）:"]
    for spec in (ts.AGV_1, ts.CART_2):
        parts = []
        for origin, destination in (("B", "A"), ("D", "E"), ("A", "D")):
            route = ts.plan_route(origin, destination)
            total = (spec.travel_time(route) + spec.load_time
                     + spec.unload_time)
            parts.append(f"{origin}→{destination} {total:5.1f}s")
        lines.append(f"  {spec.vehicle_id:<7s} 速度 {spec.speed:.2f} m/s  "
                     + "  ".join(parts))
    return lines


def render_compare(jobs, vehicles) -> list[str]:
    result = ts.compare_policies(vehicles, jobs)
    lines = [f"派工对比（{len(jobs)} 单）:"]
    lines.append(f"  {'策略':<10s} {'完成':>5s} {'完工':>8s} "
                 f"{'平均等待':>8s} {'最长等待':>8s}  利用率")
    for policy, data in result.items():
        util = " ".join(f"{name}={value:.2f}"
                        for name, value in data["utilization"].items())
        lines.append(
            f"  {_POLICY_LABEL[policy]:<10s} "
            f"{data['completed']}/{data['jobs']:<3d} "
            f"{data['makespan_s']:7.1f}s {data['avg_wait_s']:7.1f}s "
            f"{data['max_wait_s']:7.1f}s  {util}")
    base = result["single"]["makespan_s"] or 1.0
    ratio = result["eta"]["makespan_s"] / base
    lines.append(f"  结论: 配对派工把总完工时间压到单车的 {ratio:.2f} 倍")
    return lines


def render_detail(jobs, vehicles) -> list[str]:
    data = ts.DualCartSimulator(vehicles, jobs, policy="eta").run()
    lines = ["逐单明细（配对派工）:"]
    lines.append(f"  {'任务':<6s} {'类别':<16s} {'车辆':<8s} "
                 f"{'等待':>7s} {'周期':>7s}")
    for row in data["per_job"]:
        lines.append(
            f"  {row['job_id']:<6s} {row['category']:<16s} "
            f"{row['vehicle']:<8s} {row['wait_s']:6.1f}s {row['cycle_s']:6.1f}s")
    return lines


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="cart2_plan.py",
        description="双车运输调度离线推演（不连接 CoppeliaSim）")
    parser.add_argument("--housing", type=int, default=1,
                        help="壳体回程 B→A 单数")
    parser.add_argument("--bracket", type=int, default=1,
                        help="支架运输 D→E 单数")
    parser.add_argument("--stagger", type=float, default=0.0,
                        help="相邻任务创建间隔 s（0=同时下达）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)

    jobs = ts.default_jobs(args.housing, args.bracket,
                           stagger_s=args.stagger)
    vehicles = [ts.AGV_1, ts.CART_2]

    if args.json:
        print(json.dumps({
            "jobs": [job.as_dict() for job in jobs],
            "vehicles": [spec.as_dict() for spec in vehicles],
            "compare": ts.compare_policies(vehicles, jobs),
        }, ensure_ascii=False, indent=2))
        return 0

    for block in (render_routes(), render_trips(),
                  render_compare(jobs, vehicles),
                  render_detail(jobs, vehicles)):
        print("\n".join(block))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
