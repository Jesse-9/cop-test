"""versioned orders and order run linkage.

Revision ID: 0005_orders
Revises: 0004_order_catalog_metadata
Create Date: 2026-11-06

Scope: docs/implementation/plans/order-orchestration/v1-order-dag-phase-03-api-persistence.md.
Order definitions are separate from static catalog releases. ``order_version``
is append-only; a trigger rejects UPDATE so a running/recorded version cannot
be overwritten in place. ``order_run`` links a run to an immutable version and
does not require ``production_run`` to be written first: the order service owns
the association, while RunRecorder owns the simulation snapshot.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005_orders"
down_revision: Union[str, None] = "0004_order_catalog_metadata"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "order_header",
        sa.Column("order_id", sa.Text(), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.CheckConstraint("current_version > 0",
                           name=op.f("ck_order_header_current_version_positive")),
        sa.CheckConstraint("source IN ('manual', 'planner', 'fixture')",
                           name=op.f("ck_order_header_source_enum")),
        sa.CheckConstraint("length(trim(order_id)) > 0",
                           name=op.f("ck_order_header_order_id_nonempty")),
        sa.PrimaryKeyConstraint("order_id", name=op.f("pk_order_header")),
    )
    with op.batch_alter_table("order_header", schema=None) as batch_op:
        batch_op.create_index("ix_order_header_updated", ["updated_at"],
                              unique=False)

    op.create_table(
        "order_version",
        sa.Column("order_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("contract_version", sa.Text(), nullable=False),
        sa.Column("scene_id", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("dag_digest", sa.Text(), nullable=False),
        sa.Column("spec_json", sa.Text(), nullable=False),
        sa.Column("catalog_version", sa.Text(), nullable=True),
        sa.Column("parent_version", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.CheckConstraint("version > 0",
                           name=op.f("ck_order_version_version_positive")),
        sa.CheckConstraint("length(dag_digest) = 64",
                           name=op.f("ck_order_version_dag_digest_sha256")),
        sa.CheckConstraint("source IN ('manual', 'planner', 'fixture')",
                           name=op.f("ck_order_version_source_enum")),
        sa.CheckConstraint("length(trim(contract_version)) > 0",
                           name=op.f("ck_order_version_contract_version_nonempty")),
        sa.CheckConstraint("length(trim(order_id)) > 0",
                           name=op.f("ck_order_version_order_id_nonempty")),
        sa.CheckConstraint("length(trim(scene_id)) > 0",
                           name=op.f("ck_order_version_scene_id_nonempty")),
        sa.ForeignKeyConstraint(["order_id"], ["order_header.order_id"],
                                name="fk_order_version_header",
                                ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("order_id", "version",
                                name=op.f("pk_order_version")),
    )
    with op.batch_alter_table("order_version", schema=None) as batch_op:
        batch_op.create_index("ix_order_version_order",
                              ["order_id", "version"], unique=False)

    op.execute(
        "CREATE TRIGGER trg_order_version_immutable "
        "BEFORE UPDATE ON order_version BEGIN "
        "SELECT RAISE(ABORT, 'order_version is immutable'); END")

    op.create_table(
        "order_run",
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("order_id", sa.Text(), nullable=False),
        sa.Column("order_version", sa.Integer(), nullable=False),
        sa.Column("dag_digest", sa.Text(), nullable=False),
        sa.Column("artifact_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("failure_scope", sa.Text(), nullable=True),
        sa.Column("stop_reason", sa.Text(), nullable=True),
        sa.Column("started_at", sa.Text(), nullable=False),
        sa.Column("finished_at", sa.Text(), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.CheckConstraint(
            "(status = 'STARTING' AND finished_at IS NULL) OR "
            "(status = 'RUNNING' AND finished_at IS NULL) OR "
            "(status IN ('SUCCEEDED', 'PARTIAL_FAILED', 'FAILED', "
            "'ABORTED') AND finished_at IS NOT NULL)",
            name=op.f("ck_order_run_finished_at_consistent")),
        sa.CheckConstraint(
            "status IN ('STARTING', 'RUNNING', 'SUCCEEDED', "
            "'PARTIAL_FAILED', 'FAILED', 'ABORTED')",
            name=op.f("ck_order_run_status_enum")),
        sa.CheckConstraint(
            "failure_scope IS NULL OR failure_scope IN ('NODE', 'GLOBAL')",
            name=op.f("ck_order_run_failure_scope_enum")),
        sa.CheckConstraint("length(dag_digest) = 64",
                           name=op.f("ck_order_run_dag_digest_sha256")),
        sa.CheckConstraint("length(trim(run_id)) > 0",
                           name=op.f("ck_order_run_run_id_nonempty")),
        sa.ForeignKeyConstraint(
            ["order_id", "order_version"],
            ["order_version.order_id", "order_version.version"],
            name="fk_order_run_version", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("run_id", name=op.f("pk_order_run")),
    )
    with op.batch_alter_table("order_run", schema=None) as batch_op:
        batch_op.create_index("ix_order_run_order",
                              ["order_id", "order_version"], unique=False)
        batch_op.create_index("ix_order_run_status", ["status"], unique=False)

    op.create_table(
        "order_node_run",
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("instance_id", sa.Text(), nullable=False),
        sa.Column("node_id", sa.Text(), nullable=False),
        sa.Column("template_id", sa.Text(), nullable=False),
        sa.Column("uid", sa.Integer(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("failure_scope", sa.Text(), nullable=True),
        sa.Column("failure_code", sa.Text(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("blocked_by_json", sa.JSON(), nullable=True),
        sa.Column("input_uids_json", sa.JSON(), nullable=True),
        sa.Column("output_uids_json", sa.JSON(), nullable=True),
        sa.Column("evidence_json", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint("uid >= 0",
                           name=op.f("ck_order_node_run_uid_nonnegative")),
        sa.CheckConstraint("length(trim(node_id)) > 0",
                           name=op.f("ck_order_node_run_node_id_nonempty")),
        sa.CheckConstraint("length(trim(template_id)) > 0",
                           name=op.f("ck_order_node_run_template_id_nonempty")),
        sa.ForeignKeyConstraint(["run_id"], ["order_run.run_id"],
                                name="fk_order_node_run_run",
                                ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("run_id", "instance_id",
                                name=op.f("pk_order_node_run")),
    )
    with op.batch_alter_table("order_node_run", schema=None) as batch_op:
        batch_op.create_index("ix_order_node_run_node",
                              ["run_id", "node_id"], unique=False)
        batch_op.create_index("ix_order_node_run_state",
                              ["run_id", "state"], unique=False)


def downgrade() -> None:
    op.drop_table("order_node_run")
    op.drop_table("order_run")
    op.execute("DROP TRIGGER IF EXISTS trg_order_version_immutable")
    op.drop_table("order_version")
    op.drop_table("order_header")
