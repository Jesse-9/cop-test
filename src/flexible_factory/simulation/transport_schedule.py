"""transport_schedule.py — 双车（AGV + 小车二）运输调度策略与离散事件验证.

本模块只做**调度决策与推演**, 不碰 CoppeliaSim:

  - 路网直接复用 v1 的 6 节点 / 3 条直道 (`config.AGV_NODES` / `AGV_EDGES`),
    不新增节点, 所以 ``layout_digest`` 不变、v002 工件仍然通过校验;
  - 两台车都具备 ``agv_transport`` 能力, 谁接哪一单由策略决定;
  - 资源模型沿用项目既有语义: **整条路线全有或全无** (占用它经过的所有直道
    与两端 dock), 因此不存在"持有并等待", 死锁在构造上被排除;
  - 提供三种策略对比: 单 AGV 基线 / 固定分工 / ETA 最小分配。

术语:
  lane   直道资源 (agv_lane_west / south / east), 同一时刻只允许一台车占用;
  dock   装卸窗口 (A/B/D/E), 容量 1;
  route  从起点到终点的节点序列, 其资源 = 经过的 lane 集合 + 两端 dock。
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Iterable, Optional

# ═══════════════════════════════════════════════════════════════
# 路网（与 v1 的 config.AGV_NODES / AGV_EDGES 逐项一致）
# ═══════════════════════════════════════════════════════════════

NODES: dict[str, tuple[float, float]] = {
    "A": (0.40, 0.70),
    "B": (0.40, -0.70),
    "D": (2.90, -1.35),
    "E": (2.90, 1.35),
    "J_SW": (0.40, -2.10),
    "J_SE": (2.90, -2.10),
}

EDGES: tuple[tuple[str, str, str], ...] = (
    ("A", "B", "agv_lane_west"),
    ("B", "J_SW", "agv_lane_west"),
    ("J_SW", "J_SE", "agv_lane_south"),
    ("J_SE", "D", "agv_lane_east"),
    ("D", "E", "agv_lane_east"),
)

LANES = ("agv_lane_west", "agv_lane_south", "agv_lane_east")
DOCKS = ("A", "B", "D", "E")


def edge_length(start: str, end: str) -> float:
    return math.dist(NODES[start], NODES[end])


def _neighbors() -> dict[str, list[tuple[str, str]]]:
    out: dict[str, list[tuple[str, str]]] = {node: [] for node in NODES}
    for start, end, lane in EDGES:
        out[start].append((end, lane))
        out[end].append((start, lane))
    return out


_NEIGHBORS = _neighbors()


# ═══════════════════════════════════════════════════════════════
# 路线
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Route:
    """一条节点序列及其派生资源。"""

    nodes: tuple[str, ...]
    lanes: tuple[str, ...]
    length: float

    @property
    def turns(self) -> int:
        return max(0, len(self.nodes) - 2)

    @property
    def origin(self) -> str:
        return self.nodes[0]

    @property
    def destination(self) -> str:
        return self.nodes[-1]

    def resources(self) -> frozenset[str]:
        return frozenset(self.lanes) | {self.origin, self.destination}

    def as_dict(self) -> dict:
        return {"nodes": list(self.nodes), "lanes": list(self.lanes),
                "length": round(self.length, 4), "turns": self.turns}


def plan_route(origin: str, destination: str) -> Route:
    """最短路线 (Dijkstra, 按直道长度); 端点相同返回零长度路线。"""
    for node in (origin, destination):
        if node not in NODES:
            raise ValueError(f"未知节点: {node!r}")
    if origin == destination:
        return Route((origin,), (), 0.0)

    dist = {node: math.inf for node in NODES}
    prev: dict[str, str] = {}
    lane_of: dict[str, str] = {}
    dist[origin] = 0.0
    heap: list[tuple[float, str]] = [(0.0, origin)]
    while heap:
        cost, node = heapq.heappop(heap)
        if cost > dist[node] + 1e-12:
            continue
        for other, lane in _NEIGHBORS[node]:
            candidate = cost + edge_length(node, other)
            if candidate < dist[other] - 1e-12:
                dist[other] = candidate
                prev[other] = node
                lane_of[other] = lane
                heapq.heappush(heap, (candidate, other))
    if not math.isfinite(dist[destination]):
        raise ValueError(f"路网不连通: {origin!r} → {destination!r}")

    path = [destination]
    while path[-1] != origin:
        path.append(prev[path[-1]])
    path.reverse()

    lanes: list[str] = []
    for node in path[1:]:
        lane = lane_of[node]
        if not lanes or lanes[-1] != lane:
            lanes.append(lane)
    return Route(tuple(path), tuple(lanes), float(dist[destination]))


# ═══════════════════════════════════════════════════════════════
# 车辆与任务
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class VehicleSpec:
    """一台运输车的运动学与作业时间参数。"""

    vehicle_id: str
    home: str
    speed: float                 # 巡航速度 m/s
    turn_time: float             # 每个中间节点停车转向耗时 s
    load_time: float             # 装货停留 s
    unload_time: float           # 卸货停留 s

    def travel_time(self, route: Route) -> float:
        if route.length <= 0.0:
            return 0.0
        return route.length / self.speed + route.turns * self.turn_time

    def as_dict(self) -> dict:
        return {"vehicle_id": self.vehicle_id, "home": self.home,
                "speed": self.speed, "turn_time": self.turn_time,
                "load_time": self.load_time,
                "unload_time": self.unload_time}


#: AGV（现有）：0.12 m/s, 停车转向 0.4 s, 装卸各 1.5 s。
AGV_1 = VehicleSpec("agv_1", "A", 0.12, 0.40, 1.5, 1.5)
#: 小车二：慢 17%、转向更笨重、装卸各 2.0 s（人力/半自动上料）。
CART_2 = VehicleSpec("cart_2", "D", 0.10, 0.50, 2.0, 2.0)


@dataclass(frozen=True)
class TransportJob:
    job_id: str
    item_type: str
    origin: str
    destination: str
    created_at: float = 0.0
    priority: int = 1              # 数值越大越先派

    @property
    def category(self) -> str:
        if (self.origin, self.destination) == ("B", "A"):
            return "housing_return"
        if (self.origin, self.destination) == ("D", "E"):
            return "bracket_supply"
        return "cross"

    def as_dict(self) -> dict:
        return {"job_id": self.job_id, "item_type": self.item_type,
                "origin": self.origin, "destination": self.destination,
                "created_at": round(self.created_at, 3),
                "priority": self.priority,
                "category": self.category}


# ═══════════════════════════════════════════════════════════════
# 策略
# ═══════════════════════════════════════════════════════════════

POLICIES = ("single", "fixed", "eta")

#: 等待超过该值视为饥饿, 参与 aging 提升 (与现有 D_E_STARVATION_UPGRADE_S 对齐)
AGING_AFTER_S = 15.0
#: 单任务等待上限 (对齐 TASK_TIMEOUT)
WAIT_LIMIT_S = 300.0


def eligible_vehicles(policy: str, job: TransportJob,
                      vehicles: Iterable[VehicleSpec]) -> list[VehicleSpec]:
    """策略层面的候选车辆（不含资源可见性）。"""
    vehicles = list(vehicles)
    if policy == "single":
        return [v for v in vehicles if v.vehicle_id == "agv_1"]
    if policy == "fixed":
        if job.category == "housing_return":
            return [v for v in vehicles if v.vehicle_id == "agv_1"]
        if job.category == "bracket_supply":
            return [v for v in vehicles if v.vehicle_id == "cart_2"]
        return vehicles          # 跨区任务两台都可
    return vehicles              # eta: 全部参与竞争


def eta_for(vehicle: VehicleSpec, position: str,
            job: TransportJob) -> float:
    """空载到取货点 + 装货 + 载货到卸货点 + 卸货 的总时长。"""
    empty = vehicle.travel_time(plan_route(position, job.origin))
    loaded = vehicle.travel_time(plan_route(job.origin, job.destination))
    return empty + vehicle.load_time + loaded + vehicle.unload_time


def dispatch_cost(policy: str, vehicle: VehicleSpec, position: str,
                  job: TransportJob, now: float) -> float:
    """单对 (车,任务) 的 ETA 代价; 仅供诊断与测试, 实际派工用配对评分。"""
    cost = eta_for(vehicle, position, job)
    if policy == "fixed":
        return cost
    waited = max(0.0, now - job.created_at)
    if waited > AGING_AFTER_S:
        cost -= min(20.0, waited - AGING_AFTER_S)
    cost -= 5.0 * max(0, job.priority - 1)
    return cost


# ═══════════════════════════════════════════════════════════════
# 离散事件推演
# ═══════════════════════════════════════════════════════════════

@dataclass
class _VehicleState:
    spec: VehicleSpec
    position: str
    busy_until: float = 0.0
    job: Optional[TransportJob] = None
    busy_time: float = 0.0
    trips: int = 0
    lanes: tuple[str, ...] = ()
    docks: tuple[str, ...] = ()


@dataclass
class _JobRecord:
    job: TransportJob
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    vehicle: str = ""
    blocked_seconds: float = 0.0


class DualCartSimulator:
    """按"整条路线全有或全无"占用资源, 逐事件推进。"""

    def __init__(self, vehicles: Iterable[VehicleSpec],
                 jobs: Iterable[TransportJob], *, policy: str = "eta",
                 unavailable: Iterable[str] = ()):
        if policy not in POLICIES:
            raise ValueError(f"未知策略: {policy!r}")
        self.policy = policy
        offline = set(unavailable)
        self.vehicles = [_VehicleState(v, v.home)
                         for v in vehicles if v.vehicle_id not in offline]
        self.unavailable = tuple(sorted(offline))
        if not self.vehicles:
            raise ValueError("没有可用车辆")
        self.records = [_JobRecord(j) for j in jobs]
        self.pending = sorted(
            (r for r in self.records),
            key=lambda r: (r.job.created_at, -r.job.priority, r.job.job_id))
        self.lane_owner: dict[str, str] = {}
        self.dock_owner: dict[str, str] = {}
        self.now = 0.0
        self.events: list[dict] = []

    # ── 资源 ────────────────────────────────────────────────

    # ── 资源 ────────────────────────────────────────────────

    def _free(self, lanes, docks, vehicle_id: str) -> bool:
        for lane in lanes:
            owner = self.lane_owner.get(lane)
            if owner is not None and owner != vehicle_id:
                return False
        for dock in docks:
            owner = self.dock_owner.get(dock)
            if owner is not None and owner != vehicle_id:
                return False
        return True

    def _acquire(self, lanes, docks, vehicle_id: str) -> None:
        for lane in lanes:
            self.lane_owner[lane] = vehicle_id
        for dock in docks:
            self.dock_owner[dock] = vehicle_id

    def _release(self, lanes, docks, vehicle_id: str) -> None:
        for lane in lanes:
            if self.lane_owner.get(lane) == vehicle_id:
                del self.lane_owner[lane]
        for dock in docks:
            if self.dock_owner.get(dock) == vehicle_id:
                del self.dock_owner[dock]

    # ── 主循环 ──────────────────────────────────────────────

    def run(self) -> dict:
        while True:
            self._assign_ready_jobs()
            if not self.pending and not self._busy():
                break
            next_times = [v.busy_until for v in self.vehicles
                          if v.job is not None]
            nxt_job = [r.job.created_at for r in self.pending
                       if r.job.created_at > self.now + 1e-9]
            candidates = next_times + nxt_job
            if not candidates:
                break
            self.now = min(candidates)
            self._complete_due()
        return self._metrics()

    def _busy(self) -> bool:
        return any(v.job is not None for v in self.vehicles)

    def _duration(self, state: _VehicleState, job: TransportJob) -> float:
        route_in = plan_route(state.position, job.origin)
        route_work = plan_route(job.origin, job.destination)
        return (state.spec.travel_time(route_in) + state.spec.load_time
                + state.spec.travel_time(route_work) + state.spec.unload_time)

    def _plan(self, state: "_VehicleState", job: TransportJob):
        """返回 (空载段, 载货段, 合并路线, 占用直道, 占用 dock)。"""
        route_in = plan_route(state.position, job.origin)
        route_work = plan_route(job.origin, job.destination)
        merged = _merge_routes(route_in, route_work)
        docks = {state.position, job.origin, job.destination}
        return route_in, route_work, merged, merged.lanes, docks

    def _feasible(self, state: _VehicleState, job: TransportJob) -> bool:
        _, _, _, lanes, docks = self._plan(state, job)
        return self._free(lanes, docks, state.spec.vehicle_id)

    def _assign_ready_jobs(self) -> None:
        """每轮做一次"资源感知"的配对派工。

        朴素 ETA 逐对贪心会先让一台车占掉共享直道, 把另一台堵在节点上
        (实测: 两车都在 A 时, 小车二利用率 0)。这里枚举 (车,任务) 组合,
        只接受**资源不冲突**的配对, 并以"最晚完工时间最小"为准则, 让两车
        真正并行。
        """
        while True:
            committed = self._commit_best()
            if not committed:
                return

    def _ready_jobs(self) -> list[_JobRecord]:
        return [r for r in self.pending if r.job.created_at <= self.now + 1e-9]

    def _commit_best(self) -> bool:
        idle = [v for v in self.vehicles if v.job is None]
        if not idle:
            return False
        ready = self._ready_jobs()
        if not ready:
            return False

        options: list[tuple[float, list[tuple[_VehicleState, _JobRecord]]]] = []
        # 单车方案
        for state in idle:
            for record in ready:
                if not self._eligible(state, record.job):
                    continue
                if not self._feasible(state, record.job):
                    continue
                options.append((self._score([(state, record)]),
                                [(state, record)]))
        # 两车配对方案 (要求资源不冲突)
        for i, first in enumerate(idle):
            for second in idle[i + 1:]:
                for record_a in ready:
                    if not self._eligible(first, record_a.job):
                        continue
                    if not self._feasible(first, record_a.job):
                        continue
                    for record_b in ready:
                        if record_b is record_a:
                            continue
                        if not self._eligible(second, record_b.job):
                            continue
                        if not self._feasible(second, record_b.job):
                            continue
                        if self._overlap(first, record_a.job,
                                         second, record_b.job):
                            continue
                        options.append((self._score(
                            [(first, record_a), (second, record_b)]),
                            [(first, record_a), (second, record_b)]))

        if not options:
            return False
        options.sort(key=lambda item: item[0])
        for state, record in options[0][1]:
            self._start(state, record)
        return True

    def _eligible(self, state: _VehicleState, job: TransportJob) -> bool:
        if self.policy in ("single", "fixed"):
            allowed = {v.vehicle_id for v in eligible_vehicles(
                self.policy, job, [state.spec])}
            return state.spec.vehicle_id in allowed
        return True

    def _overlap(self, first: _VehicleState, job_a: TransportJob,
                 second: _VehicleState, job_b: TransportJob) -> bool:
        _, _, _, lanes_a, docks_a = self._plan(first, job_a)
        _, _, _, lanes_b, docks_b = self._plan(second, job_b)
        if set(lanes_a) & set(lanes_b):
            return True
        return bool(set(docks_a) & set(docks_b))

    def _score(self, plan: list[tuple[_VehicleState, _JobRecord]]) -> float:
        """最晚完工时间; 久等任务享有 aging 折价。"""
        latest = 0.0
        bonus = 0.0
        for state, record in plan:
            finish = self.now + self._duration(state, record.job)
            latest = max(latest, finish)
            waited = max(0.0, self.now - record.job.created_at)
            if waited > AGING_AFTER_S:
                bonus += min(20.0, waited - AGING_AFTER_S)
            bonus += 5.0 * max(0, record.job.priority - 1)
        return latest - self.now - bonus

    def _start(self, state: _VehicleState, record: _JobRecord) -> None:
        job = record.job
        route_in, route_work, merged, lanes, docks = self._plan(state, job)
        self._acquire(lanes, docks, state.spec.vehicle_id)
        duration = (state.spec.travel_time(route_in) + state.spec.load_time
                    + state.spec.travel_time(route_work)
                    + state.spec.unload_time)
        state.job = job
        state.lanes = tuple(lanes)
        state.docks = tuple(docks)
        state.busy_until = self.now + duration
        state.busy_time += duration
        state.position = job.destination
        record.started_at = self.now
        record.vehicle = state.spec.vehicle_id
        self.pending.remove(record)
        self.events.append({
            "t": round(self.now, 3), "event": "start",
            "vehicle": state.spec.vehicle_id, "job": job.job_id,
            "route": merged.nodes, "duration": round(duration, 3)})

    def _complete_due(self) -> None:
        for state in self.vehicles:
            if state.job is None:
                continue
            if state.busy_until > self.now + 1e-9:
                continue
            job = state.job
            if state.lanes or state.docks:
                self._release(state.lanes, state.docks,
                              state.spec.vehicle_id)
            state.job = None
            state.lanes = ()
            state.docks = ()
            state.trips += 1
            record = next(r for r in self.records if r.job is job)
            record.finished_at = self.now
            self.events.append({"t": round(self.now, 3), "event": "finish",
                                "vehicle": state.spec.vehicle_id,
                                "job": job.job_id})

    # ── 指标 ────────────────────────────────────────────────

    def _metrics(self) -> dict:
        finished = [r for r in self.records if r.finished_at is not None]
        waits = [max(0.0, (r.started_at or self.now) - r.job.created_at)
                 for r in self.records]
        cycles = [((r.finished_at or self.now) - r.job.created_at)
                  for r in self.records]
        makespan = max((r.finished_at or 0.0) for r in self.records) if self.records else 0.0
        return {
            "policy": self.policy,
            "jobs": len(self.records),
            "completed": len(finished),
            "pending": len(self.records) - len(finished),
            "unavailable": list(self.unavailable),
            "makespan_s": round(makespan, 3),
            "avg_wait_s": round(sum(waits) / len(waits), 3) if waits else 0.0,
            "max_wait_s": round(max(waits), 3) if waits else 0.0,
            "avg_cycle_s": round(sum(cycles) / len(cycles), 3) if cycles else 0.0,
            "utilization": {
                state.spec.vehicle_id: (
                    round(state.busy_time / makespan, 4) if makespan else 0.0)
                for state in self.vehicles},
            "trips": {state.spec.vehicle_id: state.trips
                      for state in self.vehicles},
            "starved_jobs": sum(1 for w in waits if w > 60.0),
            "per_job": [
                {"job_id": r.job.job_id, "category": r.job.category,
                 "vehicle": r.vehicle,
                 "wait_s": round(max(0.0, (r.started_at or 0.0)
                                     - r.job.created_at), 3),
                 "cycle_s": round((r.finished_at or 0.0)
                                  - r.job.created_at, 3)}
                for r in self.records],
            "events": self.events,
        }


def _merge_routes(first: Route, second: Route) -> Route:
    """把"空载到取货点"和"载货到卸货点"合并成一条占用路线。"""
    if first.length <= 0.0:
        return second
    if second.length <= 0.0:
        return first
    nodes = tuple(first.nodes) + tuple(second.nodes[1:])
    lanes = list(first.lanes)
    for lane in second.lanes:
        if not lanes or lanes[-1] != lane:
            lanes.append(lane)
    return Route(nodes, tuple(lanes), first.length + second.length)


# ═══════════════════════════════════════════════════════════════
# 场景与对比
# ═══════════════════════════════════════════════════════════════

def default_jobs(housing: int = 1, bracket: int = 1,
                 stagger_s: float = 0.0) -> list[TransportJob]:
    """按批次生成运输任务; stagger_s>0 时相邻任务错开创建时间。"""
    jobs: list[TransportJob] = []
    seq = 0
    for index in range(max(housing, bracket)):
        if index < housing:
            seq += 1
            jobs.append(TransportJob(
                job_id=f"ba{seq}", item_type="SEMI_AB", origin="B",
                destination="A", created_at=index * stagger_s,
                priority=0))
        if index < bracket:
            seq += 1
            jobs.append(TransportJob(
                job_id=f"de{seq}", item_type="PART_DE", origin="D",
                destination="E", created_at=index * stagger_s,
                priority=1))
    return jobs


def compare_policies(vehicles: Iterable[VehicleSpec],
                     jobs: Iterable[TransportJob]) -> dict:
    vehicles = list(vehicles)
    jobs = list(jobs)
    out: dict[str, dict] = {}
    for policy in POLICIES:
        sim = DualCartSimulator(vehicles, jobs, policy=policy)
        out[policy] = sim.run()
    base = out["single"]["makespan_s"] or 1.0
    for policy, result in out.items():
        result["makespan_vs_single"] = round(result["makespan_s"] / base, 4)
    return out
