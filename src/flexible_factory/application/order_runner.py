"""order_runner.py — 订单运行的真实 CoppeliaSim worker runner (阶段 3).

本模块只在 worker 线程内装配 CoppeliaSim 会话:

    connect ZMQ → LifecycleManager.load/prepare_start → build components
    → start stepping → Stage4Coordinator(order_plan) → 单步循环
    → stop/destroy → 运行记录.

HTTP handler / CLI 不直接持有 sim、scene、controller 或 handle. 纯逻辑测试
通过注入 runner 使用本模块, 不启动仿真.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from .. import spec
from ..domain.process_model import ProcessCatalog
from ..persistence.db import CatalogDatabase
from ..persistence.runs import RunRecorder
from ..simulation import config as cfg

if TYPE_CHECKING:
    from .orders import OrderRunCommand


@dataclass(frozen=True)
class OrderRunnerConfig:
    host: str = "localhost"
    port: int = 23000
    artifact_root: Optional[Path] = None
    record_db: Optional[Path] = None
    run_records: bool = True
    catalog_source: str = "code"
    log: str = spec.DEFAULT_LOG_FILE
    out_dir: Path = spec.REPORT_DIR


def required_arm_units(compiled) -> tuple[str, ...]:
    """订单 DAG 可能调度到的臂工位并集.

    取每个实例的候选设备并集, 不只看 planner 当前选择; 这样调度改选时
    不会落到未构建的运行时。没有臂候选时返回空集, 由调用方决定保守回退。
    """
    wanted: set[str] = set()
    for instance in getattr(compiled, "instances", ()) or ():
        devices = list(getattr(instance, "candidate_device_ids", ()) or ())
        selected = str(getattr(instance, "selected_device_id", "") or "")
        if selected:
            devices.append(selected)
        for device_id in devices:
            text = str(device_id)
            if text.startswith("arm_"):
                wanted.add(text[len("arm_"):])
    return tuple(unit_id for unit_id in cfg.UNIT_IDS if unit_id in wanted)


def make_order_runner(config: OrderRunnerConfig,
                      catalog: Optional[ProcessCatalog] = None):
    """构造 worker runner; 延迟导入 CoppeliaSim 相关模块."""
    def run(command: "OrderRunCommand", stop_event) -> dict:
        return run_order_once(config, command, stop_event, catalog=catalog)
    return run


def run_order_once(config: OrderRunnerConfig, command: "OrderRunCommand",
                   stop_event, *,
                   catalog: Optional[ProcessCatalog] = None) -> dict:
    from ..simulation.component_builder import (connect, start_stepping,
                                                stop_simulation)
    from .lifecycle import LifecycleManager
    from .run import build_components_v1, setup_logger
    from .coordinator import Stage4Coordinator

    logger = setup_logger(config.log)
    logger.info("[ORDER] start run=%s order=%s@%s artifact=%s",
                command.run_id, command.order.order_id,
                command.order.version, command.artifact_id)
    _client, sim, simIK = connect(config.host, config.port)
    manager = LifecycleManager(
        sim, simIK, host=config.host, port=config.port, actor="order",
        artifact_root=config.artifact_root, report_dir=Path(config.out_dir))
    recorder = None
    coordinator = None
    summary: Optional[dict] = None
    try:
        manager.load(command.artifact_id or "current")
        manager.prepare_start(target="order", run_id=command.run_id)
        if config.run_records and config.record_db is not None:
            recorder = RunRecorder(
                CatalogDatabase(Path(config.record_db)),
                run_id=command.run_id, scenario="order",
                artifact_id=command.artifact_id,
                catalog_source=config.catalog_source or "code",
                catalog_version=(catalog.version
                                 if catalog is not None else ""))
            started = recorder.start(
                sim_time=float(sim.getSimulationTime()))
            if not started.get("ok"):
                logger.error("[ORDER] 运行记录启动失败, 运行继续: %s",
                             started.get("error"))
        unit_ids = required_arm_units(command.compiled)
        if not unit_ids:
            logger.warning(
                "[ORDER] 订单没有可识别臂候选; 保守构建全部五台")
            unit_ids = tuple(cfg.UNIT_IDS)
        else:
            logger.info("[ORDER] 按订单候选构建臂运行时: %s",
                        list(unit_ids))
        comp = build_components_v1(
            sim, simIK, manager.scene, logger, unit_ids=unit_ids)
        start_stepping(sim)
        coordinator = Stage4Coordinator(
            sim, manager.scene, comp, scenario=cfg.SCENARIO_FULL_FLOW,
            catalog=catalog, order_plan=command.compiled, logger=logger)
        coordinator.start(float(sim.getSimulationTime()))
        task_start = float(sim.getSimulationTime())
        wall_deadline = time.time() + max(
            600.0, float(command.max_sim_time) * 6.0)
        steps = 0
        while not coordinator.finished:
            if stop_event is not None and stop_event.is_set():
                coordinator._freeze(
                    "STOP_REQUESTED", coordinator._sim_time(), abort=True)
                break
            coordinator.step()
            steps += 1
            if command.progress is not None:
                # 仅内存快照, 不写数据库; HTTP 查询从 worker 读取.
                command.progress({
                    "status": "RUNNING",
                    "order_status": coordinator.order_status(),
                    "sim_time": round(float(sim.getSimulationTime()), 4),
                    "nodes": [row.as_dict()
                              for row in coordinator.op_runtime.instances()],
                    "events": list(coordinator.events[-500:]),
                    "failures": list(coordinator.failures),
                })
            if float(sim.getSimulationTime()) - task_start > \
                    float(command.max_sim_time):
                coordinator._freeze(
                    f"SIM_TIME_LIMIT: {command.max_sim_time:.1f}s",
                    coordinator._sim_time(), abort=True)
                break
            if time.time() > wall_deadline:
                coordinator._freeze(
                    "WALL_CLOCK_TIMEOUT", coordinator._sim_time(),
                    abort=True)
                break
        summary = coordinator.summary()
        summary["steps"] = steps
        summary["run_id"] = command.run_id
        summary["artifact_id"] = command.artifact_id
        summary["active_unit_ids"] = list(unit_ids)
        summary["all_ok"] = bool(
            (summary.get("acceptance") or {}).get("ok"))
        summary["lifecycle"] = manager.finish_run(
            "DONE" if summary["all_ok"] else "FAILED")
        if recorder is not None:
            summary["run_record"] = recorder.finish(coordinator, summary)
        _write_report(config.out_dir, command, summary)
        return summary
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        logger.exception("[ORDER] run %s 失败: %s", command.run_id, error)
        if recorder is not None:
            recorder.finish(coordinator, summary, error=error)
        try:
            manager.finish_run("FAILED", error)
        except Exception:
            pass
        raise
    finally:
        try:
            stop_simulation(sim)
        except Exception:
            logger.exception("[ORDER] stop_simulation 失败")
        if coordinator is not None:
            try:
                destroy_report = coordinator.runtime.destroy()
                if summary is not None:
                    summary["runtime_destroy"] = destroy_report
            except Exception:
                logger.exception("[ORDER] runtime destroy 失败")


def _write_report(out_dir, command: "OrderRunCommand",
                  summary: dict) -> None:
    try:
        path = Path(out_dir)
        path.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = path / f"v1_order_run_{stamp}.json"
        payload = {
            "command": command.as_dict(),
            "summary": summary,
        }
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
    except Exception:      # 报告写入失败不改变运行终态
        logging.getLogger("flexible_factory.orders").exception(
            "[ORDER] 运行报告写入失败")


__all__ = ["OrderRunnerConfig", "make_order_runner", "run_order_once"]
