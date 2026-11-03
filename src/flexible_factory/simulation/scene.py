"""scene.py — v1 UFLOW 五岛场景派生、句柄解析与契约断言.

UFLOW 层不从空场景重建: 它加载已验收的父层场景, 保留 A/B 单元、共享
传送带、AGV 与运动/双站层契约, 只在**UFLOW 副本**中增加:

  - 三条独立物流线: `Conveyor_S1_to_B`、`Conveyor_S2_to_D`、`Conveyor_C_to_E`;
  - C/D/E 三个工位: `Island_C/D/E` + `Robot_UR5_03/04/05` + 吸盘/TCP/传感器;
  - S2 仓库与两个来源 dummy (`Source_S1_B`、`Source_S2_D`);
  - A→C 固定交接传感器、E 岛两个输入缓冲与成品输出 dummy;
  - dock D/E 传感器.

A/B 单元的坐标与对象保持不变; AGV 初始位姿保持 dock A 朝 -Y. 所有别名
都是扁平下划线, 不使用 `/` 路径 (CLAUDE.md §3). 构建完成后执行五套单元
契约与 UFLOW 布局/物流线断言.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from . import config_base as base
from . import physics as phys
from .conveyor import (configure_conveyor_model)
from .scene_base import (SceneBuildError, Stage1Scene, _chain_joints,
                          _create_box, _create_cylinder_sensor,
                          _end_link_max_z, _find_scripts, _load_model,
                          _measure_top_run, _model_bbox, _quat_equivalent,
                          _shape_size, _world_axis, _dot, alias_index,
                          alias_of, all_objects, resolve_alias, subtree)
from .scene_dual import (Stage3Scene, Stage3Unit, _create_dummy,
                          _create_unit_robot, _verify_unit)

from . import config as cfg
from .layout import (Stage4Layout, UnitSpec4, UnitView4, default_layout,
                     layout_from_dict, line_specs, quat_from_yaw,
                     yaw_from_quat)

LINE_ALIASES = {line_id: spec.as_dict() for line_id, spec in
                line_specs().items()}


class Stage4SceneError(SceneBuildError):
    code = "STAGE4_SCENE"


# ═══════════════════════════════════════════════════════════════
# 工位
# ═══════════════════════════════════════════════════════════════

@dataclass
class Stage4Unit:
    unit_id: str
    index: int
    spec: UnitSpec4
    layout: UnitView4
    island: int
    robot_root: int
    robot_connection: int
    robot_joints: list[int]
    suction_root: int
    tcp: int
    suction_sensor: int
    suction_model_sensor: int
    suction_link: int
    ik_target: int
    station: int
    place_target: int
    dock_sensor: int
    output_targets: dict[str, int] = field(default_factory=dict)

    @property
    def aliases(self) -> dict[str, str]:
        return self.spec.aliases

    def handle(self, key: str) -> int:
        values = {
            "island": self.island,
            "station": self.station,
            "robot": self.robot_root,
            "suction": self.suction_root,
            "tcp": self.tcp,
            "suction_sensor": self.suction_sensor,
            "ik_target": self.ik_target,
            "place_target": self.place_target,
            "dock_sensor": self.dock_sensor,
        }
        if key in self.output_targets:
            return self.output_targets[key]
        return values.get(key, -1)

    def as_dict(self) -> dict:
        return {
            "unit_id": self.unit_id,
            "index": int(self.index),
            "arm_base": [round(float(v), 6) for v in self.spec.arm_base],
            "island_pos": [round(float(v), 6)
                           for v in self.spec.island_pos],
            "station_pos": [round(float(v), 6)
                            for v in self.spec.station_pos],
            "dock": (None if self.spec.dock is None
                     else [round(float(v), 6) for v in self.spec.dock]),
            "has_station": bool(self.spec.has_station),
            "has_dock": bool(self.spec.has_dock),
            "aliases": self.aliases,
            "joints": len(self.robot_joints),
            "handles": {
                "island": int(self.island),
                "robot": int(self.robot_root),
                "connection": int(self.robot_connection),
                "suction": int(self.suction_root),
                "tcp": int(self.tcp),
                "suction_sensor": int(self.suction_sensor),
                "model_sensor": int(self.suction_model_sensor),
                "link": int(self.suction_link),
                "ik_target": int(self.ik_target),
                "station": int(self.station),
                "place_target": int(self.place_target),
                "dock_sensor": int(self.dock_sensor),
                "output_targets": {key: int(value)
                                   for key, value in
                                   self.output_targets.items()},
            },
        }


# ═══════════════════════════════════════════════════════════════
# 场景
# ═══════════════════════════════════════════════════════════════

@dataclass
class Stage4Scene(Stage3Scene):
    stage4_layout: Optional[Stage4Layout] = None
    units: dict[str, Stage4Unit] = field(default_factory=dict)
    line_meta: dict[str, dict] = field(default_factory=dict)

    # ── 访问 ────────────────────────────────────────────────

    def unit(self, unit_id: str) -> Stage4Unit:
        try:
            return self.units[str(unit_id)]
        except KeyError as exc:
            raise Stage4SceneError(
                f"未知工位 {unit_id!r}; 可用 {sorted(self.units)}") from exc

    def line(self, line_id: str) -> dict:
        try:
            return self.line_meta[str(line_id)]
        except KeyError as exc:
            raise Stage4SceneError(f"未知物流线: {line_id!r}") from exc

    @property
    def unit_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.units))

    @property
    def builder_info(self) -> dict:
        return {
            "module": "flexible_factory.simulation.scene",
            "version": cfg.STAGE4_SCENE_BUILDER_VERSION,
            "clear_scene": False,
            "loads_parent_artifact": True,
            "parent_phase": cfg.PARENT_PHASE,
            "builds_units": list(cfg.UNIT_IDS),
            "builds_lines": list(cfg.LINE_IDS),
        }

    # ── AGV dock ────────────────────────────────────────────

    def agv_dock_hit_at(self, unit_id: str, agv_handle: int) -> bool:
        unit = self.unit(unit_id)
        sensor = unit.dock_sensor
        if sensor < 0:
            return False
        candidates = [int(agv_handle)] + subtree(self.sim, int(agv_handle))
        for candidate in candidates:
            try:
                res = self.sim.checkProximitySensor(sensor, int(candidate))
            except Exception:
                continue
            if isinstance(res, (tuple, list)) and len(res) >= 4:
                if res[0] and res[3] is not None and int(res[3]) >= 0:
                    return True
            elif res not in (None, 0, -1):
                return True
        return False

    def agv_dock_hit(self, agv_handle: int) -> bool:
        """兼容阶段一接口: 任一 dock 命中 (仅用于诊断)."""
        return any(self.agv_dock_hit_at(unit_id, agv_handle)
                   for unit_id in self.units)

    # ── 描述 ────────────────────────────────────────────────

    def describe(self) -> list[dict]:
        rows = super().describe()
        known = {row.get("alias") for row in rows}
        index = alias_index(self.sim)
        for alias in cfg.REQUIRED_ALIASES:
            if alias in known:
                continue
            hits = index.get(alias, [])
            if len(hits) != 1:
                continue
            handle = hits[0]
            parent = -1
            try:
                parent = int(self.sim.getObjectParent(handle))
            except Exception:
                pass
            rows.append({
                "alias": alias,
                "handle": handle,
                "type": int(self.sim.getObjectType(handle)),
                "parent": alias_of(self.sim, parent) if parent >= 0 else "",
                "world_pos": [round(float(v), 4)
                              for v in self.sim.getObjectPosition(
                                  handle, self.sim.handle_world)],
            })
        rows.sort(key=lambda row: row.get("alias") or "")
        return rows

    # ── 契约 ────────────────────────────────────────────────

    def verify(self) -> dict:
        checks: list[dict] = self.build_report.get("checks", [])
        layout = self.stage4_layout
        if layout is None:
            raise Stage4SceneError("Stage4Scene 缺少 stage4_layout")

        def check(name: str, ok: bool, detail: str = "") -> bool:
            checks.append({"name": name, "ok": bool(ok), "detail": detail})
            return ok

        from .layout import validate_layout
        layout_report = validate_layout(layout)
        check("stage4_layout_static_validation", layout_report["ok"],
              f"layout={layout.name} "
              f"({sum(1 for c in layout_report['checks'] if c['ok'])}"
              f"/{len(layout_report['checks'])} checks)")
        check("stage4_layout_name", layout.name == cfg.STAGE4_LAYOUT_NAME,
              f"name={layout.name!r}")

        index = alias_index(self.sim)
        for alias in cfg.REQUIRED_ALIASES:
            hits = index.get(alias, [])
            check(f"stage4_alias_unique:{alias}", len(hits) == 1,
                  f"{len(hits)} 个匹配")

        # 五套单元契约 (沿用阶段三的关节/TCP/安装/可达检查)
        for unit_id in cfg.UNIT_IDS:
            try:
                unit = self.unit(unit_id)
            except Stage4SceneError as exc:
                check(f"stage4_unit_present:{unit_id}", False, str(exc))
                continue
            for row in _verify_unit(self.sim, unit, unit.spec):
                check(f"stage4_{unit_id}:{row['name']}", row["ok"],
                      row["detail"])

        # 物流线: 长度/有效行程/传感器/驱动配置
        for line_id in cfg.LINE_IDS:
            try:
                meta = self.line(line_id)
            except Stage4SceneError as exc:
                check(f"stage4_line_present:{line_id}", False, str(exc))
                continue
            spec = layout.lines[line_id]
            actual = meta.get("actual_length")
            check(f"stage4_line_length:{line_id}",
                  actual is not None
                  and abs(actual - spec.design_length) <= 0.01,
                  f"design={spec.design_length:.4f} actual={actual}")
            check(f"stage4_line_surface:{line_id}",
                  abs(float(meta.get("surface_z", -999))
                      - base.CONVEYOR_SURFACE_Z) <= 0.005,
                  f"surface_z={meta.get('surface_z')}")
            check(f"stage4_line_script:{line_id}",
                  int(meta.get("script_handle", -1)) >= 0,
                  f"script={meta.get('script_handle')}")
            check(f"stage4_line_accel:{line_id}",
                  meta.get("config_accel") is not None
                  and abs(float(meta["config_accel"])
                          - cfg.LINE_ACCEL) < 1e-6,
                  f"accel={meta.get('config_accel')}")
            check(f"stage4_line_start_stopped:{line_id}",
                  meta.get("config_target_vel") is not None
                  and abs(float(meta["config_target_vel"])) < 1e-6,
                  f"targetVel={meta.get('config_target_vel')} "
                  f"(阶段四线必须启动即停止)")
            check(f"stage4_line_entry:{line_id}",
                  math.dist(meta["entry_world"], spec.entry) <= 0.01,
                  f"actual={meta['entry_world']} plan={spec.entry}")
            check(f"stage4_line_stop:{line_id}",
                  math.dist(meta["stop_world"], spec.stop) <= 0.01,
                  f"actual={meta['stop_world']} plan={spec.stop}")

        # dock 传感器位置
        for unit_id in ("A", "B", "D", "E"):
            unit = self.unit(unit_id)
            try:
                pos = list(self.sim.getObjectPosition(
                    unit.dock_sensor, self.sim.handle_world))
                expected = [unit.spec.dock[0], unit.spec.dock[1],
                            base.AGV_DOCK_SENSOR_Z]
                check(f"stage4_dock_sensor:{unit_id}",
                      all(abs(pos[i] - expected[i]) <= 0.002
                          for i in range(3)),
                      f"pos={[round(v, 4) for v in pos]}")
            except Exception as exc:
                check(f"stage4_dock_sensor:{unit_id}", False, str(exc))

        # 固定交接 dummy/传感器
        try:
            handoff = self.handle(cfg.HANDOFF_AC["alias"])
            pos = list(self.sim.getObjectPosition(handoff,
                                                  self.sim.handle_world))
            expected = [cfg.HANDOFF_AC["position"][0],
                        cfg.HANDOFF_AC["position"][1],
                        base.STATION_TOP_Z + base.MATERIAL_HALF_HEIGHT]
            check("stage4_handoff_target",
                  all(abs(pos[i] - expected[i]) <= 0.005
                      for i in range(3)),
                  f"pos={[round(v, 4) for v in pos]}")
        except Exception as exc:
            check("stage4_handoff_target", False, str(exc))

        # E 缓冲/输出 dummy
        for key, spec in cfg.E_BUFFERS.items():
            try:
                handle = self.handle(spec["alias"])
                pos = list(self.sim.getObjectPosition(handle,
                                                      self.sim.handle_world))
                expected = [spec["position"][0], spec["position"][1],
                            base.ISLAND_TOP_Z + base.MATERIAL_HALF_HEIGHT]
                check(f"stage4_buffer_target:{key}",
                      all(abs(pos[i] - expected[i]) <= 0.005
                          for i in range(3)),
                      f"pos={[round(v, 4) for v in pos]}")
            except Exception as exc:
                check(f"stage4_buffer_target:{key}", False, str(exc))
        try:
            output = self.handle(cfg.E_OUTPUT["alias"])
            pos = list(self.sim.getObjectPosition(output,
                                                  self.sim.handle_world))
            expected = [cfg.E_OUTPUT["position"][0],
                        cfg.E_OUTPUT["position"][1],
                        cfg.E_OUTPUT["material_z"]]
            check("stage4_e_output_target",
                  all(abs(pos[i] - expected[i]) <= 0.005
                      for i in range(3)),
                  f"pos={[round(v, 4) for v in pos]}")
        except Exception as exc:
            check("stage4_e_output_target", False, str(exc))
        try:
            table = self.handle(cfg.E_OUTPUT_TABLE["alias"])
            size = _shape_size(self.sim, table)
            pos = list(self.sim.getObjectPosition(table,
                                                  self.sim.handle_world))
            expected_size = list(cfg.E_OUTPUT_TABLE["size"])
            check("stage4_e_output_table",
                  size is not None
                  and all(abs(size[i] - expected_size[i]) <= 0.002
                          for i in range(3))
                  and abs(pos[0] - cfg.E_OUTPUT_TABLE["position"][0]) <= 0.002
                  and abs(pos[1] - cfg.E_OUTPUT_TABLE["position"][1]) <= 0.002
                  and abs(pos[2] - expected_size[2] / 2.0) <= 0.002,
                  f"size={size} pos={[round(v, 4) for v in pos]}")
        except Exception as exc:
            check("stage4_e_output_table", False, str(exc))

        # 来源 dummy
        for key, spec in cfg.SOURCE_ITEMS.items():
            alias = (cfg.ALIASES["source_s1_b"] if key == "s1b"
                     else cfg.ALIASES["source_s2_d"] if key == "s2d"
                     else base.ALIASES["conveyor_entry"])
            try:
                handle = self.handle(alias)
                check(f"stage4_source:{key}", handle >= 0,
                      f"alias={alias} handle={handle}")
            except Exception as exc:
                check(f"stage4_source:{key}", False, str(exc))

        # AGV 初始位姿
        try:
            pose = list(self.sim.getObjectPose(self.agv_root,
                                               self.sim.handle_world))
            expected_xy = layout.dock_positions[layout.agv_initial_dock]
            yaw = yaw_from_quat(pose[3:7])
            pos_ok = (abs(pose[0] - expected_xy[0]) <= 0.02
                      and abs(pose[1] - expected_xy[1]) <= 0.02)
            yaw_ok = abs((yaw - layout.agv_initial_yaw + math.pi)
                         % (2.0 * math.pi) - math.pi) <= 0.02
            check("stage4_agv_initial_pose", pos_ok and yaw_ok,
                  f"pos=({pose[0]:.4f},{pose[1]:.4f}) yaw={yaw:.4f}")
        except Exception as exc:
            check("stage4_agv_initial_pose", False, str(exc))

        # 会话资源不得存在
        shadow = sorted({alias_of(self.sim, h) for h in all_objects(self.sim)
                         if (alias_of(self.sim, h) or "").startswith(
                             cfg.SESSION_RESOURCE_PREFIX)})
        check("stage4_no_session_resources", not shadow, f"found={shadow}")

        # 地面/共享对象尺寸未变 (阶段四不改地面)
        try:
            floor = self.handle(base.ALIASES["floor"])
            size = _shape_size(self.sim, floor)
            check("stage4_floor_unchanged",
                  size is not None
                  and all(abs(size[i] - base.FLOOR_SIZE[i]) <= 0.002
                          for i in range(3)),
                  f"size={size}")
        except Exception as exc:
            check("stage4_floor_unchanged", False, str(exc))

        self.build_report["stage4"] = {
            "layout": layout.as_dict(),
            "layout_report": layout_report,
            "units": {unit_id: unit.as_dict()
                      for unit_id, unit in self.units.items()},
            "lines": dict(self.line_meta),
            "agv_initial_pose": layout.agv_pose_at(
                layout.agv_initial_dock),
            "agv_route_rect": list(layout.agv_route_rect),
        }
        self.build_report["tools"] = {
            unit_id: self.tool_geometry_for(unit_id)
            for unit_id in self.units}
        self.build_report["tool"] = self.tool_geometry_for("A")
        ok = all(item["ok"] for item in checks)
        self.build_report["checks"] = checks
        self.build_report["ok"] = ok
        return self.build_report


# ═══════════════════════════════════════════════════════════════
# 传送带构建/测量
# ═══════════════════════════════════════════════════════════════

def _rotated_world(line, local_x: float, local_y: float,
                   z: float) -> list[float]:
    c, s = math.cos(line.yaw), math.sin(line.yaw)
    return [line.center[0] + c * local_x - s * local_y,
            line.center[1] + s * local_x + c * local_y,
            float(z)]


def _create_line(sim, parent: int, line) -> dict:
    pos = (line.center[0], line.center[1], base.CONVEYOR_SURFACE_Z)
    belt = _load_model(sim, "conveyor", line.conveyor, pos, parent,
                       strip_scripts=False)
    bbox = _model_bbox(sim, belt)
    if bbox is None:
        raise Stage4SceneError(f"无法读取 {line.conveyor} native 包围盒")
    native_len = bbox[3] - bbox[0]
    if native_len <= 1e-6:
        raise Stage4SceneError(f"{line.conveyor} native 长度异常")
    scale = line.design_length / native_len
    if abs(scale - 1.0) > 1e-6:
        sim.scaleObjects([belt], scale, True)
        sim.setObjectPosition(belt, list(pos), sim.handle_world)
        bbox = _model_bbox(sim, belt)
    actual_length = (bbox[3] - bbox[0]) if bbox else native_len * scale
    actual_width = (bbox[4] - bbox[1]) if bbox else \
        base.CONVEYOR_NATIVE_WIDTH * scale

    run = _measure_top_run(sim, belt)
    sim.setObjectPosition(
        belt, [line.center[0], line.center[1],
               base.CONVEYOR_SURFACE_Z - run["surface_off"]],
        sim.handle_world)
    sim.setObjectOrientation(belt, [0.0, 0.0, float(line.yaw)],
                             sim.handle_world)
    run = _measure_top_run(sim, belt)
    run["actual_length"] = actual_length
    run["actual_width"] = actual_width
    run["native_length"] = native_len
    run["scale"] = scale

    entry_local = run["min_x"] + base.CONVEYOR_ENTRY_INSET
    stop_local = run["max_x"] - base.CONVEYOR_STOP_INSET
    entry_world = _rotated_world(line, entry_local, 0.0,
                                 base.CONVEYOR_SURFACE_Z)
    stop_world = _rotated_world(line, stop_local, 0.0,
                                base.CONVEYOR_SURFACE_Z)

    entry = int(sim.createDummy(0.02))
    sim.setObjectAlias(entry, line.entry_alias)
    sim.setObjectParent(entry, belt, True)
    sim.setObjectPosition(entry, [entry_local, 0.0, run["surface_off"]],
                          sim.handle_parent)
    stop = int(sim.createDummy(0.02))
    sim.setObjectAlias(stop, line.stop_alias)
    sim.setObjectParent(stop, belt, True)
    sim.setObjectPosition(stop, [stop_local, 0.0, run["surface_off"]],
                          sim.handle_parent)

    _create_cylinder_sensor(
        sim, line.sensor_alias, parent=belt,
        local_pos=[stop_local, 0.0,
                   base.PICK_SENSOR_Z - base.CONVEYOR_SURFACE_Z],
        local_orientation=base.PICK_SENSOR_ORIENTATION,
        range_=base.PICK_SENSOR_RANGE, radius=base.PICK_SENSOR_RADIUS)

    model_cfg = configure_conveyor_model(
        sim, belt, cfg.LINE_ACCEL, 0.0)
    if model_cfg is None:
        raise Stage4SceneError(
            f"无法写入 {line.conveyor} customData.__config__")
    scripts = _find_scripts(sim, belt)
    return {
        "line_id": line.line_id,
        "conveyor": line.conveyor,
        "handle": int(belt),
        "native_length": native_len,
        "scale": scale,
        "actual_length": actual_length,
        "actual_width": actual_width,
        "top_run": {"min_x": run["min_x"], "max_x": run["max_x"]},
        "surface_off": run["surface_off"],
        "surface_z": base.CONVEYOR_SURFACE_Z,
        "entry_local_x": entry_local,
        "stop_local_x": stop_local,
        "entry_world": entry_world,
        "stop_world": stop_world,
        "script_handle": scripts[0] if scripts else -1,
        "drive_mode": (base.DRIVE_SOURCE_SCRIPT if scripts
                       else "PYTHON_PAD_DRIVEN"),
        "config_accel": model_cfg.get("accel"),
        "config_target_vel": model_cfg.get("targetVel"),
    }


def _adopt_line_meta(sim, line) -> dict:
    belt = resolve_alias(sim, line.conveyor)
    bbox = _model_bbox(sim, belt)
    run = _measure_top_run(sim, belt)
    entry = resolve_alias(sim, line.entry_alias, required=False)
    stop = resolve_alias(sim, line.stop_alias, required=False)
    entry_world = (list(sim.getObjectPosition(entry, sim.handle_world))
                   if entry >= 0 else list(line.entry))
    stop_world = (list(sim.getObjectPosition(stop, sim.handle_world))
                  if stop >= 0 else list(line.stop))
    scripts = _find_scripts(sim, belt)
    try:
        model_cfg = sim.readCustomTableData(belt, "__config__")
    except Exception:
        model_cfg = None
    return {
        "line_id": line.line_id,
        "conveyor": line.conveyor,
        "handle": int(belt),
        "actual_length": (bbox[3] - bbox[0]) if bbox else None,
        "actual_width": (bbox[4] - bbox[1]) if bbox else None,
        "top_run": {"min_x": run["min_x"], "max_x": run["max_x"]},
        "surface_off": run["surface_off"],
        "surface_z": base.CONVEYOR_SURFACE_Z,
        "entry_local_x": float(sim.getObjectPosition(entry, belt)[0])
        if entry >= 0 else None,
        "stop_local_x": float(sim.getObjectPosition(stop, belt)[0])
        if stop >= 0 else None,
        "entry_world": entry_world,
        "stop_world": stop_world,
        "script_handle": scripts[0] if scripts else -1,
        "drive_mode": (base.DRIVE_SOURCE_SCRIPT if scripts
                       else "PYTHON_PAD_DRIVEN"),
        "config_accel": (model_cfg.get("accel")
                         if isinstance(model_cfg, dict) else None),
        "config_target_vel": (model_cfg.get("targetVel")
                              if isinstance(model_cfg, dict) else None),
    }


# ═══════════════════════════════════════════════════════════════
# 构建
# ═══════════════════════════════════════════════════════════════

def _stage4_aliases() -> set[str]:
    """只包含**阶段四新增**对象别名 (不含 A/B 既有对象)."""
    aliases: set[str] = set()
    for unit_id in ("C", "D", "E"):
        aliases.update(cfg.UNIT_ALIASES[unit_id].values())
    for line_id in ("line_s1b", "line_s2d", "line_ce"):
        aliases.update(cfg.ALIASES["lines"][line_id].values())
    aliases.update({
        cfg.HANDOFF_AC["sensor"],
        cfg.ALIASES["warehouse_s2"],
        cfg.ALIASES["source_s1_b"],
        cfg.ALIASES["source_s2_d"],
        cfg.ALIASES["dock_sensor"]["D"],
        cfg.ALIASES["dock_sensor"]["E"],
        cfg.E_OUTPUT["alias"],
        cfg.E_OUTPUT_TABLE["alias"],
        "PlaceTarget_Conveyor_C_to_E",
        "PlaceTarget_AGV_Dock_D",
    })
    for spec in cfg.E_BUFFERS.values():
        aliases.update({spec["alias"], spec["sensor"]})
    return aliases


def _remove_stage4_objects(sim) -> None:
    aliases = _stage4_aliases()
    for _ in range(3):
        pending = [h for h in all_objects(sim)
                   if alias_of(sim, h) in aliases]
        if not pending:
            break
        try:
            sim.removeObjects(pending)
        except Exception:
            for handle in pending:
                try:
                    sim.removeObjects([handle])
                except Exception:
                    pass


def _resolve_unit4(sim, spec: UnitSpec4, layout: Stage4Layout,
                   index: Optional[dict] = None) -> Stage4Unit:
    aliases = spec.aliases
    index = index or alias_index(sim)

    def find(alias: str) -> int:
        return resolve_alias(sim, alias, index=index)

    arm = find(aliases["robot"])
    connection = -1
    for handle in subtree(sim, arm):
        if alias_of(sim, handle) == "connection":
            connection = handle
            if sim.getObjectType(handle) == sim.object_forcesensor_type:
                break
    if connection < 0:
        raise Stage4SceneError(f"{aliases['robot']} 找不到 connection 末端")
    joints = _chain_joints(sim, arm, connection)
    if len(joints) != 6:
        raise Stage4SceneError(
            f"{aliases['robot']} 关节数不是 6: {len(joints)}")

    pad = find(aliases["suction"])
    model_sensor = -1
    link = -1
    for handle in subtree(sim, pad):
        alias = alias_of(sim, handle)
        if alias in (aliases["model_sensor"], "Sensor") and \
                sim.getObjectType(handle) == sim.object_proximitysensor_type:
            model_sensor = handle
        elif alias == "Link" and sim.getObjectType(handle) == \
                sim.object_forcesensor_type:
            link = handle

    station = (find(aliases["station"]) if spec.has_station else -1)
    dock_sensor = (find(spec.dock_sensor_alias)
                   if spec.has_dock else -1)
    output_targets = {
        "primary": find(aliases["output_target"]),
    }
    if spec.unit_id == "E":
        output_targets.update({
            "buffer_e_c": find(cfg.E_BUFFERS["buffer_e_c"]["alias"]),
            "buffer_e_d": find(cfg.E_BUFFERS["buffer_e_d"]["alias"]),
            "e_output": find(cfg.E_OUTPUT["alias"]),
        })
    return Stage4Unit(
        unit_id=spec.unit_id, index=spec.index, spec=spec,
        layout=layout.view(spec.unit_id),
        island=find(aliases["island"]), robot_root=arm,
        robot_connection=connection, robot_joints=joints,
        suction_root=pad, tcp=find(aliases["tcp"]),
        suction_sensor=find(aliases["suction_sensor"]),
        suction_model_sensor=model_sensor, suction_link=link,
        ik_target=find(aliases["ik_target"]), station=station,
        place_target=find(aliases["output_target"]),
        dock_sensor=dock_sensor, output_targets=output_targets)


def _create_source_dummies(sim, island_root: int) -> None:
    for key in ("s1b", "s2d"):
        spec = cfg.SOURCE_ITEMS[key]
        alias = (cfg.ALIASES["source_s1_b"] if key == "s1b"
                 else cfg.ALIASES["source_s2_d"])
        _create_dummy(sim, alias, spec["position"], island_root)


def _create_handoff_sensor(sim, island_root: int) -> None:
    pos = cfg.HANDOFF_AC["position"]
    _create_cylinder_sensor(
        sim, cfg.HANDOFF_AC["sensor"], parent=island_root,
        local_pos=[pos[0], pos[1],
                   base.STATION_TOP_Z + base.MATERIAL_HALF_HEIGHT + 0.10],
        local_orientation=base.PICK_SENSOR_ORIENTATION,
        range_=base.PICK_SENSOR_RANGE, radius=base.PICK_SENSOR_RADIUS)


def _create_e_targets(sim, island_root: int) -> None:
    material_z = base.ISLAND_TOP_Z + base.MATERIAL_HALF_HEIGHT
    for key, spec in cfg.E_BUFFERS.items():
        _create_dummy(sim, spec["alias"],
                      [spec["position"][0], spec["position"][1], material_z],
                      island_root)
        _create_cylinder_sensor(
            sim, spec["sensor"], parent=island_root,
            local_pos=[spec["position"][0], spec["position"][1],
                       material_z + 0.10],
            local_orientation=base.PICK_SENSOR_ORIENTATION,
            range_=base.PICK_SENSOR_RANGE, radius=base.PICK_SENSOR_RADIUS)
    table = cfg.E_OUTPUT_TABLE
    _create_box(sim, table["alias"], table["size"],
                [table["position"][0], table["position"][1],
                 table["size"][2] / 2.0],
                color=(0.55, 0.55, 0.62), parent=island_root)
    out = cfg.E_OUTPUT
    _create_dummy(sim, out["alias"],
                  [out["position"][0], out["position"][1],
                   out["material_z"]], island_root)


def _create_dock_sensors(sim, mobile_root: int) -> None:
    for unit_id in ("D", "E"):
        pos = cfg.UNIT_DEFS[unit_id]["dock"]
        _create_cylinder_sensor(
            sim, cfg.ALIASES["dock_sensor"][unit_id], parent=mobile_root,
            local_pos=[pos[0], pos[1], base.AGV_DOCK_SENSOR_Z],
            local_orientation=base.PICK_SENSOR_ORIENTATION,
            range_=base.AGV_DOCK_SENSOR_RANGE,
            radius=base.AGV_DOCK_SENSOR_RADIUS)


def _create_output_targets(sim, island_root: int, layout: Stage4Layout
                           ) -> None:
    _create_dummy(sim, "PlaceTarget_Conveyor_C_to_E",
                  layout.lines["line_ce"].place_material_pose, island_root)
    _create_dummy(sim, "PlaceTarget_AGV_Dock_D",
                  layout.unit("D").agv_material_pose, island_root)


def build_stage4_scene(sim, base_scene: Stage3Scene,
                       layout: Optional[Stage4Layout] = None) -> Stage4Scene:
    """在已加载的 stage3 场景上增加 C/D/E 与三条物流线.

    调用前必须由生命周期加载 stage3 工件并处于停止态. 本函数不重建 A/B
    单元, 不清场; 任何失败都会尝试删除本次新建对象.
    """
    layout = default_layout() if layout is None else layout
    from .layout import validate_layout
    report = validate_layout(layout)
    if not report["ok"]:
        failed = [c for c in report["checks"] if not c["ok"]]
        raise Stage4SceneError(
            f"布局 {layout.name!r} 静态校验失败: " + "; ".join(
                f"{c['check']}({c['detail']})" for c in failed))
    try:
        state = int(sim.getSimulationState())
    except Exception as exc:
        raise Stage4SceneError(f"无法读取仿真状态: {exc}") from exc
    if state != int(sim.simulation_stopped):
        raise Stage4SceneError(f"构建阶段四场景必须处于停止态 (state={state})")

    index = alias_index(sim)
    existing = sorted(alias for alias in _stage4_aliases() if alias in index)
    if existing:
        raise Stage4SceneError(f"场景已包含阶段四对象: {existing}")

    island_root = base_scene.handle(base.ALIASES["roots"][2])
    mobile_root = base_scene.handle(base.ALIASES["roots"][3])
    conveyor_root = base_scene.handle(base.ALIASES["roots"][1])
    warehouse_root = base_scene.handle(base.ALIASES["roots"][0])
    if min(island_root, mobile_root, conveyor_root, warehouse_root) < 0:
        raise Stage4SceneError("父场景缺少 Warehouse/Conveyor/Island/Mobile 根")

    try:
        _create_box(sim, cfg.ALIASES["warehouse_s2"],
                    cfg.WAREHOUSE_S2_SIZE,
                    (cfg.WAREHOUSE_S2_POS[0], cfg.WAREHOUSE_S2_POS[1],
                     cfg.WAREHOUSE_S2_SIZE[2] / 2.0),
                    color=(0.20, 0.80, 0.80), parent=warehouse_root)

        line_meta = {}
        line_meta["line_s1a"] = _adopt_line_meta(sim, layout.lines["line_s1a"])
        # 阶段四所有物流线必须在 simulation start 时保持停止: 来源物料在
        # 单元任务启动前不得自行走完行程. 已在 stage1/3 中冻结的 accel
        # 保持不变, 仅把初始目标速度改为 0.
        belt_a = resolve_alias(sim, layout.lines["line_s1a"].conveyor)
        cfg_a = configure_conveyor_model(sim, belt_a, cfg.LINE_ACCEL, 0.0)
        if cfg_a is None:
            raise Stage4SceneError("无法重置 S1→A 传送带启动速度")
        line_meta["line_s1a"]["config_accel"] = cfg_a.get("accel")
        line_meta["line_s1a"]["config_target_vel"] = cfg_a.get("targetVel")
        for line_id in ("line_s1b", "line_s2d", "line_ce"):
            line_meta[line_id] = _create_line(
                sim, conveyor_root, layout.lines[line_id])

        for unit_id in ("C", "D", "E"):
            spec = layout.unit(unit_id)
            island = _create_box(
                sim, spec.aliases["island"], spec.island_size,
                (spec.island_pos[0], spec.island_pos[1],
                 spec.island_top_z / 2.0),
                color=(0.35, 0.40, 0.50), parent=island_root)
            _create_unit_robot(sim, island, spec)
            _create_dummy(sim, spec.aliases["ik_target"],
                          _ik_target_pose(layout, unit_id), island_root)

        _create_output_targets(sim, island_root, layout)
        _create_source_dummies(sim, island_root)
        _create_handoff_sensor(sim, island_root)
        _create_e_targets(sim, island_root)
        _create_dock_sensors(sim, mobile_root)
    except Exception:
        _remove_stage4_objects(sim)
        raise

    scene = Stage4Scene(sim=sim, layout=layout, stage4_layout=layout)
    _inherit_base_scene(scene, base_scene)
    new_index = alias_index(sim)
    scene.units = {
        unit_id: _resolve_unit4(sim, layout.unit(unit_id), layout, new_index)
        for unit_id in cfg.UNIT_IDS
    }
    scene.line_meta = {
        line_id: _adopt_line_meta(sim, layout.lines[line_id])
        for line_id in cfg.LINE_IDS
    }
    for unit in scene.units.values():
        for key, alias in unit.aliases.items():
            required = (key != "station") or unit.spec.has_station
            handle = resolve_alias(sim, alias, required=required,
                                   index=new_index)
            if handle >= 0:
                scene.handles[alias] = handle
        if unit.dock_sensor >= 0:
            scene.handles[unit.spec.dock_sensor_alias] = unit.dock_sensor
    for alias in cfg.REQUIRED_ALIASES:
        scene.handles[alias] = resolve_alias(sim, alias, index=new_index)
    scene.parent_build_report = dict(base_scene.build_report or {})

    result = scene.verify()
    if not result["ok"]:
        failed = [item for item in result["checks"] if not item["ok"]]
        _remove_stage4_objects(sim)
        raise Stage4SceneError(
            "阶段四场景契约校验失败: " + "; ".join(
                f"{item['name']}({item['detail']})" for item in failed))
    return scene


def _ik_target_pose(layout: Stage4Layout, unit_id: str) -> list[float]:
    if unit_id == "C":
        pose = layout.lines["line_ce"].place_material_pose
    elif unit_id == "D":
        pose = layout.lines["line_s2d"].pick_material_pose
    elif unit_id == "E":
        pose = layout.lines["line_ce"].pick_material_pose
    else:  # pragma: no cover - 只有新单元调用
        pose = layout.unit(unit_id).station_material_pose
    return [pose[0], pose[1], pose[2] + base.MATERIAL_HALF_HEIGHT,
            *base.TOOL_TARGET_QUATERNION]


def _inherit_base_scene(scene: Stage4Scene,
                        base_scene: Stage3Scene) -> None:
    """复制 A/B/传送带/AGV 的运行时字段 (句柄在当前会话有效)."""
    scene.handles.update(dict(base_scene.handles))
    scene.robot_root = base_scene.robot_root
    scene.robot_connection = base_scene.robot_connection
    scene.robot_joints = list(base_scene.robot_joints)
    scene.suction_root = base_scene.suction_root
    scene.tcp = base_scene.tcp
    scene.suction_sensor = base_scene.suction_sensor
    scene.suction_model_sensor = base_scene.suction_model_sensor
    scene.suction_link = base_scene.suction_link
    scene.agv_root = base_scene.agv_root
    scene.agv_left_wheel = base_scene.agv_left_wheel
    scene.agv_right_wheel = base_scene.agv_right_wheel
    scene.conveyor_meta = dict(base_scene.conveyor_meta or {})
    scene.warnings = list(base_scene.warnings or [])


# ═══════════════════════════════════════════════════════════════
# 从已加载工件重新解析
# ═══════════════════════════════════════════════════════════════

def adopt_stage4_scene(sim, layout: Optional[Stage4Layout] = None
                       ) -> Stage4Scene:
    """把当前已加载的 stage4 .ttt 重新解析为 Stage4Scene (只读)."""
    layout = default_layout() if layout is None else layout
    scene = Stage4Scene(sim=sim, layout=layout, stage4_layout=layout)
    index = alias_index(sim)

    for name in base.ALIASES["roots"]:
        scene.handles[name] = resolve_alias(sim, name, index=index)
    for key, alias in base.ALIASES.items():
        if key == "roots":
            continue
        handle = resolve_alias(sim, alias, required=False, index=index)
        if handle >= 0:
            scene.handles[alias] = handle
    for alias in cfg.REQUIRED_ALIASES:
        scene.handles[alias] = resolve_alias(sim, alias, index=index)

    scene.units = {
        unit_id: _resolve_unit4(sim, layout.unit(unit_id), layout, index)
        for unit_id in cfg.UNIT_IDS
    }
    unit_a = scene.units["A"]
    scene.robot_root = unit_a.robot_root
    scene.robot_connection = unit_a.robot_connection
    scene.robot_joints = list(unit_a.robot_joints)
    scene.suction_root = unit_a.suction_root
    scene.tcp = unit_a.tcp
    scene.suction_sensor = unit_a.suction_sensor
    scene.suction_model_sensor = unit_a.suction_model_sensor
    scene.suction_link = unit_a.suction_link

    agv = resolve_alias(sim, base.ALIASES["agv"], index=index)
    agv_joints = {alias_of(sim, handle): handle
                  for handle in subtree(sim, agv)
                  if sim.getObjectType(handle) == sim.object_joint_type}
    left = agv_joints.get("leftMotor", -1)
    right = agv_joints.get("rightMotor", -1)
    if left < 0 or right < 0:
        raise Stage4SceneError("pioneer p3dx 找不到 leftMotor/rightMotor")
    scene.agv_root = agv
    scene.agv_left_wheel = left
    scene.agv_right_wheel = right
    scene.conveyor_meta = _adopt_line_meta(sim, layout.lines["line_s1a"])
    scene.line_meta = {
        line_id: _adopt_line_meta(sim, layout.lines[line_id])
        for line_id in cfg.LINE_IDS
    }
    return scene
