import uuid
from datetime import datetime
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class User(Timestamps, Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('admin','employee')", name="ck_users_role"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(16), default="employee", server_default="employee")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    auth_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class EmployeeProfile(Timestamps, Base):
    __tablename__ = "employee_profiles"
    __table_args__ = (CheckConstraint("grade IN ('S1','S2')", name="ck_employee_grade"),)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    display_name: Mapped[str] = mapped_column(String(64))
    department: Mapped[str] = mapped_column(String(80))
    grade: Mapped[str] = mapped_column(String(8))
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class AuthTokenFamily(Timestamps, Base):
    __tablename__ = "auth_token_families"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    auth_version: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoke_reason: Mapped[str | None] = mapped_column(String(64))


class RefreshToken(Timestamps, Base):
    __tablename__ = "refresh_tokens"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    family_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("auth_token_families.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("refresh_tokens.id", ondelete="SET NULL")
    )


class KnowledgeBase(Timestamps, Base):
    __tablename__ = "knowledge_bases"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class KBGrant(Base):
    __tablename__ = "kb_grants"
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    kb_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), primary_key=True
    )
    granted_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Document(Timestamps, Base):
    __tablename__ = "documents"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    kb_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_bases.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_versions.id", use_alter=True, name="fk_documents_current_version")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentVersion(Timestamps, Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        Index("uq_document_version_number", "document_id", "version_no", unique=True),
        CheckConstraint(
            "status IN ('queued','processing','ready','failed')", name="ck_version_status"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    version_no: Mapped[int] = mapped_column(Integer)
    base_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document_versions.id"))
    original_name: Mapped[str] = mapped_column(String(200))
    blob_key: Mapped[str] = mapped_column(String(80), unique=True)
    sha256: Mapped[str] = mapped_column(String(64))
    byte_size: Mapped[int] = mapped_column(Integer)
    format: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    effective_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extracted_chars: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    embedding_space: Mapped[str] = mapped_column(String(64))


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        Index("uq_chunk_ordinal", "version_id", "ordinal", unique=True),
        Index("ix_chunks_search_vector", "search_vector", postgresql_using="gin"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    anchor: Mapped[dict] = mapped_column(JSONB)
    search_vector: Mapped[str] = mapped_column(TSVECTOR)
    embedding: Mapped[list[float]] = mapped_column(Vector(1024))


class IngestionJob(Timestamps, Base):
    __tablename__ = "ingestion_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','processing','completed','failed')", name="ck_job_status"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="queued")
    attempt_no: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(300))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UsageRecord(Base):
    __tablename__ = "usage_records"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    operation: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(24))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    estimated_cost: Mapped[Decimal] = mapped_column(Numeric(14, 8))
    provider_request_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), index=True
    )
    price_snapshot: Mapped[dict | None] = mapped_column(JSONB)


class Conversation(Timestamps, Base):
    __tablename__ = "conversations"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(80), default="新会话")
    trip_context: Mapped[dict] = mapped_column(JSONB, default=dict)
    expense_context: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    context_revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConversationScope(Base):
    __tablename__ = "conversation_scopes"
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True
    )
    kb_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_bases.id"), primary_key=True)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (CheckConstraint("role IN ('user','assistant')", name="ck_message_role"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32))
    structured_result: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MessageSource(Base):
    __tablename__ = "message_sources"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), index=True
    )
    chunk_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_chunks.id"))
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"))
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_versions.id"))
    quote_snapshot: Mapped[str] = mapped_column(Text)
    anchor_snapshot: Mapped[dict] = mapped_column(JSONB)
    is_cited: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    citation_index: Mapped[int | None] = mapped_column(Integer)


class AgentRun(Timestamps, Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("uq_run_request", "actor_id", "client_request_id", unique=True),
        Index(
            "uq_active_conversation_run",
            "conversation_id",
            unique=True,
            postgresql_where=text("status IN ('queued','running')"),
        ),
        CheckConstraint(
            "status IN ('queued','running','completed','needs_clarification','insufficient_evidence','partial','failed','cancelled','timeout')",
            name="ck_run_status",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    family_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("auth_token_families.id"))
    auth_version: Mapped[int] = mapped_column(Integer)
    client_request_id: Mapped[uuid.UUID] = mapped_column()
    request_hash: Mapped[str] = mapped_column(String(64))
    request_message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("messages.id"))
    response_message_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("messages.id"))
    status: Mapped[str] = mapped_column(String(32), default="queued")
    search_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    model_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    tool_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    stop_reason: Mapped[str | None] = mapped_column(String(64))
    profile_revision: Mapped[int] = mapped_column(Integer)
    context_revision: Mapped[int] = mapped_column(Integer)
    config_snapshot: Mapped[dict] = mapped_column(JSONB)


class RunEvent(Base):
    __tablename__ = "run_events"
    __table_args__ = (Index("uq_run_event_sequence", "run_id", "seq", unique=True),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(32))
    label: Mapped[str] = mapped_column(String(100))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    meta: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FeeRule(Timestamps, Base):
    __tablename__ = "fee_rules"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('lodging','daily','office','training','hospitality')", name="ck_fee_rule_kind"
        ),
        CheckConstraint("status IN ('draft','active','suspended')", name="ck_fee_rule_status"),
        CheckConstraint("unit_rate >= 0 AND unit_rate <= 999999.99", name="ck_fee_rule_rate"),
        CheckConstraint(
            "(kind='lodging' AND grade IS NOT NULL AND grade IN ('S1','S2')) OR (kind IN ('daily','office','training','hospitality') AND grade IS NULL)",
            name="ck_fee_rule_grade",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(16), index=True)
    grade: Mapped[str | None] = mapped_column(String(8))
    cities: Mapped[list] = mapped_column(JSONB)
    unit_rate: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    application_cap: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    reimbursement_ratio: Mapped[Decimal] = mapped_column(
        Numeric(6, 4), default=Decimal("1"), server_default="1"
    )
    max_quantity: Mapped[int] = mapped_column(Integer, default=100, server_default="100")
    source_chunk_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_chunks.id"))
    source_quote: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="draft", server_default="draft")
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    suspension_reason: Mapped[str | None] = mapped_column(String(64))


class Calculation(Base):
    __tablename__ = "calculations"
    __table_args__ = (
        Index("uq_calculation_request", "actor_id", "client_request_id", unique=True),
        CheckConstraint(
            "status IN ('complete','needs_information','blocked')", name="ck_calculation_status"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id"), index=True)
    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("messages.id"), unique=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_runs.id"))
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    client_request_id: Mapped[uuid.UUID | None] = mapped_column()
    request_hash: Mapped[str | None] = mapped_column(String(64))
    profile_snapshot: Mapped[dict] = mapped_column(JSONB)
    trip_snapshot: Mapped[dict] = mapped_column(JSONB)
    expense_snapshot: Mapped[dict | None] = mapped_column(JSONB)
    context_revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24))
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    missing_fields: Mapped[list] = mapped_column(JSONB)
    blocked_reasons: Mapped[list] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CalculationItem(Base):
    __tablename__ = "calculation_items"
    __table_args__ = (
        CheckConstraint(
            "quantity >= 0 AND unit_rate >= 0 AND amount >= 0",
            name="ck_calculation_item_nonnegative",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    calculation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("calculations.id", ondelete="CASCADE"), index=True
    )
    rule_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("fee_rules.id"))
    rule_revision: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_rate: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    rule_snapshot: Mapped[dict] = mapped_column(JSONB)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_entity", "entity_type", "entity_id"),)
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[uuid.UUID] = mapped_column()
    action: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    actor_label: Mapped[str] = mapped_column(String(100))
    before_snapshot: Mapped[dict | None] = mapped_column(JSONB)
    after_snapshot: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
