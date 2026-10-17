# Exception Handling Reference

> Mirrors [src/reference/factory_exceptions.py](../../../src/reference/factory_exceptions.py) and
> [docs/wiki/06-工业案例/06-03-src模块参考.md](../../../docs/wiki/06-工业案例/06-03-src模块参考.md).

## Exception Taxonomy (by severity)

`ExceptionHandler.poll()` runs once per `SIM_STEP`, before any control logic,
and checks four sources in severity order:

```
                    ┌──────────────────────┐
                    │   ExceptionHandler  │
                    │   poll() per step   │
                    └──────────┬───────────┘
                               │
          ┌────────────────────┼────────────────────┐
          │                    │                    │
    ┌─────▼──────┐    ┌────────▼────────┐    ┌──────▼──────┐
    │ 1. ZMQ Comm │    │ 2. Workstation  │    │ 3. Overflow │
    │   (highest) │    │    Timeout      │    │             │
    └─────┬──────┘    └────────┬────────┘    └──────┬──────┘
          │                    │                    │
    ┌─────▼──────┐    ┌────────▼────────┐    ┌──────▼──────┐
    │ dead→stop   │    │ TIMEOUT→pause  │    │ pause       │
    │ rec→skip    │    │ upstream;      │    │ upstream;   │
    │             │    │ 3 normal→resume│    │ ≤half→resume│
    └─────────────┘    └─────────────────┘    └─────────────┘
                                                  │
                                          ┌───────▼───────┐
                                          │ 4. AGV        │
                                          │  Congestion   │
                                          │  (log event)  │
                                          └───────────────┘
```

| Priority | Exception | Detection | Recovery |
|----------|-----------|-----------|----------|
| 1 | ZMQ communication | `ZMQMonitor.check(sim_time)` | `"dead"` → emergency stop; `"reconnecting"` → skip control this step |
| 2 | Workstation timeout | `BeatTracker.check(island)` returns `"timeout"` | `IslandController.enter_timeout()` + `SupplyController.pause_all_upstream(island)`; 3 normal cycles → `exit_timeout()` + `resume_all_upstream()` |
| 3 | Material overflow | queue length ≥ `MAX_QUEUE_LENGTH` | `pause_all_upstream`; recovers when queue ≤ `MAX_QUEUE_LENGTH // 2` |
| 4 | AGV congestion | `AGVScheduler.state == BLOCKED` for > `CONGESTION_TIMEOUT` | Records event; `BLOCKED`→`IDLE` recovery handled by `AGVScheduler.update()` |

## ExceptionHandler Public API

```python
class ExceptionHandler:
    def __init__(self, zmq_monitor: ZMQMonitor, beat_tracker,
                 supply_ctrl, logger: logging.Logger): ...

    def poll(self, sim_time: float,
             island_controllers: dict,   # dict[str, IslandController]
             agv_scheduler) -> bool:
        """Poll all sources for one step. Returns False → main loop should
        emergency-stop. 'reconnecting' returns True but the step's control
        logic is skipped (physics still advances)."""
```

> Note: `poll()` takes only `(sim_time, island_controllers, agv_scheduler)` —
> there is no separate `reservation_system` argument and no standalone
> `CongestionMonitor`/`OverflowMonitor` classes; congestion and overflow are
> checked inline by `_check_congestion` / `_check_overflow`.

## 1. ZMQ Communication Exception

The most critical — without communication the system is blind.

### Detection (`ZMQMonitor`, src/reference/factory_core.py)

```python
class ZMQMonitor:
    def __init__(self, connector, timeout: float = 3.0):
        self.connector = connector
        self.timeout = timeout            # cfg.ZMQ_TIMEOUT
        self.last_response: float = 0.0
        self.consecutive_failures = 0
        self.max_failures = 3             # cfg.ZMQ_MAX_RETRIES
        self.comm_paused: bool = False

    def check(self, sim_time: float) -> str:
        """Return 'ok', 'reconnecting', or 'dead'. Called before each step."""
        if self.connector.check_alive():          # tolerates None version signal
            self.last_response = sim_time
            self.consecutive_failures = 0
            if self.comm_paused:
                self.comm_paused = False
            return "ok"

        elapsed = sim_time - self.last_response
        if elapsed > self.timeout:
            self.consecutive_failures += 1
            if self.consecutive_failures >= self.max_failures:
                return "dead"
            if self.connector.reconnect_if_needed():
                self.consecutive_failures = 0
                self.comm_paused = False
                return "ok"
            self.comm_paused = True
            return "reconnecting"
        return "ok"
```

### Recovery Actions when `comm_paused`
1. **Stop issuing new movement commands** — current movements coast to stop
2. **Hold material spawn timers** — don't spawn new materials
3. **Preserve state** — island states, queues, AGV position stay in memory
4. **On reconnect**: resume from saved state without re-initialization

### Loop integration (FactoryController._single_step)
```python
comm_status = self.zmq_monitor.check(self.sim_time)
if comm_status == "dead":
    self._emergency_stop("ZMQ connection lost")
    return False
if comm_status == "reconnecting":
    # physics still advances via sim.step(); control logic skipped this step
    return True
# ... physical workflow control logic (sensor → conveyor/material → IK/suction
#     → pioneer wheel control → handoff/logging) ...
```

## 2. Workstation Timeout (auto-degrade + auto-restore)

Detected by `BeatTracker.check(island)` (see
[control-framework.md](control-framework.md) §Beat Tracking). When
`cycle_elapsed > takt_target * TAKT_TIMEOUT_FACTOR`, `check()` returns
`"timeout"` and `ExceptionHandler._check_workstation_timeouts` coordinates:

```python
def _check_workstation_timeouts(self, sim_time, island_controllers):
    from factory_island import IslandState
    for name, ctrl in island_controllers.items():
        if ctrl.state == IslandState.PROCESSING:
            status = self.beat.check(name)
            if status == "timeout" and not ctrl.timeout_notified:
                ctrl.enter_timeout()
                self.supply.pause_all_upstream(name)        # gate upstream
                self._record("workstation_timeout", name, sim_time)
            elif status == "ok" and ctrl.state == IslandState.TIMEOUT:
                # BeatTracker auto-restored via normal_cycles >= 3
                ctrl.exit_timeout()
                self.supply.resume_all_upstream(name)      # reopen upstream
                self._record("workstation_recovered", name, sim_time)
```

`BeatTracker` itself does the degrade (`takt_target *= 1.5`) and restore
(`takt_target /= 1.5` after 3 normal cycles). `SupplyController` pauses/resumes
**all** upstream sources per `cfg.UPSTREAM_MAP`:

```python
UPSTREAM_MAP = {"A": ["S1"], "B": ["S1"], "C": ["A"], "D": ["S2"], "E": ["C", "D"]}
```

When E times out, both C→E and D→E supply pause to prevent overflow.

## 3. Material Overflow

```python
def _check_overflow(self, sim_time, island_controllers):
    for name, ctrl in island_controllers.items():
        if len(ctrl.queue) >= cfg.MAX_QUEUE_LENGTH:             # default 5
            self.supply.pause_all_upstream(name)
            self._record("overflow", name, sim_time)
        elif (len(ctrl.queue) <= cfg.MAX_QUEUE_LENGTH // 2
              and self.supply.is_target_paused(name)):
            self.supply.resume_all_upstream(name)
            self._record("overflow_recovered", name, sim_time)
```

Pause upstream when the queue hits `MAX_QUEUE_LENGTH`; resume when it drops to
≤ half and the island was paused.

## 4. AGV Congestion

```python
def _check_congestion(self, sim_time, agv_scheduler):
    from factory_agv import AGVState
    if agv_scheduler.state == AGVState.BLOCKED:
        if sim_time - agv_scheduler.blocked_since > cfg.CONGESTION_TIMEOUT:  # 5.0s
            self._record("congestion", "AGV_Shared", sim_time)
```

This only **records** the congestion event. The actual `BLOCKED → IDLE`
recovery (path clears) is handled inside `AGVScheduler.update()`. With a
single shared AGV there is no multi-AGV deadlock and no path-reservation
system.

## SupplyController (gating)

`SupplyController` ([src/reference/factory_island.py](../../../src/reference/factory_island.py)) is
the upstream gate used by timeout and overflow handling. API:
`pause(source, target)` / `resume(source, target)` / `is_paused(source)` /
`is_target_paused(target)` / `pause_all_upstream(target)` /
`resume_all_upstream(target)`.

## Emergency Stop

When `poll()` returns `False` (or an unrecoverable error occurs), the
controller performs an emergency stop — logging CRITICAL and writing a
state dump for post-mortem:

```python
def emergency_stop(self, reason: str):
    self.logger.critical(f"[EMERGENCY] {reason}")
    state = {
        "sim_time": self.sim_time,
        "step_count": self.step_count,
        "island_states": {n: c.state.name for n, c in self.island_controllers.items()},
        "agv_state": self.agv_scheduler.state.name,
        "material_count": len(self.materials),
        "event_log_tail": self.event_log[-50:],
    }
    import json
    dump_file = f"emergency_dump_{stamp}.json"     # timestamp passed in (no Date.now in workflows)
    with open(dump_file, "w") as f:
        json.dump(state, f, indent=2, default=str)
    self.logger.info(f"[EMERGENCY] State saved to {dump_file}")
    try:
        self.sim.stopSimulation()
    except Exception:
        pass
```

> On a real run, `factory_sim.log` records DEBUG (file) / INFO (console);
> the dump JSON is the structured post-mortem artifact.
