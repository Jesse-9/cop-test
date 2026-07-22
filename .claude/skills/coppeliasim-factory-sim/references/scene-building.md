# Scene Building Reference — CoppeliaSim Island Workshop

> Mirrors [docs/wiki/02-仿真建模/02-01-分层命名场景构建.md](../../../docs/wiki/02-仿真建模/02-01-分层命名场景构建.md)
> and the real [src/build_scene.py](../../../src/build_scene.py).

## Naming: UNDERSCORE aliases, never slash-path getObject

CoppeliaSim's scene tree is *conceptually* slash-separated (`/Warehouse/S1_Main`),
but in **4.10+** `setObjectAlias` converts any `/` to `_` when storing the
alias. So `setObjectAlias(h, "Conveyor/S1_to_A")` is stored as
`_Conveyor_S1_to_A`, and `getObject("Conveyor/S1_to_A")` /
`getObject("/Conveyor/S1_to_A")` **both fail**.

**Rule**:
- **Aliases are flat underscore names** (`Warehouse_S1_Main`, `Conveyor_S1_to_A`,
  `Island_A_Assembly`, `Mobile_AGV_Shared`). The slash form is only a
  documentation/grouping aid.
- **Handle lookup is by alias-leaf matching**, never slash-path `getObject`.

The project's hierarchy roots (`cfg.HIERARCHY_ROOTS`) are:
`Warehouse`, `Conveyor`, `Island`, `Mobile`, `Part` — created as invisible
dummy "folder" objects, with devices `setObjectParent`'d under them.

## Object Creation Order

Build bottom-up so parent roots exist before children:

1. Floor (root level)
2. Hierarchy roots: `Warehouse`, `Conveyor`, `Island`, `Mobile`, `Part`
3. Warehouses: `Warehouse_S1_Main`, `Warehouse_S2_Auxiliary`
4. Conveyors: `Conveyor_S1_to_A`, `Conveyor_S1_to_B`, `Conveyor_S2_to_D`,
   `Conveyor_C_to_E`
5. Island bases: `Island_A_Assembly` … `Island_E_FinalAssembly`
6. Robot arms per island (UR5), aliased `Island_<X>_<...>_Robot_<X>`
7. AGV: `Mobile_AGV_Shared`
8. Part tags: created at runtime (spawned materials), not during scene build

## Hierarchy Root + Child Creation

```python
def create_hierarchy_roots(sim) -> dict[str, int]:
    roots = {}
    for name in ("Warehouse", "Conveyor", "Island", "Mobile", "Part"):
        h = sim.createDummy(0.01)
        sim.setObjectAlias(h, name)                 # flat, no slash
        sim.setObjectInt32Param(h, sim.objintparam_visibility, 0)
        roots[name] = h
    return roots

def create_child_cuboid(sim, parent_handle: int, alias: str,
                        size: list, position: list,
                        color: list = None) -> int:
    """Create a cuboid under a hierarchy root, with a flat underscore alias."""
    handle = sim.createPrimitiveShape(sim.primitiveshape_cuboid, size)
    sim.setObjectParent(handle, parent_handle, True)   # inPlace=True keeps world coords
    sim.setObjectPosition(handle, position, sim.handle_world)
    if color:
        sim.setObjectColor(handle, 0, sim.colorcomponent_ambient_diffuse, color)
    sim.setObjectAlias(handle, alias)                  # e.g. "Warehouse_S1_Main"
    return handle
```

> Do **not** write `sim.setObjectAlias(handle, f"{parent_path}/{name}")` — the
> slash will be mangled to `_` and you'll have a hard-to-predict stored alias.
> Pass the full flat alias directly.

## Built-in Models: load + strip scripts + reparent

Prefer `sim.loadModel()` over primitives — realistic geometry and joints. The
project's model base is `cfg.MODEL_BASE`
(`C:\Program Files\CoppeliaRobotics\CoppeliaSimEdu\models`), with relative
paths in `cfg.BUILTIN_MODELS`.

```python
import os

def load_model(sim, model_relative_path: str, target_position: list,
               new_alias: str, parent_handle: int = None) -> int:
    """Load a built-in model, strip its scripts, reparent, and alias it."""
    full_path = os.path.join(cfg.MODEL_BASE, model_relative_path)
    if not os.path.isfile(full_path):
        return _fallback_primitive(sim, new_alias, parent_handle)  # 几何体回退

    model_handle = sim.loadModel(full_path)

    # ⚠ Strip the model's embedded Lua scripts — without this, the mixed
    # script set blocks the 4.10 ZMQ addOn layer ~3 frames in and freezes
    # the whole scene (state=8). See SKILL.md §Robot Arm Policy.
    _strip_model_scripts(sim, model_handle)

    sim.setObjectPosition(model_handle, target_position, sim.handle_world)
    if parent_handle is not None:
        sim.setObjectParent(model_handle, parent_handle, True)
    sim.setObjectAlias(model_handle, new_alias)   # flat underscore alias
    return model_handle
```

### Script stripping (`build_scene._strip_model_scripts`)

After `loadModel`, enumerate the model's subtree and remove objects whose
`object_script_type` is set **and** whose alias contains "script":

```python
def _strip_model_scripts(sim, model_handle: int):
    """Remove a loaded model's Lua customization/child scripts."""
    i = 0
    while True:
        try:
            h = sim.getObjects(i, sim.object_script_type)
            if h is None or h < 0:
                break
        except Exception:
            break
        # Only strip scripts that belong to THIS model's subtree
        if _is_in_subtree(sim, h, model_handle):
            alias = sim.getObjectAlias(h)
            if "script" in alias.lower():
                sim.removeObject(h)
                continue   # don't increment i — indices shift after removal
        i += 1
```

> This is the mitigation for the "abort execution Connectivity >> ZMQ remote
> API server@addOnScript" freeze. If a scene still freezes, use
> `list_objects filter="*cript*"` (via the MCP `list_objects` tool) to find
> residual scripts, then `step_simulation` frame-by-frame to localize the
> blocker.

## Robot Arm Policy: Unified UR5 (6-axis)

All island arms are **UR5** (`cfg.BUILTIN_MODELS["robot_arm_ur5"]` =
`robots/non-mobile/UR5.ttm`). Never mix in KUKA iiwa or Franka:

- Their 7-axis joint count misaligns with the project's 6-element keyframe
  arrays (`cfg.ARM_ANIMATIONS`) → animation pose corruption / index overflow.
- Mixed model scripts trigger the startup freeze above.

Per-island config lives in `cfg.ISLAND_ROBOTS` (A/B/C/D each one arm; E has
two — `E_C` left, `E_D` right — for paired assembly). Aliases follow
`Island_<X>_<Name>_Robot_<X>` (e.g. `Island_A_Assembly_Robot_A`,
`Island_E_FinalAssembly_Robot_E_C`).

Animation is **pure visual** — Python-side keyframe interpolation in
[factory_robot.py](../../../src/factory_robot.py), no IK, no model scripts.
`RobotArmAnimator._collect_arm_joints()` scans the scene for joints with a
full-scene fallback (since `getObjectsInTree` throws "Operation cannot be
accomplished in current state" when sim is stopped or the subtree contains
non-queryable objects).

## Idempotent Clear (clear_scene)

Repeated builds cause alias collisions (multiple objects with the same leaf
name). `build_scene.clear_scene(sim)` enumerates all main object types and
batch-removes them before construction:

> ⚠ The clear type list includes `object_camera_type` / `object_light_type`,
> so it will delete the default viewport camera and light. If you need to
> keep them, remove those two types from the list. (Documented in
> [docs/wiki/02-仿真建模/02-01-分层命名场景构建.md](../../../docs/wiki/02-仿真建模/02-01-分层命名场景构建.md).)

## Handle Lookup by Alias-Leaf Matching

Because slash-path `getObject` is unreliable in 4.10, the project locates
objects by enumerating and matching the alias leaf:

```python
def find_by_leaf(sim, leaf: str, obj_type) -> int | None:
    """Locate an object by its alias leaf name (slash- and underscore-aware)."""
    i = 0
    while True:
        try:
            h = sim.getObjects(i, obj_type)
            if h is None or h < 0:
                break
            alias = sim.getObjectAlias(h)
            norm = alias.rsplit("/", 1)[-1].rsplit("_", 1)[-1]
            if alias == leaf or norm == leaf:
                return h
            i += 1
        except Exception:
            break
    return None
```

> This matching logic is currently duplicated in `build_scene.clear_scene`,
> `factory_controller._setup_agv_visual`, and
> `factory_robot._collect_arm_joints` — a known cleanup item.

## Standard Dimensions (from cfg)

| Object | Dimensions (X, Y, Z) m | Notes |
|--------|------------------------|-------|
| Floor | (18.0, 11.0, 0.05) | Centered at world origin; Y from -5.5 to 5.5 to fit D island lowered |
| Warehouse | (1.2, 1.2, 0.8) | Colored cuboid |
| Work Island | (1.4, 1.4, 0.9) | Base + identifier pillar |
| Conveyor | (length, 0.5, ~) | Length derived from start→end |
| AGV Body | (0.6, 0.4, 0.25) | White |
| Material Cube | (0.15, 0.15, 0.12) | Color by type |
| `Z_BASE` | 0.6 | Working height for all devices/conveyors |

## Color Coding (from cfg)

| Element | RGB | Visual |
|---------|-----|--------|
| Warehouse S1 | (0.2, 0.4, 0.9) | Blue |
| Warehouse S2 | (0.2, 0.8, 0.8) | Cyan |
| Island A | (0.2, 0.8, 0.3) | Green |
| Island B | (0.9, 0.8, 0.2) | Yellow |
| Island C | (0.9, 0.5, 0.2) | Orange |
| Island D | (0.6, 0.3, 0.8) | Purple |
| Island E | (0.9, 0.2, 0.2) | Red |
| Conveyor | (0.5, 0.5, 0.5) | Gray |
| AGV | (0.95, 0.95, 0.95) | White |

## Material Color Coding (`cfg.MATERIAL_COLORS`)

| Material | RGB | Meaning |
|----------|-----|---------|
| raw_A | (0.3, 0.7, 1.0) | Light blue — raw from S1 to A |
| raw_B | (1.0, 0.7, 0.3) | Orange — raw from S1 to B |
| semi_AB | (0.3, 1.0, 0.3) | Green — B→A transferred |
| semi_AC | (1.0, 0.5, 0.5) | Pink — A→C semi-finished |
| raw_D | (0.7, 0.3, 1.0) | Purple — auxiliary from S2 to D |
| semi_CE | (1.0, 0.3, 0.3) | Red — C→E semi-finished |
| part_DE | (0.8, 0.5, 1.0) | Light purple — D→E special part |
| final_E | (0.9, 0.9, 0.1) | Gold — finished product |

## Layout Coordinates (from cfg)

Origin (0,0) at floor center; `Z_BASE = 0.6` is the working height:

```
Y-axis ↑
  2 ┤  S1(-7,2)            A(-2.5,2)       C(2,2)
  0 ┤                                         ● E(6,-0.5)
 -1 ┤                       B(-2.5,-1)
 -4.5┤ S2(-7,-4.5)          D(-2.5,-4.5)
    └──────────────────────────────────────────→ X-axis
  -7      -2.5    0      2        6
```

**Layout principles**:
1. Warehouses on the left (negative X), final assembly on the right (positive X)
2. Material flows generally left-to-right
3. D island is lowered to Y=-4.5 to give the D→E AGV route clearance
4. AGV D→E route has mid-turn waypoints to avoid crossing conveyors at grade

## Physics / Collision Configuration

- **Conveyors**: Static, non-respondable (materials ride on top via script
  positioning, not physics)
- **Island bases**: Static, respondable — collision surface
- **AGV body**: Script-driven position + orientation (yaw recomputed each
  frame from the displacement vector; small movements keep prior yaw to
  avoid jitter — see `factory_controller._update_scene_positions`)
- **Materials**: Non-respondable, script-driven (purely positional)
- **Floor**: Static, respondable — ground plane
