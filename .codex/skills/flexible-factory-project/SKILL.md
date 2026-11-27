---
name: flexible-factory-project
description: "Use and maintain the cop-test Flexible Factory v1 project: find authoritative project docs, prepare CoppeliaSim and ZeroMQ, run the CLI and order workflows, operate scene artifacts, run Python/frontend tests, and diagnose common failures. Apply when a task concerns this repository's usage, runtime, tests, artifacts, local API/frontend, catalog, knowledge import, or project-specific engineering rules."
metadata:
  short-description: Operate and maintain the cop-test project
---

# cop-test project

Use this skill for work in this repository (`cop-test`). Treat the repository's authoritative documents as the source of truth and keep commands scoped to the current checkout.

## First orientation

- Start with [README.md](../../../README.md) for the product boundary and quick start.
- Use [USAGE.md](../../../docs/wiki/98-ProjectUsage/USAGE.md) for operational workflows.
- Use [CLI_REFERENCE.md](../../../docs/wiki/98-ProjectUsage/CLI_REFERENCE.md) when an option, default, or subcommand is uncertain.
- Use [CLAUDE.md](../../../CLAUDE.md) for project-specific invariants and routing.
- Use [PROJECT_ENGINEERING_REFERENCE.md](../../../PROJECT_ENGINEERING_REFERENCE.md) for module ownership, runtime invariants, and CoppeliaSim traps.
- Use [docs/README.md](../../../docs/README.md) to route to scene artifacts, implementation evidence, path records, and Neo4j procedures.

When these sources disagree, prefer the current implementation and the command's `--help` output, then update the unique authoritative document if behavior changed.

## Environment and startup

Run commands from the repository root in Windows PowerShell.

```powershell
uv sync
flexible-factory --help
```

For real simulation, start CoppeliaSim 4.10+ with the ZeroMQ Remote API server add-on listening on `localhost:23000`. The project expects the built-in UR5, suction pad, generic conveyor, and Pioneer P3DX models. Keep the simulator running while invoking the CLI. The HTTP API address (`--host/--port` on `api serve`) is separate from the simulator address (`--sim-host/--sim-port`).

## Daily v1 workflow

The protected v1 baseline is selected through `docs/scene_artifacts/v1/current.json`. The normal user scenario is `full_flow`.

```powershell
flexible-factory load
flexible-factory validate
flexible-factory run --scenario full_flow --time 700
flexible-factory status
flexible-factory list
flexible-factory stop
```

`validate` is diagnostic; `run` repeats the required read-only startup checks. Do not start a second control task or run `build`, `load`, `save`, or `validate` while a control task is stepping the simulator. Internal scenarios such as `no_pair`, `agv_priority`, `agv_blocked`, `conveyor_fault`, and `staggered_batch` are regression/diagnostic modes, not normal user modes.

Useful run options:

```powershell
flexible-factory run --scenario full_flow --batch-profile direct_heavy
flexible-factory run --scenario full_flow --batch-profile detour_heavy
flexible-factory run --scenario full_flow --run-records off
```

Reports go to `docs/implementation/runs/`; run records default to `var/catalog/catalog.db`.

## Scene artifacts

Treat scene packages as versioned, hashed, protected artifacts:

- Read `docs/scene_artifacts/v1/current.json` instead of copying a pointer into another document.
- Do not hand-edit `manifest.json`, `validation.json`, lifecycle records, or a protected `scene.ttt`.
- Do not use CoppeliaSim **File > Save Scene** to overwrite a package.
- `save`/`migrate` create a new `v<NNN>_<stamp>` directory; failed migration must not update `current.json`.
- `assembly_mvp` lives under `docs/scene_artifacts/experiments/assembly-mvp/` and is separate from the protected v1 baseline.

Read [scene_artifacts/README.md](../../../docs/scene_artifacts/README.md) before changing artifact roots, profiles, or current pointers.

## Orders, API, and frontend

Orders are declarative, versioned DAGs. Validate and preview before starting; versions are append-only. Order JSON may contain only documented contract fields and registered template/port identities. Do not embed Python/Lua code, CoppeliaSim handles, or unregistered executors.

```powershell
flexible-factory order validate --file <order.json> --db var/catalog/catalog.db
flexible-factory order create --file <order.json> --db var/catalog/catalog.db
flexible-factory order preview --order-id <id> --version 1 --db var/catalog/catalog.db
flexible-factory order start --order-id <id> --version 1 --artifact-id current --time 700 --run-records on
flexible-factory order status --run-id <run_id> --db var/catalog/catalog.db
flexible-factory order nodes --run-id <run_id> --db var/catalog/catalog.db
flexible-factory order events --run-id <run_id> --db var/catalog/catalog.db
flexible-factory order stop --run-id <run_id> --db var/catalog/catalog.db
```

`single` is the default fleet. `dual` requires an artifact whose manifest contains `Cart2` and `Cart2_Tray`; its fixed assignment is `agv_1` for B→A logistics and `cart_2` for D→E logistics. Fleet and path-reuse choices are run settings and do not mutate the immutable order version or DAG digest. Supported path-reuse modes are `off`, `PLAN_ONLY`, `CACHE_ONLY`, and `CACHE_THEN_PLAN`.

Start the local order API and frontend as separate processes:

```powershell
flexible-factory api serve --host 127.0.0.1 --port 8080
cd frontend
npm ci
npm run dev
```

Open `http://127.0.0.1:5173/orders`. The frontend talks to `/api` and does not control CoppeliaSim directly. Keep the API on loopback; the first version has no authentication or TLS. See [frontend/README.md](../../../frontend/README.md) for details.

## Catalog, knowledge, and reports

These are maintenance workflows, not prerequisites for the default code-backed `full_flow` run:

```powershell
flexible-factory catalog init
flexible-factory catalog import --release-id <release_id>
flexible-factory catalog publish --release-id <release_id>
flexible-factory catalog status
flexible-factory catalog diff
flexible-factory catalog verify
flexible-factory report runs
flexible-factory report run --run-id <run_id>
flexible-factory report genealogy --run-id <run_id> --uid <final_uid>
flexible-factory report waits --run-id <run_id> --resource <resource_id>
flexible-factory report timeline --run-id <run_id> --instance-id <instance_id>
```

Use `--catalog-source db` only after a release has been imported, published, and verified. A database mismatch is an explicit failure; do not silently fall back to code definitions. For Neo4j dump import/export, follow [tools/neo4j/README.md](../../../tools/neo4j/README.md); `knowledge import` writes staging and must be verified before publish.

## Testing and evidence

Run the smallest relevant checks first, then broaden when shared contracts or runtime behavior are affected.

```powershell
uv run pytest -q
cd frontend
npm test
npm run build
npm run e2e
```

CoppeliaSim-focused tests require a running simulator and ZMQ server:

```powershell
$env:FLEXIBLE_FACTORY_SIM = "1"
uv run pytest -q tests/test_v1_stage2_arm_sim.py -s
uv run pytest -q tests/test_v1_stage2_agv_sim.py -s
uv run pytest -q tests/test_v1_stage4_scheduler_sim.py -s
```

Do not describe a simulator test as passed when CoppeliaSim was unavailable or the test was skipped. Real-run reports belong under `docs/implementation/runs/`; formal evidence is summarized under `docs/implementation/evidence/`.

## Diagnostics

- ZMQ connection failure: check CoppeliaSim, the ZMQ add-on, and port `23000`.
- Unknown or missing artifact: run `flexible-factory list` and inspect `v1/current.json`.
- Validation mismatch: run `load` then `validate`; never edit protected files to bypass the digest check.
- Stale run lock: inspect `flexible-factory status`, confirm the owner exited, then use `flexible-factory stop`.
- Frontend/API mismatch: verify API is on `127.0.0.1:8080`, Vite is on `127.0.0.1:5173`, and `/api` is proxied to the correct target.
- `assembly_mvp` or `dual` rejection: confirm the artifact root/profile and required manifest capabilities; do not substitute a logical test result for real simulator evidence.
- Startup performance: use `uv run python tools/measure_startup_stages.py`; read `startup_total_wall` and do not add overlapping sub-timers.

## Change discipline

When changing a command, option, startup path, artifact lifecycle rule, or output location, update the unique authoritative project document and its index. Check the affected command's `--help` output and relevant tests. Keep project-specific instructions here; do not copy the entire wiki into the skill.

## References

For a compact route to the maintained operational docs, see [references/README.md](references/README.md).

- [Project usage guide](../../../docs/wiki/98-ProjectUsage/USAGE.md)
- [CLI reference](../../../docs/wiki/98-ProjectUsage/CLI_REFERENCE.md)
- [Engineering reference](../../../PROJECT_ENGINEERING_REFERENCE.md)
- [Project index](../../../CLAUDE.md)
- [Documentation index](../../../docs/README.md)
- [Scene artifacts](../../../docs/scene_artifacts/README.md)
- [Frontend guide](../../../frontend/README.md)
- [Neo4j tools](../../../tools/neo4j/README.md)
