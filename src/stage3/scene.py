"""scene.py — 阶段三双工位场景派生、句柄解析与契约断言 (06-12 §3.0–§3.1).

阶段三不从空场景重建: 它加载已验收的 stage2 工件, 保留 A 单元、共享
传送带、AGV 与阶段二契约, 只在**阶段三副本**中增加:
  - `Island_B` / `Island_B_Station`;
  - `Robot_UR5_02` + `SuctionPad_02` + `SuctionPad_02_TCP` +
    `Sensor_SuctionPad_02` + `IK_Target_02`;
  - `Sensor_AGV_Dock_B` 与两个放置目标 dummy;
  - `Stage3_PickZone` 共享取料区标记;
  - AGV 初始位姿改为 dock A 朝 -Y (构建期停止态一次设置, 非运行期瞬移).

A 单元的所有对象保持不变. 所有别名都是扁平下划线, 不使用 `/` 路径
(CLAUDE.md §3). 构建完成后执行 A/B 两套构建契约与阶段三布局断言.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from stage1 import config as base
from stage1 import physics as phys
from stage1.scene import (SceneBuildError, Stage1Scene, _chain_joints,
                          _create_box, _create_cylinder_sensor,
                          _end_link_max_z, _find_scripts, _load_model,
                          _measure_top_run, _model_bbox, _quat_equivalent,
                          _shape_size, _world_axis, _dot, alias_index,
                          alias_of, all_objects, resolve_alias, subtree)

from . import config as cfg
from .layout import (Stage3Layout, UnitLayoutView, UnitSpec, default_layout,
                     layout_from_dict, quat_from_yaw, validate_layout,
                     yaw_from_quat)


class Stage3SceneError(SceneBuildError):
    code = "STAGE3_SCENE"


# ═══════════════════════════════════════════════════════════════
# 单工位句柄
# ═══════════════════════════════════════════════════════════════

@dataclass
class Stage3Unit:
    unit_id: str
    index: int
    spec: UnitSpec
    layout: UnitLayoutView
    island: int
    station: int
    robot_root: int
    robot_connection: int
    robot_joints: list[int]
    suction_root: int
    tcp: int
    suction_sensor: int
    suction_model_sensor: int
    suction_link: int
    ik_target: int
    place_target: int
    dock_sensor: int

    @property
    def aliases(self) -> dict[str, str]:
        return self.spec.aliases

    def handle(self, key: str) -> int:
        return {
            "island": self.island,
            "station": self.station,
            "robot": self.robot_root,
            "suction": self.suction_root,
            "tcp": self.tcp,
            "suction_sensor": self.suction_sensor,
            "ik_target": self.ik_target,
            "place_target": self.place_target,
            "dock_sensor": self.dock_sensor,
        }.get(key, -1)

    def as_dict(self) -> dict:
        return {
            "unit_id": self.unit_id,
            "index": int(self.index),
            "arm_base": [round(float(v), 6) for v in self.spec.arm_base],
            "island_pos": [round(float(v), 6)
                           for v in self.spec.island_pos],
            "station_pos": [round(float(v), 6)
                            for v in self.spec.station_pos],
            "dock": [round(float(v), 6) for v in self.spec.dock],
            "aliases": self.aliases,
            "joints": len(self.robot_joints),
            "handles": {
                "island": int(self.island),
                "station": int(self.station),
                "robot": int(self.robot_root),
                "connection": int(self.robot_connection),
                "suction": int(self.suction_root),
                "tcp": int(self.tcp),
                "suction_sensor": int(self.suction_sensor),
                "model_sensor": int(self.suction_model_sensor),
                "link": int(self.suction_link),
                "ik_target": int(self.ik_target),
                "place_target": int(self.place_target),
                "dock_sensor": int(self.dock_sensor),
            },
        }


# ═══════════════════════════════════════════════════════════════
# 场景
# ═══════════════════════════════════════════════════════════════

@dataclass
class Stage3Scene(Stage1Scene):
    stage3_layout: Optional[Stage3Layout] = None
    units: dict[str, Stage3Unit] = field(default_factory=dict)
    parent_build_report: dict = field(default_factory=dict)

    # ── 工位访问 ────────────────────────────────────────────

    def unit(self, unit_id: str) -> Stage3Unit:
        try:
            return self.units[str(unit_id)]
        except KeyError as exc:
            raise Stage3SceneError(
                f"未知工位 {unit_id!r}; 可用 {sorted(self.units)}") from exc

    @property
    def unit_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.units))

    @property
    def builder_info(self) -> dict:
        return {
            "module": "stage3.scene",
            "version": cfg.STAGE3_SCENE_BUILDER_VERSION,
            "clear_scene": False,
            "loads_parent_artifact": True,
            "builds_second_unit": True,
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

    # ── 描述/证据 ────────────────────────────────────────────

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
            h = hits[0]
            parent = -1
            try:
                parent = int(self.sim.getObjectParent(h))
            except Exception:
                pass
            rows.append({
                "alias": alias,
                "handle": h,
                "type": int(self.sim.getObjectType(h)),
                "parent": alias_of(self.sim, parent) if parent >= 0 else "",
                "world_pos": [round(float(v), 4)
                              for v in self.sim.getObjectPosition(
                                  h, self.sim.handle_world)],
            })
        rows.sort(key=lambda row: row.get("alias") or "")
        return rows

    def tool_geometry_for(self, unit_id: str) -> dict:
        unit = self.unit(unit_id)
        sim = self.sim
        pad, tcp = unit.suction_root, unit.tcp
        out = {
            "unit_id": unit_id,
            "version": base.SUCTION_TOOL_VERSION,
            "ok": pad >= 0 and tcp >= 0,
            "mount_z": base.SUCTION_MOUNT_Z,
            "mount_quaternion": list(base.SUCTION_MOUNT_QUATERNION),
            "flange_protrusion_z": base.SUCTION_FLANGE_PROTRUSION_Z,
            "link_clearance_design": base.SUCTION_LINK_CLEARANCE,
            "joint_positions": [float(sim.getJointPosition(j))
                                for j in unit.robot_joints],
        }
        if not out["ok"]:
            out["error"] = "吸盘或 TCP 句柄缺失"
            return out
        out["pad_world_pose"] = [float(v) for v in sim.getObjectPose(
            pad, sim.handle_world)]
        out["tcp_world_pose"] = [float(v) for v in sim.getObjectPose(
            tcp, sim.handle_world)]
        out["tcp_in_pad"] = [float(v) for v in sim.getObjectPose(tcp, pad)]
        if unit.robot_connection >= 0:
            out["pad_in_connection"] = [float(v) for v in sim.getObjectPose(
                pad, unit.robot_connection)]
        z_pad = _world_axis(sim, pad, 2)
        out["suction_normal_world"] = [-z_pad[0], -z_pad[1], -z_pad[2]]
        for name, handle in (("sensor", unit.suction_sensor),
                             ("model_sensor", unit.suction_model_sensor)):
            if handle >= 0:
                out[f"{name}_axis_world"] = list(
                    _world_axis(sim, handle, 2))
        if unit.robot_connection >= 0:
            try:
                link_max = _end_link_max_z(sim, unit.robot_connection)
                back_plane = base.SUCTION_MOUNT_Z - base.SUCTION_BACK_LOCAL_Z
                out["end_link_max_z_in_connection"] = link_max
                out["pad_back_plane_z"] = back_plane
                out["link_clearance"] = back_plane - link_max
            except Exception as exc:
                out["ok"] = False
                out["link_clearance"] = None
                out["clearance_error"] = str(exc)
        return out

    # ── 契约 ────────────────────────────────────────────────

    def verify(self) -> dict:
        # 先执行阶段一 A 单元/传送带/AGV 的既有契约 (43 项基线保持不变)
        super().verify()
        checks = self.build_report.get("checks", [])
        layout = self.stage3_layout or self.layout
        if layout is None:
            raise Stage3SceneError("Stage3Scene 缺少 layout")

        def check(name, ok, detail=""):
            checks.append({"name": name, "ok": bool(ok), "detail": detail})
            return ok

        layout_report = validate_layout(layout)
        check("stage3_layout_static_validation", layout_report["ok"],
              f"layout={layout.name} "
              f"({sum(1 for c in layout_report['checks'] if c['ok'])}"
              f"/{len(layout_report['checks'])} checks)")
        check("stage3_layout_name", layout.name == cfg.STAGE3_LAYOUT_NAME,
              f"name={layout.name!r} expected={cfg.STAGE3_LAYOUT_NAME!r}")

        index = alias_index(self.sim)
        for alias in cfg.REQUIRED_ALIASES:
            hits = index.get(alias, [])
            check(f"stage3_alias_unique:{alias}", len(hits) == 1,
                  f"{len(hits)} 个匹配")

        for unit_id in ("A", "B"):
            try:
                unit = self.unit(unit_id)
            except Stage3SceneError as exc:
                check(f"stage3_unit_present:{unit_id}", False, str(exc))
                continue
            for row in _verify_unit(self.sim, unit, layout.unit(unit_id)):
                check(f"stage3_{unit_id}:{row['name']}", row["ok"],
                      row["detail"])

        # dock 传感器位置
        for unit_id in ("A", "B"):
            unit = self.unit(unit_id)
            try:
                pos = list(self.sim.getObjectPosition(
                    unit.dock_sensor, self.sim.handle_world))
                expected = [unit.spec.dock[0], unit.spec.dock[1],
                            base.AGV_DOCK_SENSOR_Z]
                ok = all(abs(pos[i] - expected[i]) <= 0.002
                         for i in range(3))
                check(f"stage3_dock_sensor:{unit_id}", ok,
                      f"pos={[round(v, 4) for v in pos]} "
                      f"expected={[round(v, 4) for v in expected]}")
            except Exception as exc:
                check(f"stage3_dock_sensor:{unit_id}", False, str(exc))

        # 共享取料区 dummy
        try:
            pick = self.handle(cfg.ALIASES["pick_zone"])
            pos = list(self.sim.getObjectPosition(pick, self.sim.handle_world))
            expected = list(layout.pick_zone_center)
            ok = pick >= 0 and all(abs(pos[i] - expected[i]) <= 0.005
                                   for i in range(3))
            check("stage3_pick_zone_dummy", ok,
                  f"pos={[round(v, 4) for v in pos]} "
                  f"expected={[round(v, 4) for v in expected]}")
        except Exception as exc:
            check("stage3_pick_zone_dummy", False, str(exc))

        # 放置目标 dummy
        for unit_id in ("A", "B"):
            unit = self.unit(unit_id)
            try:
                pos = list(self.sim.getObjectPosition(
                    unit.place_target, self.sim.handle_world))
                expected = list(unit.spec.station_material_pose)
                ok = all(abs(pos[i] - expected[i]) <= 0.005 for i in range(3))
                check(f"stage3_place_target:{unit_id}", ok,
                      f"pos={[round(v, 4) for v in pos]} "
                      f"expected={[round(v, 4) for v in expected]}")
            except Exception as exc:
                check(f"stage3_place_target:{unit_id}", False, str(exc))

        # AGV 初始位姿: dock A + -Y 朝向. 运行后保存必须失败 (has_run 保护).
        try:
            pose = list(self.sim.getObjectPose(
                self.agv_root, self.sim.handle_world))
            expected_xy = layout.unit("A").dock
            yaw = yaw_from_quat(pose[3:7])
            pos_ok = (abs(pose[0] - expected_xy[0]) <= 0.02
                      and abs(pose[1] - expected_xy[1]) <= 0.02)
            yaw_ok = abs((yaw - layout.agv_initial_yaw + math.pi)
                         % (2.0 * math.pi) - math.pi) <= 0.02
            check("stage3_agv_initial_pose", pos_ok and yaw_ok,
                  f"pos=({pose[0]:.4f},{pose[1]:.4f}) yaw={yaw:.4f} "
                  f"expected=({expected_xy[0]:.4f},{expected_xy[1]:.4f}) "
                  f"yaw={layout.agv_initial_yaw:.4f}")
        except Exception as exc:
            check("stage3_agv_initial_pose", False, str(exc))

        # 会话资源不得在构建/保存时存在
        shadow = sorted({alias_of(self.sim, h) for h in all_objects(self.sim)
                         if (alias_of(self.sim, h) or "").startswith(
                             cfg.SESSION_RESOURCE_PREFIX)})
        check("stage3_no_session_resources", not shadow,
              f"found={shadow}")

        self.build_report["stage3"] = {
            "layout": layout.as_dict(),
            "layout_report": layout_report,
            "units": {unit_id: unit.as_dict()
                      for unit_id, unit in self.units.items()},
            "agv_route_pose_a": layout.agv_route_pose_a,
            "agv_route_pose_b": layout.agv_route_pose_b,
            "agv_route_rect": list(layout.agv_route_rect),
        }
        self.build_report["tool"] = self.tool_geometry_for("A")
        self.build_report["tools"] = {
            unit_id: self.tool_geometry_for(unit_id)
            for unit_id in self.units}
        ok = all(c["ok"] for c in checks)
        self.build_report["checks"] = checks
        self.build_report["ok"] = ok
        return self.build_report


# ═══════════════════════════════════════════════════════════════
# 单元契约
# ═══════════════════════════════════════════════════════════════

def _verify_unit(sim, unit: Stage3Unit, spec: UnitSpec) -> list[dict]:
    out: list[dict] = []

    def check(name, ok, detail=""):
        out.append({"name": name, "ok": bool(ok), "detail": detail})

    check("joint_count", len(unit.robot_joints) == 6,
          f"{len(unit.robot_joints)} 个关节")
    try:
        modes = []
        for joint in unit.robot_joints:
            mode = sim.getJointMode(joint)
            modes.append(int(mode[0] if isinstance(mode, (tuple, list))
                             else mode))
        check("joint_modes",
              all(mode == int(sim.jointmode_kinematic) for mode in modes),
              f"modes={modes}")
    except Exception as exc:
        check("joint_modes", False, f"无法读取: {exc}")

    tcp, pad = unit.tcp, unit.suction_root
    check("tcp_exists", tcp >= 0 and pad >= 0, f"tcp={tcp} pad={pad}")
    if tcp >= 0 and pad >= 0:
        local = list(sim.getObjectPosition(tcp, pad))
        check("tcp_contact_offset",
              all(abs(local[i] - base.TCP_LOCAL_POS[i])
                  <= base.SUCTION_MOUNT_POS_TOL for i in range(3)),
              f"TCP local={[round(v, 5) for v in local]}")
        tcp_q = sim.getObjectQuaternion(tcp, pad)
        check("tcp_local_orientation",
              _quat_equivalent(tcp_q, base.TCP_LOCAL_QUATERNION),
              f"quat={[round(float(v), 6) for v in tcp_q]}")
        if unit.robot_connection >= 0:
            mount = list(sim.getObjectPose(pad, unit.robot_connection))
            expected = [0.0, 0.0, base.SUCTION_MOUNT_Z]
            check("pad_mount_pose",
                  all(abs(mount[i] - expected[i])
                      <= base.SUCTION_MOUNT_POS_TOL for i in range(3))
                  and _quat_equivalent(mount[3:],
                                       base.SUCTION_MOUNT_QUATERNION),
                  f"pad in connection="
                  f"{[round(float(v), 6) for v in mount]}")
        z_pad = _world_axis(sim, pad, 2)
        z_tcp = _world_axis(sim, tcp, 2)
        check("tcp_pad_axis",
              _dot(z_tcp, z_pad) >= 1.0 - base.SUCTION_AXIS_DOT_TOL,
              f"z_tcp·z_pad={_dot(z_tcp, z_pad):.6f}")
        for name, handle in (("sensor", unit.suction_sensor),
                             ("model_sensor", unit.suction_model_sensor)):
            if handle < 0:
                check(f"{name}_present", False, "handle<0")
                continue
            z_sensor = _world_axis(sim, handle, 2)
            dot = _dot(z_sensor, (-z_pad[0], -z_pad[1], -z_pad[2]))
            check(f"{name}_axis", dot >= 1.0 - base.SUCTION_AXIS_DOT_TOL,
                  f"z_sensor·(-z_pad)={dot:.6f}")
        if unit.suction_link >= 0:
            marker = list(sim.getObjectPosition(unit.suction_link, pad))
            check("tcp_contact_marker",
                  math.dist(marker, local) <= base.SUCTION_MOUNT_POS_TOL,
                  f"Link={[round(v, 5) for v in marker]} "
                  f"TCP={[round(v, 5) for v in local]}")
        if unit.robot_connection >= 0:
            try:
                link_max = _end_link_max_z(sim, unit.robot_connection)
                back_plane = (base.SUCTION_MOUNT_Z
                              - base.SUCTION_BACK_LOCAL_Z)
                gap = back_plane - link_max
                check("mount_clearance",
                      gap >= base.SUCTION_LINK_CLEARANCE
                      - base.SUCTION_MOUNT_POS_TOL,
                      f"back={back_plane:.6f} link_max={link_max:.6f} "
                      f"gap={gap:.6f}")
            except Exception as exc:
                check("mount_clearance", False, f"无法测量: {exc}")

    for name, target in spec.targets().items():
        distance = math.dist(spec.arm_base, target[:3])
        check(f"reach:{name}", distance <= base.REACH_LIMIT,
              f"{distance:.3f} m <= {base.REACH_LIMIT:.3f} m")

    try:
        z = float(sim.getObjectPosition(tcp, sim.handle_world)[2])
        check("tcp_above_island", z > spec.island_top_z, f"TCP z={z:.3f}")
    except Exception as exc:
        check("tcp_above_island", False, str(exc))
    return out


# ═══════════════════════════════════════════════════════════════
# 构建第二单元
# ═══════════════════════════════════════════════════════════════

def _create_unit_robot(sim, island: int, spec: UnitSpec) -> dict:
    """按稳定别名创建第二台内置 UR5 + suction pad (与 A 单元同构)."""
    aliases = spec.aliases
    arm = _load_model(sim, "ur5", aliases["robot"], spec.arm_base, island)
    for h in subtree(sim, arm):
        if sim.getObjectType(h) == sim.object_shape_type:
            phys.set_static(sim, h, True)
            phys.set_respondable(sim, h, False)
    if sim.getObjectType(arm) == sim.object_shape_type:
        phys.set_static(sim, arm, True)
        phys.set_respondable(sim, arm, False)

    connection = -1
    for h in subtree(sim, arm):
        if alias_of(sim, h) == "connection":
            connection = h
            if sim.getObjectType(h) == sim.object_forcesensor_type:
                break
    if connection < 0:
        raise Stage3SceneError(f"{aliases['robot']} 找不到 connection 末端")

    joints = _chain_joints(sim, arm, connection)
    if len(joints) != 6:
        raise Stage3SceneError(
            f"{aliases['robot']} 关节数不是 6: {len(joints)}")

    low, high = base.ARM_JOINT_LIMIT
    for joint, q in zip(joints, base.ARM_SEED_JOINTS):
        sim.setJointInterval(joint, False, [low, high - low])
        sim.setJointMode(joint, sim.jointmode_kinematic, 0)
        sim.setJointPosition(joint, float(q))

    pad = _load_model(sim, "suction_pad", aliases["suction"], None, connection)
    sim.setObjectPose(
        pad, [0.0, 0.0, base.SUCTION_MOUNT_Z,
              *base.SUCTION_MOUNT_QUATERNION], connection)
    for h in subtree(sim, pad):
        if sim.getObjectType(h) == sim.object_shape_type:
            phys.set_static(sim, h, True)
            phys.set_respondable(sim, h, False)
    if sim.getObjectType(pad) == sim.object_shape_type:
        phys.set_static(sim, pad, True)
        phys.set_respondable(sim, pad, False)

    model_sensor = -1
    link = -1
    for h in subtree(sim, pad):
        a = alias_of(sim, h)
        if a == "Sensor" and sim.getObjectType(h) == \
                sim.object_proximitysensor_type:
            model_sensor = h
        elif a == "Link" and sim.getObjectType(h) == \
                sim.object_forcesensor_type:
            link = h
    if model_sensor >= 0:
        sim.setObjectAlias(model_sensor, aliases["model_sensor"])

    tcp = int(sim.createDummy(0.01))
    sim.setObjectAlias(tcp, aliases["tcp"])
    sim.setObjectParent(tcp, pad, True)
    sim.setObjectPose(tcp, [*base.TCP_LOCAL_POS, *base.TCP_LOCAL_QUATERNION],
                      sim.handle_parent)

    sensor = _create_cylinder_sensor(
        sim, aliases["suction_sensor"], parent=tcp,
        local_pos=[0.0, 0.0, 0.0],
        local_orientation=base.PICK_SENSOR_ORIENTATION,
        range_=base.SUCTION_SENSOR_RANGE,
        radius=base.SUCTION_SENSOR_RADIUS)
    return {
        "arm": arm,
        "connection": connection,
        "joints": joints,
        "pad": pad,
        "sensor": sensor,
        "model_sensor": model_sensor,
        "link": link,
        "tcp": tcp,
    }


def _restore_shared_objects(sim, agv_root: int,
                            dock_a_local=None, agv_pose=None) -> None:
    """构建失败时恢复 AGV 初始位姿与 dock A 传感器局部位置."""
    if agv_pose is not None and agv_root >= 0:
        try:
            sim.setObjectPose(agv_root, list(agv_pose), sim.handle_world)
            sim.resetDynamicObject(agv_root)
        except Exception:
            pass
    if dock_a_local is not None:
        try:
            sensor = resolve_alias(sim, cfg.ALIASES["dock_sensor"]["A"],
                                   required=False)
            if sensor >= 0:
                mobile = resolve_alias(sim, base.ALIASES["roots"][3])
                sim.setObjectPosition(sensor, list(dock_a_local), mobile)
        except Exception:
            pass


def _create_dummy(sim, alias: str, pose, parent: int) -> int:
    h = int(sim.createDummy(0.01))
    sim.setObjectAlias(h, alias)
    if parent >= 0:
        sim.setObjectParent(h, parent, True)
    values = [float(v) for v in pose]
    if len(values) == 3:
        sim.setObjectPosition(h, values, sim.handle_world)
    elif len(values) == 7:
        sim.setObjectPose(h, values, sim.handle_world)
    else:
        raise Stage3SceneError(
            f"{alias} dummy 位姿必须是 3 或 7 元素, 实际 {len(values)}")
    return h


def _move_agv_to_layout(sim, agv_root: int, layout: Stage3Layout) -> None:
    """构建期把 AGV 放到 dock A 并转向 -Y; 只在停止态执行一次."""
    pose = layout.agv_route_pose_a
    sim.setObjectPose(agv_root, list(pose), sim.handle_world)
    try:
        sim.resetDynamicObject(agv_root)
    except Exception:
        pass


def _move_dock_sensor(sim, sensor: int, dock) -> None:
    # 用世界坐标设置, 不假设 Mobile 层级根位于原点.
    sim.setObjectPosition(
        sensor, [float(dock[0]), float(dock[1]), base.AGV_DOCK_SENSOR_Z],
        sim.handle_world)


def _remove_second_unit(sim) -> None:
    """构建失败时删除 B 单元对象; 不影响 A 单元/共享对象."""
    aliases = set(cfg.ALIASES["unit"]["B"].values()) | {
        cfg.ALIASES["dock_sensor"]["B"],
        cfg.ALIASES["place_target_a"],
        cfg.ALIASES["place_target_b"],
        cfg.ALIASES["pick_zone"],
    }
    for _ in range(3):
        pending = [h for h in all_objects(sim) if alias_of(sim, h) in aliases]
        if not pending:
            break
        try:
            sim.removeObjects(pending)
        except Exception:
            for h in pending:
                try:
                    sim.removeObjects([h])
                except Exception:
                    pass


def _resolve_unit(sim, spec: UnitSpec,
                  index: Optional[dict] = None) -> Stage3Unit:
    aliases = spec.aliases
    index = index or alias_index(sim)

    def find(alias: str) -> int:
        return resolve_alias(sim, alias, index=index)

    arm = find(aliases["robot"])
    connection = -1
    for h in subtree(sim, arm):
        if alias_of(sim, h) == "connection":
            connection = h
            if sim.getObjectType(h) == sim.object_forcesensor_type:
                break
    if connection < 0:
        raise Stage3SceneError(f"{aliases['robot']} 找不到 connection 末端")
    joints = _chain_joints(sim, arm, connection)
    if len(joints) != 6:
        raise Stage3SceneError(f"{aliases['robot']} 关节数不是 6: {len(joints)}")

    pad = find(aliases["suction"])
    model_sensor = -1
    link = -1
    for h in subtree(sim, pad):
        a = alias_of(sim, h)
        if a == aliases["model_sensor"] and sim.getObjectType(h) == \
                sim.object_proximitysensor_type:
            model_sensor = h
        elif a == "Sensor" and sim.getObjectType(h) == \
                sim.object_proximitysensor_type:
            model_sensor = h
        elif a == "Link" and sim.getObjectType(h) == \
                sim.object_forcesensor_type:
            link = h

    return Stage3Unit(
        unit_id=spec.unit_id, index=spec.index, spec=spec,
        layout=UnitLayoutView(layout_name=cfg.STAGE3_LAYOUT_NAME, spec=spec),
        island=find(aliases["island"]), station=find(aliases["station"]),
        robot_root=arm, robot_connection=connection, robot_joints=joints,
        suction_root=pad, tcp=find(aliases["tcp"]),
        suction_sensor=find(aliases["suction_sensor"]),
        suction_model_sensor=model_sensor, suction_link=link,
        ik_target=find(aliases["ik_target"]),
        place_target=find(cfg.ALIASES["place_target_a"]
                          if spec.unit_id == "A" else
                          cfg.ALIASES["place_target_b"]),
        dock_sensor=find(spec.dock_sensor_alias),
    )


def build_second_unit(sim, base_scene: Stage1Scene,
                      layout: Optional[Stage3Layout] = None) -> Stage3Scene:
    """在已加载的 stage2 场景上增加 B 单元, 返回 Stage3Scene.

    调用前必须由生命周期加载 stage2 工件并处于停止态. 本函数不重建 A 单元,
    不清场; 任何失败都会尝试删除本次新建的 B 对象并抛异常.
    """
    layout = default_layout() if layout is None else layout
    report = validate_layout(layout)
    if not report["ok"]:
        failed = [c for c in report["checks"] if not c["ok"]]
        raise Stage3SceneError(
            f"布局 {layout.name!r} 静态校验失败: " + "; ".join(
                f"{c['check']}({c['detail']})" for c in failed))
    try:
        state = int(sim.getSimulationState())
    except Exception as exc:
        raise Stage3SceneError(f"无法读取仿真状态: {exc}") from exc
    if state != int(sim.simulation_stopped):
        raise Stage3SceneError(f"构建第二单元必须处于停止态 (state={state})")

    # A 单元必须与布局一致 (dock 允许因 AGV 朝向调整而移动)
    for key in ("island_pos", "station_pos", "arm_base"):
        expected = getattr(layout.unit("A"), key)
        actual = getattr(base_scene.layout, key) if base_scene.layout else None
        if actual is not None and not all(
                abs(float(a) - float(b)) <= 1e-6
                for a, b in zip(actual, expected)):
            raise Stage3SceneError(
                f"父场景 A 单元 {key}={actual} 与阶段三布局 {expected} 不一致")

    index = alias_index(sim)
    stage3_added = {
        *cfg.ALIASES["unit"]["B"].values(),
        cfg.ALIASES["dock_sensor"]["B"],
        cfg.ALIASES["place_target_a"],
        cfg.ALIASES["place_target_b"],
        cfg.ALIASES["pick_zone"],
    }
    existing = sorted(alias for alias in stage3_added if alias in index)
    if existing:
        raise Stage3SceneError(f"场景已包含阶段三对象: {existing}")

    island_root = base_scene.handle(base.ALIASES["roots"][2])
    mobile_root = base_scene.handle(base.ALIASES["roots"][3])
    if island_root < 0 or mobile_root < 0:
        raise Stage3SceneError("父场景缺少 Island/Mobile 层级根")

    spec_b = layout.unit("B")
    agv_pose_before = None
    dock_a_local_before = None
    try:
        agv_pose_before = list(sim.getObjectPose(
            base_scene.agv_root, sim.handle_world))
        dock_a_before = base_scene.handle(cfg.ALIASES["dock_sensor"]["A"])
        if dock_a_before >= 0:
            dock_a_local_before = list(sim.getObjectPosition(
                dock_a_before, mobile_root))
    except Exception:
        pass
    try:
        island_b = _create_box(
            sim, spec_b.aliases["island"], spec_b.island_size,
            (spec_b.island_pos[0], spec_b.island_pos[1],
             spec_b.island_top_z / 2.0),
            color=(0.35, 0.40, 0.50), parent=island_root)
        station_b = _create_box(
            sim, spec_b.aliases["station"], spec_b.station_size,
            (spec_b.station_pos[0], spec_b.station_pos[1],
             spec_b.station_top_z / 2.0),
            color=(0.35, 0.45, 0.40), parent=island_root)
        robot = _create_unit_robot(sim, island_b, spec_b)
        _create_dummy(sim, spec_b.aliases["ik_target"],
                      spec_b.pick_tcp_pose, island_root)
        _create_dummy(sim, cfg.ALIASES["place_target_a"],
                      layout.unit("A").station_material_pose, island_root)
        _create_dummy(sim, cfg.ALIASES["place_target_b"],
                      spec_b.station_material_pose, island_root)
        _create_dummy(sim, cfg.ALIASES["pick_zone"],
                      layout.pick_zone_center, island_root)

        agv = base_scene.agv_root
        if agv < 0:
            raise Stage3SceneError("父场景缺少 AGV")
        _move_agv_to_layout(sim, agv, layout)
        dock_a = base_scene.handle(cfg.ALIASES["dock_sensor"]["A"])
        if dock_a < 0:
            raise Stage3SceneError("父场景缺少 dock A 传感器")
        _move_dock_sensor(sim, dock_a, layout.unit("A").dock)
        _create_cylinder_sensor(
            sim, cfg.ALIASES["dock_sensor"]["B"], parent=mobile_root,
            local_pos=[spec_b.dock[0], spec_b.dock[1],
                       base.AGV_DOCK_SENSOR_Z],
            local_orientation=base.PICK_SENSOR_ORIENTATION,
            range_=base.AGV_DOCK_SENSOR_RANGE,
            radius=base.AGV_DOCK_SENSOR_RADIUS)
    except Exception:
        _remove_second_unit(sim)
        _restore_shared_objects(sim, base_scene.agv_root,
                                dock_a_local_before, agv_pose_before)
        raise

    scene = Stage3Scene(sim=sim, layout=layout, stage3_layout=layout)
    _inherit_base_scene(scene, base_scene)
    new_index = alias_index(sim)
    scene.units = {
        "A": _resolve_unit(sim, layout.unit("A"), new_index),
        "B": _resolve_unit(sim, layout.unit("B"), new_index),
    }
    for unit in scene.units.values():
        for alias in unit.aliases.values():
            scene.handles[alias] = resolve_alias(sim, alias, index=new_index)
        scene.handles[unit.spec.dock_sensor_alias] = unit.dock_sensor
        scene.handles[cfg.ALIASES["place_target_a"]
                      if unit.unit_id == "A" else
                      cfg.ALIASES["place_target_b"]] = unit.place_target
    scene.handles[cfg.ALIASES["pick_zone"]] = resolve_alias(
        sim, cfg.ALIASES["pick_zone"], index=new_index)
    scene.parent_build_report = dict(base_scene.build_report or {})

    result = scene.verify()
    if not result["ok"]:
        failed = [c for c in result["checks"] if not c["ok"]]
        _remove_second_unit(sim)
        _restore_shared_objects(sim, base_scene.agv_root,
                                dock_a_local_before, agv_pose_before)
        raise Stage3SceneError(
            "阶段三场景契约校验失败: " + "; ".join(
                f"{c['name']}({c['detail']})" for c in failed))
    return scene


def _inherit_base_scene(scene: Stage3Scene, base_scene: Stage1Scene) -> None:
    """复制 A 单元/传送带/AGV 的运行时字段 (句柄在当前会话有效)."""
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

def _adopt_conveyor_meta(sim, belt: int) -> dict:
    bbox = _model_bbox(sim, belt)
    run = _measure_top_run(sim, belt)
    run["top_run"] = {"min_x": run["min_x"], "max_x": run["max_x"]}
    run["actual_length"] = (bbox[3] - bbox[0]) if bbox else None
    run["actual_width"] = (bbox[4] - bbox[1]) if bbox else None
    scripts = _find_scripts(sim, belt)
    run["script_handle"] = scripts[0] if scripts else -1
    run["drive_mode"] = (base.DRIVE_SOURCE_SCRIPT if scripts
                         else "PYTHON_PAD_DRIVEN")
    try:
        model_cfg = sim.readCustomTableData(belt, "__config__")
    except Exception:
        model_cfg = None
    if isinstance(model_cfg, dict):
        run["config_accel"] = model_cfg.get("accel")
        run["config_target_vel"] = model_cfg.get("targetVel")
    entry = resolve_alias(sim, base.ALIASES["conveyor_entry"])
    stop = resolve_alias(sim, base.ALIASES["conveyor_stop"])
    run["entry_local_x"] = float(sim.getObjectPosition(entry, belt)[0])
    run["stop_local_x"] = float(sim.getObjectPosition(stop, belt)[0])
    run["entry_world"] = list(sim.getObjectPosition(entry, sim.handle_world))
    run["stop_world"] = list(sim.getObjectPosition(stop, sim.handle_world))
    run["surface_z"] = base.CONVEYOR_SURFACE_Z
    return run


def adopt_stage3_scene(sim, layout: Optional[Stage3Layout] = None
                       ) -> Stage3Scene:
    """把当前已加载的 stage3 .ttt 重新解析为 Stage3Scene (只读)."""
    layout = default_layout() if layout is None else layout
    scene = Stage3Scene(sim=sim, layout=layout, stage3_layout=layout)
    index = alias_index(sim)

    for name in base.ALIASES["roots"]:
        scene.handles[name] = resolve_alias(sim, name, index=index)
    # 阶段一/二共享对象的非必需别名 (AGV 载台/挡边、传送带出口传感器等)
    # 也要进入句柄表与 manifest, 否则父子差异会误报“对象被移除”.
    for key, alias in base.ALIASES.items():
        if key == "roots":
            continue
        h = resolve_alias(sim, alias, required=False, index=index)
        if h >= 0:
            scene.handles[alias] = h
    for alias in cfg.REQUIRED_ALIASES:
        h = resolve_alias(sim, alias, index=index)
        scene.handles[alias] = h

    scene.units = {
        unit_id: _resolve_unit(sim, layout.unit(unit_id), index)
        for unit_id in ("A", "B")
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
    agv_joints = {alias_of(sim, h): h for h in subtree(sim, agv)
                  if sim.getObjectType(h) == sim.object_joint_type}
    left = agv_joints.get("leftMotor", -1)
    right = agv_joints.get("rightMotor", -1)
    if left < 0 or right < 0:
        raise Stage3SceneError("pioneer p3dx 找不到 leftMotor/rightMotor")
    scene.agv_root = agv
    scene.agv_left_wheel = left
    scene.agv_right_wheel = right
    belt = resolve_alias(sim, base.ALIASES["conveyor"], index=index)
    scene.conveyor_meta = _adopt_conveyor_meta(sim, belt)
    return scene
