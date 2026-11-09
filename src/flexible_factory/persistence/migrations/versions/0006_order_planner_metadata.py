"""order version planner metadata.

Revision ID: 0006_order_planner_metadata
Revises: 0005_orders
Create Date: 2026-11-07

Scope: docs/implementation/plans/order-orchestration/v1-order-dag-phase-04-auto-planner.md.
Planner drafts remain ordinary immutable order versions; these nullable columns
record the policy version, frozen input summary, explain result and candidate
count for audit/replay. They are not part of the runtime order graph.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006_order_planner_metadata"
down_revision: Union[str, None] = "0005_orders"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "order_version",
        sa.Column("planner_policy_version", sa.Text(), nullable=True))
    op.add_column(
        "order_version",
        sa.Column("planner_input_json", sa.JSON(), nullable=True))
    op.add_column(
        "order_version",
        sa.Column("planner_explain_json", sa.JSON(), nullable=True))
    op.add_column(
        "order_version",
        sa.Column("planner_candidate_count", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("order_version", "planner_candidate_count")
    op.drop_column("order_version", "planner_explain_json")
    op.drop_column("order_version", "planner_input_json")
    op.drop_column("order_version", "planner_policy_version")
