"""Documents, durable ingestion, hybrid retrieval and embedding usage."""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR

revision = "0002_m2"
down_revision = "0001_m1"
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
        "documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("kb_id", sa.Uuid(), sa.ForeignKey("knowledge_bases.id"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("current_version_id", sa.Uuid()),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        *timestamps(),
    )
    op.create_index("ix_documents_kb_id", "documents", ["kb_id"])
    op.create_table(
        "document_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("base_version_id", sa.Uuid(), sa.ForeignKey("document_versions.id")),
        sa.Column("original_name", sa.String(200), nullable=False),
        sa.Column("blob_key", sa.String(80), unique=True, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("format", sa.String(8), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_until", sa.DateTime(timezone=True)),
        sa.Column("extracted_chars", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("embedding_space", sa.String(64), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued','processing','ready','failed')", name="ck_version_status"
        ),
        *timestamps(),
    )
    op.create_index("ix_document_versions_document_id", "document_versions", ["document_id"])
    op.create_index(
        "uq_document_version_number",
        "document_versions",
        ["document_id", "version_no"],
        unique=True,
    )
    op.create_foreign_key(
        "fk_documents_current_version",
        "documents",
        "document_versions",
        ["current_version_id"],
        ["id"],
    )
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "version_id",
            sa.Uuid(),
            sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("anchor", JSONB(), nullable=False),
        sa.Column("search_vector", TSVECTOR(), nullable=False),
        sa.Column("embedding", Vector(1024), nullable=False),
    )
    op.create_index("ix_document_chunks_version_id", "document_chunks", ["version_id"])
    op.create_index("uq_chunk_ordinal", "document_chunks", ["version_id", "ordinal"], unique=True)
    op.create_index(
        "ix_chunks_search_vector", "document_chunks", ["search_vector"], postgresql_using="gin"
    )
    op.create_table(
        "ingestion_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "version_id",
            sa.Uuid(),
            sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.String(300)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('queued','processing','completed','failed')", name="ck_job_status"
        ),
        *timestamps(),
    )
    op.create_index("ix_ingestion_jobs_version_id", "ingestion_jobs", ["version_id"])
    op.create_table(
        "usage_records",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("model", sa.String(80), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("estimated_cost", sa.Numeric(14, 8), nullable=False),
        sa.Column("provider_request_id", sa.String(128)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade():
    op.drop_table("usage_records")
    op.drop_table("ingestion_jobs")
    op.drop_table("document_chunks")
    op.drop_constraint("fk_documents_current_version", "documents", type_="foreignkey")
    op.drop_table("document_versions")
    op.drop_table("documents")
