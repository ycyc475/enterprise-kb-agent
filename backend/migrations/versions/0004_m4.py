"""Source-backed fee rules and deterministic budget snapshots."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004_m4"
down_revision = "0003_m3"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "fee_rules",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("grade", sa.String(8)),
        sa.Column("cities", JSONB(), nullable=False),
        sa.Column("unit_rate", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "source_chunk_id", sa.Uuid(), sa.ForeignKey("document_chunks.id"), nullable=False
        ),
        sa.Column("source_quote", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("confirmed_by", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("suspension_reason", sa.String(64)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("kind IN ('lodging','daily')", name="ck_fee_rule_kind"),
        sa.CheckConstraint("status IN ('draft','active','suspended')", name="ck_fee_rule_status"),
        sa.CheckConstraint("unit_rate >= 0 AND unit_rate <= 999999.99", name="ck_fee_rule_rate"),
        sa.CheckConstraint(
            "(kind='lodging' AND grade IS NOT NULL AND grade IN ('S1','S2')) OR (kind='daily' AND grade IS NULL)",
            name="ck_fee_rule_grade",
        ),
    )
    op.create_index("ix_fee_rules_kind", "fee_rules", ["kind"])
    op.create_table(
        "calculations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("conversation_id", sa.Uuid(), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column(
            "message_id", sa.Uuid(), sa.ForeignKey("messages.id"), nullable=False, unique=True
        ),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("agent_runs.id")),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("client_request_id", sa.Uuid()),
        sa.Column("request_hash", sa.String(64)),
        sa.Column("profile_snapshot", JSONB(), nullable=False),
        sa.Column("trip_snapshot", JSONB(), nullable=False),
        sa.Column("context_revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("total_amount", sa.Numeric(14, 2)),
        sa.Column("missing_fields", JSONB(), nullable=False),
        sa.Column("blocked_reasons", JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "status IN ('complete','needs_information','blocked')", name="ck_calculation_status"
        ),
    )
    op.create_index("ix_calculations_conversation_id", "calculations", ["conversation_id"])
    op.create_index(
        "uq_calculation_request", "calculations", ["actor_id", "client_request_id"], unique=True
    )
    op.create_table(
        "calculation_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "calculation_id",
            sa.Uuid(),
            sa.ForeignKey("calculations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rule_id", sa.Uuid(), sa.ForeignKey("fee_rules.id"), nullable=False),
        sa.Column("rule_revision", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_rate", sa.Numeric(12, 2), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("rule_snapshot", JSONB(), nullable=False),
        sa.CheckConstraint(
            "quantity >= 0 AND unit_rate >= 0 AND amount >= 0",
            name="ck_calculation_item_nonnegative",
        ),
    )
    op.create_index("ix_calculation_items_calculation_id", "calculation_items", ["calculation_id"])


def downgrade():
    op.drop_table("calculation_items")
    op.drop_table("calculations")
    op.drop_table("fee_rules")
