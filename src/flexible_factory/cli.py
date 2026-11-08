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
from .application.batch import (BatchError, DEFAULT_BATCH_PROFILE,
                                batch_profile_names, resolve_batch)
from .application.catalog import (catalog_status, catalog_verify,
                                  publish_release, runtime_catalog,
                                  shadow_compare, stage_default_catalog)
from .application.knowledge import (EXTRACTOR_VERSION,
                                    import_knowledge_export,
                                    knowledge_status as kg_status,
                                    knowledge_verify as kg_verify,
                                    publish_knowledge)
from .application.report import (report_genealogy, report_run, report_runs,
                                 report_timeline, report_waits)
from .application.lifecycle import (LifecycleError, LifecycleManager,
                                    ValidationFailedError)
from .application.orders import OrderService
from .application.run import REPORT_DIR, run_loaded_scene_v1, setup_logger
from .api.server import OrderApiServer
from .artifacts import migration
from .artifacts import store as sa
from .domain.default_process import default_catalog
from .domain.orders import OrderCompileError, OrderSpecError, \
    OrderValidationError
from .persistence.db import CatalogDatabase, initialize_database
from .persistence.errors import CatalogPersistenceError
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


def _catalog_db(ns) -> CatalogDatabase:
    path = getattr(ns, "db", None) or ns.catalog_db
    return CatalogDatabase(Path(path))


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


def cmd_catalog_init(ns) -> int:
    _print(initialize_database(_catalog_db(ns)))
    return 0


def cmd_catalog_import(ns) -> int:
    database = _catalog_db(ns)
    # 导入前幂等升级 schema; init 仍是独立的显式入口.
    initialize_database(database)
    _print(stage_default_catalog(
        database, release_id=ns.release_id or "",
        catalog_version=ns.catalog_version or ""))
    return 0


def cmd_catalog_publish(ns) -> int:
    _print(publish_release(_catalog_db(ns), ns.release_id))
    return 0


def cmd_catalog_status(ns) -> int:
    _print(catalog_status(_catalog_db(ns), ns.release_id or None))
    return 0


def cmd_catalog_diff(ns) -> int:
    report = shadow_compare(_catalog_db(ns), ns.release_id or None)
    _print(report)
    return 0 if report.get("ok") else 1


def cmd_catalog_verify(ns) -> int:
    report = catalog_verify(_catalog_db(ns), ns.release_id or None)
    _print(report)
    return 0 if report.get("ok") else 1


def cmd_knowledge_import(ns) -> int:
    database = _catalog_db(ns)
    initialize_database(database)
    _print(import_knowledge_export(
        database, export_path=Path(ns.export),
        source_dump=Path(ns.source_dump),
        extractor_version=ns.extractor_version))
    return 0


def cmd_knowledge_publish(ns) -> int:
    _print(publish_knowledge(_catalog_db(ns), ns.extraction_id))
    return 0


def cmd_knowledge_status(ns) -> int:
    _print(kg_status(_catalog_db(ns), ns.extraction_id or None))
    return 0


def cmd_knowledge_verify(ns) -> int:
    report = kg_verify(_catalog_db(ns), ns.extraction_id or None)
    _print(report)
    return 0 if report.get("ok") else 1


def cmd_report_runs(ns) -> int:
    _print({"runs": report_runs(_catalog_db(ns), limit=ns.limit)})
    return 0


def cmd_report_run(ns) -> int:
    _print(report_run(_catalog_db(ns), ns.run_id or None))
    return 0


def cmd_report_genealogy(ns) -> int:
    _print(report_genealogy(_catalog_db(ns), uid=ns.uid,
                            run_id=ns.run_id or None))
    return 0


def cmd_report_waits(ns) -> int:
    _print(report_waits(_catalog_db(ns), run_id=ns.run_id or None,
                        resource_id=ns.resource or ""))
    return 0


def cmd_report_timeline(ns) -> int:
    _print(report_timeline(_catalog_db(ns), run_id=ns.run_id or None,
                           instance_id=ns.instance_id or ""))
    return 0


# ═══════════════════════════════════════════════════════════════
# 订单 DAG 命令 (阶段 1–3)
# ═══════════════════════════════════════════════════════════════

def _read_spec(ns) -> dict:
    if ns.file == "-":
        return json.load(sys.stdin)
    return json.loads(Path(ns.file).read_text(encoding="utf-8"))


def _order_catalog(ns):
    if getattr(ns, "catalog_source", "code") == "db":
        return runtime_catalog(_catalog_db(ns))
    return default_catalog()


def _order_service(ns, *, runner=None) -> OrderService:
    database = _catalog_db(ns)
    initialize_database(database)
    catalog = _order_catalog(ns)
    resolver = None
    if hasattr(ns, "artifact_root"):
        store = _store(ns)
        resolver = lambda ref: store.find(ref).artifact_id
    return OrderService(
        database, catalog=catalog,
        artifact_id=getattr(ns, "artifact_id", "") or "current",
        runner=runner, artifact_resolver=resolver)


def _order_runner(ns):
    from .application.order_runner import (OrderRunnerConfig,
                                           make_order_runner)
    record_db = Path(ns.db) if ns.run_records == "on" else None
    sim_host = getattr(ns, "sim_host", None) or ns.host
    sim_port = getattr(ns, "sim_port", None) or ns.port
    return make_order_runner(OrderRunnerConfig(
        host=sim_host, port=int(sim_port),
        artifact_root=(Path(ns.artifact_root) if ns.artifact_root else None),
        record_db=record_db, run_records=(ns.run_records == "on"),
        catalog_source=ns.catalog_source,
        log=ns.log, out_dir=Path(ns.out_dir)), catalog=_order_catalog(ns))


def cmd_order_validate(ns) -> int:
    service = _order_service(ns)
    preview = service.preview(_read_spec(ns))
    _print(preview.as_dict())
    return 0 if preview.ok else 1


def cmd_order_create(ns) -> int:
    service = _order_service(ns)
    _print({"order": service.create_order(
        _read_spec(ns), source=ns.source, note=ns.note)})
    return 0


def cmd_order_version(ns) -> int:
    service = _order_service(ns)
    _print({"order": service.create_version(
        ns.order_id, _read_spec(ns), note=ns.note)})
    return 0


def cmd_order_list(ns) -> int:
    service = _order_service(ns)
    _print({"orders": service.list_orders(limit=ns.limit)})
    return 0


def cmd_order_show(ns) -> int:
    service = _order_service(ns)
    if ns.version:
        _print({"version": service.get_version(ns.order_id, ns.version)})
    else:
        _print({"order": service.get_order(ns.order_id)})
    return 0


def cmd_order_preview(ns) -> int:
    service = _order_service(ns)
    preview = service.preview_spec(
        service.repository.get_spec(ns.order_id, ns.version))
    _print(preview.as_dict())
    return 0 if preview.ok else 1


def cmd_order_plan(ns) -> int:
    service = _order_service(ns)
    payload = _read_spec(ns)
    if ns.save:
        result = service.plan_and_save(payload, note=ns.note)
        _print(result)
        return 0 if result.get("ok") else 1
    result = service.plan(payload)
    _print(result.as_dict())
    return 0 if result.ok else 1


def cmd_order_start(ns) -> int:
    service = _order_service(ns)
    result = service.run_sync(
        ns.order_id, ns.version, _order_runner(ns),
        artifact_id=ns.artifact_id, max_sim_time=ns.max_time)
    _print(result)
    return 0 if result.get("status") == "SUCCEEDED" else 1


def cmd_order_status(ns) -> int:
    service = _order_service(ns)
    _print({"run": service.status(ns.run_id)})
    return 0


def cmd_order_nodes(ns) -> int:
    service = _order_service(ns)
    _print({"nodes": service.nodes(ns.run_id)})
    return 0


def cmd_order_events(ns) -> int:
    service = _order_service(ns)
    _print({"events": service.events(ns.run_id, limit=ns.limit)})
    return 0


def cmd_order_runs(ns) -> int:
    service = _order_service(ns)
    _print({"runs": service.list_runs(order_id=ns.order_id,
                                      limit=ns.limit)})
    return 0


def cmd_order_stop(ns) -> int:
    service = _order_service(ns)
    _print({"run": service.stop(ns.run_id)})
    return 0


def cmd_api_serve(ns) -> int:
    service = _order_service(ns, runner=_order_runner(ns))
    server = OrderApiServer(service, host=ns.host, port=ns.port)
    logger = setup_logger(ns.log)
    logger.info("[API] order service listening on %s", server.base_url)
    print(json.dumps({"ok": True, "base_url": server.base_url},
                     ensure_ascii=False))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if service.worker is not None:
            service.worker.shutdown()
        server.shutdown()
    return 0


def cmd_run(ns) -> int:
    # 批次输入在连接仿真/取得运行锁之前校验; 非法值不进入生命周期.
    resolve_batch(ns.scenario, profile=ns.batch_profile)
    catalog = None
    if ns.catalog_source == "db":
        # 显式选库: 必须与当前代码目录完全等价, 否则在连接仿真前拒绝.
        catalog = runtime_catalog(_catalog_db(ns))
    run_record_db = (Path(ns.catalog_db)
                     if ns.run_records == "on" else None)
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
            target=ns.scenario, fault=ns.fault,
            batch_profile=ns.batch_profile,
            max_sim_time=ns.time,
            out_dir=Path(ns.out_dir), logger=logger,
            catalog=catalog, run_record_db=run_record_db,
            run_record_source=ns.catalog_source,
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

    catalog_parser = sub.add_parser(
        "catalog", help="静态资源/工艺目录数据库 (维护操作)")
    catalog_sub = catalog_parser.add_subparsers(
        dest="catalog_command", required=True)

    def _catalog_command(name: str, help_text: str) -> argparse.ArgumentParser:
        child = catalog_sub.add_parser(name, help=help_text)
        child.add_argument(
            "--db", default=str(spec.CATALOG_DB),
            help="SQLite 目录数据库路径 (默认 var/catalog/catalog.db)")
        return child

    p = _catalog_command("init", "创建或升级目录数据库 schema")
    p.set_defaults(func=cmd_catalog_init)

    p = _catalog_command("import", "把默认目录导入为 staging release")
    p.add_argument("--release-id", default="",
                   help="新 release ID (默认按目录版本和时间生成)")
    p.add_argument("--catalog-version", default="",
                   help="必须与目录版本一致; 留空使用目录版本")
    p.set_defaults(func=cmd_catalog_import)

    p = _catalog_command("publish", "校验并原子发布 staging release")
    p.add_argument("--release-id", required=True)
    p.set_defaults(func=cmd_catalog_publish)

    p = _catalog_command("status", "列出 release 并显示当前发布版本")
    p.add_argument("--release-id", default="",
                   help="查看指定 release (默认当前发布版本)")
    p.set_defaults(func=cmd_catalog_status)

    p = _catalog_command("diff", "对比代码默认目录与数据库目录")
    p.add_argument("--release-id", default="",
                   help="对比指定 release (默认当前发布版本)")
    p.set_defaults(func=cmd_catalog_diff)

    p = _catalog_command("verify", "校验数据库结构、外键和目录往返")
    p.add_argument("--release-id", default="",
                   help="校验指定 release (默认当前发布版本)")
    p.set_defaults(func=cmd_catalog_verify)

    knowledge_parser = sub.add_parser(
        "knowledge", help="知识图谱导出导入 (维护操作)")
    knowledge_sub = knowledge_parser.add_subparsers(
        dest="knowledge_command", required=True)

    def _knowledge_command(name: str,
                           help_text: str) -> argparse.ArgumentParser:
        child = knowledge_sub.add_parser(name, help=help_text)
        child.add_argument(
            "--db", default=str(spec.CATALOG_DB),
            help="SQLite 目录数据库路径 (默认 var/catalog/catalog.db)")
        return child

    p = _knowledge_command(
        "import", "把 Neo4j 导出 JSON 导入为 staging extraction")
    p.add_argument("--export", required=True,
                   help="tools/neo4j_export.py 生成的 JSON")
    p.add_argument("--source-dump", required=True,
                   help="原始 fmpmm.dump (只计算摘要, 不解析)")
    p.add_argument("--extractor-version", default=EXTRACTOR_VERSION,
                   help="导出器版本; 同来源/版本重跑必须幂等")
    p.set_defaults(func=cmd_knowledge_import)

    p = _knowledge_command("publish", "审核后发布 staging extraction")
    p.add_argument("--extraction-id", required=True)
    p.set_defaults(func=cmd_knowledge_publish)

    p = _knowledge_command("status", "显示抽取运行和计数")
    p.add_argument("--extraction-id", default="",
                   help="指定 extraction (默认最近一次)")
    p.set_defaults(func=cmd_knowledge_status)

    p = _knowledge_command("verify", "校验抽取、外键和未审核项")
    p.add_argument("--extraction-id", default="",
                   help="指定 extraction (默认最近一次)")
    p.set_defaults(func=cmd_knowledge_verify)

    report_parser = sub.add_parser(
        "report", help="运行记录查询 (Q1–Q4, 只读)")
    report_sub = report_parser.add_subparsers(
        dest="report_command", required=True)

    def _report_command(name: str,
                        help_text: str) -> argparse.ArgumentParser:
        child = report_sub.add_parser(name, help=help_text)
        child.add_argument(
            "--db", default=str(spec.CATALOG_DB),
            help="SQLite 数据库路径 (默认 var/catalog/catalog.db)")
        return child

    p = _report_command("runs", "列出运行记录")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_report_runs)

    p = _report_command("run", "Q1 运行汇总与验收")
    p.add_argument("--run-id", default="", help="默认最近一次运行")
    p.set_defaults(func=cmd_report_run)

    p = _report_command("genealogy", "Q2 成品物料谱系")
    p.add_argument("--run-id", default="", help="默认最近一次运行")
    p.add_argument("--uid", type=int, required=True)
    p.set_defaults(func=cmd_report_genealogy)

    p = _report_command("waits", "Q3 资源等待与竞争")
    p.add_argument("--run-id", default="", help="默认最近一次运行")
    p.add_argument("--resource", default="", help="只看指定资源")
    p.set_defaults(func=cmd_report_waits)

    p = _report_command("timeline", "Q4 工序时间线与失败取证")
    p.add_argument("--run-id", default="", help="默认最近一次运行")
    p.add_argument("--instance-id", default="")
    p.set_defaults(func=cmd_report_timeline)

    order_parser = sub.add_parser(
        "order", help="订单 DAG: 校验/版本/预览/运行 (阶段 1–3)")
    order_sub = order_parser.add_subparsers(
        dest="order_command", required=True)

    def _order_command(name: str, help_text: str) -> argparse.ArgumentParser:
        child = order_sub.add_parser(name, help=help_text)
        child.add_argument("--db", default=str(spec.CATALOG_DB),
                           help="SQLite 数据库路径")
        child.add_argument("--catalog-source", choices=["code", "db"],
                           default="code",
                           help="订单模板来源; db 必须与代码目录等价")
        return child

    p = _order_command("validate", "校验并预览订单 JSON")
    p.add_argument("--file", required=True,
                   help="订单 JSON 文件, 或 '-' 从 stdin 读取")
    p.set_defaults(func=cmd_order_validate)

    p = _order_command("create", "创建订单首版本")
    p.add_argument("--file", required=True)
    p.add_argument("--source", default="manual",
                   choices=["manual", "planner", "fixture"])
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_order_create)

    p = _order_command("version", "为订单创建新版本")
    p.add_argument("--order-id", required=True)
    p.add_argument("--file", required=True)
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_order_version)

    p = _order_command("list", "列出订单")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_order_list)

    p = _order_command("show", "查看订单或指定版本")
    p.add_argument("--order-id", required=True)
    p.add_argument("--version", type=int, default=0)
    p.set_defaults(func=cmd_order_show)

    p = _order_command("preview", "预览已保存版本的编译结果")
    p.add_argument("--order-id", required=True)
    p.add_argument("--version", type=int, required=True)
    p.set_defaults(func=cmd_order_preview)

    p = _order_command("plan", "根据目标/投料自主生成订单草稿")
    p.add_argument("--file", required=True,
                   help="planner 输入 JSON, 或 '-' 从 stdin 读取")
    p.add_argument("--save", action="store_true",
                   help="保存为 source=planner 的订单版本")
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_order_plan)

    p = _order_command("start", "同步运行一个订单版本")
    p.add_argument("--order-id", required=True)
    p.add_argument("--version", type=int, required=True)
    p.add_argument("--artifact-id", default="current")
    p.add_argument("--time", dest="max_time", type=float, default=1200.0)
    p.add_argument("--run-records", choices=["on", "off"], default="on")
    _add_common(p)
    p.add_argument("--log", default=spec.DEFAULT_LOG_FILE)
    p.add_argument("--out-dir", default=str(spec.REPORT_DIR))
    p.set_defaults(func=cmd_order_start)

    p = _order_command("status", "查询订单运行状态")
    p.add_argument("--run-id", required=True)
    p.set_defaults(func=cmd_order_status)

    p = _order_command("nodes", "查询订单节点快照")
    p.add_argument("--run-id", required=True)
    p.set_defaults(func=cmd_order_nodes)

    p = _order_command("events", "查询订单运行事件")
    p.add_argument("--run-id", required=True)
    p.add_argument("--limit", type=int, default=2000)
    p.set_defaults(func=cmd_order_events)

    p = _order_command("stop", "停止活动订单运行")
    p.add_argument("--run-id", required=True)
    p.set_defaults(func=cmd_order_stop)

    p = _order_command("runs", "列出订单运行")
    p.add_argument("--order-id", default="")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_order_runs)

    api_parser = sub.add_parser(
        "api", help="订单 HTTP 服务 (阶段 3)")
    api_sub = api_parser.add_subparsers(dest="api_command", required=True)
    p = api_sub.add_parser("serve", help="启动本地订单 HTTP 服务")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--sim-host", default="localhost",
                   help="CoppeliaSim ZMQ host (默认 localhost)")
    p.add_argument("--sim-port", type=int, default=23000,
                   help="CoppeliaSim ZMQ port (默认 23000)")
    p.add_argument("--db", default=str(spec.CATALOG_DB))
    p.add_argument("--catalog-source", choices=["code", "db"],
                   default="code")
    p.add_argument("--artifact-root", default=None)
    p.add_argument("--artifact-id", default="current")
    p.add_argument("--run-records", choices=["on", "off"], default="on")
    p.add_argument("--log", default=spec.DEFAULT_LOG_FILE)
    p.add_argument("--out-dir", default=str(spec.REPORT_DIR))
    p.set_defaults(func=cmd_api_serve)

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
    p.add_argument(
        "--batch-profile", default=DEFAULT_BATCH_PROFILE,
        choices=list(batch_profile_names()),
        help="命名批次配比: 只改变 RAW_A/RAW_B/RAW_D 数量, 不改变工艺"
             "路线或设备候选; 非 default profile 仅用于 full_flow")
    p.add_argument("--time", type=float, default=1200.0)
    p.add_argument("--log", default=spec.DEFAULT_LOG_FILE)
    p.add_argument("--out-dir", default=str(spec.REPORT_DIR))
    p.add_argument("--catalog-source", choices=["code", "db"],
                   default="code",
                   help="运行时工艺目录来源; 默认 code, db 为显式选库")
    p.add_argument("--catalog-db", default=str(spec.CATALOG_DB),
                   help="目录/运行记录数据库路径")
    p.add_argument("--run-records", choices=["on", "off"], default="on",
                   help="运行结束批量写运行记录 (默认 on; 写失败不影响运行)")
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
    except (OrderSpecError, OrderValidationError) as exc:
        print(f"[ERROR] {getattr(exc, 'code', type(exc).__name__)}: {exc}",
              file=sys.stderr)
        return 3
    except (LifecycleError, sa.ArtifactError, migration.MigrationError,
            CatalogPersistenceError, BatchError) as exc:
        print(f"[ERROR] {getattr(exc, 'code', type(exc).__name__)}: {exc}",
              file=sys.stderr)
        return 2
    except Exception as exc:
        # planner 模块延迟装配; 不因导入阶段 4 模块而扩大 CLI 导入面.
        code = str(getattr(exc, "code", ""))
        if code.startswith("PLANNER"):
            print(f"[ERROR] {code}: {exc}", file=sys.stderr)
            return 3
        raise


if __name__ == "__main__":
    raise SystemExit(main())
