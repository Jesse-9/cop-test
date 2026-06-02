"""
run_simulation.py — 工厂仿真控制器
===================================
连接到 CoppeliaSim 场景，控制传送带、AGV、物料流动。

运行前请确保:
  1. CoppeliaSim 已启动且已加载工厂场景
  2. 如未构建场景，先运行: uv run python build_scene.py

用法:
  uv run python run_simulation.py [--host HOST] [--port PORT] [--time TOTAL_TIME]
"""

import argparse
import math
import sys
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

from coppeliasim_zmqremoteapi_client import RemoteAPIClient

import factory_config as cfg


# ═══════════════════════════════════════════════════════════════
# 类型定义
# ═══════════════════════════════════════════════════════════════

class ItemType(Enum):
    """物料类型 — 对应场景中的不同工件。"""
    RAW_A     = auto()   # S1→A 原料
    RAW_B     = auto()   # S1→B 原料
    SEMI_AB   = auto()   # B→A 转运半成品
    SEMI_AC   = auto()   # A→C 半成品
    RAW_D     = auto()   # S2→D 零部件
    SEMI_CE   = auto()   # C→E 半成品
    PART_DE   = auto()   # D→E 专用件
    FINAL     = auto()   # 最终成品


class TransportType(Enum):
    CONVEYOR = auto()
    AGV      = auto()


@dataclass
class Material:
    """工厂中的物料/工件。"""
    uid:       int
    item_type: ItemType
    handle:    int = -1               # CoppeliaSim 中对应的 shape handle
    pos:       list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])

    # 状态追踪
    on_conveyor: Optional[str] = None    # 所在传送带名称
    conv_progress: float = 0.0           # 0→1 传送带行进进度
    on_agv:       Optional[str] = None   # 所在 AGV 名称
    agv_progress: float = 0.0            # 0→1 AGV 行进进度
    at_island:    Optional[str] = None   # 所在工作岛名称
    processing_remaining: float = 0.0    # 剩余处理时间 (s)
    delivered:    bool = False           # 已送达终点


ITEM_COLOR_MAP = {
    ItemType.RAW_A:    cfg.MATERIAL_COLORS["raw_A"],
    ItemType.RAW_B:    cfg.MATERIAL_COLORS["raw_B"],
    ItemType.SEMI_AB:  cfg.MATERIAL_COLORS["semi_AB"],
    ItemType.SEMI_AC:  cfg.MATERIAL_COLORS["semi_AC"],
    ItemType.RAW_D:    cfg.MATERIAL_COLORS["raw_D"],
    ItemType.SEMI_CE:  cfg.MATERIAL_COLORS["semi_CE"],
    ItemType.PART_DE:  cfg.MATERIAL_COLORS["part_DE"],
    ItemType.FINAL:    cfg.MATERIAL_COLORS["final_E"],
}


# ═══════════════════════════════════════════════════════════════
# 连接
# ═══════════════════════════════════════════════════════════════

def connect(host: str = "localhost", port: int = 23000):
    """连接 CoppeliaSim。"""
    print(f"[INFO] 连接 CoppeliaSim @ {host}:{port} ...")
    try:
        client = RemoteAPIClient(host=host, port=port)
        sim = client.require("sim")
        print(f"[OK] 已连接")
        return client, sim
    except Exception as e:
        print(f"[FATAL] 无法连接: {e}")
        sys.exit(1)


# ═══════════════════════════════════════════════════════════════
# 辅助：传送带/AGV 位置插值
# ═══════════════════════════════════════════════════════════════

def lerp_2d(p1: tuple, p2: tuple, t: float) -> list[float]:
    """两点间线性插值，返回 [x, y]。"""
    x = p1[0] + (p2[0] - p1[0]) * t
    y = p1[1] + (p2[1] - p1[1]) * t
    return [x, y]


def path_position(waypoints: list[tuple], t: float) -> list[float]:
    """沿多点路径的位置插值。t ∈ [0, 1]。"""
    if t <= 0:
        return list(waypoints[0])
    if t >= 1:
        return list(waypoints[-1])

    # 计算总长度和各段长度
    segments = []
    total = 0.0
    for i in range(len(waypoints) - 1):
        dx = waypoints[i + 1][0] - waypoints[i][0]
        dy = waypoints[i + 1][1] - waypoints[i][1]
        seg_len = math.hypot(dx, dy)
        segments.append(seg_len)
        total += seg_len

    target = t * total
    accumulated = 0.0
    for i, seg_len in enumerate(segments):
        if accumulated + seg_len >= target:
            local_t = (target - accumulated) / seg_len if seg_len > 0 else 0
            return lerp_2d(waypoints[i], waypoints[i + 1], local_t)
        accumulated += seg_len

    return list(waypoints[-1])


# ═══════════════════════════════════════════════════════════════
# 物料 Spawn / Remove
# ═══════════════════════════════════════════════════════════════

_uid_counter = 0

def spawn_material(sim, item_type: ItemType, position: list[float]) -> Material:
    """在场景中创建一个物料方块，返回 Material 对象。"""
    global _uid_counter
    _uid_counter += 1
    sx, sy, sz = cfg.MATERIAL_SIZE
    color = ITEM_COLOR_MAP.get(item_type, [1, 1, 1])
    handle = sim.createPrimitiveShape(sim.primitiveshape_cuboid, [sx, sy, sz])
    sim.setObjectPosition(handle, position, sim.handle_world)
    sim.setObjectColor(handle, 0, sim.colorcomponent_ambient_diffuse, list(color))
    return Material(uid=_uid_counter, item_type=item_type, handle=handle,
                    pos=position)


def remove_material(sim, mat: Material):
    """从场景中删除物料。"""
    if mat.handle != -1:
        sim.removeObject(mat.handle)
        mat.handle = -1


# ═══════════════════════════════════════════════════════════════
# 仿真控制器
# ═══════════════════════════════════════════════════════════════

class FactoryController:
    """工厂仿真主控制器。

    管理的物料流:
      S1 ──conv_S1A──→ A ──agv_AC──→ C ──conv_CE──→ E
      S1 ──conv_S1B──→ B ──agv_BA──→ A              ↗
      S2 ──conv_S2D──→ D ──agv_DE──────────────→  E

    到达 E 的物料（来自 C 和 D）配对后完成装配。
    """

    def __init__(self, sim, total_time: float = 60.0):
        self.sim = sim
        self.total_time = total_time
        self.materials: list[Material] = []

        # 仓库产出计时器
        self.s1_timer_A = 0.0     # S1→A 路线的产出计时器
        self.s1_timer_B = 2.0     # S1→B 路线延迟 2s
        self.s2_timer_D = 1.0     # S2→D 路线延迟 1s
        self.s1_interval_A = cfg.MATERIAL_INTERVAL
        self.s1_interval_B = cfg.MATERIAL_INTERVAL * 1.5
        self.s2_interval_D = cfg.MATERIAL_INTERVAL * 2.0

        # AGV 状态
        self.agv_state = {
            "agv_BA": {"active": False, "mat_uid": -1, "progress": 0.0, "direction": 1},
            # 1 = B→A, -1 = A→B (return)
            "agv_AC": {"active": False, "mat_uid": -1, "progress": 0.0, "direction": 1},
            "agv_DE": {"active": False, "mat_uid": -1, "progress": 0.0, "direction": 1},
        }

        # 工作岛队列 — 到达该岛的物料等待处理
        self.island_queues: dict[str, list[int]] = {
            "A": [], "B": [], "C": [], "D": [], "E": [],
        }

        # 已完成的成品计数
        self.assembled_count = 0

        # 事件日志
        self.event_log: list[str] = []

        # AGV 可视化 handle（运行时创建）
        self.agv_handles: dict[str, int] = {}

    # ── 初始化 ─────────────────────────────────────────────

    def _setup_agv_visuals(self):
        """创建 AGV 小车的可视化模型方块。"""
        sim = self.sim
        sx, sy, sz = cfg.AGV_SIZE
        for agv_name, path in cfg.AGV_PATHS.items():
            start_x, start_y = path["waypoints"][-1]  # 初始停在终点
            handle = sim.createPrimitiveShape(
                sim.primitiveshape_cuboid, [sx, sy, sz]
            )
            sim.setObjectPosition(
                handle,
                [start_x, start_y, cfg.Z_BASE + sz / 2],
                sim.handle_world,
            )
            sim.setObjectColor(
                handle, 0, sim.colorcomponent_ambient_diffuse, list(cfg.AGV_COLOR)
            )
            self.agv_handles[agv_name] = handle
        print("[OK] AGV 可视化方块已创建")

    # ── 主循环 ─────────────────────────────────────────────

    def run(self):
        """启动仿真主循环。"""
        sim = self.sim
        sim.setStepping(True)
        sim.startSimulation()

        # 创建 AGV 可视化方块
        self._setup_agv_visuals()

        print("\n" + "=" * 55)
        print(f"  仿真开始  |  总时长: {self.total_time}s")
        print("=" * 55)

        sim_time = 0.0
        step_count = 0

        try:
            while sim_time < self.total_time:
                dt = cfg.SIM_STEP

                self._spawn_from_warehouses(dt)
                self._move_conveyors(dt)
                self._move_agvs(dt)
                self._process_islands(dt)
                self._update_scene_positions()
                self._log_periodic(sim_time, step_count)

                sim.step()
                sim_time = sim.getSimulationTime()
                step_count += 1

        except KeyboardInterrupt:
            print("\n[INFO] 用户中断")
        finally:
            sim.stopSimulation()
            self._print_summary()

    # ── 仓库产出 ──────────────────────────────────────────

    def _spawn_from_warehouses(self, dt: float):
        """检查仓库计时器，到时间则在仓库位置生成物料。"""
        sim = self.sim
        s1_pos = cfg.WAREHOUSES["S1"]["pos"]
        s2_pos = cfg.WAREHOUSES["S2"]["pos"]

        # S1 → A (raw_A)
        self.s1_timer_A += dt
        if self.s1_timer_A >= self.s1_interval_A:
            self.s1_timer_A = 0.0
            mat = spawn_material(sim, ItemType.RAW_A, list(s1_pos))
            mat.on_conveyor = "conv_S1A"
            mat.conv_progress = 0.0
            self.materials.append(mat)
            self._log(f"S1 产出 raw_A → conv_S1A (mat#{mat.uid})")

        # S1 → B (raw_B)
        self.s1_timer_B += dt
        if self.s1_timer_B >= self.s1_interval_B:
            self.s1_timer_B = 0.0
            mat = spawn_material(sim, ItemType.RAW_B, list(s1_pos))
            mat.on_conveyor = "conv_S1B"
            mat.conv_progress = 0.0
            self.materials.append(mat)
            self._log(f"S1 产出 raw_B → conv_S1B (mat#{mat.uid})")

        # S2 → D (raw_D)
        self.s2_timer_D += dt
        if self.s2_timer_D >= self.s2_interval_D:
            self.s2_timer_D = 0.0
            mat = spawn_material(sim, ItemType.RAW_D, list(s2_pos))
            mat.on_conveyor = "conv_S2D"
            mat.conv_progress = 0.0
            self.materials.append(mat)
            self._log(f"S2 产出 raw_D → conv_S2D (mat#{mat.uid})")

    # ── 传送带逻辑 ─────────────────────────────────────────

    def _move_conveyors(self, dt: float):
        """移动传送带上的物料。到达终点时转移到目标工作岛。"""
        conv_map = {
            "conv_S1A": ("S1", "A"),
            "conv_S1B": ("S1", "B"),
            "conv_S2D": ("S2", "D"),
            "conv_CE":  ("C",  "E"),
        }

        for mat in self.materials:
            if mat.delivered or mat.on_conveyor is None:
                continue
            cname = mat.on_conveyor
            if cname not in conv_map:
                continue

            # 计算传送带长度，用于速度换算
            conv_cfg = cfg.CONVEYORS[cname]
            x1, y1 = conv_cfg["start"]
            x2, y2 = conv_cfg["end"]
            belt_len = math.hypot(x2 - x1, y2 - y1)
            speed_fraction = cfg.CONVEYOR_SPEED / belt_len if belt_len > 0 else 1.0

            mat.conv_progress += speed_fraction * dt

            if mat.conv_progress >= 1.0:
                # 到达传送带终点 → 转入目标工作岛
                mat.conv_progress = 1.0
                src, dst = conv_map[cname]
                mat.on_conveyor = None
                mat.at_island = dst
                mat.processing_remaining = cfg.PROCESS_TIME
                self.island_queues[dst].append(mat.uid)
                # 类型转换
                if cname == "conv_S1A":
                    pass  # 保持 RAW_A
                elif cname == "conv_S1B":
                    pass  # 保持 RAW_B
                elif cname == "conv_S2D":
                    pass  # 保持 RAW_D
                elif cname == "conv_CE":
                    mat.item_type = ItemType.SEMI_CE
                self._log(f"mat#{mat.uid} 到达工作岛 {dst} (via {cname})")

    # ── AGV 逻辑 ───────────────────────────────────────────

    def _move_agvs(self, dt: float):
        """控制 AGV 小车：触发条件 → 移动 → 装卸。"""
        # AGV BA: B → A
        self._control_agv("agv_BA", "B", "A", ItemType.SEMI_AB, dt)

        # AGV AC: A → C (当 A 处理完来自 B 的物料后)
        self._control_agv("agv_AC", "A", "C", ItemType.SEMI_AC, dt)

        # AGV DE: D → E
        self._control_agv("agv_DE", "D", "E", ItemType.PART_DE, dt)

    def _control_agv(self, agv_name: str, src: str, dst: str,
                     result_type: ItemType, dt: float):
        """通用 AGV 控制逻辑。"""
        state = self.agv_state[agv_name]
        path = cfg.AGV_PATHS[agv_name]["waypoints"]

        if not state["active"]:
            # 尝试从源工作岛取料（物料已处理完毕）
            ready = [
                uid for uid in self.island_queues.get(src, [])
                if self._find_mat(uid).processing_remaining <= 0
                and not self._find_mat(uid).delivered
            ]
            if ready:
                mat_uid = ready[0]
                mat = self._find_mat(mat_uid)
                self.island_queues[src].remove(mat_uid)
                mat.at_island = None
                mat.on_agv = agv_name
                mat.agv_progress = 0.0
                state["active"] = True
                state["mat_uid"] = mat_uid
                state["progress"] = 0.0
                state["direction"] = 1
                self._log(f"AGV {agv_name} 装载 mat#{mat_uid} ({src}→{dst})")

        else:
            # 正在移动
            mat = self._find_mat(state["mat_uid"])
            if mat is None:
                state["active"] = False
                return

            speed = cfg.AGV_SPEED
            path_len = self._path_length(path)
            state["progress"] += (speed / path_len) * dt if path_len > 0 else 1.0

            if state["progress"] >= 1.0:
                # 到达目的地
                state["progress"] = 1.0
                mat.on_agv = None
                mat.agv_progress = 1.0
                mat.at_island = dst
                mat.item_type = result_type
                mat.processing_remaining = cfg.PROCESS_TIME
                self.island_queues[dst].append(mat.uid)
                self._log(f"AGV {agv_name} 卸载 mat#{mat.uid} → 工作岛 {dst}")
                state["active"] = False
                state["mat_uid"] = -1
                # AGV 停留在此处等待下次任务

    # ── 工作岛处理 ─────────────────────────────────────────

    def _process_islands(self, dt: float):
        """减少各工作岛中物料的剩余处理时间。"""
        for mat in self.materials:
            if mat.processing_remaining > 0:
                mat.processing_remaining -= dt
                if mat.processing_remaining <= 0:
                    mat.processing_remaining = 0
                    self._log(
                        f"mat#{mat.uid} ({mat.item_type.name}) "
                        f"在 {mat.at_island} 处理完毕"
                    )

        # E 岛特殊逻辑：当 C→E 和 D→E 的物料都到达后完成装配
        self._check_assembly_at_E()

    def _check_assembly_at_E(self):
        """检查 E 岛是否有可配对的物料（C线+D线），完成装配。"""
        e_items = [
            mat for mat in self.materials
            if mat.at_island == "E"
            and mat.processing_remaining <= 0
            and not mat.delivered
        ]
        from_c = [m for m in e_items if m.item_type == ItemType.SEMI_CE]
        from_d = [m for m in e_items if m.item_type == ItemType.PART_DE]

        while from_c and from_d:
            c_mat = from_c.pop(0)
            d_mat = from_d.pop(0)
            c_mat.delivered = True
            d_mat.delivered = True
            c_mat.at_island = None
            d_mat.at_island = None
            self.assembled_count += 1
            # 生成成品
            e_pos = cfg.WORK_ISLANDS["E"]["pos"]
            final = spawn_material(self.sim, ItemType.FINAL, list(e_pos))
            final.delivered = True
            self.materials.append(final)
            self._log(
                f"✅ 装配完成 #{self.assembled_count}! "
                f"mat#{c_mat.uid} + mat#{d_mat.uid} → mat#{final.uid} @ E"
            )

    # ── 场景更新 ───────────────────────────────────────────

    def _update_scene_positions(self):
        """将 Python 状态同步回 CoppeliaSim 中的物体位置。"""
        sim = self.sim

        for mat in self.materials:
            if mat.handle == -1 or mat.delivered:
                continue
            new_pos = None

            if mat.on_conveyor:
                cname = mat.on_conveyor
                if cname in cfg.CONVEYORS:
                    c = cfg.CONVEYORS[cname]
                    pt = lerp_2d(c["start"], c["end"], mat.conv_progress)
                    new_pos = [pt[0], pt[1], cfg.Z_BASE + 0.05]

            elif mat.on_agv:
                agv_name = mat.on_agv
                if agv_name in cfg.AGV_PATHS:
                    wp = cfg.AGV_PATHS[agv_name]["waypoints"]
                    pt = path_position(wp, mat.agv_progress)
                    new_pos = [pt[0], pt[1], cfg.Z_BASE + cfg.AGV_SIZE[2] + 0.06]

            elif mat.at_island:
                if mat.at_island in cfg.WORK_ISLANDS:
                    ip = cfg.WORK_ISLANDS[mat.at_island]["pos"]
                    offset = len([
                        m for m in self.materials
                        if m.at_island == mat.at_island and m.uid < mat.uid
                    ]) * 0.2
                    new_pos = [ip[0] + offset - 0.3, ip[1], ip[2] + 0.5]

            if new_pos:
                sim.setObjectPosition(mat.handle, new_pos, sim.handle_world)

        # 更新 AGV 车身位置
        for agv_name, state in self.agv_state.items():
            agv_handle = self.agv_handles.get(agv_name)
            if agv_handle is None:
                continue

            wp = cfg.AGV_PATHS[agv_name]["waypoints"]
            if state["active"]:
                pt = path_position(wp, state["progress"])
            else:
                pt = wp[-1]  # 停在终点
            sim.setObjectPosition(
                agv_handle,
                [pt[0], pt[1], cfg.Z_BASE + cfg.AGV_SIZE[2] / 2],
                sim.handle_world,
            )

    # ── 辅助 ───────────────────────────────────────────────

    def _find_mat(self, uid: int) -> Optional[Material]:
        for m in self.materials:
            if m.uid == uid:
                return m
        return None

    def _path_length(self, waypoints: list[tuple]) -> float:
        total = 0.0
        for i in range(len(waypoints) - 1):
            total += math.hypot(
                waypoints[i + 1][0] - waypoints[i][0],
                waypoints[i + 1][1] - waypoints[i][1],
            )
        return total

    def _log(self, msg: str):
        self.event_log.append(msg)
        print(f"  [{len(self.event_log):03d}] {msg}")

    def _log_periodic(self, sim_time: float, step: int):
        if step % 100 == 0:
            active = sum(1 for m in self.materials if not m.delivered)
            print(f"  [t={sim_time:.1f}s] 活跃物料: {active}  |  "
                  f"装配完成: {self.assembled_count}")

    def _print_summary(self):
        print("\n" + "=" * 55)
        print(f"  仿真结束")
        print(f"  总事件: {len(self.event_log)}")
        print(f"  总物料: {len(self.materials)}")
        print(f"  装配完成: {self.assembled_count}")
        print("=" * 55)


# ═══════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="工厂仿真控制器")
    parser.add_argument("--host", default="localhost", help="CoppeliaSim 主机地址")
    parser.add_argument("--port", type=int, default=23000, help="ZMQ Remote API 端口")
    parser.add_argument("--time", type=float, default=cfg.TOTAL_SIM_TIME,
                        help="仿真总时长 (秒), 默认 60s")
    args = parser.parse_args()

    client, sim = connect(args.host, args.port)

    controller = FactoryController(sim, total_time=args.time)
    controller.run()


if __name__ == "__main__":
    main()
