"""conveyor_controller.py — HYBRID_DYNAMIC 传送带输送 (§2.4 / §2.5).

驱动: 内置 `generic conveyor (belt).ttm` 的 customization 脚本通过
`customData.__ctrl__` buffer 属性调速 (4.10 实测; 无公开脚本函数)。

物料输送: 4.10 的 pad 不会把切向速度传给 dynamic 物料 (实测 <0.01 m/10s),
因此采用 HYBRID_DYNAMIC 的受限补偿: 先让物理接触生效, 再沿带面切线按
"带速 × dt" 的上限显式推进。补偿不改变物料的 dynamic/respondable 属性,
且每一步都校验物料仍在有效区; 超出偏差阈值立即进入 FAULT。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

from . import config as cfg


class ConveyorError(Exception):
    """传送带操作异常"""


def configure_conveyor_model(sim, handle: int, accel: float,
                             target_velocity: float) -> dict | None:
    """改写内置 conveyor 的 ``customData.__config__``。

    模型脚本在每次 simulation start 的 init 中读取该表；必须在启动前写入。
    ``accel`` 过小会导致停车滑行数秒；过大则脚本在 pos 不变时不再刷新
    ``__state__``，速度回读失真。返回写入后的配置，失败返回 None。
    """
    try:
        data = sim.readCustomTableData(handle, "__config__")
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    data["accel"] = float(accel)
    data["targetVel"] = float(target_velocity)
    try:
        sim.writeCustomTableData(handle, "__config__", data)
        readback = sim.readCustomTableData(handle, "__config__")
    except Exception:
        return None
    return readback if isinstance(readback, dict) else None


class TransportFault(Exception):
    """物料偏离有效区或掉落, 必须进入 CONVEYOR_FAULT"""


# ═══════════════════════════════════════════════════════════════
# 几何
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class BeltGeometry:
    """带面有效行程 (入口 dummy → 停止区 dummy) 的世界坐标。"""
    entry: tuple[float, float, float]
    stop: tuple[float, float, float]

    @property
    def length(self) -> float:
        return math.dist(self.entry, self.stop)

    @property
    def direction(self) -> tuple[float, float, float]:
        n = self.length
        if n <= 1e-9:
            raise ConveyorError("传送带有效行程长度为 0")
        return tuple((self.stop[i] - self.entry[i]) / n for i in range(3))

    def progress(self, position) -> float:
        """物料在有效行程上的投影进度, 0..1 (超出端点自动截断)。"""
        d = self.direction
        rel = [position[i] - self.entry[i] for i in range(3)]
        s = sum(rel[i] * d[i] for i in range(3))
        return max(0.0, min(1.0, s / self.length))

    def position_at(self, progress: float, y: float, z: float) -> list[float]:
        d = self.direction
        s = max(0.0, min(1.0, progress)) * self.length
        return [self.entry[0] + d[0] * s,
                y,
                self.entry[2] + d[2] * s if abs(d[2]) > 1e-9 else z]


@dataclass
class TransportStep:
    progress: float
    delta: float
    new_position: list[float]
    at_stop: bool


def transport_step(position, geometry: BeltGeometry, belt_speed: float,
                   dt: float, max_step: float | None = None) -> TransportStep:
    """沿带面切线推进一个仿真步, 步长不超过带速 × dt。

    保留物理引擎给出的横向/高度坐标 (y/z), 只改沿带方向的分量;
    物料仍保持 dynamic + respondable。
    """
    max_step = cfg.CONVEYOR_MAX_COMPENSATION_STEP if max_step is None else max_step
    progress = geometry.progress(position)
    remaining = (1.0 - progress) * geometry.length
    delta = max(0.0, min(abs(belt_speed) * dt, remaining, max_step))
    new_progress = progress + delta / geometry.length
    new_pos = geometry.position_at(new_progress, position[1], position[2])
    return TransportStep(progress=new_progress, delta=delta,
                         new_position=new_pos,
                         at_stop=new_progress >= 1.0 - 1e-9)


def compensation_allowed(position, surface_z: float,
                         fault_drop_z: float = cfg.CONVEYOR_FAULT_DROP_Z,
                         geometry: BeltGeometry | None = None,
                         max_lateral: float = cfg.CONVEYOR_MAX_LATERAL_ERROR) -> bool:
    """物料仍在有效区内才允许补偿。

    - 高度: 不得低于带面 fault_drop_z;
    - 横向: 距带中心线的垂直距离不得超过 max_lateral (默认 4 cm),
      避免物料被推出带宽后仍沿 X 被拖行。
    """
    if position[2] < surface_z - fault_drop_z:
        raise TransportFault(
            f"物料 Z={position[2]:.3f} 低于带面 {surface_z:.3f} "
            f"超过 {fault_drop_z:.3f} m")
    if geometry is not None:
        d = geometry.direction
        rel = [position[i] - geometry.entry[i] for i in range(3)]
        along = sum(rel[i] * d[i] for i in range(3))
        lateral = [rel[i] - along * d[i] for i in range(3)]
        lateral_xy = math.hypot(lateral[0], lateral[1])
        if lateral_xy > max_lateral:
            raise TransportFault(
                f"物料横向偏离带中心线 {lateral_xy:.3f} m "
                f"> {max_lateral:.3f} m")
    return True


# ═══════════════════════════════════════════════════════════════
# 控制器
# ═══════════════════════════════════════════════════════════════

@dataclass
class ConveyorHandle:
    alias: str
    sim_handle: int
    entry: tuple[float, float, float]
    stop: tuple[float, float, float]
    surface_z: float
    drive_mode: str = cfg.DRIVE_SOURCE_SCRIPT
    commanded_velocity: float = 0.0

    @property
    def geometry(self) -> BeltGeometry:
        return BeltGeometry(self.entry, self.stop)


class ConveyorController:
    def __init__(self, sim) -> None:
        self.sim = sim
        self._by_alias: dict[str, ConveyorHandle] = {}

    def register(self, alias: str, handle: int, entry, stop,
                 surface_z: float) -> ConveyorHandle:
        h = ConveyorHandle(alias=alias, sim_handle=int(handle),
                           entry=tuple(entry), stop=tuple(stop),
                           surface_z=float(surface_z))
        self._by_alias[alias] = h
        return h

    def get(self, alias: str) -> ConveyorHandle:
        h = self._by_alias.get(alias)
        if h is None:
            raise ConveyorError(f"未知传送带别名: {alias}")
        return h

    def aliases(self) -> list[str]:
        return list(self._by_alias)

    # ── 驱动 ────────────────────────────────────────────────

    def set_speed(self, alias: str, velocity: float) -> None:
        h = self.get(alias)
        h.commanded_velocity = float(velocity)
        payload = (self.sim.packTable({"vel": float(velocity)})
                   if hasattr(self.sim, "packTable")
                   else json.dumps({"vel": float(velocity)}))
        self.sim.setBufferProperty(h.sim_handle, cfg.CONVEYOR_CTRL_KEY, payload)

    def stop(self, alias: str) -> None:
        self.set_speed(alias, 0.0)

    def read_actual_velocity(self, alias: str) -> float:
        h = self.get(alias)
        try:
            state = self.sim.readCustomTableData(h.sim_handle,
                                                 cfg.CONVEYOR_STATE_KEY)
            if isinstance(state, str):
                state = json.loads(state)
            if isinstance(state, dict):
                return float(state.get("vel", h.commanded_velocity))
        except Exception:
            pass
        return h.commanded_velocity
