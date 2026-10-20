#!/usr/bin/env python3
"""步骤 1.2 验收检查 — 真实 CoppeliaSim (可重复执行)。

覆盖:
  1. load → validate → 基线哈希不变;
  2. 移除必需 alias → validate 失败并列出缺失项 → reload 恢复;
  3. 修改配置摘要 → validate 失败并列出差异键 → reload 恢复;
  4. 运行中 load/build → RUN_CONFLICT 拒绝, 不修改场景;
  5. 基线工件 scene.ttt 哈希在全部检查前后保持不变。

用法:
    .venv/Scripts/python docs/measurement/check_step1_2.py
    .venv/Scripts/python docs/measurement/check_step1_2.py --skip-concurrency

证据写入 docs/implementation/evidence/step1_2_lifecycle_checks_<stamp>.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
CLI = str(ROOT / "src" / "run_scene_lifecycle.py")
EVIDENCE = ROOT / "docs" / "implementation" / "evidence"


def _extract_json(text: str) -> dict:
    """从 CLI 输出中提取最后一个顶层 JSON 对象 (前面可能有日志)。"""
    marker = text.rfind("\n{\n")
    start = marker + 1 if marker >= 0 else text.find("{")
    if start < 0:
        return {"parse_error": text[-500:]}
    try:
        return json.loads(text[start:])
    except Exception as exc:
        return {"parse_error": f"{exc}", "tail": text[-500:]}


def _cli(*args: str, timeout: float = 240.0) -> dict:
    r = subprocess.run([PY, CLI, *args], cwd=str(ROOT), capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       timeout=timeout)
    out = _extract_json(r.stdout)
    return {"returncode": r.returncode, **out}


def _poll_lock(timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        last = _cli("status")
        if last.get("lock_owner"):
            return last
        time.sleep(0.5)
    return last


def _setup_loaded(client, sim, simIK):
    from stage1.lifecycle import LifecycleManager
    import stage1.config as cfg

    mgr = LifecycleManager(sim, simIK, actor="acceptance")
    mgr.refresh()
    if mgr.sm.state.value == "ERROR":
        mgr.load("current")
    elif mgr.sm.state.value in ("CONNECTED_STOPPED", "SCENE_LOADED"):
        mgr.load("current")
    elif mgr.sm.state.value == "SCENE_VALIDATED":
        # 可能是运行后的非初始位姿; 重新 load 保证基线状态。
        mgr.load("current")
    mgr.validate()
    return mgr


def check_alias_and_config(sim, simIK) -> dict:
    from stage1 import config as cfg
    from stage1 import scene_artifact as sa
    from stage1.lifecycle import InvalidTransitionError, ValidationFailedError

    mgr = _setup_loaded(None, sim, simIK)
    art = mgr.artifact
    baseline_sha = sa.sha256_file(art.scene_path)

    # ── 1. 移除必需 alias ──────────────────────────────────
    floor = sim.getObject("/Floor")
    sim.setObjectAlias(floor, "Floor_Tampered")
    alias_error, alias_report = None, None
    try:
        mgr.validate()
    except ValidationFailedError as exc:
        alias_error, alias_report = exc.code, exc.report
    alias_state = mgr.sm.state.value
    start_refusal = None
    try:
        mgr.prepare_start()
    except InvalidTransitionError as exc:
        start_refusal = exc.code
    except Exception as exc:  # noqa: BLE001
        start_refusal = f"{type(exc).__name__}: {exc}"

    # 恢复
    mgr.load("current")
    mgr.validate()

    # ── 2. 修改配置摘要 ────────────────────────────────────
    old_mass = cfg.MATERIAL_MASS
    cfg.MATERIAL_MASS = old_mass + 1.0
    config_error, config_report = None, None
    try:
        mgr.validate()
    except ValidationFailedError as exc:
        config_error, config_report = exc.code, exc.report
    finally:
        cfg.MATERIAL_MASS = old_mass
    config_state = mgr.sm.state.value

    # 恢复并确认基线文件未变化
    mgr.load("current")
    mgr.validate()
    after_sha = sa.sha256_file(art.scene_path)

    return {
        "artifact_id": art.artifact_id,
        "baseline_scene_sha256": baseline_sha,
        "after_checks_scene_sha256": after_sha,
        "baseline_unchanged": baseline_sha == after_sha,
        "alias_tamper": {
            "error_code": alias_error,
            "state_after_failure": alias_state,
            "missing_aliases": (alias_report or {}).get("missing_aliases"),
            "failed_checks": [c["name"] for c in
                              (alias_report or {}).get("checks", [])
                              if not c["ok"]],
            "start_refusal": start_refusal,
        },
        "config_mismatch": {
            "error_code": config_error,
            "state_after_failure": config_state,
            "config_diffs": [d for d in
                             (config_report or {}).get("differences", [])
                             if d.get("kind") == "config"][:5],
        },
        "restored_state": mgr.sm.state.value,
    }


def check_concurrency() -> dict:
    # 确保从已校验工件开始
    load = _cli("load", "current")
    validate = _cli("validate")
    proc = subprocess.Popen(
        [PY, CLI, "start", "--target", "station", "--time", "60"],
        cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace")
    status = _poll_lock()
    time.sleep(0.5)
    load_during = _cli("load", "current")
    build_during = _cli("build")
    out, _ = proc.communicate(timeout=300)
    report = _extract_json(out)
    return {
        "preload": {"returncode": load.get("returncode"),
                    "state": load.get("lifecycle_state")},
        "prevalidate": {"returncode": validate.get("returncode"),
                        "ok": validate.get("ok")},
        "running_status": {
            "lifecycle_state": status.get("lifecycle_state"),
            "lock_owner": status.get("lock_owner"),
        },
        "load_during_run": {
            "returncode": load_during.get("returncode"),
            "code": load_during.get("code"),
            "error": load_during.get("error"),
        },
        "build_during_run": {
            "returncode": build_during.get("returncode"),
            "code": build_during.get("code"),
            "error": build_during.get("error"),
        },
        "start_returncode": proc.returncode,
        "start_all_ok": report.get("all_ok"),
        "final_lifecycle": report.get("lifecycle"),
    }


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-concurrency", action="store_true")
    ap.add_argument("--skip-negative", action="store_true")
    ns = ap.parse_args()

    from stage1.run import connect

    client, sim, simIK = connect("localhost", 23000)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    result = {
        "timestamp": stamp,
        "phase": "step1.2",
        "simulator": {
            "version": sim.getStringProperty(sim.handle_app, "productVersion"),
            "state": sim.getSimulationState(),
        },
    }
    if not ns.skip_negative:
        result["negative_checks"] = check_alias_and_config(sim, simIK)
    if not ns.skip_concurrency:
        result["concurrency"] = check_concurrency()

    result["all_ok"] = (
        result.get("negative_checks", {}).get("baseline_unchanged", True)
        and result.get("negative_checks", {}).get(
            "alias_tamper", {}).get("error_code") == "VALIDATION_FAILED"
        and result.get("negative_checks", {}).get(
            "config_mismatch", {}).get("error_code") == "VALIDATION_FAILED"
        and result.get("concurrency", {}).get(
            "load_during_run", {}).get("code") == "RUN_CONFLICT"
        and result.get("concurrency", {}).get(
            "build_during_run", {}).get("code") == "RUN_CONFLICT"
        and result.get("concurrency", {}).get("start_all_ok", True)
    )

    EVIDENCE.mkdir(parents=True, exist_ok=True)
    out = EVIDENCE / f"step1_2_lifecycle_checks_{stamp}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\n[EVIDENCE] {out}")
    return 0 if result["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
