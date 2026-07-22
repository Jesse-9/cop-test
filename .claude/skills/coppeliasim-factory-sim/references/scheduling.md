# Scheduling & State Machine Reference

> Mirrors [src/factory_agv.py](../../../src/factory_agv.py),
> [src/factory_island.py](../../../src/factory_island.py),
> [src/factory_types.py](../../../src/factory_types.py), and
> [docs/wiki/06-工业案例/06-03-src模块参考.md](../../../docs/wiki/06-工业案例/06-03-src模块参考.md).

## AGV State Machine

The single shared AGV (`AGVScheduler`) is the core flexible scheduling unit.
States (`AGVState`):

```python
class AGVState(Enum):
    IDLE          = auto()   # waiting for task assignment
    MOVING_B_TO_A = auto()   # executing B→A (priority 0)
    MOVING_D_TO_E = auto()   # executing D→E (priority 1, escalatable)
    BLOCKED       = auto()   # path occupied, awaiting recovery
```

Tasks (`AGVTask`): `B_TO_A` (base priority 0) and `D_TO_E` (base priority 1,
escalatable). A queued job is `AGVJob(task, priority, material_uid, wait_start)`.

```
                    ┌──────────┐
                    │   IDLE    │
                    └────┬─────┘
                         │ queue head popped
                    ┌────▼──────────┐
              ┌─────│ MOVING_B_TO_A │ (priority 0)
              │     └────┬──────────┘
              │          │ arrived
              │     ┌────▼──────────┐
              │     │ MOVING_D_TO_E │ (priority 1, escalatable)
              │     └────┬──────────┘
              │          │ path blocked > CONGESTION_TIMEOUT
         ┌────▼──────────▼────┐
         │      BLOCKED        │
         └────────┬───────────┘
                  │ path cleared → IDLE
             ┌────▼─────┐
             │   IDLE    │
             └──────────┘
```

## Priority Rule & Starvation Escalation

1. `B_TO_A` base priority 0 (highest), `D_TO_E` base priority 1
2. If a `D_TO_E` job waits longer than `D_E_STARVATION_THRESHOLD` (15.0s),
   it escalates to priority 0 (equal with B→A)
3. **Preemption is removed.** The `AGVTask` docstring still calls D→E
   "preemptable" — this is a stale contract, not live behavior (known defect
   documented in [docs/wiki/06-工业案例/06-03-src模块参考.md](../../../docs/wiki/06-工业案例/06-03-src模块参考.md)).
   Do not write code relying on in-flight preemption; the scheduler picks the
   queue head only when IDLE.

> Another known defect: `_fair_sort_priority_zero` is meant to interleave
> B→A/D→E at priority 0, but in practice it merges two already-sorted
> sub-lists by `wait_start` — equivalent to a plain sort, so the two task
> types do not actually alternate.

## AGVScheduler Public API

| Method | Description |
|--------|-------------|
| `request_task(task, material_uid, sim_time)` | Queue a transport request |
| `update(dt, sim_time, path_blocked=False) -> Optional[int]` | Advance the state machine one step; returns `material_uid` when a delivery completes, else `None`. When IDLE, pops the queue head. |
| `current_position() -> (x, y)` | Interpolated AGV position on its current route |
| `is_available() -> bool` | Whether the AGV is IDLE and can accept a new task |

## Routes (waypoints, from cfg.AGV_ROUTES)

```python
AGV_ROUTES = {
    "B_TO_A": [
        (-2.5, -1.0),   # B island
        (-2.5,  2.0),   # A island
    ],
    "D_TO_E": [
        (-2.5, -4.5),   # D island (lowered to clear the route)
        (-1.0, -2.5),   # mid-turn 1
        ( 2.0, -1.0),   # mid-turn 2
        ( 6.0, -0.5),   # E island
    ],
}
```

> Note the D island is at Y=-4.5 (not -3.0) and the D→E route has two
> mid-turn waypoints so it does not cross conveyors at grade level.

## Skeleton Implementation

```python
class AGVScheduler:
    """Flexible AGV scheduler: priority queue + starvation escalation."""

    def __init__(self, config: dict):
        # config keys: agv_speed, d_e_starvation_threshold, congestion_timeout
        self.state = AGVState.IDLE
        self.speed = config["agv_speed"]
        self.starvation_threshold = config["d_e_starvation_threshold"]
        self.congestion_timeout = config["congestion_timeout"]
        self.current_job: Optional[AGVJob] = None
        self.job_queue: list[AGVJob] = []
        self.progress = 0.0          # 0→1 on current route
        self.blocked_since = 0.0     # simTime when BLOCKED began
        self.routes = {
            AGVTask.B_TO_A: [(-2.5, -1.0), (-2.5, 2.0)],
            AGVTask.D_TO_E: [(-2.5, -4.5), (-1.0, -2.5), (2.0, -1.0), (6.0, -0.5)],
        }

    def request_task(self, task: AGVTask, material_uid: int, sim_time: float):
        base_priority = 0 if task == AGVTask.B_TO_A else 1
        self.job_queue.append(AGVJob(
            task=task, priority=base_priority,
            material_uid=material_uid, wait_start=sim_time,
        ))
        self._sort_queue(sim_time)

    def _sort_queue(self, sim_time: float):
        """Escalate starved D→E to priority 0, then sort by (priority, wait_start)."""
        for job in self.job_queue:
            if (job.task == AGVTask.D_TO_E
                    and sim_time - job.wait_start > self.starvation_threshold):
                job.priority = 0   # escalate
        self.job_queue.sort(key=lambda j: (j.priority, j.wait_start))

    def update(self, dt: float, sim_time: float, path_blocked: bool = False):
        # BLOCKED recovery
        if self.state == AGVState.BLOCKED:
            if not path_blocked:
                self.state = (AGVState.MOVING_B_TO_A
                              if self.current_job.task == AGVTask.B_TO_A
                              else AGVState.MOVING_D_TO_E)
            return None

        # Pick up queue head when IDLE
        if self.state == AGVState.IDLE:
            self._sort_queue(sim_time)
            if self.job_queue:
                self.current_job = self.job_queue.pop(0)
                self.state = (AGVState.MOVING_B_TO_A
                              if self.current_job.task == AGVTask.B_TO_A
                              else AGVState.MOVING_D_TO_E)
                self.progress = 0.0

        # Move along route
        if self.state in (AGVState.MOVING_B_TO_A, AGVState.MOVING_D_TO_E):
            if path_blocked:
                self.state = AGVState.BLOCKED
                self.blocked_since = sim_time
                return None
            route = self.routes[self.current_job.task]
            path_len = self._path_length(route)
            if path_len > 0:
                self.progress += (self.speed / path_len) * dt
            if self.progress >= 1.0:
                delivered = self.current_job.material_uid
                self.current_job = None
                self.state = AGVState.IDLE
                self.progress = 0.0
                return delivered
        return None

    def is_available(self) -> bool:
        return self.state == AGVState.IDLE

    def current_position(self) -> tuple[float, float]:
        if self.current_job is None:
            return self.routes[AGVTask.B_TO_A][-1]   # parked at B→A endpoint
        return self._interpolate(self.routes[self.current_job.task], self.progress)
```

## Deadlock Prevention

For the standard 5-island topology, **using a single shared AGV eliminates
multi-AGV deadlock entirely**. The priority queue handles contention, and the
`BLOCKED` state handles physical obstructions (path occupied >
`CONGESTION_TIMEOUT`). Recovery is path-clear-then-resume — there is no
multi-route reservation system because there is only one AGV.

## Island State Machine

Each work island (`IslandController`) follows this state machine. Note
`WAITING_FOR_AGV` — the island holds a completed material until the AGV
picks it up:

```
IDLE → PROCESSING → COMPLETE → WAITING_FOR_AGV → (AGV pickup) → IDLE
  ↑                                                        │
  └────────────────────────────────────────────────────────┘
Anomaly:  PROCESSING → TIMEOUT → (recovery) → IDLE
Emergency: any → OFFLINE
```

```python
class IslandState(Enum):
    IDLE            = auto()
    PROCESSING      = auto()
    COMPLETE        = auto()
    WAITING_FOR_AGV = auto()   # completed, awaiting AGV pickup
    TIMEOUT         = auto()   # beat exceeded — upstream supply paused, auto-degrade
    OFFLINE         = auto()   # manual or emergency stop
```

`IslandController` API: `can_accept()`, `enqueue(material_uid)`,
`update(dt)`, `release_material() -> Optional[int]`, `confirm_pickup()`,
`enter_timeout()`, `exit_timeout()`. `can_accept()` returns False when in
`TIMEOUT`/`OFFLINE` or when the queue is full (`>= MAX_QUEUE_LENGTH`).

> `IslandState` is re-exported from `factory_types` (via `factory_island`)
> to avoid a circular import.

## Material Lifecycle Tracking

Each `Material` ([src/factory_types.py](../../../src/factory_types.py)) tracks
its complete lifecycle for end-to-end traceability:

```
SPAWNED → ON_CONVEYOR → AT_ISLAND → PROCESSED → ON_AGV → AT_ISLAND → ... → ASSEMBLED
```

Key fields: `uid`, `item_type` (changes as the part advances through stages),
`handle` (CoppeliaSim object, -1 once removed), `pos`, `on_conveyor` /
`conv_progress`, `on_agv` / `agv_progress`, `at_island`,
`processing_remaining`, `delivered`, and `history` (list of
`(sim_time, event)` tuples).

`ItemType` stages: `RAW_A`, `RAW_B`, `SEMI_AB`, `SEMI_AC`, `RAW_D`,
`SEMI_CE`, `PART_DE`, `FINAL`. Use `spawn_material(sim, item_type, pos, sim_time)`
to create and `remove_material(sim, mat)` to delete the visual.
