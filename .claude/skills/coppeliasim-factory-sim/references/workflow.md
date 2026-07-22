# Development Workflow Reference

> The standard loop for any non-trivial change to the factory simulation:
> **Plan → Execute → Review → Update docs → Commit (code, then docs)**.
> This keeps code, wiki, and this skill in sync, with a traceable history.
> Mirrors the project's Conventional Commits style
> (`fix(mcp):`, `docs(wiki):`, `feat(controller):` …).

## Why a fixed workflow

The project has three sources of truth that drift easily:
- **Code** ([src/](../../../src/)) — runtime behavior
- **Wiki** ([docs/wiki/](../../../docs/wiki/)) — human-facing knowledge
- **This skill** (`.claude/skills/coppeliasim-factory-sim/`) — Claude-facing guidance

A change to one without the others produces silent contradictions (e.g. the
skill prescribing slash-path `getObject` long after the code moved to
underscore aliases). The workflow below forces all three to move together,
and splits code from docs into separate commits so history is reviewable.

## The five phases

```
┌─────────┐   ┌──────────┐   ┌────────┐   ┌──────────────┐   ┌────────┐
│ 1. Plan │──▶│ 2. Execute│──▶│3. Review│──▶│4. Update docs │──▶│5. Commit│
└─────────┘   └──────────┘   └────────┘   └──────────────┘   └────────┘
   scope         code          verify       wiki + skill       code first,
   risks         + tests       behavior     stay aligned       then docs
```

### Phase 1 — Plan

Before writing code, nail down scope and risks. Produce a short plan (in-chat
or via `EnterPlanMode` for larger changes):

- **Goal**: one sentence on what changes and why.
- **Files in scope**: list `src/`, `docs/wiki/`, skill files touched.
- **Out of scope**: explicitly call out what you will NOT touch (prevents
  scope creep and accidental edits to the working tree's other in-progress
  files).
- **Risks / 4.10 gotchas**: will this hit a known pitfall? Check
  [CLAUDE.md](../../../CLAUDE.md) §已知问题与坑 and this skill's references:
  - Slash-alias trap (any new object alias must be underscore-only)
  - UR5-only arm policy (no iiwa/Franka)
  - Script stripping after `loadModel` (freeze mitigation)
  - `coppeliaSimVersion` signal may be `None`
  - Dual ZMQ connection (don't run diagnostics on the main connection mid-loop)
- **Test plan**: how will you verify? (run sim end-to-end, inject an anomaly,
  MCP `inspect_scene_state`, unit test if applicable)

> If the change is small and mechanical (a one-line config tweak), a verbal
> plan is fine — skip `EnterPlanMode`. Reserve the full plan template for
> multi-file or behavior-changing work.

### Phase 2 — Execute

Implement against the plan, following the skill's conventions:

- Aliases underscore-only; handles via alias-leaf matching
- All new arms UR5; strip model scripts on load
- Tune via [src/factory_config.py](../../../src/factory_config.py), not hardcoded values
- 13-step loop order preserved if touching `FactoryController`
- If adding an MCP tool: register it, keep the dual-connection discipline,
  and update the "19 tools" count everywhere it appears (SKILL.md, wiki
  06-03, MCP_USAGE)

Run the relevant test/verification as you go. Don't leave the scene in a
half-built state between steps.

### Phase 3 — Review

Before touching docs, verify the change actually works and didn't regress:

- **Behavior**: run the sim (or `step_simulation` frame-by-frame if freezing
  is a risk); confirm the intended effect with no `state=8` freeze, no
  `0 joints` warnings, no exception-loop spam.
- **Self-review of the diff**: read the changed code as if reviewing a PR.
  Common catches: a new `getObject("/...")` slipped in, a hardcoded value
  that belongs in config, a stale comment/docstring now contradicted.
- **Use `/code-review` or `/simplify`** on the working diff for a second pass
  on quality/reuse/simplification (these operate on the uncommitted diff).
- **Do NOT commit yet** — docs come first, so the code+docs land together.

### Phase 4 — Update docs (keep all three sources aligned)

Walk the three truth-sources and update each that the change affects. A code
change that the docs don't reflect is a defect:

| Source | When to update | Tool |
|--------|----------------|------|
| [docs/wiki/06-工业案例/06-03-src模块参考.md](../../../docs/wiki/06-工业案例/06-03-src模块参考.md) | Module API/exports/known-defects changed | Edit |
| [docs/wiki/06-工业案例/06-02-岛式车间柔性生产案例.md](../../../docs/wiki/06-工业案例/06-02-岛式车间柔性生产案例.md) | Topology, loop order, MCP tool count, results changed | Edit |
| [docs/wiki/02-仿真建模/02-01-分层命名场景构建.md](../../../docs/wiki/02-仿真建模/02-01-分层命名场景构建.md) | Naming, model-loading, clear-scene behavior changed | Edit |
| [docs/wiki/98-ProjectUsage/MCP_USAGE.md](../../../docs/wiki/98-ProjectUsage/MCP_USAGE.md) | MCP tool added/removed/renamed, or run_lua mechanism changed | Edit |
| [CLAUDE.md](../../../CLAUDE.md) | Module table, 13-step loop, or a known-issue item changed | Edit |
| This skill (SKILL.md + references/) | Anything a future Claude session would need to know to do the same change correctly | Edit |

**Doc-sync checklist** (the exact things that drift):
- [ ] Module names / class names / method signatures match the code
- [ ] The "19 tools" count (or whatever the current count is) is consistent
      across SKILL.md, wiki 06-02, wiki 06-03, MCP_USAGE
- [ ] Any new known pitfall is added to CLAUDE.md §已知问题与坑 AND mirrored
      in the relevant skill reference
- [ ] Stale claims removed (e.g. if preemption is removed, don't leave
      "preemptable" as live guidance — mark it stale or delete it)
- [ ] Frontmatter `updated:` dates bumped on changed wiki notes

> If a change is code-only with zero user-visible/doc-relevant impact (rare —
  e.g. a pure internal refactor with identical API), say so explicitly and
  skip Phase 4 for that source rather than silently skipping.

### Phase 5 — Commit (code first, then docs)

Two separate commits, scoped tightly. **Stage explicitly** — never
`git add -A`, because the working tree often holds unrelated in-progress
files (e.g. `factory-sim.ttt`, other people's wiki edits). Only stage what
this task touched.

**Commit 1 — code** (`src/`, scene files if relevant):
```
<type>(<scope>): <imperative summary>

<2-4 lines: what changed, why, any gotcha resolved>
```
Types: `feat`, `fix`, `refactor`, `perf`, `chore`, `test`.
Scopes seen in this repo: `mcp`, `controller`, `agv`, `island`, `config`,
`scene`, `core`. Pick the most specific; omit scope if it spans many.

**Commit 2 — docs** (`docs/wiki/`, `CLAUDE.md`, `.claude/skills/`):
```
docs(<scope>): <imperative summary>
```
Scopes: `wiki`, `skill`. Keep the skill and wiki in the same docs commit when
they move together for one change (as they should).

> Example pair from this repo's history:
> `9c51956 fix(mcp): apply final review fixes — disconnect lingering reset, ro_lock guard, …`
> `226012a docs: sync all references to 19-tool MCP server + dual-connection`

**Branch rule**: if the repo is on `main` (the default branch), create a
short feature branch first (e.g. `fix/agv-preemption-doc`) before committing.
Only commit directly to `main` if the user explicitly says so.

**After committing**: report the two commit hashes and a one-line summary of
what each contains. Do not push unless asked.

## Anti-patterns to avoid

- ❌ Committing code + docs in one commit (coarse history, hard to revert
  just the docs).
- ❌ `git add -A` / `git add .` — sweeps in unrelated working-tree files.
- ❌ Updating code but leaving wiki/skill stale (silent contradiction).
- ❌ Marking a stale contract as live guidance (e.g. re-asserting
  "preemptable" after preemption was removed).
- ❌ Committing directly to `main` without branching when not authorized.
- ❌ Skipping Phase 3 review because "it's a small change" — small changes
  are where slash-path `getObject` and hardcoded values sneak back in.
