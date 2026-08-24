import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    username: Mapped[str] = mapped_column(String(120), default="anonymous", index=True)
    title: Mapped[str] = mapped_column(String(200), default="New conversation")
    domain: Mapped[str] = mapped_column(String(20), default="")  # "" | "HR" | "IT" scope
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class ActivityLog(Base):
    """Audit trail: who did what, when. Log events, never secrets/raw PII."""

    __tablename__ = "activity_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    username: Mapped[str] = mapped_column(String(120), index=True)
    action: Mapped[str] = mapped_column(String(60), index=True)  # e.g. "login", "feedback.submit"
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, index=True
    )


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    message_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("messages.id", ondelete="CASCADE"), index=True
    )
    question: Mapped[str] = mapped_column(Text)
    answer_summary: Mapped[str] = mapped_column(Text)
    feedback_text: Mapped[str] = mapped_column(Text, default="")
    thumbs: Mapped[str] = mapped_column(String(10), default="")  # "up" | "down" | ""
    logged_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    username: Mapped[str] = mapped_column(String(120), default="anonymous")
    cited_sources: Mapped[str] = mapped_column(Text, default="")
    domain: Mapped[str] = mapped_column(String(20), default="")  # "IT" | "HR" | ""
    review_status: Mapped[str] = mapped_column(
        String(20), default="open"
    )  # "open" | "reviewed" | "resolved"


class GoldenQuestion(Base):
    """Admin-maintained regression test questions for answer quality."""

    __tablename__ = "golden_questions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    question: Mapped[str] = mapped_column(Text)
    expected_topic: Mapped[str] = mapped_column(Text, default="")
    domain: Mapped[str] = mapped_column(String(20), default="")  # "IT" | "HR" | ""
    active: Mapped[bool] = mapped_column(default=True)
    created_by: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class GoldenRun(Base):
    __tablename__ = "golden_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    started_by: Mapped[str] = mapped_column(String(120), default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|done|error

    results: Mapped[list["GoldenResult"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="GoldenResult.created_at"
    )


class GoldenResult(Base):
    __tablename__ = "golden_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("golden_runs.id", ondelete="CASCADE"), index=True
    )
    question_id: Mapped[str] = mapped_column(String(36), default="")
    question: Mapped[str] = mapped_column(Text)
    expected_topic: Mapped[str] = mapped_column(Text, default="")
    answer: Mapped[str] = mapped_column(Text, default="")
    sources_count: Mapped[int] = mapped_column(default=0)
    auto_flagged: Mapped[bool] = mapped_column(default=False)  # e.g. no cited sources
    verdict: Mapped[str] = mapped_column(String(10), default="")  # "pass" | "fail" | ""
    reviewed_by: Mapped[str] = mapped_column(String(120), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    run: Mapped[GoldenRun] = relationship(back_populates="results")


class KBSection(Base):
    """Knowledge base section — the assistant prompt is assembled from active sections."""

    __tablename__ = "kb_sections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(300))
    domain: Mapped[str] = mapped_column(String(20), default="")  # "IT" | "HR" | ""
    body: Mapped[str] = mapped_column(Text, default="")
    position: Mapped[int] = mapped_column(default=0)
    active: Mapped[bool] = mapped_column(default=True)
    version: Mapped[int] = mapped_column(default=1)
    source_id: Mapped[str] = mapped_column(String(36), default="", index=True)
    updated_by: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class KBSectionVersion(Base):
    """Snapshot of a section before each edit — enables history and revert."""

    __tablename__ = "kb_section_versions"
    __table_args__ = (UniqueConstraint("section_id", "version", name="uq_kb_section_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    section_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("kb_sections.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column()
    title: Mapped[str] = mapped_column(String(300))
    domain: Mapped[str] = mapped_column(String(20), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(default=True)
    position: Mapped[int] = mapped_column(default=0)
    edited_by: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ApiKey(Base):
    """Admin-issued credentials for programmatic access.

    kind="api": long-lived API key for POST /api/v1/ask (no expiry, revocable).
    kind="mcp": scoped bearer token for the MCP endpoint (expires).
    Only a SHA-256 hash is stored; the plaintext is shown once at creation.
    """

    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String(10), default="api", index=True)  # api|mcp
    name: Mapped[str] = mapped_column(String(200))
    owner: Mapped[str] = mapped_column(String(120), default="")
    scope: Mapped[str] = mapped_column(String(30), default="ask")  # ask-only for now
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    prefix: Mapped[str] = mapped_column(String(12), default="")  # display hint only
    rate_limit_per_min: Mapped[int] = mapped_column(default=30)
    revoked: Mapped[bool] = mapped_column(default=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    usage_count: Mapped[int] = mapped_column(default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ApiKeyUsage(Base):
    """Per-request usage log for API keys (IDs and status only, no content)."""

    __tablename__ = "api_key_usage"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    key_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("api_keys.id", ondelete="CASCADE"), index=True
    )
    endpoint: Mapped[str] = mapped_column(String(120), default="")
    status_code: Mapped[int] = mapped_column(default=200)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, index=True
    )


class KnowledgeSource(Base):
    """External content source registry with sync status."""

    __tablename__ = "knowledge_sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(30), default="upload")  # upload|notion|gdrive|manual
    url: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    domain: Mapped[str] = mapped_column(String(20), default="")  # default domain for synced sections
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_status: Mapped[str] = mapped_column(String(20), default="never")  # never|ok|error
    last_sync_detail: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
