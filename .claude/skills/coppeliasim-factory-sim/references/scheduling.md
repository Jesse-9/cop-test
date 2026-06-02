# Scheduling & State Machine Reference

## AGV State Machine Implementation

The shared AGV is the core flexible scheduling unit. Its state machine must
handle concurrent requests, priority preemption, and blockage recovery.

```python
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Optional
import math

class AGVState(Enum):
    IDLE = auto()
    MOVING_B_TO_A = auto()
    MOVING_D_TO_E = auto()
    BLOCKED = auto()

class AGVTask(Enum):
    B_TO_A = auto()    # Higher base priority
    D_TO_E = auto()    # Lower base priority, escalatable

@dataclass
class AGVJob:
    task: AGVTask
    priority: int          # 0=highest, higher number = lower priority
    material_uid: int
    wait_start: float = 0.0  # simTime when job was queued
    preemptable: bool = True

class AGVScheduler:
    """Flexible AGV scheduler with priority-based task management."""

    def __init__(self, config: dict):
        """
        config keys:
          - agv_speed: float (m/s)
          - d_e_starvation_threshold: float (seconds)
          - congestion_timeout: float (seconds)
        """
        self.state = AGVState.IDLE
        self.speed = config["agv_speed"]
        self.starvation_threshold = config["d_e_starvation_threshold"]
        self.congestion_timeout = config["congestion_timeout"]

        self.current_job: Optional[AGVJob] = None
        self.job_queue: list[AGVJob] = []
        self.progress: float = 0.0   # 0→1 on current path
        self.blocked_since: float = 0.0  # simTime when BLOCKED started

        # Route definitions (waypoints)
        self.routes = {
            AGVTask.B_TO_A: [
                (-2.5, -1.0), (-2.5, 2.0)  # B → A
            ],
            AGVTask.D_TO_E: [
                (-2.5, -3.0), (-1.0, -2.0), (2.0, -1.0), (6.0, -0.5)
            ],
        }

    def request_task(self, task: AGVTask, material_uid: int,
                     sim_time: float):
        """Queue a new transport request."""
        base_priority = 0 if task == AGVTask.B_TO_A else 1
        job = AGVJob(
            task=task,
            priority=base_priority,
            material_uid=material_uid,
            wait_start=sim_time,
            preemptable=(task == AGVTask.D_TO_E),
        )
        self.job_queue.append(job)
        self._sort_queue(sim_time)

    def _sort_queue(self, sim_time: float):
        """Sort queue by priority. Escalate starved D→E jobs."""
        for job in self.job_queue:
            if (job.task == AGVTask.D_TO_E and
                sim_time - job.wait_start > self.starvation_threshold):
                job.priority = 0  # Escalate to equal B→A
                job.preemptable = False
        self.job_queue.sort(key=lambda j: (j.priority, j.wait_start))

    def update(self, dt: float, sim_time: float,
               path_blocked: bool = False) -> Optional[int]:
        """
        Advance AGV state machine by one time step.
        Returns material_uid when a delivery completes, else None.

        Caller passes path_blocked=True when the target route is occupied.
        """
        # Check for preemption
        if self.state == AGVState.MOVING_D_TO_E:
            waiting_b_to_a = [
                j for j in self.job_queue
                if j.task == AGVTask.B_TO_A
            ]
            if waiting_b_to_a and self.current_job.preemptable:
                # Preempt: return current job to queue, start B→A
                self.job_queue.append(self.current_job)
                self.current_job = None
                self.state = AGVState.IDLE
                self.progress = 0.0

        # Handle BLOCKED state
        if self.state == AGVState.BLOCKED:
            if not path_blocked:
                # Path cleared, recover
                self.state = (
                    AGVState.MOVING_B_TO_A
                    if self.current_job.task == AGVTask.B_TO_A
                    else AGVState.MOVING_D_TO_E
                )
            elif sim_time - self.blocked_since > self.congestion_timeout * 2:
                # Extended blockage — try replanning
                # (In practice, this would call a path replanner)
                # For now, log and wait
                pass
            return None

        # Try to pick up new job
        if self.state == AGVState.IDLE:
            self._sort_queue(sim_time)
            if self.job_queue:
                self.current_job = self.job_queue.pop(0)
                self.state = (
                    AGVState.MOVING_B_TO_A
                    if self.current_job.task == AGVTask.B_TO_A
                    else AGVState.MOVING_D_TO_E
                )
                self.progress = 0.0

        # Move along current route
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
                # Delivery complete
                delivered_uid = self.current_job.material_uid
                self.current_job = None
                self.state = AGVState.IDLE
                self.progress = 0.0
                return delivered_uid

        return None

    def _path_length(self, waypoints: list[tuple]) -> float:
        total = 0.0
        for i in range(len(waypoints) - 1):
            total += math.hypot(
                waypoints[i + 1][0] - waypoints[i][0],
                waypoints[i + 1][1] - waypoints[i][1],
            )
        return total

    def current_position(self) -> tuple[float, float]:
        """Get AGV's interpolated position on its current route."""
        if self.current_job is None:
            route = self.routes[AGVTask.B_TO_A]
            return route[-1]  # Default: park at B→A endpoint
        route = self.routes[self.current_job.task]
        return self._interpolate(route, self.progress)

    @staticmethod
    def _interpolate(waypoints, t):
        if t <= 0:
            return waypoints[0]
        if t >= 1:
            return waypoints[-1]
        # ... (linear interpolation along multi-segment path)
        return waypoints[0]  # Simplified — see full implementation
```

## Deadlock Prevention

Deadlocks happen when two transport units wait for each other's paths.
Prevention strategies:

### 1. Path Reservation
Before an AGV begins moving, reserve its target path. Other AGVs check
reservations before starting:

```python
class PathReservationSystem:
    """Prevents AGV collisions via path reservation."""

    def __init__(self):
        self.reservations: dict[str, str] = {}  # path_segment → agv_name

    def try_reserve(self, agv_name: str, route_key: str) -> bool:
        """Try to reserve a route. Returns True if successful."""
        if route_key in self.reservations:
            existing = self.reservations[route_key]
            if existing != agv_name:
                return False  # Reserved by another AGV
        self.reservations[route_key] = agv_name
        return True

    def release(self, agv_name: str, route_key: str):
        """Release a route reservation."""
        if self.reservations.get(route_key) == agv_name:
            del self.reservations[route_key]
```

### 2. Timeout-Based Deadlock Breaking
If path reservation exceeds `CONGESTION_TIMEOUT`, forcibly release and
attempt alternative routing.

### 3. Single AGV Simplification
For the standard 5-island topology, using a single shared AGV eliminates
multi-AGV deadlock scenarios entirely. The priority queue handles
contention, and BLOCKED state handles physical obstructions.

## Island State Machine

Each work island follows this state machine:

```
IDLE → PROCESSING → COMPLETE → (material released for transport) → IDLE
  ↑                     ↓
  └─────────────────────┘ (new material arrives, no processing needed)
```

```python
class IslandState(Enum):
    IDLE = auto()
    PROCESSING = auto()
    COMPLETE = auto()
    TIMEOUT = auto()     # Beat exceeded — upstream supply paused
    OFFLINE = auto()     # Manual or emergency stop

@dataclass
class IslandController:
    name: str
    state: IslandState = IslandState.IDLE
    current_material_uid: int = -1
    queue: list[int] = field(default_factory=list)
    processing_remaining: float = 0.0
    max_queue: int = 5

    def can_accept(self) -> bool:
        """Check if island can accept more materials."""
        return (self.state not in (IslandState.TIMEOUT, IslandState.OFFLINE)
                and len(self.queue) < self.max_queue)

    def enqueue(self, material_uid: int):
        if self.state == IslandState.IDLE and not self.queue:
            self.current_material_uid = material_uid
            self.state = IslandState.PROCESSING
        else:
            self.queue.append(material_uid)

    def update(self, dt: float):
        if self.state == IslandState.PROCESSING:
            self.processing_remaining -= dt
            if self.processing_remaining <= 0:
                self.processing_remaining = 0
                self.state = IslandState.COMPLETE

    def release_material(self) -> Optional[int]:
        """Release completed material for transport. Returns material uid."""
        if self.state != IslandState.COMPLETE:
            return None
        released = self.current_material_uid
        if self.queue:
            self.current_material_uid = self.queue.pop(0)
            self.state = IslandState.PROCESSING
        else:
            self.current_material_uid = -1
            self.state = IslandState.IDLE
        return released
```

## Material Lifecycle Tracking

Each material goes through predictable state transitions. Track these to
enable end-to-end traceability:

```
SPAWNED → ON_CONVEYOR → AT_ISLAND → PROCESSED → ON_AGV → AT_ISLAND → ... → ASSEMBLED
```

Key tracking fields per material:
- `uid`: unique identifier
- `item_type`: material type (ItemType enum)
- `location`: where it currently is (conveyor name, island name, AGV name)
- `progress`: 0→1 movement progress on current transport
- `processing_remaining`: seconds remaining if at island
- `history`: list of (sim_time, event) tuples for traceability
