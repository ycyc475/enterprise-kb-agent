"""Append-only management history with an explicit migration baseline."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0005_m5"
down_revision = "0004_m4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("actor_label", sa.String(100), nullable=False),
        sa.Column("before_snapshot", JSONB()),
        sa.Column("after_snapshot", JSONB()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_audit_entity", "audit_events", ["entity_type", "entity_id"])
    op.execute("""
        INSERT INTO audit_events (entity_type, entity_id, action, actor_label, after_snapshot)
        SELECT 'rule', id, 'rule.baseline', '迁移基线', jsonb_build_object(
            'name',name,'kind',kind,'grade',grade,'cities',cities,'unit_rate',unit_rate::text,
            'source_chunk_id',source_chunk_id,'source_quote',source_quote,'status',status,
            'revision',revision,'confirmed_by',confirmed_by,'confirmed_at',confirmed_at,
            'suspension_reason',suspension_reason)
        FROM fee_rules
    """)
    op.execute("""
        CREATE FUNCTION reject_audit_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'management history is append-only' USING ERRCODE = '23514';
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER audit_append_only BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation()
    """)


def downgrade():
    op.drop_table("audit_events")
    op.execute("DROP FUNCTION reject_audit_mutation()")
