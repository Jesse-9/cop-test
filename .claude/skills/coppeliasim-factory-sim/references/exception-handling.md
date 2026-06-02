# Exception Handling Reference

## Exception Taxonomy

All exceptions in the factory simulation fall into four categories.
Each has a detection mechanism, a recovery strategy, and an escalation path.

```
                    ┌──────────────────────┐
                    │   Exception Handler   │
                    │   (runs every step)   │
                    └──────────┬───────────┘
                               │
          ┌────────────────────┼────────────────────┐
          │                    │                    │
    ┌─────▼──────┐    ┌───────▼───────┐    ┌───────▼──────┐
    │ ZMQ Comm    │    │   Logistics   │    │  Workstation │
    │ Exception   │    │  Congestion   │    │   Timeout    │
    └─────┬──────┘    └───────┬───────┘    └───────┬──────┘
          │                    │                    │
    ┌─────▼──────┐    ┌───────▼───────┐    ┌───────▼──────┐
    │ Reconnect   │    │ BLOCKED →    │    │ Pause upstream│
    │ (3 retries) │    │ Replan/Wait  │    │ Auto-degrade  │
    └────────────┘    └───────────────┘    └───────────────┘
```

## 1. ZMQ Communication Exception

The most critical exception — without communication, the entire system is
blind.

### Detection

```python
class ZMQMonitor:
    """Monitors ZMQ connection health."""

    def __init__(self, connector, timeout: float = 3.0):
        self.connector = connector
        self.timeout = timeout
        self.last_response: float = 0.0
        self.consecutive_failures: int = 0
        self.max_failures: int = 3
        self.comm_paused: bool = False

    def check(self, sim_time: float) -> str:
        """
        Check connection health. Must be called before each step.
        Returns: 'ok', 'reconnecting', or 'dead'
        """
        if self.connector.check_alive():
            self.last_response = sim_time
            self.consecutive_failures = 0
            if self.comm_paused:
                print("[INFO] ZMQ connection restored, resuming commands")
                self.comm_paused = False
            return "ok"

        # No response
        elapsed = sim_time - self.last_response
        if elapsed > self.timeout:
            self.consecutive_failures += 1
            print(f"[WARN] ZMQ no response for {elapsed:.1f}s "
                  f"(failure {self.consecutive_failures}/{self.max_failures})")

            if self.consecutive_failures >= self.max_failures:
                print("[FATAL] ZMQ connection lost — cannot recover")
                return "dead"

            # Attempt reconnect
            print("[INFO] Attempting ZMQ reconnect...")
            if self.connector.reconnect_if_needed():
                self.consecutive_failures = 0
                self.comm_paused = False
                return "ok"
            else:
                self.comm_paused = True
                return "reconnecting"

        return "ok"
```

### Recovery Actions

When `comm_paused` is True:
1. **Stop issuing new movement commands** — current movements coast to stop
2. **Hold all material spawn timers** — don't spawn new materials
3. **Preserve state** — all island states, queue contents, and AGV positions
   are maintained in Python memory
4. **On reconnect**: resume from saved state without re-initialization

### Simulation Loop Integration

```python
def simulation_step(self):
    comm_status = self.zmq_monitor.check(self.sim_time)

    if comm_status == "dead":
        self._emergency_stop()
        return False  # Signal loop to terminate

    if comm_status == "reconnecting":
        # Skip physics updates, just track time
        self.sim_time += self.sim_step
        return True

    # Normal operation
    self._spawn_from_warehouses(dt)
    self._move_conveyors(dt)
    # ... rest of normal step
    return True
```

## 2. Logistics Congestion / AGV Deadlock

### Detection

```python
class CongestionMonitor:
    """Detects AGV path blockages and triggers recovery."""

    def __init__(self, congestion_timeout: float = 5.0):
        self.congestion_timeout = congestion_timeout
        self.path_occupied_since: dict[str, float] = {}

    def check_path_blocked(self, route_key: str,
                           reservation_system,
                           sim_time: float) -> bool:
        """Check if a route is blocked. Track blockage duration."""
        blocked = not reservation_system.try_reserve("checker", route_key)
        reservation_system.release("checker", route_key)

        if blocked:
            if route_key not in self.path_occupied_since:
                self.path_occupied_since[route_key] = sim_time
            duration = sim_time - self.path_occupied_since[route_key]
            if duration > self.congestion_timeout:
                return True  # Confirmed congestion
        else:
            self.path_occupied_since.pop(route_key, None)

        return False
```

### Recovery Actions

When BLOCKED is triggered:
1. Log the event with timestamp, route, and duration
2. Attempt alternative path (if multiple routes exist for the task)
3. If no alternative, enter waiting mode — AGV stays at current position
4. After 2× congestion_timeout, log a WARNING with recommendation for
   manual intervention
5. When path clears, automatically resume and log recovery time

```python
def handle_congestion(self, agv_scheduler, route_key, sim_time):
    """Handle confirmed congestion on a route."""
    self.logger.warning(
        f"[CONGESTION] Route {route_key} blocked for "
        f"{sim_time - self.path_occupied_since[route_key]:.1f}s"
    )

    # Try re-planning
    alt_route = self._find_alternative_route(route_key)
    if alt_route:
        self.logger.info(f"[RECOVERY] Switching to alternative route")
        agv_scheduler.set_alternate_route(alt_route)
        return True

    # No alternative — wait
    return False
```

## 3. Workstation Timeout

### Detection

Detected by the `BeatTracker` (see control-framework.md). When
`TAKT_TIME * 1.5` is exceeded, the island enters TIMEOUT state.

### Recovery Actions (Auto-Degrade)

```python
def handle_workstation_timeout(self, island: str):
    """Handle workstation timeout via auto-degrade and recovery."""
    state = self.beat_tracker.states[island]

    # 1. Mark anomaly
    state.anomaly = True

    # 2. Pause upstream supply to this island
    upstream_sources = self._get_upstream_sources(island)
    for source in upstream_sources:
        self.logger.info(f"[TIMEOUT] Pausing supply from {source} → {island}")
        self.supply_controller.pause(source, island)

    # 3. Auto-degrade: extend beat by 50%
    old_target = state.takt_target
    state.takt_target *= 1.5
    state.normal_cycles = 0

    self.logger.warning(
        f"[TIMEOUT] Island {island}: beat extended "
        f"{old_target:.1f}s → {state.takt_target:.1f}s"
    )

def handle_workstation_recovery(self, island: str):
    """Auto-restore after 3 consecutive normal cycles."""
    state = self.beat_tracker.states[island]
    state.normal_cycles += 1

    if state.normal_cycles >= 3:
        state.takt_target /= 1.5
        state.anomaly = False
        state.normal_cycles = 0

        # Resume upstream supply
        upstream_sources = self._get_upstream_sources(island)
        for source in upstream_sources:
            self.supply_controller.resume(source, island)

        self.logger.info(
            f"[RESTORE] Island {island}: beat restored to "
            f"{state.takt_target:.1f}s, supply resumed"
        )
```

### Upstream Source Mapping

```python
UPSTREAM_MAP = {
    "A": ["S1"],           # A depends on S1 (via conveyor)
    "B": ["S1"],           # B depends on S1 (via conveyor)
    "C": ["A"],            # C depends on A (via AGV/conveyor)
    "D": ["S2"],           # D depends on S2 (via conveyor)
    "E": ["C", "D"],       # E depends on both C and D
}
```

When E times out, both C→E and D→E supply must pause to prevent overflow.

## 4. Material Overflow

### Detection

```python
class OverflowMonitor:
    """Detects material accumulation beyond queue limits."""

    def __init__(self, max_queue: int = 5):
        self.max_queue = max_queue

    def check(self, island_controllers: dict[str, IslandController],
              sim_time: float) -> list[str]:
        """Check all islands for overflow. Returns list of island names."""
        overflowing = []
        for name, controller in island_controllers.items():
            if len(controller.queue) >= self.max_queue:
                overflowing.append(name)
        return overflowing
```

### Recovery Actions

1. Pause upstream supply for the overflowing island
2. Log the overflow event with queue snapshot
3. Resume supply when queue drops below 50% of max

```python
def handle_overflow(self, island: str, queue_length: int):
    self.logger.warning(
        f"[OVERFLOW] Island {island}: queue={queue_length} "
        f"(max={self.max_queue}) — pausing upstream supply"
    )
    # Pause upstream sources
    upstream = UPSTREAM_MAP.get(island, [])
    for source in upstream:
        self.supply_controller.pause(source, island)

def check_overflow_recovery(self, island_controllers):
    for name, ctrl in island_controllers.items():
        if (self.supply_controller.is_paused(name) and
            len(ctrl.queue) <= self.max_queue // 2):
            self.logger.info(f"[RECOVER] Island {name}: queue "
                             f"recovered to {len(ctrl.queue)}, resuming supply")
            upstream = UPSTREAM_MAP.get(name, [])
            for source in upstream:
                self.supply_controller.resume(source, name)
```

## Emergency Stop

When unrecoverable errors occur (ZMQ dead, physics engine crash), perform
an emergency stop:

```python
def emergency_stop(self, reason: str):
    """Emergency stop — save state and terminate gracefully."""
    self.logger.critical(f"[EMERGENCY] {reason}")
    self.logger.info("[EMERGENCY] Saving state...")

    # Save complete state to JSON for post-mortem analysis
    state = {
        "sim_time": self.sim_time,
        "step_count": self.step_count,
        "island_states": {
            name: ctrl.state.name
            for name, ctrl in self.island_controllers.items()
        },
        "agv_state": self.agv_scheduler.state.name,
        "material_count": len(self.materials),
        "event_log_tail": self.event_log[-50:],  # Last 50 events
    }

    import json
    from datetime import datetime
    dump_file = f"emergency_dump_{datetime.now():%Y%m%d_%H%M%S}.json"
    with open(dump_file, "w") as f:
        json.dump(state, f, indent=2, default=str)

    self.logger.info(f"[EMERGENCY] State saved to {dump_file}")

    # Try to stop simulation
    try:
        self.sim.stopSimulation()
    except Exception:
        pass
```

## Exception Handler — Main Integration

The exception handler runs as part of every simulation step, before any
control logic executes:

```python
class ExceptionHandler:
    """Unified exception monitoring and recovery."""

    def __init__(self, zmq_monitor, congestion_monitor,
                 overflow_monitor, beat_tracker, logger):
        self.zmq = zmq_monitor
        self.congestion = congestion_monitor
        self.overflow = overflow_monitor
        self.beat = beat_tracker
        self.logger = logger
        self.exception_log: list[dict] = []

    def poll(self, sim_time: float,
             island_controllers: dict,
             agv_scheduler,
             reservation_system) -> bool:
        """
        Poll all exception sources. Returns False if simulation should stop.

        Called once per simulation step, before any control actions.
        """
        # 1. Communication (highest priority)
        comm = self.zmq.check(sim_time)
        if comm == "dead":
            return False
        if comm == "reconnecting":
            return True  # Skip this step, wait for reconnect

        # 2. Workstation timeouts
        for name, ctrl in island_controllers.items():
            status = self.beat.check(name)
            if status == "timeout":
                self._record_exception("workstation_timeout", name, sim_time)

        # 3. Congestion
        for route_key in reservation_system.reservations:
            if self.congestion.check_path_blocked(
                route_key, reservation_system, sim_time
            ):
                self._record_exception("congestion", route_key, sim_time)

        # 4. Overflow
        overflowing = self.overflow.check(island_controllers, sim_time)
        for name in overflowing:
            self._record_exception("overflow", name, sim_time)

        return True

    def _record_exception(self, exc_type: str, target: str, sim_time: float):
        self.exception_log.append({
            "type": exc_type,
            "target": target,
            "sim_time": sim_time,
        })
```
