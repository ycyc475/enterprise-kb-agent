"""Private conversations, bounded agent runs, evidence and chat usage."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003_m3"
down_revision = "0002_m2"
branch_labels = None
depends_on = None


def timestamps():
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    ]


def upgrade():
    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(80), nullable=False),
        sa.Column("trip_context", JSONB(), nullable=False),
        sa.Column("context_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        *timestamps(),
    )
    op.create_index("ix_conversations_owner_id", "conversations", ["owner_id"])
    op.create_table(
        "conversation_scopes",
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("kb_id", sa.Uuid(), sa.ForeignKey("knowledge_bases.id"), primary_key=True),
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("structured_result", JSONB()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("role IN ('user','assistant')", name="ck_message_role"),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])
    op.create_table(
        "message_sources",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "message_id",
            sa.Uuid(),
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_id", sa.Uuid(), sa.ForeignKey("document_chunks.id"), nullable=False),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("version_id", sa.Uuid(), sa.ForeignKey("document_versions.id"), nullable=False),
        sa.Column("quote_snapshot", sa.Text(), nullable=False),
        sa.Column("anchor_snapshot", JSONB(), nullable=False),
        sa.Column("is_cited", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("citation_index", sa.Integer()),
    )
    op.create_index("ix_message_sources_message_id", "message_sources", ["message_id"])
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("family_id", sa.Uuid(), sa.ForeignKey("auth_token_families.id"), nullable=False),
        sa.Column("auth_version", sa.Integer(), nullable=False),
        sa.Column("client_request_id", sa.Uuid(), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("request_message_id", sa.Uuid(), sa.ForeignKey("messages.id"), nullable=False),
        sa.Column("response_message_id", sa.Uuid(), sa.ForeignKey("messages.id")),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("search_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("model_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tool_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("stop_reason", sa.String(64)),
        sa.Column("profile_revision", sa.Integer(), nullable=False),
        sa.Column("context_revision", sa.Integer(), nullable=False),
        sa.Column("config_snapshot", JSONB(), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued','running','completed','needs_clarification','insufficient_evidence','partial','failed','cancelled','timeout')",
            name="ck_run_status",
        ),
        *timestamps(),
    )
    op.create_index("ix_agent_runs_conversation_id", "agent_runs", ["conversation_id"])
    op.create_index("uq_run_request", "agent_runs", ["actor_id", "client_request_id"], unique=True)
    op.create_index(
        "uq_active_conversation_run",
        "agent_runs",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued','running')"),
    )
    op.create_table(
        "run_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "run_id", sa.Uuid(), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_run_events_run_id", "run_events", ["run_id"])
    op.create_index("uq_run_event_sequence", "run_events", ["run_id", "seq"], unique=True)
    op.add_column("usage_records", sa.Column("output_tokens", sa.Integer()))
    op.add_column("usage_records", sa.Column("run_id", sa.Uuid()))
    op.add_column("usage_records", sa.Column("price_snapshot", JSONB()))
    op.create_foreign_key(
        "fk_usage_run", "usage_records", "agent_runs", ["run_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("ix_usage_records_run_id", "usage_records", ["run_id"])


def downgrade():
    op.drop_index("ix_usage_records_run_id", table_name="usage_records")
    op.drop_constraint("fk_usage_run", "usage_records", type_="foreignkey")
    for col in ["price_snapshot", "run_id", "output_tokens"]:
        op.drop_column("usage_records", col)
    for table in [
        "run_events",
        "agent_runs",
        "message_sources",
        "messages",
        "conversation_scopes",
        "conversations",
    ]:
        op.drop_table(table)
