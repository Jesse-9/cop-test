"""stage2_harness.py — 阶段 2 focused 仿真验收入口 (仅供测试).

不是产品入口: 不新增 CLI 参数、scenario 或运行模式, 也不生成来源批次。
只在 `FLEXIBLE_FACTORY_SIM=1` 且本机已启动 CoppeliaSim 4.10 (ZeroMQ
Remote API) 时执行; 否则调用方 skip 并标注"未执行"。

唯一 stepping owner 仍是 :meth:`Stage4Coordinator.step`; 本模块只准备
场景与任务, 然后反复调用协调器步进, 不直接调用 `sim.step()`。
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Callable, Optional

from flexible_factory.application.coordinator import Stage4Coordinator
from flexible_factory.application.lifecycle import LifecycleManager
from flexible_factory.application.run import build_components_v1
from flexible_factory.domain.materials import ItemType
from flexible_factory.domain.tasks import Stage4Task, make_task_from_operation
from flexible_factory.simulation import config as cfg
from flexible_factory.simulation import config_base as base
from flexible_factory.simulation.component_builder import (
    connect, start_stepping, stop_simulation)

SIM_ENV = "FLEXIBLE_FACTORY_SIM"


def sim_enabled() -> bool:
    """是否显式允许连接本机仿真器."""
    return os.environ.get(SIM_ENV, "") == "1"


def require_sim() -> None:
    """未启用仿真时跳过测试, 并给出可复现的启用方式."""
    if sim_enabled():
        return
    import pytest

    pytest.skip(
        f"focused 仿真未执行: 需要 {SIM_ENV}=1 且本机已启动 CoppeliaSim "
        f"4.10 (ZMQ 23000)")


@dataclass
class Stage2Context:
    """一次 focused 验收的会话: 场景 + 组件 + 仿真时钟."""

    sim: object
    simIK: object
    manager: LifecycleManager
    scene: object
    comp: dict
    logger: object
    client: object = None

    # ── 使用 ────────────────────────────────────────────────

    def new_coordinator(self, **kwargs) -> Stage4Coordinator:
        """构造协调器并抑制来源批次, 由测试显式注入任务."""
        kwargs.setdefault("logger", self.logger)
        coordinator = Stage4Coordinator(self.sim, self.scene, self.comp,
                                       **kwargs)
        coordinator._batch = {}
        coordinator.start(coordinator._sim_time())
        return coordinator

    def inject_source(self, coordinator: Stage4Coordinator, *,
                      source_key: str, operation_id: str,
                      uid: int = 1) -> Stage4Task:
        """在来源线入口生成一个物料并提交对应工序任务."""
        entry = coordinator._source_entries[source_key]
        spec = cfg.SOURCE_ITEMS[source_key]
        item_type = ItemType(entry.item_type.value)
        alias = f"Part_{uid:04d}"
        sim_time = coordinator._sim_time()
        handle = self.scene.spawn_material(uid, alias,
                                           position=spec["position"])
        coordinator._set_material_color(handle, item_type)
        coordinator.materials.register(uid, alias, sim_handle=handle)
        coordinator.flow.register(
            uid, item_type, alias, sim_handle=handle, source=entry.line,
            destination="", zone=entry.line, sim_time=sim_time)
        task = make_task_from_operation(
            coordinator.catalog.operation(operation_id), uid,
            source=entry.line, sim_time=sim_time)
        coordinator._submit(task, sim_time)
        return task

    def inject_tray_material(self, coordinator: Stage4Coordinator, *,
                             uid: int, item_type: ItemType) -> str:
        """在 AGV 载台上放置一个已登记物料, 供载货路线测试."""
        sim = self.sim
        tray = self.scene.handle(base.ALIASES["agv_tray"])
        pose = sim.getObjectPose(tray, sim.handle_world)
        position = [float(pose[0]), float(pose[1]),
                    float(pose[2]) + base.AGV_TRAY_THICKNESS / 2.0
                    + base.MATERIAL_HALF_HEIGHT]
        alias = f"Part_{uid:04d}"
        sim_time = coordinator._sim_time()
        handle = self.scene.spawn_material(uid, alias, position=position)
        coordinator._set_material_color(handle, item_type)
        coordinator.materials.register(uid, alias, sim_handle=handle)
        coordinator.flow.register(
            uid, item_type, alias, sim_handle=handle, source="agv",
            destination="", zone="agv", sim_time=sim_time)
        return alias

    def close(self) -> None:
        """停止仿真并在停止态释放会话资源 (影子臂/碰撞集合)."""
        try:
            stop_simulation(self.sim)
        finally:
            try:
                self.comp["runtime"].destroy()
            except Exception:
                pass

    def __enter__(self) -> "Stage2Context":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def _probe_port(host: str, port: int, timeout: float = 3.0) -> None:
    """先做 TCP 探测: ZMQ 客户端在端口无监听时会无限重试而不是报错."""
    import socket

    with socket.socket() as sock:
        sock.settimeout(timeout)
        try:
            sock.connect((host, int(port)))
        except OSError as exc:
            raise RuntimeError(
                f"无法连接仿真器 {host}:{port} ({exc}); 请启动 "
                f"CoppeliaSim 4.10 并开启 ZMQ Remote API server") from exc


def open_context(*, host: str = "localhost", port: int = 23000,
                 log_path: str = "stage2_harness.log") -> Stage2Context:
    """连接本机仿真器, 加载当前 v1 工件并构建运行组件."""
    import logging

    _probe_port(host, port)

    logger = logging.getLogger("stage2")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.addHandler(logging.FileHandler(log_path, encoding="utf-8"))

    client, sim, simIK = connect(host, port)
    manager = LifecycleManager(sim, simIK, host=host, port=port)
    manager.load("current")
    scene = manager._ensure_scene()
    if scene is None:
        raise RuntimeError("未解析到 v1 场景; 请先加载工件")
    comp = build_components_v1(sim, simIK, scene, logger)
    start_stepping(sim)
    return Stage2Context(sim=sim, simIK=simIK, manager=manager,
                         scene=scene, comp=comp, logger=logger,
                         client=client)


def write_evidence(name: str, payload: dict) -> str:
    """把 focused 证据写到临时运行目录 (正式结论再复制到 evidence/)."""
    import json
    from datetime import datetime

    from flexible_factory import spec

    out_dir = spec.REPORT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"v1_stage2_{name}_{stamp}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2,
                               default=str), encoding="utf-8")
    return str(path)


def drive(coordinator: Stage4Coordinator, *,
          should_stop: Optional[Callable[[], bool]] = None,
          max_steps: int = 20000,
          max_wall_s: float = 900.0) -> dict:
    """反复调用协调器步进, 直到条件满足/收敛/超时.

    只调用 ``coordinator.step()``, 因此 pre_tick → sim.step → post_tick
    的顺序与单一 stepping owner 由协调器保证。
    """
    stop = should_stop or (lambda: coordinator.finished)
    started = time.time()
    steps = 0
    while not stop():
        coordinator.step()
        steps += 1
        if steps >= max_steps or time.time() - started > max_wall_s:
            break
    return {
        "steps": steps,
        "wall_s": round(time.time() - started, 3),
        "sim_time": round(coordinator._sim_time(), 4),
        "finished": bool(coordinator.finished),
        "frozen": bool(coordinator.frozen),
        "aborted": bool(coordinator.aborted),
        "stop_reason": coordinator.stop_reason,
    }
