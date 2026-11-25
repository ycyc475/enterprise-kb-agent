"""Add span duration and safe structured telemetry metadata to run_events."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0007_observability"
down_revision = "0006_expenses"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("run_events", sa.Column("duration_ms", sa.Integer(), nullable=True))
    op.add_column("run_events", sa.Column("meta", JSONB(), nullable=True))


def downgrade():
    op.drop_column("run_events", "meta")
    op.drop_column("run_events", "duration_ms")
