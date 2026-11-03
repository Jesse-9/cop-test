"""cli.py — flexible-factory v1 唯一命令入口.

用户流程 (方案 §1):
    flexible-factory load
    flexible-factory validate
    flexible-factory run --scenario full_flow

内部/维护命令: ``migrate``、``build``、``save``、``stop``、``status``、``list``.
start 不隐式 build/clear; run 只启动已加载并完成启动点校验的 v1 工件.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import spec
from .application.lifecycle import (LifecycleError, LifecycleManager,
                                    ValidationFailedError)
from .application.run import REPORT_DIR, run_loaded_scene_v1, setup_logger
from .artifacts import migration
from .artifacts import store as sa
from .simulation.component_builder import connect


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=23000)
    parser.add_argument("--artifact-root", default=None,
                        help="v1 工件根 (默认 docs/scene_artifacts/v1)")
    parser.add_argument("--actor", default="cli")


def _store(ns) -> sa.ArtifactStore:
    root = Path(ns.artifact_root) if ns.artifact_root else None
    return sa.ArtifactStore(root)


def _manager(ns, sim=None, simIK=None) -> LifecycleManager:
    return LifecycleManager(
        sim, simIK, host=ns.host, port=ns.port, actor=ns.actor,
        store=_store(ns), report_dir=Path(REPORT_DIR))


def _connect(ns):
    return connect(ns.host, ns.port)


def _print(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _fail(message: str, code: int = 2) -> int:
    print(f"[ERROR] {message}", file=sys.stderr)
    return code


def cmd_list(ns) -> int:
    _print({"artifacts": _store(ns).list("v1")})
    return 0


def cmd_status(ns) -> int:
    if ns.offline:
        manager = _manager(ns)
    else:
        _, sim, simIK = _connect(ns)
        manager = _manager(ns, sim, simIK)
    _print(manager.status())
    return 0


def cmd_load(ns) -> int:
    _, sim, simIK = _connect(ns)
    manager = _manager(ns, sim, simIK)
    _print(manager.load(ns.artifact))
    return 0


def cmd_validate(ns) -> int:
    _, sim, simIK = _connect(ns)
    manager = _manager(ns, sim, simIK)
    manager.load(ns.artifact) if ns.load else None
    _print(manager.validate())
    return 0


def cmd_stop(ns) -> int:
    _, sim, simIK = _connect(ns)
    manager = _manager(ns, sim, simIK)
    _print(manager.stop(timeout=ns.timeout))
    return 0


def cmd_migrate(ns) -> int:
    _, sim, simIK = _connect(ns)
    store = _store(ns)
    result = migration.migrate(
        ns.source, store, sim, actor=ns.actor, note=ns.note,
        force=ns.force)
    _print(result)
    return 0 if result.get("ok") else 1


def cmd_build(ns) -> int:
    _, sim, simIK = _connect(ns)
    manager = _manager(ns, sim, simIK)
    parent = Path(ns.parent).expanduser().resolve()
    if parent.is_file() and parent.name in sa.ARTIFACT_FILES:
        parent = parent.parent
    manifest_path = parent / sa.MANIFEST_FILE
    if not manifest_path.exists():
        return _fail(f"父工件缺少 manifest: {manifest_path}")
    parent_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    parent_layout = parent_manifest.get("layout") or {}
    layout = None
    if ns.layout:
        from .simulation.layout import layout_from_dict
        layout = layout_from_dict(json.loads(Path(ns.layout).read_text(
            encoding="utf-8")))
    manager.build(parent_scene=parent / sa.SCENE_FILE,
                  parent_layout=parent_layout, layout=layout)
    if ns.save:
        provenance = {
            "source_phase": parent_manifest.get("source_phase"),
            "source_artifact_id": parent_manifest.get("artifact_id"),
            "source_manifest_digest": sa.sha256_file(manifest_path),
            "source_scene_sha256": sa.sha256_file(parent / sa.SCENE_FILE),
            "migrated_at": sa.utc_now(),
            "policy": "built from parent scene with v1 contract",
        }
        _print(manager.save(note=ns.note, provenance=provenance))
    else:
        _print(manager.summary())
    return 0


def cmd_run(ns) -> int:
    logger = setup_logger(ns.log)
    _, sim, simIK = _connect(ns)
    manager = _manager(ns, sim, simIK)
    if not ns.no_load:
        manager.load(ns.artifact)
    try:
        prepared = manager.prepare_start(target=ns.scenario)
    except ValidationFailedError as exc:
        logger.error("[LIFECYCLE] 启动前校验失败: %s", exc)
        return 3
    except LifecycleError as exc:
        logger.error("[LIFECYCLE] 启动前置失败 (%s): %s",
                     getattr(exc, "code", "ERROR"), exc)
        return 2

    try:
        summary = run_loaded_scene_v1(
            sim, simIK, manager.scene, scenario=ns.scenario,
            target=ns.scenario, fault=ns.fault, max_sim_time=ns.time,
            out_dir=Path(ns.out_dir), logger=logger,
            run_id=prepared["run_id"],
            artifact_id=manager.session.artifact_id)
    except Exception as exc:
        logger.exception("[TASK][v1] 运行异常: %s", exc)
        error = f"{type(exc).__name__}: {exc}"
        try:
            manager.stop()
        except Exception:
            pass
        manager.finish_run("FAILED", error)
        return 1

    lifecycle = manager.finish_run(
        "DONE" if summary.get("all_ok") else "FAILED")
    summary["lifecycle"] = lifecycle
    _print(summary)
    return 0 if summary.get("all_ok") else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=spec.PRODUCT_NAME,
        description=f"柔性工业车间 v1 ({spec.PRODUCT_VERSION})")
    parser.add_argument("--version", action="version",
                        version=f"{spec.PRODUCT_NAME} {spec.PRODUCT_VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list", help="列出 v1 工件")
    _add_common(p)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("status", help="查看生命周期状态")
    _add_common(p)
    p.add_argument("--offline", action="store_true",
                   help="不连接 CoppeliaSim, 只读会话状态")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("load", help="加载 v1 工件 (不 build/不清场)")
    _add_common(p)
    p.add_argument("--artifact", default="current")
    p.set_defaults(func=cmd_load)

    p = sub.add_parser("validate", help="只读校验当前场景")
    _add_common(p)
    p.add_argument("--artifact", default="current")
    p.add_argument("--no-load", dest="load", action="store_false",
                   help="不先 load, 校验当前已加载场景")
    p.set_defaults(func=cmd_validate, load=True)

    p = sub.add_parser("stop", help="停止仿真并确认停止态")
    _add_common(p)
    p.add_argument("--timeout", type=float, default=10.0)
    p.set_defaults(func=cmd_stop)

    p = sub.add_parser("migrate", help="从 stage4 L5_UFLOW 工件生成 v1 工件")
    _add_common(p)
    p.add_argument("--source", required=True,
                   help="stage4 工件目录 (只读)")
    p.add_argument("--note", default="")
    p.add_argument("--force", action="store_true",
                   help="即使 current 已指向同一来源也创建新版本")
    p.set_defaults(func=cmd_migrate)

    p = sub.add_parser("build", help="从历史 stage3 父场景显式构建 v1 场景")
    _add_common(p)
    p.add_argument("--parent", required=True, help="历史 stage3 工件目录")
    p.add_argument("--layout", default=None, help="可选布局 JSON")
    p.add_argument("--save", action="store_true", help="构建后立即保存 v1 工件")
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("run", help="运行 full_flow (唯一用户运行模式)")
    _add_common(p)
    p.add_argument("--artifact", default="current")
    p.add_argument("--no-load", action="store_true",
                   help="使用当前已加载场景, 不重新 load")
    p.add_argument("--scenario", default="full_flow",
                   choices=["full_flow", "no_pair", "agv_priority",
                            "agv_blocked", "conveyor_fault",
                            "staggered_batch"],
                   help="full_flow 为正式入口; 其余为内部回归/诊断场景")
    p.add_argument("--fault", default="none",
                   choices=["none", "agv_blocked", "conveyor_stop"])
    p.add_argument("--time", type=float, default=1200.0)
    p.add_argument("--log", default=spec.DEFAULT_LOG_FILE)
    p.add_argument("--out-dir", default=str(spec.REPORT_DIR))
    p.set_defaults(func=cmd_run)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    ns = parser.parse_args(argv)
    try:
        return int(ns.func(ns))
    except ValidationFailedError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 3
    except (LifecycleError, sa.ArtifactError, migration.MigrationError) as exc:
        print(f"[ERROR] {getattr(exc, 'code', type(exc).__name__)}: {exc}",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
