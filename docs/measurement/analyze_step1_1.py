"""analyze_step1_1.py — 从步骤 1.1 证据 JSON/JSONL 生成报告统计.

用法:
    .venv/Scripts/python.exe docs/measurement/analyze_step1_1.py \
        --experiments docs/implementation/step1_1_experiments_<stamp>.json \
        --trials-prefix docs/implementation/step1_1_trials_ \
        --baseline docs/implementation/step1_1_baseline_flow_<stamp>.json
    # 或只给 --stamp 20261020_104655, 自动在 docs/implementation 下找文件

输出 JSON 到 stdout; 可选 --out 写文件。脚本只用标准库, 不连接仿真。
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sys
from collections import Counter


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_jsonl(path):
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def stats(values):
    vals = [float(v) for v in values
            if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not vals:
        return None
    return {"n": len(vals), "min": round(min(vals), 5),
            "max": round(max(vals), 5),
            "mean": round(sum(vals) / len(vals), 5)}


def _x(pose):
    return float(pose[0]) if pose else None


def _z(pose):
    return float(pose[2]) if pose else None


def trajectory_average_speed(record):
    """用轨迹首末点估算平均速度 (含落稳等待, 故为下限)。"""
    traj = record.get("trajectory") or []
    if len(traj) < 2:
        return None
    t0, x0 = float(traj[0][0]), float(traj[0][1])
    t1, x1 = float(traj[-1][0]), float(traj[-1][1])
    if t1 <= t0:
        return None
    return round((x1 - x0) / (t1 - t0), 5)


def summarize_trials(records):
    out = {
        "total": len(records),
        "states": {},
        "success": sum(1 for r in records if r.get("success")),
        "gate_ok": sum(1 for r in records if r.get("gate_ok")),
        "failure_reasons": {},
        "by_phase": {},
        "by_position": {},
    }
    out["states"] = dict(Counter(r.get("state", "") for r in records))
    out["failure_reasons"] = dict(Counter(
        r.get("failure_reason", "") for r in records
        if r.get("failure_reason")))
    for phase in sorted({r.get("phase", "") for r in records}):
        out["by_phase"][phase] = _summarize_subset(
            [r for r in records if r.get("phase", "") == phase])
    for label in sorted({r.get("initial_position", "")
                         for r in records}):
        out["by_position"][label] = _summarize_subset(
            [r for r in records if r.get("initial_position", "") == label])
    return out


def _summarize_subset(records):
    final_x = [_x(r.get("material_final_pose")) for r in records
               if r.get("material_final_pose")]
    stop_x = [_x(r.get("material_stop_pose")) for r in records
              if r.get("material_stop_pose")]
    delays = []
    for r in records:
        if (r.get("stop_command_time") is not None
                and r.get("sensor_trigger_time") is not None):
            delays.append(r["stop_command_time"] - r["sensor_trigger_time"])
    speeds = [r.get("average_speed_mps") for r in records]
    if all(v is None for v in speeds):
        speeds = [trajectory_average_speed(r) for r in records]
    return {
        "total": len(records),
        "success": sum(1 for r in records if r.get("success")),
        "gate_ok": sum(1 for r in records if r.get("gate_ok")),
        "inside_allowed_region": sum(
            1 for r in records if r.get("inside_allowed_region")),
        "transported": sum(1 for r in records if r.get("transported")),
        "reached_detection_zone": sum(
            1 for r in records if r.get("reached_detection_zone")),
        "sensor_triggered": sum(
            1 for r in records if r.get("sensor_trigger_time") is not None),
        "sensor_to_stop_delay_s": stats(delays),
        "pad_stop_duration_s": stats(
            [r.get("pad_stop_duration_s") for r in records]),
        "pad_stop_distance_m": stats(
            [r.get("pad_stop_distance_m") for r in records]),
        "material_displacement_from_trigger_m": stats(
            [r.get("material_displacement_from_trigger_m")
             for r in records]),
        "material_settle_time_s": stats(
            [r.get("material_settle_time_s") for r in records]),
        "material_stop_x": stats(stop_x),
        "material_final_x": stats(final_x),
        "material_final_z": stats(
            [_z(r.get("material_final_pose")) for r in records
             if r.get("material_final_pose")]),
        "compensation_count": stats(
            [r.get("compensation_count") for r in records]),
        "average_speed_mps": stats(speeds),
        "place_position_error_m": stats(
            [r.get("place_position_error_m") for r in records]),
        "failure_reasons": dict(Counter(
            r.get("failure_reason", "") for r in records
            if r.get("failure_reason"))),
        "pad_stop_modes": dict(Counter(
            r.get("pad_stop_mode", "") for r in records)),
    }


def summarize_baseline(records):
    out = []
    for r in records:
        traj = r.get("trajectory") or []
        spawn = r.get("material_spawn_pose")
        spawn_t = r.get("material_spawn_time")
        if spawn is None and traj:
            spawn = [traj[0][1], traj[0][2], traj[0][3]]
            spawn_t = traj[0][0]
        avg = r.get("average_speed_to_sensor_mps")
        if avg is None and spawn and r.get("first_sensor_hit_pose") \
                and r.get("first_sensor_hit_time") is not None \
                and spawn_t is not None:
            dt = float(r["first_sensor_hit_time"]) - float(spawn_t)
            if dt > 1e-9:
                avg = round(math.dist(
                    r["first_sensor_hit_pose"][:3], spawn[:3]) / dt, 5)
        out.append({
            "uid": r.get("uid"),
            "state": r.get("task_state"),
            "ok": r.get("ok"),
            "sensor_hit_time": r.get("first_sensor_hit_time"),
            "sensor_hit_x": _x(r.get("first_sensor_hit_pose")),
            "zero_time": r.get("first_zero_command_time"),
            "zero_x": _x(r.get("first_zero_pose")),
            "zero_before_sensor": r.get("zero_command_before_sensor"),
            "sensor_to_zero_delay_s": r.get("sensor_to_zero_delay_s"),
            "hit_to_zero_displacement_m":
                r.get("material_displacement_hit_to_zero_m"),
            "pad_stop_time": r.get("pad_stop_time"),
            "pad_stop_duration_s": r.get("pad_stop_duration_s"),
            "pad_stop_distance_m": r.get("pad_stop_distance_m"),
            "average_speed_to_sensor_mps": avg,
            "place_error_m": r.get("material_place_error_m"),
            "speed_commands": r.get("speed_commands"),
        })
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="步骤 1.1 证据统计")
    ap.add_argument("--stamp", default=None)
    ap.add_argument("--dir", default="docs/implementation")
    ap.add_argument("--experiments", default=None)
    ap.add_argument("--baseline", default=None)
    ap.add_argument("--trials-prefix", default=None)
    ap.add_argument("--out", default=None)
    ns = ap.parse_args(argv)

    if ns.experiments is None:
        if not ns.stamp:
            ap.error("需要 --stamp 或 --experiments")
        ns.experiments = os.path.join(
            ns.dir, f"step1_1_experiments_{ns.stamp}.json")
    if ns.baseline is None and ns.stamp:
        candidate = os.path.join(
            ns.dir, f"step1_1_baseline_flow_{ns.stamp}.json")
        if os.path.exists(candidate):
            ns.baseline = candidate
    if ns.trials_prefix is None:
        ns.trials_prefix = os.path.join(ns.dir, "step1_1_trials_")

    experiments = load_json(ns.experiments)
    result = {
        "experiments": ns.experiments,
        "selected_layout": experiments.get("selected_layout"),
        "matrix": experiments.get("matrix"),
        "groups": {},
        "baseline_flow": [],
    }
    for group, info in experiments.get("groups", {}).items():
        trial_file = info.get("trial_file")
        records = []
        if trial_file and os.path.exists(trial_file):
            records = load_jsonl(trial_file)
        if not records:
            matches = sorted(glob.glob(f"{ns.trials_prefix}{group}_*.jsonl"))
            if matches:
                records = load_jsonl(matches[-1])
        result["groups"][group] = {
            "trial_file": trial_file,
            "summary": summarize_trials(records) if records else None,
            "recorded_summary": info.get("summary"),
        }
    if ns.baseline and os.path.exists(ns.baseline):
        result["baseline_flow"] = summarize_baseline(
            load_json(ns.baseline).get("records", []))

    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if ns.out:
        with open(ns.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
