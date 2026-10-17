# Python Control Framework Reference

> Mirrors [docs/wiki/06-工业案例/06-03-src模块参考.md](../../../docs/wiki/06-工业案例/06-03-src模块参考.md)
> (factory_core / factory_controller sections) and [src/reference/factory_core.py](../../../src/reference/factory_core.py),
> [src/reference/factory_controller.py](../../../src/reference/factory_controller.py).

## ZMQ Connection with Auto-Reconnect

`CoppeliaConnector` ([src/reference/factory_core.py](../../../src/reference/factory_core.py)) wraps
`RemoteAPIClient` with retry logic and a health check. **Important 4.10 quirk**:
the `coppeliaSimVersion` signal may be `None` in some instance configs, so
`getStringSignal("coppeliaSimVersion")` returns `None`. The connector handles
this via `_read_version()` (which tolerates `None`) and `check_alive()` falls
back to `getSimulationState()` as a probe.

```python
import time
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

class CoppeliaConnector:
    """ZMQ connection manager with auto-reconnect + 4.10 version-signal tolerance."""

    def __init__(self, host="localhost", port=23000,
                 max_retries=3, timeout=3.0):
        self.host = host
        self.port = port
        self.max_retries = max_retries      # cfg.ZMQ_MAX_RETRIES
        self.timeout = timeout              # cfg.ZMQ_TIMEOUT
        self.client = None
        self.sim = None

    def connect(self) -> bool:
        for attempt in range(1, self.max_retries + 1):
            try:
                self.client = RemoteAPIClient(host=self.host, port=self.port)
                self.sim = self.client.require("sim")
                version = self._read_version()      # tolerates None
                print(f"[OK] Connected to CoppeliaSim {version} (attempt {attempt})")
                return True
            except Exception as e:
                print(f"[WARN] Connection attempt {attempt}/{self.max_retries} failed: {e}")
                if attempt < self.max_retries:
                    time.sleep(1.0 * attempt)
        print(f"[FATAL] Cannot connect to CoppeliaSim after {self.max_retries} attempts")
        return False

    def _read_version(self) -> str:
        """Read version signal; tolerate None (4.10 quirk)."""
        try:
            v = self.sim.getStringSignal("coppeliaSimVersion")
            return v if v else "unknown (version signal is None)"
        except Exception:
            return "unknown"

    def check_alive(self) -> bool:
        """Quick health check. Falls back to getSimulationState() if version signal is None."""
        try:
            v = self.sim.getStringSignal("coppeliaSimVersion")
            if v:                  # signal present → healthy
                return True
            # signal None → fall back to a probe that always responds
            self.sim.getSimulationState()
            return True
        except Exception:
            return False

    def reconnect_if_needed(self) -> bool:
        if self.check_alive():
            return True
        print("[WARN] ZMQ connection lost, attempting reconnect...")
        return self.connect()
```

> The MCP server maintains **two** `CoppeliaConnector`s: the main connection
> (write/control tools + the main sim loop) and `ro_connector` (read-only
> diagnostics, guarded by `ro_lock`). This avoids false-disconnect reports
> while the main loop holds the main connection busy. See SKILL.md §MCP.

## Stepping Mode: The Clock Master Pattern

Python is the clock master. `sim.setStepping(True)` then each `sim.step()`
advances exactly one `SIM_STEP` (default 0.05s). The stepping loop is
**inlined directly in `FactoryController.run()`** — there is no separate
`SimulationLoop` wrapper class:

```python
class FactoryController:
    """Top-level orchestrator for the restructured physical workflow."""

    def __init__(self, sim, total_time=60.0, log_file="factory_sim.log"):
        self.sim = sim
        self.total_time = total_time            # cfg.TOTAL_SIM_TIME
        self.sim_step = 0.05                    # cfg.SIM_STEP
        self.sim_time = 0.0
        self.step_count = 0
        self.running = False
        # ... build island controllers, conveyor/material managers, UR5 IK,
        #     suction and pioneer controllers, scheduler, diagnostics ...

    def run(self):
        self.sim.setStepping(True)
        self.sim.startSimulation()
        self.running = True
        while self._should_continue():
            self._single_step()
            self.sim.step()                     # advance physics one SIM_STEP
            self.sim_time = self.sim.getSimulationTime()
            self.step_count += 1
        self._shutdown()

    def _single_step(self):
        # The physical workflow order — see "Single-Step Loop Order" below
        ...
```

**Why stepping mode matters**: without it the simulation runs on
CoppeliaSim's internal clock, decoupled from your control logic. Beat timing
becomes unreliable and exception detection (which depends on time
comparisons) produces false positives. Stepping makes every simulated second
predictable and measurable.

## Single-Step Loop Order (restructured FactoryController.run)

Each `SIM_STEP` executes this fixed order (matches [CLAUDE.md](../../../CLAUDE.md)):

1. Poll communication, collision, workstation timeout, material overflow and
   proximity/dock sensor faults.
2. If `comm_paused`, issue no new commands and advance only `sim.step()`.
3. Read conveyor entry/stop, pick, suction and AGV dock sensors; correlate
   hits with the active material UID/handle.
4. Advance the `HYBRID_DYNAMIC` conveyors and material lifecycle, applying
   only bounded, sensor-gated tangential compensation.
5. Update workstation beats, queues and resource locks.
6. For executable pick/place tasks, create target poses and call `simIK`;
   write the six solved UR5 joint targets and validate TCP error, attitude,
   limits, collision state and tool speed.
7. Attach, lift, place and release through the suction controller. Restore
   the material's saved dynamic/respondable, mass, friction and collision
   properties on release.
8. Schedule the shared pioneer p3dx and set left/right wheel target speeds;
   arrival requires stable chassis speed and dock sensor confirmation.
9. Process A→C, C→E, D→E handoffs and E-side pair assembly.
10. Append UID state transitions, physical compensation, failures and
    diagnostics, then call `sim.step()`.

## Device Handle Mapping (by alias leaf, NOT slash path)

Acquire handles by **enumerating and alias-leaf matching**, not by
`getObject("/Warehouse/S1_Main")` (unreliable in 4.10 — see
[scene-building.md](scene-building.md) §Handle Lookup).

> ⚠ The project does **not** have a centralized `DeviceHandleMap` class. A
> draft one was removed as unused in commit `c1b0223`. The alias-leaf matching
> logic is **inlined and duplicated** across `build_scene.clear_scene`,
> `factory_controller._setup_agv_visual`, and `factory_robot._collect_arm_joints`
> (known cleanup item — see [scene-building.md](scene-building.md)). The class
> below is an *illustrative pattern* for how to centralize it, not an existing
> export:

```python
class DeviceHandleMap:  # illustrative — not in src/ today
    """Maps all scene objects by alias leaf name (4.10-safe)."""

    def __init__(self, sim):
        self.sim = sim
        self.handles: dict[str, int] = {}

    def populate(self):
        leaves = [
            # Warehouses
            "Warehouse_S1_Main", "Warehouse_S2_Auxiliary",
            # Conveyors
            "Conveyor_S1_to_A", "Conveyor_S1_to_B",
            "Conveyor_S2_to_D", "Conveyor_C_to_E",
            # Islands
            "Island_A_Assembly", "Island_B_Preprocess",
            "Island_C_Processing", "Island_D_QC_Assembly",
            "Island_E_FinalAssembly",
            # Robot arms (UR5)
            "Island_A_Assembly_Robot_A", "Island_B_Preprocess_Robot_B",
            "Island_C_Processing_Robot_C", "Island_D_QC_Assembly_Robot_D",
            "Island_E_FinalAssembly_Robot_E_C", "Island_E_FinalAssembly_Robot_E_D",
            "SuctionPad_01_TCP",  # extend from the stage-specific alias manifest
            # AGV
            "Mobile_AGV_Shared",
        ]
        for leaf in leaves:
            h = find_by_leaf(self.sim, leaf, sim.object_sceneobject_type)
            if h is not None:
                self.handles[leaf] = h
            else:
                print(f"[WARN] Cannot find object by leaf '{leaf}'")
        print(f"[OK] Handle map populated: {len(self.handles)} objects")

    def get(self, leaf: str) -> int:
        if leaf not in self.handles:
            raise KeyError(f"Object '{leaf}' not in handle map")
        return self.handles[leaf]
```

(`find_by_leaf` is the alias-leaf matcher shown in
[scene-building.md](scene-building.md) §Handle Lookup.)

> A single `getObject()` is a ZMQ round-trip; caching handles once at startup
> avoids per-step round-trips.

## Beat Tracking System

`BeatTracker` ([src/reference/factory_island.py](../../../src/reference/factory_island.py)) validates
each island's actual processing time against its `TAKT_TIME` target. Runtime
state per island is the `IslandBeatState` dataclass
([src/reference/factory_types.py](../../../src/reference/factory_types.py)):

```python
@dataclass
class IslandBeatState:
    island: str
    takt_target: float          # target processing time (seconds); auto-extended on timeout
    cycle_start: float = 0.0    # simTime when current cycle began
    cycle_elapsed: float = 0.0  # accumulated processing time
    deviation: float = 0.0      # current deviation from target
    anomaly: bool = False       # True if timeout detected
    normal_cycles: int = 0      # consecutive normal cycles for auto-recovery
```

`BeatTracker` API: `start_cycle(island, sim_time)`, `update(island, sim_time, dt)`,
`check(island) -> 'ok' | 'warn' | 'timeout' | None`.

Logic (thresholds from [factory_config.py](../../../src/reference/factory_config.py)):
- Timeout (checked first, most severe): if `cycle_elapsed > takt_target *
  TAKT_TIMEOUT_FACTOR` (1.5) → set `anomaly`, reset `normal_cycles`, auto-degrade
  by extending `takt_target *= 1.5`, return `"timeout"`
- Warning: if `|deviation| > TAKT_DEVIATION_THRESHOLD` (1.0s) → `"warn"`
- Normal + was anomalous: increment `normal_cycles`; on 3 → auto-restore
  (`takt_target /= 1.5`, clear `anomaly`, resume upstream supply), return `"ok"`

## Logger Configuration

`setup_logger` ([src/reference/factory_core.py](../../../src/reference/factory_core.py)) — dual output:
file at DEBUG, console at INFO:

```python
import logging

def setup_logger(name="factory-sim", log_file="factory_sim.log") -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                                      datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(fh)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(ch)
    return logger
```

On emergency stop, the controller writes `emergency_dump_<timestamp>.json`
with sim time, step count, island states, AGV state, material count, and the
last 50 events — for post-mortem analysis.

**Log level usage**:
- `DEBUG`: Per-step position updates, state transitions
- `INFO`: Material spawn/arrival, AGV task start/complete, assembly events
- `WARNING`: Beat deviation, connection retry, queue near-full
- `ERROR`: Timeout, deadlock, connection loss, recovery failure

## Configuration Centralization

All tunable parameters live in [src/reference/factory_config.py](../../../src/reference/factory_config.py),
never hardcoded. Key groups:

```python
# Simulation
SIM_STEP = 0.05; CONVEYOR_SPEED = 0.5; AGV_SPEED = 0.8
MATERIAL_INTERVAL = 5.0      # warehouse spawn cadence (s)
TOTAL_SIM_TIME = 60.0        # 0 = run until stop
PROCESS_TIME = 1.5          # island processing time (s)

# Beat
TAKT_TIME = {'A': 5.0, 'B': 3.0, 'C': 4.0, 'D': 6.0, 'E': 8.0}

# Exception thresholds
ZMQ_TIMEOUT = 3.0; ZMQ_MAX_RETRIES = 3
CONGESTION_TIMEOUT = 5.0
TAKT_DEVIATION_THRESHOLD = 1.0
TAKT_TIMEOUT_FACTOR = 1.5
D_E_STARVATION_THRESHOLD = 15.0
MAX_QUEUE_LENGTH = 5

# Upstream dependency map (for timeout/overflow supply gating)
UPSTREAM_MAP = {"A": ["S1"], "B": ["S1"], "C": ["A"], "D": ["S2"], "E": ["C", "D"]}
```

Changing layout/size/model keys needs `build_scene`; changing timings or
thresholds just needs re-running the simulation.
