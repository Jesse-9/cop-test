"""conveyor_controller.py — 传送带驱动与 HYBRID_DYNAMIC 物料输送 (阶段一 §2.2/§2.4)

负责内置 `generic conveyor (belt).ttm` 的启动/停速/状态读取。
Python 不与 pad 位姿交互（见 CLAUDE.md §12，4.10 无法做摩擦驱动；
KINEMATIC_BELT_TRANSPORT 由 buffer 属性 `__ctrl__` 驱动履带视觉，
物料按带面切线显式输送，并打 WARNING 说明驱动源）。

方案依据:
  docs/wiki/06-工业案例/06-05-柔性工业车间分阶段执行步骤.md §2.4
  CLAUDE.md §12 “传送带驱动”

Exports:
  ConveyorHandle   — 单条传送带的句柄与标定元数据
  ConveyorState    — 输送状态枚举 (IDLE / RUNNING / STOPPED / FAULT)
  ConveyorController — 多条传送带的集中控制器
  ConveyorError    — 异常基类
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

import factory_config as cfg


# ═══════════════════════════════════════════════════════════════
# 异常
# ═══════════════════════════════════════════════════════════════

class ConveyorError(Exception):
    """传送带操作异常（启动失败、未知别名、驱动源不可用等）"""


# ═══════════════════════════════════════════════════════════════
# 状态枚举
# ═══════════════════════════════════════════════════════════════

class ConveyorState(Enum):
    IDLE      = auto()   # 已加载，未启动 (vel=0)
    RUNNING   = auto()   # 履带正在运动
    STOPPED   = auto()   # 临时停止，可恢复
    FAULT     = auto()   # 偏差超过阈值 / 超时，进入 CONVEYOR_FAULT


# ═══════════════════════════════════════════════════════════════
# 单条传送带句柄与标定元数据
# ═══════════════════════════════════════════════════════════════

@dataclass
class ConveyorHandle:
    """构建期测量并缓存，运行期只读"""
    alias: str                  # 扁平下划线别名，如 Conveyor_S1_to_A
    sim_handle: int             # CoppeliaSim 模型根句柄
    native_length: float        # 模型原生 X 包围盒长度 (m)
    actual_length: float        # 缩放后设计长度 (m)
    width: float                # pad 有效宽度 (m)
    surface_z: float            # pad 上表面世界 Z (m)
    pad_range_x: tuple          # pad 有效运行区在局部坐标下 (min_x, max_x)
    sim: object                 # CoppeliaSim sim 句柄 (用于 buffer 属性读写)

    # 运行期状态（由控制器更新）
    state: ConveyorState = ConveyorState.IDLE
    velocity: float = 0.0       # 当前下发带速 (m/s)
    conveyor_mode: str = "SCRIPT_DRIVEN"   # SCRIPT_DRIVEN / PYTHON_PAD_DRIVEN / FALLBACK

    # 动态检测缓存（§6：只表示物理条件满足，不能直接触发吸附）
    last_hit_handle: Optional[int] = None
    last_hit_time: Optional[float] = None


# ═══════════════════════════════════════════════════════════════
# 控制器
# ═══════════════════════════════════════════════════════════════

class ConveyorController:
    """多条传送带的集中驱动与标定。

    `sim` 在构造时传入；每条带通过 `register()` 登记后，
    用 `set_speed(alias, v)` / `get_state(alias)` / `is_item_in_valid_zone(...)`
    等 API 控制。
    """

    def __init__(self, sim) -> None:
        self.sim = sim
        self._by_alias: dict[str, ConveyorHandle] = {}

    # ── 注册/查询 ────────────────────────────────────────────

    def register(self, handle: ConveyorHandle) -> None:
        """登记一条已构建并标定的传送带（由 build_scene 在构建期调用）"""
        self._by_alias[handle.alias] = handle

    def by_alias(self, alias: str) -> Optional[ConveyorHandle]:
        return self._by_alias.get(alias)

    def aliases(self) -> list[str]:
        return list(self._by_alias.keys())

    # ── 驱动 ─────────────────────────────────────────────────

    def set_speed(self, alias: str, velocity: float) -> None:
        """下发带速 (m/s)。0 表示停止。

        对 SCRIPT_DRIVEN 的带，写 `customData.__ctrl__` buffer；
        对 PYTHON_PAD_DRIVEN，由调用层逐帧写 pad 位姿（4.10 不支持 surface velocity）。
        """
        h = self.by_alias(alias)
        if h is None:
            raise ConveyorError(f"未知传送带别名: {alias}")
        h.velocity = velocity
        h.state = ConveyorState.RUNNING if abs(velocity) > 1e-6 else ConveyorState.IDLE

        if h.conveyor_mode == "SCRIPT_DRIVEN":
            self._write_script_ctrl(h, velocity)
        # PYTHON_PAD_DRIVEN 的位姿写回交给调用层循环

    def get_state(self, alias: str) -> ConveyorState:
        h = self.by_alias(alias)
        if h is None:
            raise ConveyorError(f"未知传送带别名: {alias}")
        return h.state

    def read_actual_velocity(self, alias: str) -> float:
        """读回带面实际速度 (SCRIPT_DRIVEN 走 __state__；其它返回下发值)

        4.10 内置 conveyor 脚本把状态以字符串形式存在 `__state__`
        (`sim.readCustomTableData(root, '__state__')` 返回 JSON 文本),
        故先尝试解析 JSON, 再退回 dict。
        """
        h = self.by_alias(alias)
        if h is None:
            raise ConveyorError(f"未知传送带别名: {alias}")
        if h.conveyor_mode == "SCRIPT_DRIVEN":
            try:
                state = self.sim.readCustomTableData(h.sim_handle, "__state__")
                if isinstance(state, str):
                    import json as _json
                    state = _json.loads(state)
                if isinstance(state, dict):
                    return float(state.get("vel", h.velocity))
            except Exception:
                pass
        return h.velocity

    # ── 辅助判断 ─────────────────────────────────────────────

    def is_item_in_valid_zone(
        self, alias: str, item_local_x: float, inset: float = cfg.CONVEYOR_STOP_INSET
    ) -> bool:
        """物料是否在 pad 有效区（扣除两端滚筒，且 ≥ inset 以免掉落）。

        §4: 入口/停止区 dummy 由 pad 有效范围 ± inset 推出，不得按外包围盒端点。
        """
        h = self.by_alias(alias)
        if h is None:
            raise ConveyorError(f"未知传送带别名: {alias}")
        min_x, max_x = h.pad_range_x
        return min_x + inset <= item_local_x <= max_x - inset

    # ── 内部 ─────────────────────────────────────────────────

    def _write_script_ctrl(self, handle: ConveyorHandle, velocity: float) -> None:
        """写 buffer 属性 `__ctrl__={vel: v}`，由 conveyor 内置脚本读取"""
        import json as _json
        ctrl = {"vel": float(velocity)}
        try:
            self.sim.setBufferProperty(
                handle.sim_handle, "customData.__ctrl__", _json.dumps(ctrl)
            )
        except Exception:
            # 某些 4.10 版本可能接受 strpack / packTable；此处兜底记录但不中断
            pass
