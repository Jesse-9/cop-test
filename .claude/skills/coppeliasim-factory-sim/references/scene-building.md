# Scene Building Reference — CoppeliaSim Island Workshop

## Hierarchical Naming: The Why

CoppeliaSim's scene tree uses slash-separated paths like a filesystem.
This is not cosmetic — it enables:

- **Wildcard queries**: `sim.getObjectsInTree('/Island/A_Assembly')` returns
  all children of Island A in one call
- **No name collisions**: `/Island/A_Assembly/Robot` vs `/Island/D_QC_Assembly/Robot`
  are distinct even though both end in `Robot`
- **Scene browser readability**: Objects group naturally in the hierarchy panel
- **Lua-side scripting**: `sim.getObject('/Island/A_Assembly/Robot_A')` is
  self-documenting

**Rule**: Every object created via Python or Lua MUST be placed under its
canonical parent path. Never create objects at the scene root.

## Object Creation Order

Build the scene bottom-up so parent paths exist before children:

1. Floor (root level, no parent)
2. Warehouse group → S1_Main, S2_Auxiliary
3. Conveyor group → each conveyor belt + rollers
4. Island group → each island body + internal equipment
5. Mobile group → AGV_Shared
6. Part group → material tags (created at runtime, not during scene build)

## Creating Objects with Proper Hierarchy

### Via Python (scene build phase)

```python
def create_child_cuboid(sim, parent_path: str, name: str,
                         size: list, position: list,
                         color: list = None) -> int:
    """Create a cuboid as a child of parent_path."""
    parent_handle = sim.getObject(parent_path)
    handle = sim.createPrimitiveShape(
        sim.primitiveshape_cuboid, size
    )
    # Set as child of parent
    sim.setObjectParent(handle, parent_handle, True)
    # Position relative to parent (use sim.handle_parent)
    sim.setObjectPosition(handle, position, sim.handle_parent)
    if color:
        sim.setObjectColor(handle, 0, sim.colorcomponent_ambient_diffuse, color)
    # Set alias to the full path name
    full_path = f"{parent_path}/{name}"
    sim.setObjectAlias(handle, full_path)
    return handle
```

### Via Lua (CoppeliaSim init script)

```lua
-- In CoppeliaSim's embedded script, create hierarchical objects:
function createIsland(name, pos, size, color)
    local parent = sim.createPrimitiveShape(
        sim.primitiveshape_cuboid, size
    )
    sim.setObjectAlias(parent, '/Island/' .. name)
    sim.setObjectPosition(parent, pos, sim.handle_world)
    sim.setObjectColor(parent, 0, sim.colorcomponent_ambient_diffuse, color)
    return parent
end

-- Then create child equipment:
local robotA = sim.createPrimitiveShape(
    sim.primitiveshape_cylinder, {0.15, 0.15, 0.4}
)
sim.setObjectParent(robotA, parentA, true)
sim.setObjectAlias(robotA, '/Island/A_Assembly/Robot_A')
```

## Using CoppeliaSim Built-in Models

CoppeliaSim ships with a rich library of pre-built industrial models —
articulated robots, conveyor belts, mobile platforms, sensors, and more.
**Prefer loading built-in models over building everything from primitive
shapes** — they come with realistic geometry, joint configurations, and
often include embedded control scripts.

### Model Base Directory

```
C:\Program Files\CoppeliaRobotics\CoppeliaSimEdu\models\
```

The directory tree under `models/` is organized by category:

```
models/
├── robots/
│   ├── mobile/          ← AGVs, mobile platforms, differential drive robots
│   └── non-mobile/      ← Articulated robot arms (KUKA, ABB, UR, etc.)
├── conveyors/           ← Belt conveyors, roller conveyors
├── equipment/           ← Machines, fixtures, tool changers
├── infrastructure/      ← Floors, walls, fences, pillars, safety barriers
├── sensors/             ← Proximity, vision, laser scanners
├── components/          ← Generic parts, grippers, nozzles
├── tools/               ← Welding guns, suction cups, cutting tools
└── people/              ← Human models (for workspace ergonomics)
```

### Loading a Model via Python

Use `sim.loadModel()` to bring a built-in model into the scene at a target
position. The model's internal hierarchy is preserved — you then reparent
and alias its root to fit the workshop naming convention:

```python
import os

MODEL_BASE = r"C:\Program Files\CoppeliaRobotics\CoppeliaSimEdu\models"

def load_model(sim, model_relative_path: str, target_position: list,
               new_alias: str, parent_path: str = None) -> int:
    """
    Load a CoppeliaSim built-in model and integrate into the workshop hierarchy.

    Args:
        model_relative_path: Path relative to MODEL_BASE, e.g.
                             'robots/non-mobile/KUKA LBR iiwa 14 R820.ttm'
        target_position:    [x, y, z] world position to place the model
        new_alias:          Canonical alias, e.g. '/Island/A_Assembly/Robot_A'
        parent_path:        Optional parent to reparent under, e.g. '/Island/A_Assembly'

    Returns:
        Handle of the loaded model's root object.
    """
    full_path = os.path.join(MODEL_BASE, model_relative_path)
    if not os.path.exists(full_path):
        raise FileNotFoundError(f"Model not found: {full_path}")

    # loadModel returns the handle of the model's root object
    model_handle = sim.loadModel(full_path)

    # Set position in world before reparenting
    sim.setObjectPosition(model_handle, target_position, sim.handle_world)

    # Reparent under workspace hierarchy if requested
    if parent_path:
        parent_handle = sim.getObject(parent_path)
        sim.setObjectParent(model_handle, parent_handle, True)

    # Assign canonical alias
    sim.setObjectAlias(model_handle, new_alias)

    return model_handle
```

### Loading a Model via Lua (CoppeliaSim embedded script)

```lua
local modelBase = [[C:\Program Files\CoppeliaRobotics\CoppeliaSimEdu\models\]]

function loadWorkshopModel(relativePath, pos, alias, parentPath)
    local fullPath = modelBase .. relativePath
    local handle = sim.loadModel(fullPath)
    sim.setObjectPosition(handle, pos, sim.handle_world)
    if parentPath then
        sim.setObjectParent(handle, sim.getObject(parentPath), true)
    end
    sim.setObjectAlias(handle, alias)
    return handle
end
```

### Recommended Built-in Models for Island Workshop

This table maps each workshop element to suitable built-in models. Paths are
relative to the model base directory. If a listed model is missing, check your
CoppeliaSim edition (Edu vs Pro) or fall back to primitive shapes.

| Workshop Element | Recommended Model (relative path) | Notes |
|-----------------|----------------------------------|-------|
| **Articulated Robot (Island A/D)** | `robots/non-mobile/KUKA LBR iiwa 14 R820.ttm` | 7-axis lightweight, good for assembly. Alternatives: `UR5.ttm`, `ABB IRB 120.ttm` |
| **Conveyor Belt** | `conveyors/belt conveyor/50cm/50cm belt conveyor.ttm` | Adjustable length. Several width variants available under `conveyors/` |
| **Mobile AGV** | `robots/mobile/pioneer p3dx.ttm` | Differential drive platform. Alternatives: `robots/mobile/youBot.ttm` (omnidirectional with arm) |
| **Proximity Sensor** | `sensors/proximity sensor.ttm` | Generic photoelectric-like sensor |
| **Vision Sensor** | `sensors/vision sensor.ttm` | For QC inspection at Island D |
| **Fixture / Table** | `equipment/table.ttm` or `infrastructure/table.ttm` | Work surface for islands |
| **Safety Fence** | `infrastructure/safety fence.ttm` | Surrounds robot work cells |
| **Pallet / Tray** | `equipment/pallet.ttm` | Material carrier on conveyors |
| **Floor** | `infrastructure/floor.ttm` | Textured industrial floor; or use primitive cuboid |

### Model Names Can Vary by Version

CoppeliaSim model filenames may differ between versions and editions (Edu vs
Pro). Before writing code that references a specific model, **verify the
exact filename** by browsing the directory. For example:

```bash
# List available conveyor models
ls "C:\Program Files\CoppeliaRobotics\CoppeliaSimEdu\models\conveyors\"

# List robot arms
ls "C:\Program Files\CoppeliaRobotics\CoppeliaSimEdu\models\robots\non-mobile\"

# List mobile robots (for AGV)
ls "C:\Program Files\CoppeliaRobotics\CoppeliaSimEdu\models\robots\mobile\"
```

If a preferred model is missing, fall back to the primitive-shape approach
described in the previous section. Always document which models were used
in the project README.

### After Loading: Rename Internal Objects

Built-in models contain their own internal hierarchy (links, joints, shapes).
After loading, iterate the model's children and assign meaningful aliases so
the ZMQ API can address specific parts:

```python
def rename_model_children(sim, model_handle: int, base_alias: str):
    """Assign hierarchical aliases to all children of a loaded model."""
    child_count = sim.getObjectChildCount(model_handle)
    for i in range(child_count):
        child = sim.getObjectChild(model_handle, i)
        child_name = sim.getObjectAlias(child)
        # Build canonical path: e.g. /Island/A_Assembly/Robot_A/Joint_1
        new_alias = f"{base_alias}/{child_name}"
        sim.setObjectAlias(child, new_alias)
```

## Standard Object Dimensions

These are the recommended proportions for an island workshop. Adjust based on
your actual equipment sizes:

| Object | Dimensions (X, Y, Z) meters | Notes |
|--------|---------------------------|-------|
| Floor | (16, 8, 0.05) | Dark gray, matte |
| Warehouse | (1.2, 1.2, 0.8) | Colored cuboid + label |
| Work Island | (1.4, 1.4, 0.9) | Base + identifier pillar |
| Conveyor Belt | (length, 0.5, 0.06) | Flat cuboid, length from start→end |
| Conveyor Roller | (0.08r, 0.08r, width+0.04) | Cylinder at each end |
| AGV Body | (0.6, 0.4, 0.25) | White cuboid |
| Material Cube | (0.15, 0.15, 0.12) | Color varies by type |
| Island Pillar | (0.12r, 0.12r, 0.35) | Cylinder on top of island |

## Color Coding Convention

Use consistent colors to make the scene immediately readable:

| Element Type | RGB | Visual |
|-------------|-----|--------|
| Warehouse S1 | (0.2, 0.4, 0.9) | Blue |
| Warehouse S2 | (0.2, 0.8, 0.8) | Cyan |
| Island A | (0.2, 0.8, 0.3) | Green |
| Island B | (0.9, 0.8, 0.2) | Yellow |
| Island C | (0.9, 0.5, 0.2) | Orange |
| Island D | (0.6, 0.3, 0.8) | Purple |
| Island E | (0.9, 0.2, 0.2) | Red |
| Conveyor | (0.5, 0.5, 0.5) | Gray |
| AGV | (0.95, 0.95, 0.95) | White |
| Floor | (0.25, 0.25, 0.28) | Dark gray |

## Material Color Coding

Each material type gets a distinct color for visual tracking:

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

## Sensors and Triggers

For material detection and process automation, place these sensor types:

### Proximity Sensors (Photoelectric Switch Simulation)
Use CoppeliaSim's proximity sensor type to detect when materials arrive:

```python
def create_proximity_sensor(sim, parent_path: str, name: str,
                             position: list, detection_range: float = 0.3):
    """Create a proximity sensor to detect material arrival."""
    handle = sim.createProximitySensor(
        sim.proximitysensor_ray, detection_range, [0, 0, 1]
    )
    sim.setObjectParent(handle, sim.getObject(parent_path), True)
    sim.setObjectPosition(handle, position, sim.handle_parent)
    sim.setObjectAlias(handle, f"{parent_path}/{name}")
    return handle
```

### Recommended Sensor Placements

| Location | Sensor Name | Purpose |
|----------|------------|---------|
| End of each conveyor | `Conveyor/*/Sensor_Arrival` | Detect material reaching conveyor end |
| Each island input | `Island/*/Sensor_Input` | Detect material arriving at island |
| AGV loading point | `Mobile/AGV_Shared/Sensor_Load` | Confirm material loaded onto AGV |

## Physics and Collision Configuration

For realistic material handling:

- **Conveyors**: Set as **static, non-respondable** (materials ride on top via
  script positioning, not physics simulation)
- **Island bases**: **Static, respondable** — provides collision surface
- **AGV body**: **Non-static, respondable** — moves via script but should
  collide with obstacles
- **Materials**: **Non-static, respondable** — if you want physics-based
  interactions; otherwise **non-respondable** if purely script-driven
- **Floor**: **Static, respondable** — the ground plane

## Layout Coordinates Template

When designing a new island workshop, start from this coordinate template
and adjust for your specific layout. Origin (0,0) at floor center:

```
Y-axis ↑
  4 ┤
  2 ┤  S1(-7,2)          A(-2.5,2)        C(2,2)
  0 ┤                                        ● E(6,-0.5)
 -1 ┤                     B(-2.5,-1)
 -3 ┤  S2(-7,-3)          D(-2.5,-3)
 -4 ┤
     └──────────────────────────────────────────→ X-axis
    -8   -6    -4    -2     0     2     4     6     8
```

**Layout principles**:
1. Warehouses on the left (negative X), final assembly on the right (positive X)
2. Material flows generally left-to-right
3. Related islands paired vertically (A/B, C/D) for efficient AGV routing
4. AGV paths should avoid crossing conveyors at grade level
5. Leave at least 1.5m clearance between islands for AGV maneuverability
