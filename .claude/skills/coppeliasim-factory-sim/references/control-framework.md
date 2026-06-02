# Python Control Framework Reference

## ZMQ Connection with Auto-Reconnect

The connection to CoppeliaSim must be robust. Wrap the RemoteAPIClient in a
retry-capable connector:

```python
import time
import sys
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

class CoppeliaConnector:
    """ZMQ connection manager with auto-reconnect capability."""

    def __init__(self, host: str = "localhost", port: int = 23000,
                 max_retries: int = 3, timeout: float = 3.0):
        self.host = host
        self.port = port
        self.max_retries = max_retries
        self.timeout = timeout
        self.client = None
        self.sim = None

    def connect(self) -> bool:
        """Establish connection with retry logic."""
        for attempt in range(1, self.max_retries + 1):
            try:
                self.client = RemoteAPIClient(host=self.host, port=self.port)
                self.sim = self.client.require("sim")
                # Verify connection is alive
                version = self.sim.getStringSignal("coppeliaSimVersion")
                print(f"[OK] Connected to CoppeliaSim {version} "
                      f"(attempt {attempt})")
                return True
            except Exception as e:
                print(f"[WARN] Connection attempt {attempt}/{self.max_retries} "
                      f"failed: {e}")
                if attempt < self.max_retries:
                    time.sleep(1.0 * attempt)  # Exponential backoff
        print(f"[FATAL] Cannot connect to CoppeliaSim after "
              f"{self.max_retries} attempts")
        return False

    def check_alive(self) -> bool:
        """Quick health check. Returns False if connection is dead."""
        try:
            self.sim.getStringSignal("coppeliaSimVersion")
            return True
        except Exception:
            return False

    def reconnect_if_needed(self) -> bool:
        """Attempt reconnect if connection is dead."""
        if self.check_alive():
            return True
        print("[WARN] ZMQ connection lost, attempting reconnect...")
        return self.connect()
```

## Stepping Mode: The Clock Master Pattern

The Python side MUST be the clock master. This is the foundation for all
beat timing and exception handling:

```python
class SimulationLoop:
    """Time-driven simulation loop with stepping mode control."""

    def __init__(self, connector: CoppeliaConnector,
                 takt_time: dict, sim_step: float = 0.05):
        self.conn = connector
        self.sim = connector.sim
        self.takt_time = takt_time
        self.sim_step = sim_step
        self.sim_time = 0.0
        self.step_count = 0
        self.running = False

    def start(self):
        """Initialize stepping mode and start simulation."""
        self.sim.setStepping(True)
        self.sim.startSimulation()
        self.running = True
        print(f"[INFO] Stepping mode enabled, dt={self.sim_step}s")

    def step(self) -> float:
        """Execute one simulation step. Returns current simulation time."""
        self.sim.step()
        self.sim_time = self.sim.getSimulationTime()
        self.step_count += 1
        return self.sim_time

    def stop(self):
        """Gracefully stop simulation."""
        self.sim.stopSimulation()
        self.running = False
```

**Why stepping mode matters**: Without it, the simulation runs on CoppeliaSim's
internal clock, decoupled from your control logic. Beat timing becomes
unreliable, and exception detection (which depends on wall-clock comparisons)
produces false positives or misses real issues. Stepping mode makes every
simulated second predictable and measurable.

## Device Handle Mapping

Build a complete handle map on startup. This avoids repeated `getObject()`
calls (each is a ZMQ round-trip):

```python
class DeviceHandleMap:
    """Complete mapping of all scene objects by canonical path."""

    def __init__(self, sim):
        self.sim = sim
        self.handles: dict[str, int] = {}

    def populate(self):
        """Acquire handles for all known objects by canonical path."""
        paths = [
            # Warehouses
            "/Warehouse/S1_Main",
            "/Warehouse/S2_Auxiliary",
            # Conveyors
            "/Conveyor/S1_to_A",
            "/Conveyor/S1_to_B",
            "/Conveyor/S2_to_D",
            "/Conveyor/C_to_E",
            # Islands
            "/Island/A_Assembly",
            "/Island/A_Assembly/Robot_A",
            "/Island/B_Preprocess",
            "/Island/B_Preprocess/Fixture_B",
            "/Island/C_Processing",
            "/Island/C_Processing/Machine_C",
            "/Island/D_QC_Assembly",
            "/Island/D_QC_Assembly/Robot_D",
            "/Island/E_FinalAssembly",
            "/Island/E_FinalAssembly/Line_E",
            # AGV
            "/Mobile/AGV_Shared",
        ]
        for path in paths:
            try:
                handle = self.sim.getObject(path)
                self.handles[path] = handle
            except Exception as e:
                print(f"[WARN] Cannot find object '{path}': {e}")

        print(f"[OK] Handle map populated: {len(self.handles)} objects")

    def get(self, path: str) -> int:
        """Get handle by canonical path. Raises KeyError if missing."""
        if path not in self.handles:
            raise KeyError(f"Object '{path}' not in handle map")
        return self.handles[path]
```

## Beat Tracking System

The beat tracker validates that each island's actual processing time matches
the planned TAKT_TIME:

```python
import logging
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class IslandBeatState:
    """Runtime beat tracking for one island."""
    island: str
    takt_target: float          # Target processing time (seconds)
    cycle_start: float = 0.0    # simTime when current cycle began
    cycle_elapsed: float = 0.0  # accumulated processing time
    deviation: float = 0.0      # current deviation from target
    anomaly: bool = False       # True if timeout detected
    normal_cycles: int = 0      # consecutive normal cycles for auto-recovery
    warn_threshold: float = 1.0 # absolute deviation to trigger warning
    timeout_factor: float = 1.5 # multiplier on takt_target for timeout

class BeatTracker:
    """Monitors per-island processing time against TAKT_TIME."""

    def __init__(self, takt_time: dict, logger: logging.Logger):
        self.states: dict[str, IslandBeatState] = {
            name: IslandBeatState(island=name, takt_target=target)
            for name, target in takt_time.items()
        }
        self.logger = logger

    def start_cycle(self, island: str, sim_time: float):
        """Mark the start of a processing cycle for an island."""
        state = self.states.get(island)
        if state:
            state.cycle_start = sim_time
            state.cycle_elapsed = 0.0

    def update(self, island: str, sim_time: float, dt: float):
        """Update elapsed time for a processing island."""
        state = self.states.get(island)
        if state is None or state.cycle_start == 0:
            return
        state.cycle_elapsed += dt
        state.deviation = state.cycle_elapsed - state.takt_target

    def check(self, island: str) -> Optional[str]:
        """Check beat status. Returns 'ok', 'warn', or 'timeout'."""
        state = self.states.get(island)
        if state is None:
            return None

        abs_dev = abs(state.deviation)

        # Timeout check (must happen first — most severe)
        if state.cycle_elapsed > state.takt_target * state.timeout_factor:
            if not state.anomaly:
                state.anomaly = True
                state.normal_cycles = 0
                self.logger.warning(
                    f"[TIMEOUT] Island {island}: "
                    f"{state.cycle_elapsed:.1f}s > "
                    f"{state.takt_target * state.timeout_factor:.1f}s "
                    f"(target: {state.takt_target}s)"
                )
                # Auto-degrade: extend beat by 50%
                old_target = state.takt_target
                state.takt_target *= 1.5
                self.logger.info(
                    f"[DEGRADE] Island {island} beat extended: "
                    f"{old_target:.1f}s → {state.takt_target:.1f}s"
                )
            return "timeout"

        # Warning check
        if abs_dev > state.warn_threshold:
            return "warn"

        # Normal — track consecutive normal cycles for auto-recovery
        if state.anomaly:
            state.normal_cycles += 1
            if state.normal_cycles >= 3:
                # Auto-restore
                state.takt_target /= 1.5
                state.anomaly = False
                state.normal_cycles = 0
                self.logger.info(
                    f"[RESTORE] Island {island} beat restored to "
                    f"{state.takt_target:.1f}s"
                )

        return "ok"
```

## Logger Configuration

Use Python's structured logging for all events. Log levels map to severity:

```python
import logging

def setup_logger(name: str = "factory-sim",
                 log_file: str = "factory_sim.log") -> logging.Logger:
    """Configure the factory simulation logger."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    # File handler — complete record
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(fh)

    # Console handler — INFO and above
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(ch)

    return logger
```

**Log level usage**:
- `DEBUG`: Per-step position updates, state transitions
- `INFO`: Material spawn/arrival, AGV task start/complete, assembly events
- `WARNING`: Beat deviation, connection retry, queue near-full
- `ERROR`: Timeout, deadlock, connection loss, recovery failure

## Configuration Centralization

All tunable parameters belong in a single config module, never hardcoded:

```python
# config keys that MUST be centralized:
# - TAKT_TIME dict (per-island processing targets)
# - All positions, sizes, and colors
# - Conveyor speeds, AGV speeds
# - Material spawn intervals
# - SIM_STEP
# - Exception thresholds:
#   ZMQ_TIMEOUT = 3.0          # seconds, no-response detection
#   CONGESTION_TIMEOUT = 5.0   # seconds, path blocked before BLOCKED
#   TAKT_DEVIATION_THRESHOLD = 1.0  # seconds, beat warning
#   TAKT_TIMEOUT_FACTOR = 1.5  # multiplier for timeout
#   D_E_STARVATION_THRESHOLD = 15.0  # seconds before D→E priority escalation
#   MAX_QUEUE_LENGTH = 5       # items before overflow throttling
```
