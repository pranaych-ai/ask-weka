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
    # "" = unresolved | "solved" (fixed without a ticket) | "ticket" (escalated)
    resolution: Mapped[str] = mapped_column(String(20), default="")
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
    ai_verdict: Mapped[str] = mapped_column(String(10), default="")  # judge: "pass"|"fail"|""
    ai_reasoning: Mapped[str] = mapped_column(Text, default="")  # judge explanation / failure note
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
    # "" = full KB; otherwise comma-separated domain allowlist, e.g. "HR" or "HR,IT".
    allowed_domains: Mapped[str] = mapped_column(String(60), default="")
    # Reversible per-app off-switch (revoked is permanent).
    enabled: Mapped[bool] = mapped_column(default=True)
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
    """Per-request usage log for API keys: which app asked what, on whose
    behalf. Question text is truncated; no secrets, no answer content."""

    __tablename__ = "api_key_usage"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    key_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("api_keys.id", ondelete="CASCADE"), index=True
    )
    endpoint: Mapped[str] = mapped_column(String(120), default="")
    question: Mapped[str] = mapped_column(String(500), default="")
    on_behalf_of: Mapped[str] = mapped_column(String(120), default="")
    # True only when the end-user identity was proven via a verified
    # Okta-issued token; a plain caller-supplied "user" string stays False.
    user_verified: Mapped[bool] = mapped_column(default=False)
    status_code: Mapped[int] = mapped_column(default=200)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, index=True
    )


class SlackChannel(Base):
    """Maps a Slack DM channel to its Ask WEKA conversation so context
    persists across messages."""

    __tablename__ = "slack_channels"

    channel_id: Mapped[str] = mapped_column(String(30), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Ticket(Base):
    """Support ticket created only after the assistant could not solve the
    issue — and only with the employee's explicit approval of the draft."""

    __tablename__ = "tickets"
    # One ticket per conversation, enforced by the database so concurrent
    # approvals can never double-file.
    __table_args__ = (UniqueConstraint("conversation_id", name="uq_ticket_conversation"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(String(36), default="", index=True)
    username: Mapped[str] = mapped_column(String(120), index=True)
    domain: Mapped[str] = mapped_column(String(20), default="")  # "IT" | "HR" | ""
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")  # issue + what was already tried
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    # "open" | "in_progress" | "resolved" | "closed"
    jira_key: Mapped[str] = mapped_column(String(30), default="")  # e.g. ITSM-123
    jira_url: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class JiraAccount(Base):
    """Per-employee Jira connection (Atlassian OAuth, Okta-backed SSO):
    tickets are filed AS the employee, never by a shared bot account."""

    __tablename__ = "jira_accounts"

    username: Mapped[str] = mapped_column(String(120), primary_key=True)
    access_token: Mapped[str] = mapped_column(Text, default="")
    refresh_token: Mapped[str] = mapped_column(Text, default="")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    cloud_id: Mapped[str] = mapped_column(String(60), default="")
    site_url: Mapped[str] = mapped_column(String(200), default="")
    account_id: Mapped[str] = mapped_column(String(120), default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AppSetting(Base):
    """Small admin-editable key/value settings (e.g. Jira project routing)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class SlackIntegration(Base):
    """Singleton row of NON-SECRET Slack integration state.

    The bot token and signing secret live ONLY in Replit Secrets
    (SLACK_BOT_TOKEN / SLACK_SIGNING_SECRET) — never in this table, never
    accepted or returned by any API. This row stores admin configuration and
    verified workspace/bot metadata only.
    """

    __tablename__ = "slack_integration"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(default=True)  # admin master switch
    verified: Mapped[bool] = mapped_column(default=False)
    team_id: Mapped[str] = mapped_column(String(30), default="")
    team_name: Mapped[str] = mapped_column(String(200), default="")
    workspace_url: Mapped[str] = mapped_column(String(300), default="")
    bot_user_id: Mapped[str] = mapped_column(String(30), default="")
    bot_name: Mapped[str] = mapped_column(String(200), default="")
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_verify_error: Mapped[str] = mapped_column(String(300), default="")
    # JSON object: feature key -> bool (see slack_service.FEATURES)
    features: Mapped[str] = mapped_column(Text, default="")
    # Channel for admin-facing posts (digests, regressions, sync alerts)
    notify_channel: Mapped[str] = mapped_column(String(120), default="")
    digest_time: Mapped[str] = mapped_column(String(10), default="09:00")  # HH:MM
    digest_timezone: Mapped[str] = mapped_column(String(60), default="UTC")
    # Idempotency key of the last successful digest, e.g. "2026-08-30"
    digest_last_run: Mapped[str] = mapped_column(String(20), default="")
    # JSON list of slash command definitions [{command, description, usage_hint}]
    slash_commands: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class SlackUserPref(Base):
    """Per-employee Slack notification consent + resolved Slack identity.

    Employees opt in per feature; a delivery that is user-specific is only
    ever sent when the matching consent is true AND the Slack account was
    resolved from their WEKA email."""

    __tablename__ = "slack_user_prefs"

    username: Mapped[str] = mapped_column(String(120), primary_key=True)
    slack_user_id: Mapped[str] = mapped_column(String(30), default="")
    slack_email: Mapped[str] = mapped_column(String(200), default="")
    # JSON object: feature key -> bool (consent)
    prefs: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
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
