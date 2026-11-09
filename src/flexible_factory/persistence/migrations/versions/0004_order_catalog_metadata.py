"""order DAG catalog metadata: ports, orchestration allowlist and scene scope.

Revision ID: 0004_order_catalog_metadata
Revises: 0003_run_records
Create Date: 2026-11-06

Scope: docs/implementation/plans/order-orchestration/v1-order-dag-phase-01-foundation.md step 4.
The columns extend the existing static catalog with information the order
compiler needs. They do not change runtime behavior of the recipe entry.

Existing releases are superseded by the catalog version bump
(``v1_process_20261106a``); re-import them before selecting a database
catalog. The migration leaves old rows with the documented defaults.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_order_catalog_metadata"
down_revision: Union[str, None] = "0003_run_records"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "zone",
        sa.Column("spawnable", sa.Boolean(), nullable=False,
                  server_default=sa.text("0")))
    op.add_column(
        "operation_template",
        sa.Column("orchestratable", sa.Boolean(), nullable=False,
                  server_default=sa.text("1")))
    op.add_column(
        "operation_template",
        sa.Column("scene_id", sa.Text(), nullable=False,
                  server_default=sa.text("''")))
    op.add_column(
        "operation_template",
        sa.Column("output_port_id", sa.Text(), nullable=False,
                  server_default=sa.text("''")))
    op.add_column(
        "operation_template",
        sa.Column("join_handler_id", sa.Text(), nullable=False,
                  server_default=sa.text("''")))
    op.add_column(
        "operation_input",
        sa.Column("port_id", sa.Text(), nullable=False,
                  server_default=sa.text("''")))


def downgrade() -> None:
    op.drop_column("operation_input", "port_id")
    op.drop_column("operation_template", "join_handler_id")
    op.drop_column("operation_template", "output_port_id")
    op.drop_column("operation_template", "scene_id")
    op.drop_column("operation_template", "orchestratable")
    op.drop_column("zone", "spawnable")
