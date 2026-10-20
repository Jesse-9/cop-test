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

Guide for building and controlling industrial-grade factory simulations in
CoppeliaSim 4.10+ with Python ZeroMQ Remote API control. Focuses on the
**island-style workshop architecture** used by the real `cop-test` project:
  The current approved reconstruction uses staged unit-to-island delivery:
  built-in generic conveyor belts in `HYBRID_DYNAMIC`, six-axis UR5 arms with
  `simIK` and suction pads, a physical pioneer p3dx AGV, and UID-traceable
  exception handling. The older five-island topology remains a reference
  layout only until the staged implementation is accepted.

> This skill mirrors the authoritative wiki and source. When details here and
> the repo disagree, the repo (CLAUDE.md / `src/` / `docs/wiki/`) wins. See
> [06-02-岛式车间柔性生产案例.md](../../../docs/wiki/06-工业案例/06-02-岛式车间柔性生产案例.md)
> and [06-03-src模块参考.md](../../../docs/wiki/06-工业案例/06-03-src模块参考.md).

## When to Read Reference Files

This SKILL.md covers the architecture and core workflow. Read the detailed
references when you need specifics:

| Reference | When to Read |
|-----------|-------------|
| [references/scene-building.md](references/scene-building.md) | Placing 3D objects, **underscore aliases (not slash paths)**, built-in model loading, script stripping, idempotent clear |
| [references/control-framework.md](references/control-framework.md) | ZMQ connection + reconnect, stepping mode, **alias-leaf handle mapping**, beat dictionary, logger |
| [references/scheduling.md](references/scheduling.md) | AGV state machine, priority queue, starvation escalation, island state machine, material lifecycle |
| [references/exception-handling.md](references/exception-handling.md) | ZMQ reconnect, congestion/timeout/overflow, upstream supply gating, emergency stop |
| [references/workflow.md](references/workflow.md) | The change workflow: **plan → execute → review → update docs → commit** (code, then docs) |

## Architecture Overview

The system follows a **three-tier architecture plus an MCP access layer**:

```
┌─────────────────────────────────────────────────────┐
│  MCP Tool Layer  — factory_mcp_server.py            │  ← Claude Code 接入点
│  (FastMCP / stdio, 24 个工具, 默认 stage1 后端, 仿真跑在守护线程) │
└───────────────┬─────────────────────────────────────┘
                │ 调用
┌───────────────▼─────────────────────────────────────┐
│  Python Control Layer                              │
│  factory_controller.py  — FactoryController (编排)   │
│  factory_island.py      — 工作岛/节拍追踪/供料门控   │
│  factory_agv.py         — 共享 AGV 柔性调度器        │
│  factory_exceptions.py  — 统一异常处理              │
│  stage1/ur5_ik_controller.py — UR5 simIK + joint targets │
│  stage1/suction_controller.py — TCP/sensor attach-release │
│  stage1/pioneer_controller.py — p3dx wheel control       │
│  factory_types.py       — 枚举/数据类/物料           │
└───────────────┬─────────────────────────────────────┘
                │ ZMQ Remote API (同步, stepping 模式)
┌───────────────▼─────────────────────────────────────┐
│  ZeroMQ Remote API Layer — factory_core.py          │
│  CoppeliaConnector / ZMQMonitor / setup_logger      │
└───────────────┬─────────────────────────────────────┘
                │ TCP localhost:23000
        ┌────────▼────────┐
        │  CoppeliaSim 4.10+ │  (3D 物理引擎 + 内置模型)
        └──────────────────┘
```

**Clock master**: The Python side is always the clock master. After
`sim.setStepping(True)`, each `sim.step()` advances one `SIM_STEP`
(default 0.05s), so control logic and the physics engine stay strictly
synchronized — the foundation for precise industrial beat timing.

## Island Workshop Topology

The standard topology (matches [factory_config.py](../../../src/reference/factory_config.py)):
five work islands A–E, two warehouses S1/S2, four conveyors, one shared AGV.

```
Material flow:
  S1 ──Conveyor_S1_to_A──→ A ──A→C刚性──→ C ──Conveyor_C_to_E──→ E
  S1 ──Conveyor_S1_to_B──→ B ──AGV B→A (高优先)──→ A              ↘
  S2 ──Conveyor_S2_to_D──→ D ──AGV D→E (低优先,饥饿升级)────────→ E

Flexible paths:
  B → A: Cross-island replenishment, AGV dynamic routing (priority 0)
  D → E: Custom/rework parts, AGV (priority 1, escalatable)

Assembly at E (pair-matched gating):
  SEMI_CE (from C) + PART_DE (from D) → FINAL product
```

## Mandatory Naming Convention (4.10+ — UNDERSCORE, NOT SLASH)

> ⚠ This is the single most important 4.10+ rule, and the most common
> mistake. See the wiki's "4.10 别名路径陷阱":
> [docs/wiki/02-仿真建模/02-01-分层命名场景构建.md](../../../docs/wiki/02-仿真建模/02-01-分层命名场景构建.md).

In CoppeliaSim 4.10+, `setObjectAlias` **converts any `/` in an alias to an
underscore** when storing it. So `setObjectAlias(h, "Conveyor/S1_to_A")` is
stored as `_Conveyor_S1_to_A`, and subsequent `getObject("Conveyor/S1_to_A")`
or `getObject("/Conveyor/S1_to_A")` queries **fail**.

**Rule**: All aliases are flat underscore names. Slash paths are only a
conceptual grouping aid and must never be passed to `getObject`.

```
Warehouse_S1_Main            — Primary raw material storage
Warehouse_S2_Auxiliary       — Secondary/auxiliary material storage

Conveyor_S1_to_A              — Conveyor from S1 to Island A
Conveyor_S1_to_B              — Conveyor from S1 to Island B
Conveyor_S2_to_D              — Conveyor from S2 to Island D
Conveyor_C_to_E               — Conveyor from Island C to E

Island_A_Assembly             — Assembly/processing island A
Island_A_Assembly_Robot_A     — UR5 arm in Island A
Island_B_Preprocess           — Preprocessing island B
Island_B_Preprocess_Robot_B  — UR5 arm in Island B
Island_C_Processing          — Processing island C
Island_C_Processing_Robot_C  — UR5 arm in Island C
Island_D_QC_Assembly         — QC/Assembly island D
Island_D_QC_Assembly_Robot_D — UR5 arm in Island D
Island_E_FinalAssembly       — Final assembly line E
Island_E_FinalAssembly_Robot_E_C — E-line arm for the C stream (left)
Island_E_FinalAssembly_Robot_E_D — E-line arm for the D stream (right)

Mobile_AGV_Shared            — Single shared AGV
```

**Handle lookup** is done by **alias-leaf matching** (enumerate objects, compare
the final path segment), never by slash-path `getObject`. See
[references/control-framework.md](references/control-framework.md) §Handle Mapping.

## Robot Arm Policy: Unified UR5 (6-axis) + simIK + suction pad

All six island arms are **UR5 (6-axis)** — never mix in KUKA iiwa or Franka.

- Mixed UR5/iiwa/Franka in 4.10 triggers a startup freeze ~3 frames in
  (`state=8`, simTime stuck at 0.15s) with
  `"abort execution Connectivity >> ZMQ remote API server@addOnScript"`.
  Root cause: each built-in model ships its own Lua customization/child
  scripts; multiple script objects contend on the 4.10 ZMQ addOn layer.
- iiwa/Franka are 7-axis and violate the six-joint UR5 IK contract.
- `build_scene._strip_model_scripts()` strips each loaded model's script
  sub-objects (matched by `object_script_type` + alias containing "script")
  right after `sim.loadModel()`. Production motion is driven by `simIK`-solved
  joint targets and a calibrated `SuctionPad_*_TCP`; keyframe animation is
  diagnostic-only and cannot report a completed transfer.

Each UR5 has its own IK environment/group and suction-pad contact sensor.
The TCP dummy is the IK tip and the center of the contact face. A sensor hit
must be matched to the active material UID/handle and allowed type before
attachment. Release restores the material's saved dynamic/respondable and
collision properties.

If a full scene still freezes: `stop_simulation` → `inspect_scene_state` to
find residual scripts → `step_simulation` frame-by-frame to locate the
blocking script, or `list_objects filter="*cript*"`.

## Production Workflow State Machine

The production lifecycle has three phases. Each island and transport unit
maintains its own sub-state-machine within these phases:

### Phase 1: Supply
- S1 produces raw materials, splits to Island A (raw_A) and Island B (raw_B)
- S2 produces auxiliary parts to Island D (raw_D)
- Supply cadence governed by `MATERIAL_INTERVAL` (default 5.0s) and gated by
  `SupplyController` per downstream island state
- If a downstream island is in TIMEOUT or queue full, upstream supply pauses

### Phase 2: Flexible Transfer (Island-to-Island)
- **B → A (flexible, AGV)**: `Mobile_AGV_Shared` dynamically dispatched,
  higher base priority (priority=0)
- **A → C (rigid)**: Fixed transfer, not via AGV (handled inline by the
  controller's `_handle_ac_transfer`)
- AGV handles route planning with collision avoidance along waypoints

### Phase 3: Main Line + Special Path Merge
- **C → E (main line)**: Continuous conveyor flow (`Conveyor_C_to_E`), produces
  `SEMI_CE`
- **D → E (special path)**: AGV `D_TO_E`, lower base priority; only when the
  AGV is IDLE. Produces `PART_DE`
- E pair-matches `SEMI_CE` + `PART_DE` → spawns `FINAL` (gold)

## Single-Step Simulation Loop Order (restructured FactoryController.run)

Each `SIM_STEP` executes this physical workflow order (see
[CLAUDE.md](../../../CLAUDE.md) and
[references/control-framework.md](references/control-framework.md)):

1. Poll communication, collision, workstation and sensor faults.
2. If communication is paused, issue no new commands and advance only physics.
3. Read conveyor, pick, suction and AGV dock sensors; verify material UID.
4. Advance `HYBRID_DYNAMIC` conveyor/material state with bounded tangential
   compensation and log every compensation.
5. Update workstation beats, queues and resource locks.
6. Solve UR5 targets with `simIK`, validate TCP error, limits, attitude,
   collision state and speed, then write six joint targets.
7. Attach/lift/place/release through the suction controller and restore saved
   material physics properties.
8. Set pioneer p3dx left/right wheel target speeds; require stable dock arrival.
9. Process handoffs, assembly, UID lifecycle logs and diagnostics.
10. `sim.step()` advances the physics engine.

## AGV State Machine

The single shared AGV operates with these states (`AGVState` in
[factory_types.py](../../../src/reference/factory_types.py)):

```
                    ┌──────────┐
                    │   IDLE    │
                    └────┬─────┘
                         │ task request (queue head)
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

**Priority rule (hybrid strategy)**:
1. B→A has higher base priority (priority=0) than D→E (priority=1) — B→A is
   the production critical path
2. If a D→E job waits longer than `D_E_STARVATION_THRESHOLD` (default 15.0s),
   it escalates to priority=0 (equal with B→A)
3. **Preemption is removed in the current implementation.** The `AGVTask`
   docstring still labels D→E "preemptable" — this is a stale contract, not
   live behavior (known defect documented in
   [docs/wiki/06-工业案例/06-03-src模块参考.md](../../../docs/wiki/06-工业案例/06-03-src模块参考.md)).
   Do not write code that relies on in-flight preemption.

## Industrial Beat Alignment

The Python controller maintains a global beat dictionary
([factory_config.py](../../../src/reference/factory_config.py)):

```python
TAKT_TIME = {
    'A': 5.0,   # Assembly Island A
    'B': 3.0,   # Preprocess Island B
    'C': 4.0,   # Processing Island C
    'D': 6.0,   # QC/Assembly Island D
    'E': 8.0,   # Final Assembly Line E
}
```

**Beat tracking loop** (`BeatTracker` in [factory_island.py](../../../src/reference/factory_island.py)):
1. Use `sim.getSimulationTime()` to measure actual processing duration
2. Compare against `TAKT_TIME[island]`
3. If |deviation| > `TAKT_DEVIATION_THRESHOLD` (default 1.0s) → WARNING
4. If actual time > `TAKT_TIME[island] * 1.5` (`TAKT_TIMEOUT_FACTOR`) → TIMEOUT
5. TIMEOUT → auto-degrade: extend that island's beat by 50%, pause upstream
   supply (`SupplyController.pause_all_upstream`)
6. After 3 consecutive normal cycles → auto-restore original beat and resume
   upstream supply

## Exception Handling Strategy

Four categories of exceptions, each with defined recovery (see
[references/exception-handling.md](references/exception-handling.md)):

### 1. Communication Exception (ZMQ)
- **Detect**: `ZMQMonitor.check()` polls `CoppeliaConnector.check_alive()`;
  `ZMQ_TIMEOUT` (3.0s) no-response window, `ZMQ_MAX_RETRIES` (3) retries
- **Recover**: Auto-reconnect attempt, pause new commands (`comm_paused`)
- **Escalate**: 3 failed reconnects → `poll()` returns False → emergency stop

### 2. AGV Congestion / Deadlock
- **Detect**: AGV target path occupied > `CONGESTION_TIMEOUT` (5.0s)
- **Recover**: AGV → BLOCKED state; single shared AGV eliminates multi-AGV
  deadlock, so recovery is path-clear-then-resume
- **Log**: Record congestion event

### 3. Workstation Timeout
- **Detect**: Processing time exceeds `TAKT_TIME * TAKT_TIMEOUT_FACTOR` (1.5)
- **Recover**: Island → TIMEOUT; `SupplyController` pauses all upstream
  sources (per `UPSTREAM_MAP`)
- **Auto-restore**: 3 consecutive normal cycles → resume upstream supply

### 4. Material Overflow
- **Detect**: Island queue length ≥ `MAX_QUEUE_LENGTH` (5)
- **Recover**: Pause upstream supply for that island
- **Resume**: When queue drops to ≤ `MAX_QUEUE_LENGTH // 2`

## MCP Integration (Claude Code access point)

[factory_mcp_server.py](../../../src/reference/factory_mcp_server.py) is registered via
[.mcp.json](../../../.mcp.json); Claude Code auto-loads it in this project. It exposes
**24 tools** over stdio (FastMCP), with the simulation running in a daemon
thread. Two ZMQ connections: the main connection (write/control tools + main
loop) and `ro_connector` (read-only diagnostics, guarded by `ro_lock`).

**Default backend is `stage1`** (the current unit-cell implementation).
`build_scene`, `start_simulation`, `get_config`, and `set_config` accept a
`backend` argument (`stage1` default, `legacy` for the five-island reference);
`start_simulation` also accepts `target` (`station`/`agv`/`both`) and `fault`.
Since step 1.2 (2026-10-21), stage1 `start_simulation` does **not** rebuild:
it starts only the currently loaded and validated scene/artifact. Use
`build_scene` (explicit) or `load_scene` + `validate_scene` first.
`save_scene` / `load_scene` / `validate_scene` / `lifecycle_status` manage
versioned scene artifacts under `docs/scene_artifacts/` (stage1 baseline
`stage1/L1_S1/v002_20261020_234403` is protected/read-only).
The stage1 backend writes results to
`docs/implementation/runs/phase1_mcp_<target>_<fault>_<stamp>.json`.
For stage1, `pause_simulation` also stops the runner from touching ZMQ so
`step_simulation` can advance frames safely.

| Tier | Tools |
|------|-------|
| Connect/Scene/Lifecycle/Config (15) | `connect`, `build_scene`, `save_scene`, `load_scene`, `validate_scene`, `lifecycle_status`, `disconnect`, `start_simulation`, `stop_simulation`, `step_simulation(n=1, ≤1000)`, `status`, `get_config`, `set_config`, `tail_log`, `tail_coppelia_console(lines, offset)` |
| A — Read (4, via `ro_connector`) | `get_object_pose`, `list_objects`, `read_signal`, `get_object_tree` |
| B — Diagnose (3) | `inspect_scene_state`, `get_simulation_state`, `run_lua` |
| C — Control (2) | `pause_simulation`, `resume_simulation` |

**Scene lifecycle semantics**: `build_scene` → `save_scene` → `load_scene`
→ `validate_scene` → `start_simulation` → `stop_simulation`. `start` refuses
to run without a validated scene; while running, build/load/save/validate
and a second start are rejected by the state machine and the cross-process
run lock (`docs/scene_artifacts/.runtime/run.lock`).

> `tail_coppelia_console` reads CoppeliaSim's **own** console output (script
> `print` / `sim.addLog`, plugin & add-on errors, the last message before a
> freeze). That log only exists when CoppeliaSim is launched under the ConPTY
> relay ([tools/conpty_relay.py](../../../tools/conpty_relay.py)) — plain
> stdout redirection is 4 KB block-buffered and loses small/dying messages.
> Use `offset` for incremental follow-up reads; `tail_log` remains the
> Python-control-layer log.
>
> The relay strips `PYTHONHOME` / `UV_INTERNAL__PYTHONHOME` before spawning
> CoppeliaSim: uv-managed venvs export them, and an unrelated `defaultPython`
> (e.g. Anaconda 3.12) then loads the uv 3.11 stdlib and dies with
> `SRE module mismatch` → `sandboxScript:error`. Never launch the simulator
> from a uv shell without that sanitization.

> When the simulation is running, `status` over the main connection may
> transiently report `connected:false` (the connection is busy with the main
> loop). This is normal; read-only diagnostics over `ro_connector` are
> unaffected — that is the whole point of the dual-connection design.
>
> Prefer driving the existing MCP tools over hand-writing ZMQ control code.
> Full usage (incl. `run_lua` execution model, freeze-diagnosis workflow,
> test suite): [docs/wiki/98-ProjectUsage/MCP_USAGE.md](../../../docs/wiki/98-ProjectUsage/MCP_USAGE.md).

## Change Workflow (plan → execute → review → update docs → commit)

Any non-trivial change to this simulation follows a fixed five-phase loop so
that **code, wiki, and this skill stay aligned** with a traceable history.
Read [references/workflow.md](references/workflow.md) for the full template,
review checklist, doc-sync table, and commit conventions; the summary:

1. **Plan** — goal, files in scope, out-of-scope, 4.10 gotcha risks, test plan.
   Use `EnterPlanMode` for multi-file/behavior-changing work; a verbal plan
   is fine for small mechanical tweaks.
2. **Execute** — implement per the skill's conventions (underscore aliases,
    UR5-only arms, script stripping, config-centralized tuning, and the
    sensor → HYBRID_DYNAMIC → simIK/suction → wheel-control workflow. Run
    verification as you go.
3. **Review** — verify behavior end-to-end (no `state=8` freeze, no `0 joints`,
   no exception spam); self-read the diff; run `/code-review` or `/simplify`
   on the uncommitted diff. Do not commit yet.
4. **Update docs** — walk all three truth-sources (wiki 06-02/06-03/02-01,
    MCP_USAGE, CLAUDE.md, this skill, and 06-04/06-05) and update each the
    change affects.
   Bump frontmatter `updated:` dates; keep the tool-count and known-pitfall
   claims consistent everywhere.
5. **Commit** — two scoped commits, **code first then docs**:
   - `<type>(<scope>): …` for `src/` (types: `feat`/`fix`/`refactor`/…)
   - `docs(<scope>): …` for `docs/wiki/` + `CLAUDE.md` + `.claude/skills/`
   Stage explicitly (never `git add -A` — the working tree often holds
   unrelated in-progress files). Branch off `main` first unless told
   otherwise; report both commit hashes; don't push unless asked.

**Anti-patterns**: one mega commit mixing code+docs; `git add -A`; updating
code but leaving wiki/skill stale; re-asserting a removed feature (e.g.
"preemptable") as live guidance; committing to `main` unbidden; skipping
review on "small" changes (that's where slash-path `getObject` sneaks back).

## Implementation Sequence

When building or refactoring a factory simulation, follow this order:

### Step 1: Scene Setup
Read [references/scene-building.md](references/scene-building.md). Place all
objects with **underscore aliases**, establish the hierarchy-root dummies
(`Warehouse`/`Conveyor`/`Island`/`Mobile`/`Part`), load built-in models with
`sim.loadModel()`, strip their scripts, and reparent/alias to fit the
 convention. Production builds must fail if a required built-in model is
 missing; primitive fallback is diagnostic-only.

### Step 2: ZMQ Bridge Initialization
Read [references/control-framework.md](references/control-framework.md).
Build the `CoppeliaConnector` (with `_read_version` fallback for the
4.10 `coppeliaSimVersion`-may-be-None quirk), the `ZMQMonitor`, the
alias-leaf handle map, the `setup_logger`, and the beat dictionary.

### Step 3: Core Scheduling Loop
Read [references/scheduling.md](references/scheduling.md). Implement
`FactoryController.run()`'s physical workflow: sensor polling,
`HYBRID_DYNAMIC` conveyor/material state, `simIK` + suction actions,
pioneer wheel control, handoffs, and UID logging. Keep
`IslandController` + `BeatTracker` + `SupplyController` + `ExceptionHandler`
as supporting services.

### Step 4: Integration Testing
Run end-to-end. Inject anomalies (pause an island, block an AGV path, drop
the ZMQ connection) to verify detection, logging, and recovery without
crashing. Validate that all handles are acquired via alias-leaf matching
with no slash-path queries.

## Validation Checklist

Before declaring the system complete, verify:

- [ ] Python client connects to CoppeliaSim 4.10, supports start/step/pause
- [ ] All aliases are underscore names; handles acquired via alias-leaf
      matching — **no** slash-path `getObject` queries
- [ ] All island arms are UR5 (6-axis); model scripts stripped after load;
      no startup freeze (`state=8` does not appear ~3 frames in)
- [ ] Flexible production logic works: AGV serves B→A (priority 0) and D→E
      (priority 1, escalates after 15s starvation); no logical deadlocks
- [ ] Industrial beat is controllable: processing times match `TAKT_TIME`,
      beat deviation is monitored, timeout auto-degrades + pauses upstream,
      3 normal cycles auto-restore
- [ ] Exception handling is robust: simulated communication loss, material
      congestion, workstation timeout, and material overflow all trigger
      correct detection, logging, and safe recovery (not crash)
- [ ] MCP 24 tools work (stage1 default, legacy optional); dual-connection keeps read-only diagnostics stable
      while the main loop runs
