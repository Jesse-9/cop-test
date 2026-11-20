"""order run transport fleet metadata.

Revision ID: 0007_order_run_fleet
Revises: 0006_order_planner_metadata
Create Date: 2026-11-19

Scope: docs/implementation/plans/order-orchestration/v1-order-dag-phase-07-dual-fleet.md.
`transport_fleet` freezes the execution-time fleet mode (single/dual) of a run;
`fleet_policy_version` records the fixed-assignment policy used. Neither is part
of the immutable order version or the DAG digest. Existing rows backfill to
``single`` because all historical runs used the single-AGV baseline.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0007_order_run_fleet"
down_revision: Union[str, None] = "0006_order_planner_metadata"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("order_run") as batch_op:
        batch_op.add_column(sa.Column(
            "transport_fleet", sa.Text(), nullable=False,
            server_default="single"))
        batch_op.add_column(sa.Column(
            "fleet_policy_version", sa.Text(), nullable=True))
        batch_op.create_check_constraint(
            "transport_fleet_enum",
            "transport_fleet IN ('single', 'dual')")


def downgrade() -> None:
    with op.batch_alter_table("order_run") as batch_op:
        batch_op.drop_constraint("transport_fleet_enum", type_="check")
        batch_op.drop_column("fleet_policy_version")
        batch_op.drop_column("transport_fleet")