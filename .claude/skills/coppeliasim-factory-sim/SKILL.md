---
name: coppeliasim-factory-sim
description: >
  Build and control CoppeliaSim 4.10+ island-style flexible production simulation systems
  using Python ZeroMQ Remote API. Use this skill whenever the user mentions CoppeliaSim,
  factory simulation, 工厂仿真, island workshop, 岛式车间, ZMQ Remote API, zmqRemoteApi,
  flexible manufacturing, 柔性生产, AGV scheduling, industrial simulation, 工业仿真,
  production line control, 生产线控制, material flow, or wants to build/modify/control
  a 3D factory scene with robots, conveyors, and AGVs. Also use when the user talks about
  digital twins, 数字孪生, or manufacturing digitalization involving real-time simulation
  control, even if they don't explicitly ask for a factory simulation skill.
compatibility:
  required_tools: ["Bash", "Read", "Write", "Edit", "Glob", "Grep"]
  required_packages: ["coppeliasim-zmqremoteapi-client>=2.0.4"]
  required_software: ["CoppeliaSim >= 4.10"]
---

# CoppeliaSim Island-Style Flexible Production Simulation

Guide for building industrial-grade factory simulations in CoppeliaSim 4.10+ with
Python ZeroMQ Remote API control. Focuses on island-style workshop architecture
with flexible production scheduling, industrial beat alignment, and robust
exception handling.

## When to Read Reference Files

This SKILL.md covers the architecture and core workflow. Read the detailed
references when you need specifics:

| Reference | When to Read |
|-----------|-------------|
| [references/scene-building.md](references/scene-building.md) | Creating/placing 3D objects, hierarchical naming, sensors, collision setup |
| [references/control-framework.md](references/control-framework.md) | ZMQ connection, stepping mode, handle mapping, beat dictionary |
| [references/scheduling.md](references/scheduling.md) | AGV state machine, flexible routing, priority preemption, deadlock prevention |
| [references/exception-handling.md](references/exception-handling.md) | ZMQ reconnect, congestion detection, workstation timeout, logging |

## Architecture Overview

The system follows a **three-tier architecture**:

```
┌─────────────────────────────────────────────────┐
│  Python Control Layer (this codebase)            │
│  - SimulationLoop (time-driven stepping)         │
│  - AGV_Scheduler (flexible routing + priority)   │
│  - Exception_Handler (polling + recovery)        │
│  - BeatTracker (TAKT_TIME validation)            │
├─────────────────────────────────────────────────┤
│  ZeroMQ Remote API (synchronous, stepping mode)  │
│  sim.setStepping(True) + sim.step()              │
├─────────────────────────────────────────────────┤
│  CoppeliaSim 4.10 Scene                          │
│  - Island workshop layout                        │
│  - Hierarchical object paths                     │
│  - Sensors, collision bodies                     │
└─────────────────────────────────────────────────┘
```

**Core principle**: The Python side is always the clock master. Every simulation
step is triggered by `sim.step()`, ensuring the control logic stays strictly
synchronized with the physics engine. This is what makes precise industrial beat
timing possible.

## Island Workshop Topology

The standard topology matches the user's spec — a flexible production line with
five work islands, two warehouses, conveyors, and shared AGVs:

```
Material flow:
  S1 ──conv_S1A──→ A ──agv_AC──→ C ──conv_CE──→ E
  S1 ──conv_S1B──→ B ──agv_BA──→ A              ↗
  S2 ──conv_S2D──→ D ──agv_DE──────────────→  E

Flexible paths:
  B → A: Cross-island replenishment, AGV dynamic routing
  D → E: Custom/rework parts, AGV with priority constraints

Assembly at E:
  SEMI_CE (from C) + PART_DE (from D) → FINAL product
```

## Mandatory Naming Convention

All scene objects MUST use hierarchical slash-separated paths. This is not
optional — it prevents namespace pollution, enables wildcard queries, and
makes the scene tree navigable in CoppeliaSim's browser.

```
/Warehouse/S1_Main            — Primary raw material storage
/Warehouse/S2_Auxiliary       — Secondary/auxiliary material storage

/Conveyor/S1_to_A             — Conveyor from S1 to Island A
/Conveyor/S1_to_B             — Conveyor from S1 to Island B
/Conveyor/S2_to_D             — Conveyor from S2 to Island D
/Conveyor/C_to_E              — Conveyor from Island C to E

/Island/A_Assembly            — Assembly/processing island A
/Island/A_Assembly/Robot_A    — Robot arm in Island A
/Island/B_Preprocess          — Preprocessing island B
/Island/B_Preprocess/Fixture_B — Cyclic fixture in Island B
/Island/C_Processing          — Processing island C
/Island/C_Processing/Machine_C — Machine module in Island C
/Island/D_QC_Assembly         — QC/Assembly island D
/Island/D_QC_Assembly/Robot_D — Robot arm in Island D
/Island/E_FinalAssembly       — Final assembly line E
/Island/E_FinalAssembly/Line_E — Multi-device assembly line

/Mobile/AGV_Shared            — Shared AGV for flexible transport

/Part/Raw                     — Raw material tag
/Part/Semi_A                  — Semi-finished from A
/Part/Semi_C                  — Semi-finished from C
/Part/Aux_D                   — Auxiliary part from D
```

## Production Workflow State Machine

The production lifecycle has three phases. Each island and transport unit
maintains its own sub-state-machine within these phases:

### Phase 1: Supply
- S1 continuously produces raw materials, splits to Island A and Island B
- S2 continuously produces auxiliary materials to Island D
- Supply cadence governed by `TAKT_TIME` per island
- If a downstream island is in TIMEOUT state, upstream supply pauses

### Phase 2: Flexible Transfer (Island-to-Island)
- **B → A (flexible)**: AGV_Shared dynamically dispatched. Higher priority.
- **A → C (rigid)**: Fixed conveyor or dedicated robot arm transfer
- AGV handles route planning with collision avoidance

### Phase 3: Main Line + Special Path Merge
- **C → E (main line)**: Continuous conveyor flow
- **D → E (special path)**: AGV_Shared, only when IDLE and C→E is not blocked

## AGV State Machine

The shared AGV operates with these states:

```
                    ┌──────────┐
                    │   IDLE   │
                    └────┬─────┘
                         │ task request
                    ┌────▼─────┐
              ┌─────│ MOVING_  │─────┐
              │     │ B_TO_A   │     │ (preempted)
              │     └────┬─────┘     │
              │          │ arrived   │
              │     ┌────▼─────┐     │
              │     │ MOVING_  │     │
              │     │ D_TO_E   │     │
              │     └────┬─────┘     │
              │          │           │
         ┌────▼──────────▼───────────▼────┐
         │           BLOCKED              │
         │  (path occupied > 5s timeout)  │
         └────────────┬──────────────────┘
                      │ path cleared / replan
                 ┌────▼─────┐
                 │   IDLE    │  (recovery)
                 └──────────┘
```

**Priority rule (hybrid strategy)**:
1. B→A has higher base priority than D→E (production critical path)
2. If D→E waits longer than `D_E_STARVATION_THRESHOLD` (default 15s),
   it escalates to equal priority with B→A
3. Preemption: ongoing D→E tasks CAN be preempted by new B→A requests;
   B→A tasks CANNOT be preempted

## Industrial Beat Alignment

The Python controller maintains a global beat dictionary:

```python
TAKT_TIME = {
    'A': 5.0,   # Assembly Island A processing time (seconds)
    'B': 3.0,   # Preprocess Island B
    'C': 4.0,   # Processing Island C
    'D': 6.0,   # QC/Assembly Island D
    'E': 8.0,   # Final Assembly Line E
}
```

**Beat tracking loop**:
1. Use `sim.getSimulationTime()` to measure actual processing duration
2. Compare against `TAKT_TIME[island]`
3. If deviation > `TAKT_DEVIATION_THRESHOLD` (default 1.0s), trigger WARNING
4. If actual time > `TAKT_TIME[island] * 1.5`, trigger TIMEOUT state
5. TIMEOUT → auto-degrade: extend beat for that island by 50%, pause upstream
6. After 3 consecutive normal cycles, auto-restore original beat

## Exception Handling Strategy

Four categories of exceptions, each with defined recovery:

### 1. Communication Exception (ZMQ)
- **Detect**: 3-second no-response timeout on any API call
- **Recover**: Auto-reconnect attempt (max 3 retries), pause new commands
- **Escalate**: If reconnect fails 3 times, stop simulation gracefully with log

### 2. Logistics Congestion / Deadlock
- **Detect**: AGV target path occupied for > 5 seconds
- **Recover**: Set AGV to BLOCKED state, attempt path re-planning
- **Fallback**: Degrade to waiting mode, log event for analysis

### 3. Workstation Timeout
- **Detect**: Processing time exceeds `TAKT_TIME * 1.5`
- **Recover**: Set workstation anomaly flag, pause upstream supply
- **Auto-restore**: After 3 consecutive normal cycles, clear anomaly flag

### 4. Material Overflow
- **Detect**: Island queue length exceeds `MAX_QUEUE_LENGTH` (default 5)
- **Recover**: Throttle or pause upstream supply for that island
- **Log**: Record overflow event with timestamp and queue state

## Implementation Sequence

When building or refactoring a factory simulation, follow this order:

### Step 1: Scene Setup
Read [references/scene-building.md](references/scene-building.md). Place all
objects in CoppeliaSim with hierarchical names, configure physics properties,
collision bodies, and sensors (photoelectric switches for material detection).

**Prefer built-in models over primitive shapes** where available. CoppeliaSim
ships with industrial robots, conveyors, mobile platforms, and sensors under
`C:\Program Files\CoppeliaRobotics\CoppeliaSimEdu\models\`. Use `sim.loadModel()`
to load them, then reparent and alias to fit the workshop naming convention.
See the "Using CoppeliaSim Built-in Models" section in the scene-building
reference for exact model paths and loading patterns.

### Step 2: ZMQ Bridge Initialization
Read [references/control-framework.md](references/control-framework.md).
Write the Lua-side init script in CoppeliaSim to expose core control functions
(`startProcess()`, `getSensorState()`, `moveAGV()`). Initialize the Python
RemoteAPIClient, build handle maps, and set up the beat dictionary and logger.

### Step 3: Core Scheduling Loop
Read [references/scheduling.md](references/scheduling.md). Implement the
time-driven `SimulationLoop`, the `AGV_Scheduler` for flexible routing with
conflict avoidance, and the `Exception_Handler` for polling timeout and
collision flags.

### Step 4: Integration Testing
Run the simulation end-to-end. Inject artificial anomalies (pause an island,
block an AGV path) to verify the system detects, logs, and recovers without
crashing. Validate that all handles are acquired through canonical paths with
no hierarchy pollution.

## Validation Checklist

Before declaring the system complete, verify:

- [ ] Python client connects to CoppeliaSim 4.10, supports start/step/pause
- [ ] All islands and devices acquired via canonical paths, no naming pollution
- [ ] Flexible production logic works: AGV correctly responds to B→A priority
      requests and D→E special transport when idle, no logical deadlocks
- [ ] Industrial beat is controllable: processing times match TAKT_TIME dict,
      beat deviation is monitored and logged
- [ ] Exception handling is robust: simulated communication loss, material
      congestion, and workstation timeout all trigger correct detection,
      logging, and safe recovery (not crash)
