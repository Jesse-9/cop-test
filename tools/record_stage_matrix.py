#!/usr/bin/env python3
"""record_stage_matrix.py — 批量录制各阶段不同运行条件.

用法:
    .venv/Scripts/python.exe tools/record_stage_matrix.py
    .venv/Scripts/python.exe tools/record_stage_matrix.py --stage 3 4
    .venv/Scripts/python.exe tools/record_stage_matrix.py --force
    .venv/Scripts/python.exe tools/record_stage_matrix.py --only stage1_agv

逐条调用 tools/record_stage.py (单个条件一个 MP4)。默认跳过输出目录中
已存在同名条件的视频 (按文件名前缀 stageN_<mode>_ 判断); --force 重录。
任何一条失败都会记录并继续后续条件, 最后打印汇总.

条件矩阵与 USAGE 的阶段入口一致:
  stage1: station / agv / both + 6 个故障注入
  stage2: station / agv / both + runtime_obstacle
  stage3: 5 个运行场景
  stage4: 6 个运行场景
"""

from __future__ import annotations

import argparse
import glob
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / "tools" / "record_stage.py"
OUT_DIR = ROOT / "docs" / "implementation" / "runs" / "recordings"

STAGE1_FAULTS = ("ik_unreachable", "no_target", "uid_mismatch",
                 "sensor_disabled", "agv_blocked", "release_unstable")
STAGE2_FAULTS = ("runtime_obstacle",)
STAGE3_SCENARIOS = ("closed_loop", "contention", "blocked",
                    "unstable_unload", "route_baseline")
STAGE4_SCENARIOS = ("full_flow", "no_pair", "agv_priority", "agv_blocked",
                    "conveyor_fault", "staggered_batch")


def matrix() -> list[tuple[str, str, list[str]]]:
    """(名称, 文件名 mode, record_stage 参数) 列表."""
    items: list[tuple[str, str, list[str]]] = []
    for target in ("station", "agv", "both"):
        items.append((f"stage1_{target}", target,
                      ["--stage", "1", "--target", target]))
    for fault in STAGE1_FAULTS:
        items.append((f"stage1_station_fault_{fault}",
                      f"station_fault_{fault}",
                      ["--stage", "1", "--fault", fault]))
    for target in ("station", "agv", "both"):
        items.append((f"stage2_{target}", target,
                      ["--stage", "2", "--target", target]))
    for fault in STAGE2_FAULTS:
        items.append((f"stage2_station_fault_{fault}",
                      f"station_fault_{fault}",
                      ["--stage", "2", "--fault", fault]))
    for scenario in STAGE3_SCENARIOS:
        items.append((f"stage3_{scenario}", scenario,
                      ["--stage", "3", "--scenario", scenario]))
    for scenario in STAGE4_SCENARIOS:
        items.append((f"stage4_{scenario}", scenario,
                      ["--stage", "4", "--scenario", scenario]))
    return items


def _existing(out_dir: Path, stage: int, mode: str) -> list[Path]:
    prefix = f"stage{stage}_{mode}_"
    return [Path(p) for p in
            glob.glob(str(out_dir / f"{prefix}*.mp4"))]


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="批量录制各阶段运行条件")
    ap.add_argument("--stage", type=int, nargs="+", choices=(1, 2, 3, 4),
                    default=[1, 2, 3, 4])
    ap.add_argument("--only", nargs="+", default=None,
                    help="只跑指定名称, 如 stage4_full_flow")
    ap.add_argument("--force", action="store_true",
                    help="已有对应视频时也重录")
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    ns = ap.parse_args(argv)

    out_dir = Path(ns.out_dir).resolve()
    items = [item for item in matrix() if int(item[2][1]) in ns.stage]
    if ns.only:
        items = [item for item in items if item[0] in set(ns.only)]
    if not items:
        print("[ERR] 没有匹配的条件", file=sys.stderr)
        return 2

    summary = []
    for name, mode, args in items:
        stage = int(args[1])
        existing = _existing(out_dir, stage, mode)
        if existing and not ns.force:
            print(f"[SKIP] {name}: 已存在 {existing[0].name}")
            summary.append({"name": name, "status": "skipped",
                            "video": str(existing[0])})
            continue
        print(f"\n{'=' * 70}\n[BATCH] {name}\n{'=' * 70}", flush=True)
        cmd = [sys.executable, str(RECORD), *args,
               "--out-dir", str(out_dir)]
        rc = subprocess.call(cmd, cwd=str(ROOT))
        summary.append({"name": name,
                        "status": "ok" if rc == 0 else f"failed({rc})"})
        print(f"[BATCH] {name} → {'ok' if rc == 0 else f'FAILED rc={rc}'}",
              flush=True)

    print("\n" + "=" * 70)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    failed = [row for row in summary if row["status"].startswith("failed")]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
