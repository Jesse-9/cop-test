"""scene_contract.py — 阶段三工件契约: extra.stage3 与只读校验.

`extra.stage3` 记录**稳定规格**, 不记录运行时句柄:
  - 契约/控制器/协调器/运行入口版本;
  - 阶段三配置摘要与布局摘要;
  - 父 stage2 工件、stage2 契约版本与配置摘要;
  - 双工位别名/坐标、共享资源锁表、AGV 路线、任务流、验收场景;
  - 场景变化清单 (A 单元保留, 新增 B 单元, AGV 初始位姿/两 dock 调整).

只读校验不创建影子臂/集合、不修改场景; 任一稳定字段不匹配、发现
`Shadow_*` 残留、父 stage2 契约漂移都判失败.
"""

from __future__ import annotations

import json
from typing import Optional

from stage1 import config as base
from stage1 import scene_artifact as sa
from stage1.scene import alias_of, all_objects, subtree

from stage2 import config as s2cfg
from stage2.scene_contract import (sanitize_manifest_sections as s2_sanitize,
                                   stage2_config_digest)

from . import config as cfg
from .layout import Stage3Layout, default_layout, layout_from_dict


def stage3_config_summary() -> dict:
    out = {}
    exclude = {"DEFAULT_LOG_FILE"}
    for name, value in vars(cfg).items():
        if name.startswith("_") or not name.isupper() or name in exclude:
            continue
        if name == "base" or callable(value):
            continue
        out[name] = sa._jsonable(value)
    return out


def stage3_config_digest() -> str:
    return sa.stable_digest(stage3_config_summary())


def stage3_layout_digest(layout: Optional[Stage3Layout] = None) -> str:
    layout = default_layout() if layout is None else layout
    return sa.stable_digest(layout.digest_payload())


def sanitize_manifest_sections(manifest: dict) -> dict:
    """剥离运行时句柄/易变字段; 复用 stage2 规则并保留 stage3 稳定字段."""
    return s2_sanitize(manifest)


def diff_stage3_manifest(parent: dict, current: dict) -> list[dict]:
    return sa.diff_manifest(sanitize_manifest_sections(parent),
                            sanitize_manifest_sections(current))


def build_stage3_extra(*, sim=None, scene=None, parent_info=None,
                       notes: str = "") -> dict:
    """生成可写入 manifest ``extra.stage3`` 的稳定契约."""
    parent_info = dict(parent_info or {})
    layout = (scene.stage3_layout if scene is not None
              and getattr(scene, "stage3_layout", None) is not None
              else default_layout())
    units = {
        spec.unit_id: {
            "index": spec.index,
            "arm_base": list(spec.arm_base),
            "island_pos": list(spec.island_pos),
            "station_pos": list(spec.station_pos),
            "dock": list(spec.dock),
            "aliases": spec.aliases,
            "dock_sensor_alias": spec.dock_sensor_alias,
        }
        for spec in layout.units
    }
    resources = {
        "policy_version": cfg.STAGE3_RESOURCE_POLICY_VERSION,
        "order": list(cfg.RESOURCE_ORDER),
        "roles": {name: cfg.RESOURCE_ROLES.get(name, "")
                  for name in cfg.RESOURCE_ORDER},
        "rules": [
            "申请必须按固定顺序; 同一资源单一 owner",
            "pick_zone 从计划开始持有, TCP 退出 conveyor envelope 后释放",
            "agv_lane/dock_X 由 AGV 调度器或装卸工位独占",
            "失败/停止释放普通调度锁; 安全锁存不由资源释放清除",
        ],
    }
    route = {
        "version": cfg.STAGE3_AGV_ROUTE_VERSION,
        "initial_yaw": layout.agv_initial_yaw,
        "route_x": layout.agv_route_x,
        "route_y": list(layout.agv_route_y),
        "docks": layout.docks,
        "directions": {
            "A_to_B": {"direction": 1, "loaded": True},
            "B_to_A": {"direction": -1, "loaded": False},
        },
        "arrive_tol": cfg.AGV_ROUTE_ARRIVE_TOL,
        "heading_tol": cfg.AGV_ROUTE_HEADING_TOL,
        "stop_vel_tol": cfg.AGV_ROUTE_STOP_VEL_TOL,
        "slip_tol": cfg.AGV_ROUTE_SLIP_TOL,
        "block_timeout": cfg.AGV_ROUTE_BLOCK_TIMEOUT,
        "block_displacement": cfg.AGV_ROUTE_BLOCK_DISPLACEMENT,
        "resume_speed": cfg.AGV_ROUTE_RESUME_SPEED,
        "auto_retry": False,
        "turn_policy": "straight_shuttle_no_dock_rotation",
    }
    task_flow = {
        "version": cfg.STAGE3_TASK_FLOW_VERSION,
        "flow": list(cfg.TASK_FLOW),
        "duplicate_policy": cfg.DUPLICATE_REQUEST_POLICY,
        "max_attempts": cfg.TASK_MAX_ATTEMPTS,
        "queue_capacity": cfg.QUEUE_CAPACITY,
        "task_timeout": cfg.TASK_TIMEOUT,
        "failed_to_done": False,
    }
    collision = {
        "inter_arm_policy": "other_real_unit_links_in_workcell",
        "pick_zone_serialization": "resource_token",
        "arm_envelope_tokens": ["arm_A", "arm_B"],
        "safety_latch": "independent_per_unit",
        "no_ompl": True,
        "note": "任一臂运动仍由阶段二 ConfigurationChecker 逐段检查; "
                "另一台真实臂的可见连杆/吸盘 Body 已加入本臂工作区集合",
    }
    acceptance = {
        "scenarios": list(cfg.STAGE3_SCENARIOS),
        "closed_loop": {
            "uid_consistent": True,
            "load_handoff_on_tray": True,
            "unload_at_station_B": True,
            "no_safety_latch": True,
        },
        "contention": {"duplicate_requests_rejected": True},
        "blocked": {"enter_blocked": True, "resume_after_clear": True,
                    "never_done_while_blocked": True},
        "unstable_unload": {"refuse_release": True, "not_done": True,
                            "preserve_material": True},
        "route_baseline": {"bidirectional_dock_metrics": True},
    }
    inheritance = {
        "parent_artifact": parent_info.get("artifact_id"),
        "parent_manifest_digest": parent_info.get("manifest_digest"),
        "parent_source_phase": parent_info.get("source_phase", "stage2"),
        "parent_stage2_contract_version": parent_info.get(
            "stage2_contract_version"),
        "parent_stage2_config_digest": parent_info.get(
            "stage2_config_digest"),
        "scene_changed": True,
        "inherited": [
            "conveyor", "agv_model", "A_unit", "stage1_tool_mount",
            "stage1_material_physics", "stage2_collision_and_safety",
        ],
        "changed_scene_parameters": [
            "added Island_B/Station_B/Robot_UR5_02/SuctionPad_02/IK_Target_02",
            "added Sensor_AGV_Dock_B and PlaceTarget_Station_A/B",
            "AGV initial pose moved to dock A (0.40,0.70) with yaw -pi/2",
            "dock A sensor moved to (0.40,0.70)",
        ],
    }
    extra = {
        "contract_version": cfg.STAGE3_CONTRACT_VERSION,
        "controller": {
            "id": cfg.STAGE3_CONTROLLER_ID,
            "coordinator": cfg.STAGE3_COORDINATOR_ID,
            "runner": cfg.STAGE3_RUNNER_ID,
            "phase": cfg.STAGE3_PHASE,
        },
        "config": {
            "digest": stage3_config_digest(),
            "summary": stage3_config_summary(),
        },
        "layout": {
            "digest": stage3_layout_digest(layout),
            "spec": layout.as_dict(),
            "units": units,
        },
        "resources": resources,
        "agv_route": route,
        "task_flow": task_flow,
        "collision": collision,
        "acceptance": acceptance,
        "inheritance": inheritance,
        "notes": notes,
    }
    return extra


# ═══════════════════════════════════════════════════════════════
# 只读校验
# ═══════════════════════════════════════════════════════════════

def _check(checks: list[dict], name: str, ok: bool,
           detail: str = "") -> None:
    checks.append({"name": name, "ok": bool(ok), "detail": detail})


def _resolve_aliases(sim) -> dict[str, list[int]]:
    index: dict[str, list[int]] = {}
    for handle in all_objects(sim):
        alias = alias_of(sim, handle)
        if alias:
            index.setdefault(alias, []).append(int(handle))
    return index


def validate_stage3_contract(sim, artifact, scene=None) -> dict:
    manifest = artifact.manifest
    extra = (manifest.get("extra") or {}).get("stage3")
    checks: list[dict] = []
    differences: list[dict] = []

    _check(checks, "stage3_extra_present", isinstance(extra, dict),
           f"type={type(extra).__name__}")
    if not isinstance(extra, dict):
        return {"ok": False, "checks": checks, "differences": differences}

    _check(checks, "stage3_source_phase",
           manifest.get("source_phase") == cfg.STAGE3_PHASE,
           f"source_phase={manifest.get('source_phase')!r}")
    _check(checks, "stage3_contract_version",
           extra.get("contract_version") == cfg.STAGE3_CONTRACT_VERSION,
           f"saved={extra.get('contract_version')!r} "
           f"current={cfg.STAGE3_CONTRACT_VERSION!r}")
    controller = extra.get("controller") or {}
    _check(checks, "stage3_controller_id",
           controller.get("id") == cfg.STAGE3_CONTROLLER_ID,
           f"saved={controller.get('id')!r}")
    _check(checks, "stage3_coordinator_id",
           controller.get("coordinator") == cfg.STAGE3_COORDINATOR_ID,
           f"saved={controller.get('coordinator')!r}")
    _check(checks, "stage3_runner_id",
           controller.get("runner") == cfg.STAGE3_RUNNER_ID,
           f"saved={controller.get('runner')!r}")

    saved_digest = (extra.get("config") or {}).get("digest")
    current_digest = stage3_config_digest()
    _check(checks, "stage3_config_digest", saved_digest == current_digest,
           f"saved={saved_digest} current={current_digest}")
    if saved_digest != current_digest:
        differences.append({"kind": "stage3_config", "saved": saved_digest,
                            "current": current_digest})

    layout_saved = (extra.get("layout") or {}).get("digest")
    layout_current = stage3_layout_digest()
    _check(checks, "stage3_layout_digest", layout_saved == layout_current,
           f"saved={layout_saved} current={layout_current}")
    if layout_saved != layout_current:
        differences.append({"kind": "stage3_layout", "saved": layout_saved,
                            "current": layout_current})

    inheritance = extra.get("inheritance") or {}
    parent = manifest.get("parent_artifact") or {}
    _check(checks, "stage3_parent_recorded",
           bool(inheritance.get("parent_artifact")),
           f"parent={inheritance.get('parent_artifact')}")
    if parent.get("artifact_id") and inheritance.get("parent_artifact"):
        _check(checks, "stage3_parent_matches_manifest",
               parent.get("artifact_id") == inheritance.get("parent_artifact"),
               f"manifest={parent.get('artifact_id')} "
               f"extra={inheritance.get('parent_artifact')}")
    _check(checks, "stage3_parent_source_phase",
           inheritance.get("parent_source_phase") == "stage2",
           f"parent_source_phase="
           f"{inheritance.get('parent_source_phase')!r}")
    _check(checks, "stage3_scene_changed",
           inheritance.get("scene_changed") is True,
           f"scene_changed={inheritance.get('scene_changed')}")
    _check(checks, "stage3_stage2_contract_version",
           inheritance.get("parent_stage2_contract_version")
           == s2cfg.STAGE2_CONTRACT_VERSION,
           f"saved={inheritance.get('parent_stage2_contract_version')!r} "
           f"current={s2cfg.STAGE2_CONTRACT_VERSION!r}")
    _check(checks, "stage3_stage2_config_digest",
           inheritance.get("parent_stage2_config_digest")
           == stage2_config_digest(),
           f"saved={inheritance.get('parent_stage2_config_digest')} "
           f"current={stage2_config_digest()}")

    resources = extra.get("resources") or {}
    _check(checks, "stage3_resource_order",
           tuple(resources.get("order") or ()) == tuple(cfg.RESOURCE_ORDER),
           f"saved={resources.get('order')}")
    route = extra.get("agv_route") or {}
    expected_docks = {
        spec.unit_id: [round(float(v), 6) for v in spec.dock]
        for spec in default_layout().units}
    saved_docks = route.get("docks") or {}
    normalized_saved = {
        str(key): [round(float(v), 6) for v in value]
        for key, value in saved_docks.items()}
    _check(checks, "stage3_route_docks", normalized_saved == expected_docks,
           f"saved={saved_docks}")
    flow = extra.get("task_flow") or {}
    _check(checks, "stage3_task_flow",
           tuple(flow.get("flow") or ()) == tuple(cfg.TASK_FLOW),
           f"saved={flow.get('flow')}")

    index = _resolve_aliases(sim)
    for alias in cfg.REQUIRED_ALIASES:
        hits = index.get(alias, [])
        _check(checks, f"stage3_alias_unique:{alias}", len(hits) == 1,
               f"{len(hits)} 个匹配")
    shadow = sorted({alias for alias, hits in index.items()
                     if alias.startswith(cfg.SESSION_RESOURCE_PREFIX)
                     for _ in hits})
    _check(checks, "stage3_no_session_resources", not shadow,
           f"found={shadow}")

    if scene is not None:
        try:
            units = getattr(scene, "units", {})
            _check(checks, "stage3_scene_units",
                   set(units) == {"A", "B"}, f"units={sorted(units)}")
            for unit_id in ("A", "B"):
                unit = units.get(unit_id)
                if unit is None:
                    _check(checks, f"stage3_unit:{unit_id}", False, "missing")
                    continue
                _check(checks, f"stage3_unit:{unit_id}",
                       len(unit.robot_joints) == 6,
                       f"joints={len(unit.robot_joints)}")
                modes = []
                for joint in unit.robot_joints:
                    mode = sim.getJointMode(joint)
                    modes.append(int(mode[0] if isinstance(mode, (tuple, list))
                                     else mode))
                _check(checks, f"stage3_joint_modes:{unit_id}",
                       all(mode == int(sim.jointmode_kinematic)
                           for mode in modes), f"modes={modes}")
                local = list(sim.getObjectPosition(unit.tcp,
                                                   unit.suction_root))
                _check(checks, f"stage3_tcp_offset:{unit_id}",
                       all(abs(local[i] - base.TCP_LOCAL_POS[i])
                           <= base.SUCTION_MOUNT_POS_TOL for i in range(3)),
                       f"TCP local={[round(v, 5) for v in local]}")
                pose = list(sim.getObjectPosition(unit.dock_sensor,
                                                  sim.handle_world))
                expected = [unit.spec.dock[0], unit.spec.dock[1],
                            base.AGV_DOCK_SENSOR_Z]
                _check(checks, f"stage3_dock_sensor:{unit_id}",
                       all(abs(pose[i] - expected[i]) <= 0.002
                           for i in range(3)),
                       f"pos={[round(v, 4) for v in pose]}")
            report = scene.verify()
            _check(checks, "stage3_scene_contract", bool(report.get("ok")),
                   f"{sum(1 for c in report.get('checks', []) if c['ok'])}"
                   f"/{len(report.get('checks', []))} checks")
            for item in report.get("checks", []):
                if not item.get("ok"):
                    differences.append({"kind": "contract",
                                        "name": item.get("name"),
                                        "detail": item.get("detail")})
        except Exception as exc:
            _check(checks, "stage3_scene_observable", False,
                   f"{type(exc).__name__}: {exc}")

    ok = sa.checks_ok(checks) and not differences
    return {
        "ok": ok,
        "contract_version": extra.get("contract_version"),
        "checks": checks,
        "differences": differences,
    }
