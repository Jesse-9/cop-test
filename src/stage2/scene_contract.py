"""scene_contract.py — 阶段二工件契约: 生成与只读校验 (06-09 §8).

``extra.stage2`` 记录**稳定规格**, 不记录运行时句柄:
  - 契约版本、控制器/运行入口标识;
  - 阶段二配置摘要 (阶段一 config 摘要仍由父 manifest 的 config 章节负责);
  - 接触几何/容差、碰撞集合角色、允许接触规则、粗代理排除项;
  - 影子臂会话资源策略、采样/预算、执行/停带/联锁阈值;
  - 父工件与继承差异 (默认场景不变, 只增加控制器与检查)。

``validate_stage2_contract`` 只读: 不创建集合、不补建影子对象、不修改场景;
任一字段不匹配或发现 ``Shadow_*`` 残留都判失败。
"""

from __future__ import annotations

import json
import math
from typing import Optional

from stage1 import config as base
from stage1 import scene_artifact as sa
from stage1.scene import alias_of, all_objects, subtree

from . import config as cfg


def _round(value, digits: int = 6):
    if isinstance(value, dict):
        return {str(k): _round(v, digits) for k, v in value.items()}
    if isinstance(value, list):
        return [_round(v, digits) for v in value]
    if isinstance(value, float):
        return round(value, digits)
    return value


def sanitize_manifest_sections(manifest: dict) -> dict:
    """剥离 manifest 中的运行时句柄/易变字段, 保留稳定身份.

    阶段二工件不得把会话句柄写进 manifest (06-09 §8.4); 同时去除
    ``sim_time``、``joint_positions`` 等保存时刻相关字段, 避免父子差异
    把“等价副本”误报为场景变化.
    """
    data = json.loads(json.dumps(manifest))
    geometry = data.get("geometry")
    if isinstance(geometry, dict):
        conveyor = geometry.get("conveyor")
        if isinstance(conveyor, dict):
            conveyor.pop("script_handle", None)
    tool = data.get("tool")
    if isinstance(tool, dict):
        for key in ("handles", "sim_time", "joint_positions"):
            tool.pop(key, None)
    physics = data.get("physics")
    if isinstance(physics, dict):
        # v002 基线 manifest 没有该字段 (后续版本新增), 不是场景变化
        physics.pop("ur5_joint_modes", None)
    # 只规整几何/工具/物理章节; config.summary 必须保持原值, 否则
    # config digest 校验会把舍入误报为配置漂移.
    for section in ("geometry", "tool", "physics"):
        if isinstance(data.get(section), (dict, list)):
            data[section] = _round(data[section])
    return data


def diff_stage2_manifest(parent: dict, current: dict) -> list[dict]:
    """在稳定字段上比较父/子工件, 不把句柄或保存时刻算作差异."""
    return sa.diff_manifest(sanitize_manifest_sections(parent),
                            sanitize_manifest_sections(current))


def stage2_config_summary() -> dict:
    out = {}
    for name, value in vars(cfg).items():
        if name.startswith("_") or not name.isupper():
            continue
        if name == "base" or callable(value):
            continue
        out[name] = sa._jsonable(value)
    return out


def stage2_config_digest() -> str:
    return sa.stable_digest(stage2_config_summary())


def build_stage2_extra(*, sim=None, scene=None, parent_info=None,
                       runtime=None, notes: str = "") -> dict:
    """生成可写入 manifest ``extra.stage2`` 的稳定契约."""
    contact = {
        "model": "rigid_box_top_face",
        "cup_radius_design": cfg.CUP_RADIUS_DESIGN,
        "cup_radius_tol": cfg.CUP_RADIUS_TOL,
        "max_gap": cfg.CONTACT_MAX_GAP,
        "min_signed_distance": cfg.CONTACT_MIN_SIGNED_DISTANCE,
        "max_lateral": cfg.CONTACT_MAX_LATERAL,
        "max_normal_angle": cfg.CONTACT_MAX_NORMAL_ANGLE,
        "face_tolerance": cfg.CONTACT_FACE_TOLERANCE,
        "require_cup_coverage": cfg.CONTACT_REQUIRE_CUP_COVERAGE,
        "bbox_policy": "measured_local_bbox_min_max",
    }
    collision = {
        "collections": [
            {"name": "stage2_shadow",
             "roles": "影子 UR5 可见连杆 + 吸盘 Body"},
            {"name": "stage2_shadow_links", "roles": "影子连杆 (不含吸盘)"},
            {"name": "stage2_shadow_no_base",
             "roles": "除 link1 外连杆 + 吸盘 Body (安装平台检查)"},
            {"name": "stage2_workcell",
             "roles": "conveyor pads/工位台面/仓库/地面/AGV 车体与载台"},
            {"name": "stage2_mount",
             "roles": "安装平台; 仅 link1 固定安装接触"},
            {"name": "stage2_parts", "roles": "非目标动态物料"},
            {"name": "stage2_target", "roles": "当前目标物料"},
            {"name": "stage2_shadow_load", "roles": "已吸附载荷影子副本"},
        ],
        "allowed_contacts": [
            "link1 <-> Island_A 固定安装接触 (仅此一对)",
            "车轮/脚轮 <-> 地面 滚动接触 (AGV 不作为机械臂障碍的成员)",
            "物料 <-> conveyor pad / 工位台面 / AGV 载台 承载接触",
            "杯口 <-> 目标顶面: 仅在接触几何判据有效时放行",
        ],
        "forbidden": [
            "非相邻连杆自碰撞", "吸盘 Body 与目标以外的所有对象",
            "带载物料与工作区/非目标物料", "目标物料与非杯口部位",
        ],
        "excluded_from_queries": [
            {"alias": "shape",
             "reason": "内置 conveyor 根下整条带粗包围盒; 真实承载面由 50 个 "
                       "pad 提供 (实测顶面高约 10 cm)"},
        ],
        "self_collision": {
            "method": "explicit_non_adjacent_pairs",
            "min_index_gap": cfg.UR5_SELF_PAIR_MIN_GAP,
            "note": "4.10 实测 collection_self_collision_indicator 对 "
                    "checkCollision(collection, collection) 无过滤效果",
        },
        "margin": cfg.COLLISION_MARGIN,
        "near_margin": cfg.COLLISION_NEAR_MARGIN,
    }
    planning = {
        "joint_sample_max_delta": cfg.JOINT_SAMPLE_MAX_DELTA,
        "cartesian_sample_max_delta": cfg.CARTESIAN_SAMPLE_MAX_DELTA,
        "link_motion_max_delta": cfg.LINK_MOTION_MAX_DELTA,
        "segment_max_samples": cfg.SEGMENT_MAX_SAMPLES,
        "segment_max_subdivision": cfg.SEGMENT_MAX_SUBDIVISION,
        "ik_max_seeds": cfg.IK_MAX_SEEDS,
        "ik_max_candidates": cfg.IK_MAX_CANDIDATES,
        "ik_seed_variants": [list(s) for s in cfg.IK_SEED_VARIANTS],
        "pre_approach_clearance": cfg.PRE_APPROACH_CLEARANCE,
        "pre_approach_height_variants": list(
            cfg.PRE_APPROACH_HEIGHT_VARIANTS),
        "lateral_offset_variants": list(cfg.LATERAL_OFFSET_VARIANTS),
        "tool_yaw_variants": list(cfg.TOOL_YAW_VARIANTS),
        "max_candidates": cfg.PLAN_MAX_CANDIDATES,
        "max_total_samples": cfg.PLAN_MAX_TOTAL_SAMPLES,
        "wall_budget_s": cfg.PLAN_WALL_BUDGET_S,
        "wrist_branch": {
            "joint": cfg.WRIST_BRANCH_JOINT,
            "margin": cfg.WRIST_BRANCH_MARGIN,
            "rule": "wrapped q4 sign; singular at 0/±pi",
        },
        "joint_lock": {
            "joint": cfg.Q0_LOCK_JOINT,
            "ik_tolerance": cfg.Q0_LOCK_IK_TOL,
            "acceptance_tolerance": cfg.Q0_LOCK_TOL,
            "segments": sorted(cfg.Q0_LOCK_SEGMENTS),
            "fallback_to_free": cfg.Q0_LOCK_FALLBACK,
        },
        "lookahead": {
            "weight": cfg.PLAN_LOOKAHEAD_WEIGHT,
            "fail_penalty": cfg.PLAN_LOOKAHEAD_FAIL_PENALTY,
            "note": "只用于候选排序; 逐段碰撞检查仍是唯一放行依据",
        },
        "transfer": {
            "height": cfg.TRANSFER_HEIGHT,
            "path": "fixed_height_horizontal_cartesian",
            "lift_ends_at_transfer_height": True,
            "placing_descends_from_pre_place": True,
            "horizontal_detour_offsets": list(
                cfg.TRANSFER_HORIZONTAL_DETOUR_OFFSETS),
            "plan_wall_budget_s": cfg.TRANSFER_PLAN_WALL_BUDGET_S,
            "note": "06-15: LIFTING→TRANSFERRING→PLACING 三段; 水平段保持 "
                    "H_transfer 与工具姿态, 直线失败后只允许固定高度横向"
                    "绕行段, 不走关节路径",
        },
        "chain_search": {
            "mode": cfg.CARTESIAN_CHAIN_SEARCH,
            "choices": list(cfg.CARTESIAN_CHAIN_SEARCH_CHOICES),
            "beam_width": cfg.CARTESIAN_BEAM_WIDTH,
            "layer_candidates": cfg.CARTESIAN_LAYER_CANDIDATES,
            "max_nodes": cfg.CARTESIAN_CHAIN_MAX_NODES,
        },
        "chain_cost": {
            "weights": {
                "tv": cfg.CHAIN_COST_WEIGHT_TV,
                "q0": cfg.CHAIN_COST_WEIGHT_Q0,
                "q3": cfg.CHAIN_COST_WEIGHT_Q3,
                "b": cfg.CHAIN_COST_WEIGHT_B,
                "a": cfg.CHAIN_COST_WEIGHT_A,
            },
            "sample_dt": cfg.CHAIN_COST_SAMPLE_DT,
            "note": "首轮实验初值; q3 为 Python 0-based joint4/Wrist 1",
        },
        "smoothing": {
            "enabled": cfg.TRAJECTORY_SMOOTHING_ENABLED,
            "passes": cfg.TRAJECTORY_SMOOTHING_PASSES,
            "weight": cfg.TRAJECTORY_SMOOTHING_WEIGHT,
            "requires_full_reverify": True,
        },
        "diagnostics": {
            "plan_trace": cfg.PLAN_TRACE_ENABLED,
            "plan_trace_max_points": cfg.PLAN_TRACE_MAX_POINTS,
            "execution_trace": cfg.EXECUTION_TRACE_ENABLED,
            "execution_trace_max_samples": cfg.EXECUTION_TRACE_MAX_SAMPLES,
            "execution_profile": cfg.EXECUTION_PROFILE_ENABLED,
        },
        "trajectory_compaction": {
            "enabled": cfg.TRAJECTORY_COMPACTION_ENABLED,
            "tcp_pos_tol": cfg.COMPACTION_TCP_POS_TOL,
            "tcp_ang_tol": cfg.COMPACTION_TCP_ANG_TOL,
        },
    }
    execution = {
        "max_joint_step": cfg.EXEC_MAX_JOINT_STEP,
        "joint_tol": cfg.EXEC_JOINT_TOL,
        "command_settle_tol": cfg.EXEC_COMMAND_SETTLE_TOL,
        "tcp_pos_tol": cfg.EXEC_TCP_POS_TOL,
        "tcp_ang_tol": cfg.EXEC_TCP_ANG_TOL,
        "max_deviation": cfg.EXEC_MAX_DEVIATION,
        "reach_timeout": cfg.EXEC_REACH_TIMEOUT,
        "release_pose_correction_max": cfg.RELEASE_POSE_CORRECTION_MAX,
        "release_angle_correction_max": cfg.RELEASE_ANGLE_CORRECTION_MAX,
        "release_slip_tol": cfg.RELEASE_SLIP_TOL,
    }
    conveyor = {
        "stop_vel_tol": cfg.CONVEYOR_STOP_VEL_TOL,
        "stop_pos_tol": cfg.CONVEYOR_STOP_POS_TOL,
        "stop_confirm_steps": cfg.CONVEYOR_STOP_CONFIRM_STEPS,
        "stop_timeout": cfg.CONVEYOR_STOP_TIMEOUT,
        "max_latency": cfg.CONVEYOR_STOP_MAX_LATENCY,
        "max_overrun": cfg.CONVEYOR_STOP_MAX_OVERRUN,
        "material_stable_speed": cfg.CONVEYOR_MATERIAL_STABLE_SPEED,
        "material_stable_steps": cfg.CONVEYOR_MATERIAL_STABLE_STEPS,
        "compensation_lag_tol": cfg.COMPENSATION_LAG_TOL,
        "compensation_max_step": cfg.COMPENSATION_MAX_STEP,
        "mode": base.TRANSPORT_MODE,
        "note": "补偿只补物理输送相对带速的差额; 速度回读不可用时不回退到 0",
    }
    safety = {
        "stop_confirm_steps": cfg.SAFETY_STOP_CONFIRM_STEPS,
        "stop_vel_tol": cfg.SAFETY_STOP_VEL_TOL,
        "pose_drift_tol": cfg.SAFETY_POSE_DRIFT_TOL,
        "tcp_drift_tol": cfg.SAFETY_TCP_DRIFT_TOL,
        "auto_recovery": False,
        "retry_after_plan_failure": False,
    }
    inheritance = {
        "parent_artifact": (parent_info or {}).get("artifact_id"),
        "parent_manifest_digest": (parent_info or {}).get("manifest_digest"),
        "scene_changed": False,
        "inherited": [
            "layout", "models", "tool_mount", "tcp_offset",
            "material_physics", "conveyor_speed", "sim_step",
        ],
        "changed_scene_parameters": [],
        "shadow_policy": "session_resource_not_persisted",
    }
    extra = {
        "contract_version": cfg.STAGE2_CONTRACT_VERSION,
        "controller": {
            "id": cfg.STAGE2_CONTROLLER_ID,
            "runner": cfg.STAGE2_RUNNER_ID,
            "phase": cfg.STAGE2_PHASE,
            "motion_opt_version": cfg.MOTION_OPT_VERSION,
            "transfer_trajectory_version": cfg.TRANSFER_TRAJECTORY_VERSION,
        },
        "config": {
            "digest": stage2_config_digest(),
            "summary": stage2_config_summary(),
        },
        "contact": contact,
        "collision": collision,
        "planning": planning,
        "execution": execution,
        "conveyor": conveyor,
        "safety": safety,
        "inheritance": inheritance,
        "notes": notes,
    }
    if runtime is not None and getattr(runtime, "startup", None):
        extra["runtime_observed"] = {
            "fk_ok": bool(runtime.startup.get("fk", {}).get("ok")),
            "cup_radius_measured": runtime.startup.get(
                "cup_radius_measured"),
            "workcell_aliases": runtime.startup.get("workcell_aliases", []),
            "excluded": runtime.startup.get("excluded", []),
        }
    return extra


# ═══════════════════════════════════════════════════════════════
# 只读校验
# ═══════════════════════════════════════════════════════════════

def _check(checks: list[dict], name: str, ok: bool, detail: str = "") -> None:
    checks.append({"name": name, "ok": bool(ok), "detail": detail})


def validate_stage2_contract(sim, artifact, scene=None) -> dict:
    manifest = artifact.manifest
    extra = (manifest.get("extra") or {}).get("stage2")
    checks: list[dict] = []
    differences: list[dict] = []

    _check(checks, "stage2_extra_present", isinstance(extra, dict),
           f"type={type(extra).__name__}")
    if not isinstance(extra, dict):
        return {"ok": False, "checks": checks, "differences": differences}

    _check(checks, "stage2_contract_version",
           extra.get("contract_version") == cfg.STAGE2_CONTRACT_VERSION,
           f"saved={extra.get('contract_version')!r} "
           f"current={cfg.STAGE2_CONTRACT_VERSION!r}")
    controller = extra.get("controller") or {}
    _check(checks, "stage2_controller_id",
           controller.get("id") == cfg.STAGE2_CONTROLLER_ID,
           f"saved={controller.get('id')!r}")
    _check(checks, "stage2_runner_id",
           controller.get("runner") == cfg.STAGE2_RUNNER_ID,
           f"saved={controller.get('runner')!r}")
    _check(checks, "stage2_transfer_trajectory_version",
           controller.get("transfer_trajectory_version")
           == cfg.TRANSFER_TRAJECTORY_VERSION,
           f"saved={controller.get('transfer_trajectory_version')!r} "
           f"current={cfg.TRANSFER_TRAJECTORY_VERSION!r}")

    saved_digest = (extra.get("config") or {}).get("digest")
    current_digest = stage2_config_digest()
    _check(checks, "stage2_config_digest", saved_digest == current_digest,
           f"saved={saved_digest} current={current_digest}")
    if saved_digest != current_digest:
        differences.append({"kind": "stage2_config", "saved": saved_digest,
                            "current": current_digest})

    inheritance = extra.get("inheritance") or {}
    parent = manifest.get("parent_artifact") or {}
    _check(checks, "stage2_parent_recorded",
           bool(inheritance.get("parent_artifact")),
           f"parent={inheritance.get('parent_artifact')}")
    if parent.get("artifact_id") and inheritance.get("parent_artifact"):
        _check(checks, "stage2_parent_matches_manifest",
               parent.get("artifact_id") == inheritance.get("parent_artifact"),
               f"manifest={parent.get('artifact_id')} "
               f"extra={inheritance.get('parent_artifact')}")
    _check(checks, "stage2_scene_unchanged",
           inheritance.get("scene_changed") is False,
           f"scene_changed={inheritance.get('scene_changed')}")

    # 会话资源不得进入工件
    aliases = [alias_of(sim, h) for h in all_objects(sim)]
    shadow = sorted({a for a in aliases
                     if (a or "").startswith(cfg.SESSION_RESOURCE_PREFIX)})
    _check(checks, "no_session_resources_persisted", not shadow,
           f"found={shadow}")

    # 真实模型几何与契约
    if scene is not None:
        try:
            from .collisions import (discover_conveyor_parts,
                                     discover_pad_body, discover_ur5_links)
            links, _ = discover_ur5_links(sim, scene.robot_root)
            _check(checks, "ur5_link_count",
                   len(links) == cfg.UR5_LINK_COUNT, f"{len(links)}")
            pad_body = discover_pad_body(sim, scene.suction_root)
            value = sim.getShapeBB(pad_body)
            size = (value[0] if isinstance(value, (tuple, list))
                    and len(value) == 2 else value)
            radius = min(float(size[0]), float(size[1])) / 2.0
            expected = (extra.get("contact") or {}).get(
                "cup_radius_design", cfg.CUP_RADIUS_DESIGN)
            _check(checks, "cup_radius_matches",
                   abs(radius - float(expected)) <= cfg.CUP_RADIUS_TOL,
                   f"measured={radius:.6f} saved={expected}")
            pads, coarse = discover_conveyor_parts(
                sim, scene.handle(base.ALIASES["conveyor"]))
            _check(checks, "conveyor_pads_present", bool(pads),
                   f"pads={len(pads)}")
            _check(checks, "conveyor_coarse_proxy_present", bool(coarse),
                   f"coarse={len(coarse)}")
            excluded = [item.get("alias") for item in
                        (extra.get("collision") or {}).get(
                            "excluded_from_queries", [])]
            _check(checks, "coarse_proxy_excluded_in_contract",
                   all(alias_of(sim, h) in excluded for h in coarse),
                   f"excluded={excluded}")
        except Exception as exc:
            _check(checks, "stage2_geometry_observable", False,
                   f"{type(exc).__name__}: {exc}")

    ok = sa.checks_ok(checks) and not differences
    return {
        "ok": ok,
        "contract_version": extra.get("contract_version"),
        "checks": checks,
        "differences": differences,
    }
