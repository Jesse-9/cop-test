"""v1 静态目录持久化: 迁移、约束、导入/发布与往返等价."""

from __future__ import annotations

from dataclasses import replace

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from flexible_factory.application.catalog import (default_seed,
                                                   runtime_catalog,
                                                   shadow_compare,
                                                   stage_default_catalog)
from flexible_factory.cli import build_parser, main
from flexible_factory.domain.default_process import default_catalog
from flexible_factory.persistence import importer as importer_module
from flexible_factory.persistence.db import (CatalogDatabase, alembic_config,
                                              current_revision,
                                              initialize_database, table_names)
from flexible_factory.persistence.errors import (CatalogIntegrityError,
                                                  NoPublishedReleaseError,
                                                  ReleaseExistsError)
from flexible_factory.persistence.models import Base
from flexible_factory.persistence.records import seed_digest
from flexible_factory.persistence.repository import list_releases
from flexible_factory.persistence.service import (load_catalog, load_seed,
                                                   publish_release)

EXPECTED_TABLES = {
    "catalog_release", "material_type", "resource", "zone",
    "zone_material_type", "capability", "device", "device_capability",
    "device_resource", "scene_binding", "recipe", "operation_template",
    "operation_dependency", "operation_input", "operation_output",
    "operation_candidate_device", "operation_resource_request",
    "operation_work_step", "route_variant", "route_operation",
    "order_entry",
}
KNOWLEDGE_TABLES = {
    "source_document", "extraction_run", "knowledge_entity",
    "knowledge_relation", "entity_source", "relation_source",
}
RUN_TABLES = {
    "production_run", "operation_instance", "material_instance",
    "material_genealogy", "resource_event", "run_event",
    "operation_instance_input", "operation_instance_output",
}


@pytest.fixture()
def database(tmp_path) -> CatalogDatabase:
    db = CatalogDatabase(tmp_path / "catalog.db")
    initialize_database(db)
    return db


def _stage(database: CatalogDatabase, release_id: str = "rel_test") -> dict:
    return stage_default_catalog(database, release_id=release_id)


def _release_rows(database: CatalogDatabase) -> list[dict]:
    with database.session() as session:
        return list_releases(session)


# ═══════════════════════════════════════════════════════════════
# schema / 迁移
# ═══════════════════════════════════════════════════════════════

def test_migration_creates_all_tables_and_is_idempotent(database):
    revision = current_revision(database)
    head = ScriptDirectory.from_config(
        alembic_config(database)).get_current_head()
    assert revision == head
    names = set(table_names(database))
    all_tables = EXPECTED_TABLES | KNOWLEDGE_TABLES | RUN_TABLES
    assert names == all_tables | {"alembic_version"}
    assert set(Base.metadata.tables) == all_tables

    first = initialize_database(database)
    second = initialize_database(database)
    assert first["revision"] == second["revision"] == revision
    with database.connect() as connection:
        assert connection.execute(text(
            "SELECT COUNT(*) FROM alembic_version")).scalar() == 1


def test_migration_downgrade_to_base_and_upgrade_again(database):
    config = alembic_config(database)
    command.downgrade(config, "base")
    assert set(table_names(database)) == {"alembic_version"}
    command.upgrade(config, "head")
    assert set(table_names(database)) == (
        EXPECTED_TABLES | KNOWLEDGE_TABLES | RUN_TABLES | {"alembic_version"})


def test_foreign_keys_enabled_on_every_connection(database):
    for _ in range(2):
        with database.connect() as connection:
            assert connection.execute(
                text("PRAGMA foreign_keys")).scalar() == 1


def test_schema_contains_no_runtime_handle_or_queue_columns(database):
    inspector = inspect(database.create_engine())
    banned = ("handle", "owner", "waiting", "sim_time")
    for table in inspector.get_table_names():
        if table in RUN_TABLES:
            # 运行记录表按设计保存 owner/sim_time; 仍禁止场景句柄.
            banned_columns = ("handle",)
        else:
            banned_columns = banned
        for column in inspector.get_columns(table):
            name = column["name"].lower()
            assert not any(token in name for token in banned_columns), \
                (table, name)


# ═══════════════════════════════════════════════════════════════
# 约束
# ═══════════════════════════════════════════════════════════════

def test_foreign_key_violation_is_rejected(database):
    with database.connect() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(text(
                "INSERT INTO material_type (release_id, material_type_id, "
                "ordinal) VALUES ('missing', 'RAW_A', 0)"))
            connection.commit()


def test_capacity_check_is_rejected(database):
    _stage(database)
    with database.connect() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(text(
                "UPDATE zone SET capacity = 0 "
                "WHERE release_id = 'rel_test' AND zone_id = 'output_e'"))
        connection.rollback()


def test_duplicate_zone_id_is_rejected(database):
    _stage(database)
    with database.connect() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(text(
                "INSERT INTO zone (release_id, zone_id, ordinal, capacity) "
                "VALUES ('rel_test', 'output_e', 99, 1)"))
            connection.commit()


def test_duplicate_business_order_is_rejected(database):
    _stage(database)
    with database.connect() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(text(
                "INSERT INTO zone (release_id, zone_id, ordinal, capacity) "
                "VALUES ('rel_test', 'extra_zone', 0, 1)"))
            connection.commit()


def test_controlled_enum_and_self_loop_are_rejected(database):
    _stage(database)
    with database.connect() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(text(
                "UPDATE operation_template SET executor_kind = 'drone' "
                "WHERE release_id = 'rel_test' "
                "AND operation_id = 'housing_direct_A'"))
        connection.rollback()
    with database.connect() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(text(
                "INSERT INTO operation_dependency (release_id, operation_id, "
                "predecessor_operation_id, ordinal, successor_ordinal) "
                "VALUES ('rel_test', 'housing_finish', 'housing_finish', "
                "99, 99)"))
            connection.commit()


def test_single_published_release_index(database):
    _stage(database, "rel_a")
    _stage(database, "rel_b")
    publish_release(database, "rel_a")
    publish_release(database, "rel_b")
    with database.connect() as connection:
        assert connection.execute(text(
            "SELECT COUNT(*) FROM catalog_release "
            "WHERE status = 'published'")).scalar() == 1
    rows = {row["release_id"]: row for row in _release_rows(database)}
    assert rows["rel_a"]["status"] == "superseded"
    assert rows["rel_b"]["status"] == "published"
    assert rows["rel_a"]["published_at"] is not None


# ═══════════════════════════════════════════════════════════════
# 导入 / 发布 / 读取
# ═══════════════════════════════════════════════════════════════

def test_staging_is_not_published_and_can_be_read(database):
    result = _stage(database)
    assert result["status"] == "staging"
    assert result["catalog_version"] == "v1_process_20261103b"
    assert result["counts"]["operations"] == 10
    with pytest.raises(NoPublishedReleaseError):
        load_catalog(database)
    catalog = load_catalog(database, "rel_test")
    assert catalog.recipe("mixed_line_v1").product_type == "assembled_module"


def test_publish_and_round_trip_equal_code_catalog(database):
    _stage(database)
    published = publish_release(database, "rel_test")
    assert published["changed"] is True
    assert published["status"] == "published"

    code_catalog = default_catalog()
    database_catalog = load_catalog(database)
    assert database_catalog.as_dict() == code_catalog.as_dict()
    assert seed_digest(load_seed(database)) == seed_digest(default_seed())
    assert runtime_catalog(database).as_dict() == code_catalog.as_dict()
    report = shadow_compare(database)
    assert report["ok"] is True
    assert report["differences"] == []


def test_publish_requires_staging_state(database):
    _stage(database, "rel_a")
    _stage(database, "rel_b")
    publish_release(database, "rel_a")
    # rel_b 仍是 staging, 可发布; 已 superseded 的 release 不能重复发布.
    publish_release(database, "rel_b")
    from flexible_factory.persistence.errors import ReleaseStateError
    with pytest.raises(ReleaseStateError):
        publish_release(database, "rel_a")


def test_duplicate_release_id_is_rejected_without_mutation(database):
    _stage(database)
    before = seed_digest(load_seed(database, "rel_test"))
    with pytest.raises(ReleaseExistsError):
        _stage(database)
    assert len(_release_rows(database)) == 1
    assert seed_digest(load_seed(database, "rel_test")) == before


def test_invalid_source_rolls_back_before_staging(database):
    seed = default_seed()
    broken = replace(seed, material_types=seed.material_types[:-1])
    with pytest.raises(CatalogIntegrityError):
        from flexible_factory.persistence.service import stage_catalog
        stage_catalog(database, broken, release_id="rel_broken")
    assert _release_rows(database) == []


def test_mid_transaction_failure_leaves_no_partial_release(database,
                                                          monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated import failure")

    monkeypatch.setattr(importer_module, "_add_catalog", boom)
    with pytest.raises(RuntimeError):
        _stage(database, "rel_partial")
    assert _release_rows(database) == []


def test_catalog_version_mismatch_is_rejected(database):
    with pytest.raises(CatalogIntegrityError):
        stage_default_catalog(database, release_id="rel_bad",
                              catalog_version="not_the_catalog_version")


def test_invalid_release_id_is_rejected(database):
    with pytest.raises(CatalogIntegrityError):
        stage_default_catalog(database, release_id="bad id/with slash")


def test_tampered_published_catalog_is_rejected(database):
    _stage(database)
    publish_release(database, "rel_test")
    with database.connect() as connection:
        connection.execute(text(
            "UPDATE operation_template SET priority = priority + 1 "
            "WHERE release_id = 'rel_test' "
            "AND operation_id = 'housing_direct_A'"))
        connection.commit()
    with pytest.raises(CatalogIntegrityError):
        load_catalog(database)


def test_shadow_compare_reports_missing_published_release(database):
    _stage(database)
    report = shadow_compare(database)
    assert report["ok"] is False
    assert "NO_PUBLISHED" in report["error"] or "已发布" in report["error"]


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def test_cli_catalog_init_import_publish_diff_verify(tmp_path, capsys):
    path = tmp_path / "cli" / "catalog.db"
    assert main(["catalog", "init", "--db", str(path)]) == 0
    assert main(["catalog", "import", "--db", str(path),
                 "--release-id", "rel_cli"]) == 0
    assert main(["catalog", "publish", "--db", str(path),
                 "--release-id", "rel_cli"]) == 0
    assert main(["catalog", "diff", "--db", str(path)]) == 0
    assert main(["catalog", "verify", "--db", str(path)]) == 0
    assert main(["catalog", "status", "--db", str(path)]) == 0
    output = capsys.readouterr().out
    assert "rel_cli" in output


def test_run_parser_accepts_explicit_database_catalog():
    ns = build_parser().parse_args([
        "run", "--catalog-source", "db",
        "--catalog-db", "var/catalog/catalog.db"])
    assert ns.catalog_source == "db"
    assert ns.catalog_db == "var/catalog/catalog.db"


def test_run_with_missing_catalog_database_fails_before_connecting(tmp_path,
                                                                   capsys):
    missing = tmp_path / "missing" / "catalog.db"
    code = main(["run", "--catalog-source", "db",
                 "--catalog-db", str(missing)])
    assert code == 2
    assert "CATALOG_" in capsys.readouterr().err
