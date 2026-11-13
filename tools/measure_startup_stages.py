"""v1 启动阶段计时: 从 ZMQ 连接开始到仿真刚刚启动.

严格复现 ``flexible-factory run`` 的启动路径 (cmd_run + run_loaded_scene_v1
的前半段), 在 ``start_stepping()`` 返回后立即记录时间点, 不执行任何场景
step, 也不运行 coordinator. 随后做安全清理 (停仿真/销毁影子臂/结束生命周期).

用法 (仓库根目录):
    uv run python tools/measure_startup_stages.py
    uv run python tools/measure_startup_stages.py --run-records off --no-load
    uv run python tools/measure_startup_stages.py --probe   # 额外测驱动探测(会步进)

说明文档: docs/wiki/98-ProjectUsage/USAGE.md 的“启动阶段计时”一节.

输出:
  - stdout 分阶段表格; build.* / profile.* 是重叠子项, 不能相加,
    startup_total_wall 才是“进程开始 → 仿真启动”的真实总时间;
  - docs/implementation/runs/startup_timing_<stamp>.json (可用 --out 覆盖).
"""

from __future__ import annotations

import time

T_PROGRAM = time.perf_counter()   # 进程启动参考点 (其余导入之前)

import argparse  # noqa: E402
import json  # noqa: E402
import traceback  # noqa: E402
from contextlib import contextmanager  # noqa: E402
from datetime import datetime  # noqa: E402
from pathlib import Path  # noqa: E402

from flexible_factory import spec  # noqa: E402
from flexible_factory.application import run as run_mod  # noqa: E402
from flexible_factory.application.batch import resolve_batch  # noqa: E402
from flexible_factory.application.catalog import runtime_catalog  # noqa: E402
from flexible_factory.application.lifecycle import LifecycleManager  # noqa: E402
from flexible_factory.application.run import (  # noqa: E402
    REPORT_DIR, build_components_v1, collect_scene_snapshot, setup_logger,
    snapshot_config)
from flexible_factory.artifacts import store as sa  # noqa: E402
from flexible_factory.persistence.db import CatalogDatabase  # noqa: E402
from flexible_factory.persistence.runs import RunRecorder  # noqa: E402
from flexible_factory.simulation import config as cfg  # noqa: E402
from flexible_factory.simulation import contract  # noqa: E402
from flexible_factory.simulation.component_builder import (  # noqa: E402
    connect, probe_conveyor_drive, start_stepping, stop_simulation)


class Timings:
    """顺序阶段计时; build 子阶段允许与主阶段重叠, 表中标注."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def add(self, stage: str, seconds: float, note: str = "") -> None:
        self.rows.append({"stage": stage,
                          "seconds": round(float(seconds), 4),
                          "note": note})

    @contextmanager
    def time(self, stage: str, note: str = ""):
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.add(stage, time.perf_counter() - t0, note)


def _instrument_validation(timings: Timings) -> None:
    """拆分 artifact_load / prepare_start 内部的 adopt 与校验子阶段."""
    from flexible_factory.application import lifecycle as lifecycle_mod
    from flexible_factory.artifacts import store as store_mod
    from flexible_factory.simulation import contract as contract_mod
    from flexible_factory.simulation import scene_base as scene_base_mod

    adopt = lifecycle_mod.adopt_stage4_scene

    def timed_adopt(*args, **kwargs):
        t0 = time.perf_counter()
        try:
            return adopt(*args, **kwargs)
        finally:
            timings.add("profile.adopt_stage4_scene",
                        time.perf_counter() - t0,
                        "句柄解析; load 或 --no-load 的 _ensure_scene 内")

    lifecycle_mod.adopt_stage4_scene = timed_adopt

    ensure = lifecycle_mod.LifecycleManager._ensure_scene

    def timed_ensure(self):
        t0 = time.perf_counter()
        try:
            return ensure(self)
        finally:
            timings.add("profile.ensure_scene",
                        time.perf_counter() - t0,
                        "prepare_start 内的场景恢复/句柄解析")

    lifecycle_mod.LifecycleManager._ensure_scene = timed_ensure

    context = lifecycle_mod.build_validation_context

    def timed_context(*args, **kwargs):
        t0 = time.perf_counter()
        try:
            return context(*args, **kwargs)
        finally:
            timings.add("profile.build_validation_context",
                        time.perf_counter() - t0,
                        "全场景索引 + 一次 scene.verify()")

    lifecycle_mod.build_validation_context = timed_context
    scene_base_mod.build_validation_context = timed_context

    validate_artifact = store_mod.validate_artifact

    def timed_validate_artifact(*args, **kwargs):
        t0 = time.perf_counter()
        try:
            return validate_artifact(*args, **kwargs)
        finally:
            timings.add("profile.validate_artifact",
                        time.perf_counter() - t0,
                        "文件/环境/场景对象校验")

    store_mod.validate_artifact = timed_validate_artifact

    validate_contract = contract_mod.validate_contract

    def timed_validate_contract(*args, **kwargs):
        t0 = time.perf_counter()
        try:
            return validate_contract(*args, **kwargs)
        finally:
            timings.add("profile.validate_contract",
                        time.perf_counter() - t0,
                        "extra.v1/版本/拓扑/单元契约校验")

    contract_mod.validate_contract = timed_validate_contract


def _instrument_build(timings: Timings) -> None:
    """给 build_components_v1 的子步骤加轻量计时 (仅本进程)."""
    base = run_mod.build_stage1_components

    def timed_base(*args, **kwargs):
        t0 = time.perf_counter()
        try:
            return base(*args, **kwargs)
        finally:
            timings.add("build.base_components",
                        time.perf_counter() - t0,
                        "物料/传送带/AGV/A 臂基座组件")

    run_mod.build_stage1_components = timed_base

    ik_init = run_mod.Ur5IkController.initialize

    def timed_ik_init(self):
        t0 = time.perf_counter()
        try:
            return ik_init(self)
        finally:
            timings.add("build.ik_initialize_total",
                        time.perf_counter() - t0,
                        "A–E 五台合计; A 部分已含在 base_components 内")

    run_mod.Ur5IkController.initialize = timed_ik_init

    build_runtime = run_mod.build_v1_runtime

    def timed_runtime(*args, **kwargs):
        t0 = time.perf_counter()
        try:
            return build_runtime(*args, **kwargs)
        finally:
            timings.add("build.shadow_runtime",
                        time.perf_counter() - t0,
                        "五台影子臂/碰撞集合")

    run_mod.build_v1_runtime = timed_runtime

    suction_init = run_mod.Stage2SuctionController.__init__

    def timed_suction(self, *args, **kwargs):
        t0 = time.perf_counter()
        try:
            return suction_init(self, *args, **kwargs)
        finally:
            timings.add("build.suction_controllers",
                        time.perf_counter() - t0,
                        "五台吸盘控制器合计")

    run_mod.Stage2SuctionController.__init__ = timed_suction


def _build_snapshot(*, stamp: str, run_id: str, artifact_id: str,
                    scenario: str, max_sim_time: float, scene, comp,
                    batch: dict, batch_profile: str) -> dict:
    """与 run_loaded_scene_v1 相同的快照结构."""
    return {
        "timestamp": stamp,
        "run_id": run_id,
        "artifact_id": artifact_id,
        "phase": "v1",
        "lifecycle_state": "SIMULATION_RUNNING",
        "scenario": scenario,
        "config": snapshot_config(
            scenario, max_sim_time=max_sim_time, scene=scene,
            batch=batch, batch_profile=batch_profile),
        "scene": collect_scene_snapshot(scene, comp),
        "contract": {
            "validated_by": "lifecycle.prepare_start",
            "phase": "v1",
        },
    }


def _print_table(rows: list[dict], startup_total: float,
                 cleanup_rows: list[dict]) -> None:
    width = max(len(row["stage"]) for row in rows) if rows else 10

    def line(row: dict, total: float) -> str:
        pct = (100.0 * row["seconds"] / total) if total > 0 else 0.0
        note = f"  # {row['note']}" if row["note"] else ""
        return (f"{row['stage']:<{width}}  {row['seconds']:>9.3f}s  "
                f"{pct:>6.1f}%{note}")

    print("\n" + "=" * 78)
    print("启动阶段计时 (进程开始 → 仿真刚刚启动)")
    print("子项 build.* / profile.* 与主阶段重叠; 百分比不可相加, "
          "以 startup_total_wall 为准")
    print("=" * 78)
    for row in rows:
        print(line(row, startup_total))
    measured = sum(row["seconds"] for row in rows
                   if not row["stage"].startswith(("build.", "profile.")))
    print("-" * 78)
    print(f"{'non_overlapping_stages_total':<{width}}  {measured:>9.3f}s"
          f"  (不含 build.* / profile.* 重叠子项)")
    print(f"{'startup_total_wall':<{width}}  {startup_total:>9.3f}s"
          f"  (含未计时间隙; 真实总时间)")
    if cleanup_rows:
        print("-" * 78)
        print("清理阶段 (不计入启动总计)")
        for row in cleanup_rows:
            print(f"{row['stage']:<{width}}  {row['seconds']:>9.3f}s")
    print("=" * 78 + "\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="测量 v1 从连接到仿真启动的各阶段耗时")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=23000)
    parser.add_argument("--artifact-root", default=None)
    parser.add_argument("--artifact", default="current")
    parser.add_argument("--no-load", action="store_true",
                        help="使用当前已加载场景, 不重新 load")
    parser.add_argument("--scenario", default=cfg.SCENARIO_FULL_FLOW)
    parser.add_argument("--batch-profile", default="default")
    parser.add_argument("--time", type=float, default=1200.0,
                        help="写入快照的 max_sim_time, 仅记录, 不执行")
    parser.add_argument("--catalog-source", choices=["code", "db"],
                        default="code")
    parser.add_argument("--catalog-db", default=str(spec.CATALOG_DB))
    parser.add_argument("--run-records", choices=["on", "off"], default="on")
    parser.add_argument("--log", default=spec.DEFAULT_LOG_FILE)
    parser.add_argument("--out-dir", default=REPORT_DIR)
    parser.add_argument("--out", default="")
    parser.add_argument("--probe", action="store_true",
                        help="启动后额外运行传送带驱动探测 (会步进仿真)")
    args = parser.parse_args(argv)

    timings = Timings()
    timings.add("process_import", time.perf_counter() - T_PROGRAM,
                "解释器启动至项目模块导入完成")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    result = {
        "timestamp": stamp,
        "host": args.host,
        "port": args.port,
        "artifact": args.artifact,
        "no_load": bool(args.no_load),
        "scenario": args.scenario,
        "batch_profile": args.batch_profile,
        "catalog_source": args.catalog_source,
        "run_records": args.run_records,
        "stages": timings.rows,
    }
    cleanup = Timings()

    manager = None
    comp = None
    recorder = None
    sim = None
    lock_acquired = False
    startup_total = 0.0

    _instrument_validation(timings)
    _instrument_build(timings)

    try:
        with timings.time("batch_resolve", "运行前批次校验"):
            resolve_batch(args.scenario, profile=args.batch_profile)

        catalog = None
        if args.catalog_source == "db":
            with timings.time("catalog_resolve", "显式选库等价校验"):
                catalog = runtime_catalog(CatalogDatabase(Path(args.catalog_db)))

        with timings.time("logger_setup", "flexible_factory.log"):
            logger = setup_logger(args.log)

        with timings.time("zmq_connect", "RemoteAPIClient + sim/simIK"):
            _, sim, simIK = connect(args.host, args.port)
        result["simulator"] = sa.simulator_info(sim)
        result["config_digest"] = contract.config_digest()

        with timings.time("lifecycle_init", "会话状态/锁/工件仓库"):
            manager = LifecycleManager(
                sim, simIK, host=args.host, port=args.port,
                actor="measure-startup",
                store=sa.ArtifactStore(
                    Path(args.artifact_root) if args.artifact_root else None),
                report_dir=Path(args.out_dir))

        if not args.no_load:
            with timings.time("artifact_load", "loadScene + 句柄解析"):
                manager.load(args.artifact)

        with timings.time("prepare_start_validation",
                          "运行锁 + 启动点全量只读校验"):
            prepared = manager.prepare_start(target=args.scenario)
        lock_acquired = True
        run_id = prepared["run_id"]
        result["run_id"] = run_id
        result["artifact_id"] = manager.session.artifact_id
        result["manifest_digest"] = manager.session.manifest_digest

        if args.run_records == "on":
            with timings.time("run_record_init", "数据库初始化 + RUNNING 行"):
                recorder = RunRecorder(
                    CatalogDatabase(Path(args.catalog_db)), run_id=run_id,
                    scenario=args.scenario,
                    artifact_id=manager.session.artifact_id or "",
                    catalog_source=args.catalog_source,
                    catalog_version=(catalog.version
                                     if catalog is not None else ""))
                started = recorder.start(
                    sim_time=float(sim.getSimulationTime()))
                if not started.get("ok"):
                    result["run_record_error"] = started.get("error")
                    recorder = None

        scene = manager.scene
        if scene is None:
            raise RuntimeError("prepare_start 后仍未解析到场景; "
                               "请去掉 --no-load 或先 load 工件")

        with timings.time("build_components",
                          "含 build.* 子项, 子项与主项重叠"):
            comp = build_components_v1(sim, simIK, scene, logger)

        with timings.time("snapshot_write", "v1_scene_snapshot_*.json"):
            snapshot = _build_snapshot(
                stamp=stamp, run_id=run_id,
                artifact_id=manager.session.artifact_id or "",
                scenario=args.scenario, max_sim_time=args.time,
                scene=scene, comp=comp,
                batch=resolve_batch(args.scenario,
                                    profile=args.batch_profile),
                batch_profile=args.batch_profile)
            out_dir = Path(args.out_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            with open(out_dir / f"v1_scene_snapshot_{stamp}.json", "w",
                      encoding="utf-8") as handle:
                json.dump(snapshot, handle, ensure_ascii=False, indent=2)

        with timings.time("simulation_start", "setStepping+startSimulation"):
            start_stepping(sim)
        startup_total = time.perf_counter() - T_PROGRAM
        result["sim_time_at_start"] = float(sim.getSimulationTime())
        result["sim_started_stamp"] = datetime.now().isoformat(
            timespec="milliseconds")

        if args.probe:
            with timings.time("post_start.conveyor_drive_probe",
                              "启动后附加, 会步进约 35 次"):
                result["conveyor_drive_probe"] = probe_conveyor_drive(
                    sim, scene, comp)
    except Exception:
        result["error"] = traceback.format_exc()
        if startup_total == 0.0:
            startup_total = time.perf_counter() - T_PROGRAM
    finally:
        # ── 清理 (不计入启动总计) ──
        if sim is not None and lock_acquired:
            # 仅在本次持有运行锁 (prepare_start 成功) 后停仿真;
            # 启动失败时不误停其他进程的运行.
            try:
                with cleanup.time("cleanup.stop_simulation"):
                    stop_simulation(sim)
            except Exception:
                result["stop_simulation_error"] = traceback.format_exc()
        if comp is not None:
            try:
                with cleanup.time("cleanup.runtime_destroy",
                                  "影子臂/碰撞集合"):
                    result["runtime_destroy"] = comp["runtime"].destroy()
            except Exception:
                result["runtime_destroy_error"] = traceback.format_exc()
        if recorder is not None:
            with cleanup.time("cleanup.run_record_finish"):
                result["run_record"] = recorder.finish(
                    None, None,
                    error="startup timing measurement: scenario not executed",
                    status="ABORTED")
        if manager is not None:
            try:
                with cleanup.time("cleanup.lifecycle_finish"):
                    result["lifecycle"] = manager.finish_run(
                        "FAILED" if result.get("error") else "ABORTED",
                        error=(result.get("error") or "")[:2000] or None)
            except Exception:
                result["lifecycle_error"] = traceback.format_exc()

        result["startup_total_wall_s"] = round(startup_total, 4)
        result["stages"] = timings.rows
        result["cleanup"] = cleanup.rows
        _print_table(timings.rows, startup_total, cleanup.rows)

        out_path = Path(args.out) if args.out else (
            Path(args.out_dir) / f"startup_timing_{stamp}.json")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
        print(f"[OK] 结果写入 {out_path}")
        if result.get("error"):
            print(result["error"], file=__import__("sys").stderr)

    return 1 if result.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
