"""Slack events endpoint: signature verification, handshake, DM answer flow."""

import os

for _k in ("OKTA_ISSUER", "OKTA_CLIENT_ID", "OKTA_CLIENT_SECRET", "REPLIT_DEPLOYMENT"):
    os.environ.pop(_k, None)
os.environ["DATABASE_URL"] = "sqlite:////tmp/askweka_pytest.db"

import asyncio
import hashlib
import hmac
import json
import time

from fastapi.testclient import TestClient

import backend.slack_app as slack_module
import backend.slack_service as svc
from backend.db import SessionLocal
from backend.main import app
from backend.models import (
    ActivityLog,
    Conversation,
    SlackChannel,
    SlackDmNotice,
    SlackIntegration,
    SlackUserPref,
)

client = TestClient(app)

SECRET = "test-signing-secret"


def _signed_post(payload: dict, secret: str = SECRET, ts: str = None, app_id: str = "ATEST"):
    # Event callbacks carry the pinned app's ID by default; pass app_id=None
    # to omit it (testing the fail-closed path).
    if payload.get("type") == "event_callback" and "api_app_id" not in payload and app_id:
        payload = {**payload, "api_app_id": app_id}
    body = json.dumps(payload).encode()
    ts = ts or str(int(time.time()))
    sig = "v0=" + hmac.new(secret.encode(), f"v0:{ts}:{body.decode()}".encode(), hashlib.sha256).hexdigest()
    return client.post(
        "/api/slack/events",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Slack-Request-Timestamp": ts,
            "X-Slack-Signature": sig,
        },
    )


def _enable(monkeypatch, features: dict | None = None, app_id: str = "ATEST"):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_SIGNING_SECRET", SECRET)
    db = SessionLocal()
    try:
        row = db.get(SlackIntegration, 1) or SlackIntegration(id=1)
        row.enabled = True
        row.verified = True
        row.app_id = app_id
        row.bot_user_id = "UBOT"
        row.features = json.dumps(
            features if features is not None else {"dm_chat": True}
        )
        db.merge(row)
        db.query(SlackDmNotice).delete()
        db.query(SlackUserPref).delete()
        db.commit()
    finally:
        db.close()


def _fake_api(calls, email="jane@weka.io"):
    async def fake_slack_call(method, payload=None, **kw):
        calls.append((method, payload or {}))
        if method == "users.info":
            return {"ok": True, "user": {"profile": {"email": email}}}
        return {"ok": True}

    return fake_slack_call


def _patch_api(monkeypatch, calls, email="jane@weka.io"):
    fake = _fake_api(calls, email)
    monkeypatch.setattr(slack_module, "_slack_call", fake)
    monkeypatch.setattr(slack_module.svc, "slack_api", fake)


def _wait_for(calls, method, timeout=5.0):
    for _ in range(int(timeout * 10)):
        if any(m == method for m, _ in calls):
            return True
        time.sleep(0.1)
    return False


def test_disabled_without_secrets(monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
    assert client.post("/api/slack/events", content=b"{}").status_code == 404


def test_signature_required(monkeypatch):
    _enable(monkeypatch)
    # No headers
    assert client.post("/api/slack/events", content=b"{}").status_code == 401
    # Wrong secret
    assert _signed_post({"type": "url_verification"}, secret="wrong").status_code == 401
    # Stale timestamp (replay)
    assert (
        _signed_post({"type": "url_verification"}, ts=str(int(time.time()) - 3600)).status_code
        == 401
    )


def test_url_verification_challenge(monkeypatch):
    _enable(monkeypatch)
    r = _signed_post({"type": "url_verification", "challenge": "abc123"})
    assert r.status_code == 200 and r.json()["challenge"] == "abc123"


class FakeProvider:
    async def stream_chat(self, system, messages):
        yield "Restart your VPN client. Sources:\n"
        yield "- [VPN Guide](https://portal.weka.io/it/vpn)"


def test_dm_answered_and_logged(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(slack_module, "get_provider", None, raising=False)
    import backend.providers as providers_module

    monkeypatch.setattr(providers_module, "get_provider", lambda: FakeProvider())

    calls = []

    async def fake_slack_call(method, payload):
        calls.append((method, payload))
        if method == "users.info":
            return {"ok": True, "user": {"profile": {"email": "jane@weka.io"}}}
        return {"ok": True}

    monkeypatch.setattr(slack_module, "_slack_call", fake_slack_call)
    monkeypatch.setattr(slack_module.svc, "slack_api", fake_slack_call)

    event = {
        "type": "event_callback",
        "event_id": "Ev001",
        "event": {
            "type": "message",
            "channel_type": "im",
            "channel": "D123",
            "user": "U123",
            "text": "My VPN keeps dropping",
        },
    }
    r = _signed_post(event)
    assert r.status_code == 200

    # Background task runs on the test client's loop; give it a beat.
    for _ in range(50):
        if any(m == "chat.postMessage" for m, _ in calls):
            break
        time.sleep(0.1)

    posts = [p for m, p in calls if m == "chat.postMessage"]
    assert posts and "VPN" in posts[0]["text"] and "portal.weka.io" in posts[0]["text"]

    db = SessionLocal()
    try:
        m = db.get(SlackChannel, "D123")
        assert m is not None
        conv = db.get(Conversation, m.conversation_id)
        assert conv.username == "jane@weka.io"
        assert [x.role for x in conv.messages] == ["user", "assistant"]
        via_slack = (
            db.query(ActivityLog)
            .filter(ActivityLog.username == "jane@weka.io")
            .all()
        )
        assert any("via=slack" in log.detail for log in via_slack)
    finally:
        db.close()

    # Duplicate delivery (Slack retry) is ignored
    before = len(calls)
    assert _signed_post(event).status_code == 200
    time.sleep(0.3)
    assert len(calls) == before


def test_unverified_user_refused(monkeypatch):
    _enable(monkeypatch)

    calls = []

    async def fake_slack_call(method, payload):
        calls.append((method, payload))
        if method == "users.info":
            return {"ok": False, "error": "user_not_found"}
        return {"ok": True}

    monkeypatch.setattr(slack_module, "_slack_call", fake_slack_call)
    monkeypatch.setattr(slack_module.svc, "slack_api", fake_slack_call)

    event = {
        "type": "event_callback",
        "event_id": "Ev002",
        "event": {
            "type": "message",
            "channel_type": "im",
            "channel": "D999",
            "user": "U999",
            "text": "hello",
        },
    }
    assert _signed_post(event).status_code == 200
    for _ in range(50):
        if any(m == "chat.postMessage" for m, _ in calls):
            break
        time.sleep(0.1)
    posts = [p for m, p in calls if m == "chat.postMessage"]
    assert posts and "verified WEKA employees" in posts[0]["text"]


# ---------- App-ID pinning ----------


def test_app_id_mismatch_rejected(monkeypatch):
    _enable(monkeypatch, app_id="A111")
    calls = []
    _patch_api(monkeypatch, calls)
    r = _signed_post({
        "type": "event_callback",
        "api_app_id": "A999",
        "event_id": "EvApp1",
        "event": {
            "type": "message", "channel_type": "im", "channel": "D1",
            "user": "U1", "text": "hi",
        },
    })
    assert r.status_code == 403
    time.sleep(0.2)
    assert calls == []  # nothing processed
    db = SessionLocal()
    try:
        rej = db.query(ActivityLog).filter(ActivityLog.action == "slack.reject").all()
        assert any("app_id_mismatch" in x.detail for x in rej)
    finally:
        db.close()


def test_app_id_missing_rejected_when_pinned(monkeypatch):
    # Fail closed: with an app ID pinned, an event that omits api_app_id
    # cannot prove it came from the configured app and is rejected.
    _enable(monkeypatch, app_id="A111")
    calls = []
    _patch_api(monkeypatch, calls)
    r = _signed_post({
        "type": "event_callback",
        "event_id": "EvApp3",
        "event": {
            "type": "message", "channel_type": "im", "channel": "D1",
            "user": "U1", "text": "hi",
        },
    }, app_id=None)
    assert r.status_code == 403
    time.sleep(0.2)
    assert calls == []


def test_events_rejected_until_app_id_pinned(monkeypatch):
    # No pinned app ID (e.g. verified before this protection existed):
    # inbound event callbacks are refused until re-verification pins one.
    _enable(monkeypatch, app_id="")
    calls = []
    _patch_api(monkeypatch, calls)
    r = _signed_post({
        "type": "event_callback",
        "api_app_id": "AANY",
        "event_id": "EvApp4",
        "event": {
            "type": "message", "channel_type": "im", "channel": "D1",
            "user": "U1", "text": "hi",
        },
    })
    assert r.status_code == 403
    time.sleep(0.2)
    assert calls == []
    db = SessionLocal()
    try:
        rej = db.query(ActivityLog).filter(ActivityLog.action == "slack.reject").all()
        assert any("app_id_unpinned" in x.detail for x in rej)
    finally:
        db.close()


def test_verify_pins_app_id_or_fails(monkeypatch):
    # Successful verification persists the app ID; a failed app-ID lookup
    # fails the verification (fail closed).
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_SIGNING_SECRET", SECRET)
    import asyncio as _a

    async def api_with_app_id(method, payload=None, **kw):
        # Contract-realistic: auth.test supplies bot_id; bots.info maps it
        # to the app ID.
        if method == "auth.test":
            return {"ok": True, "team_id": "T1", "team": "WEKA",
                    "url": "https://w.slack.com/", "user_id": "UBOT",
                    "user": "askweka", "bot_id": "B123"}
        if method == "bots.info":
            assert (payload or {}).get("bot") == "B123"
            return {"ok": True, "bot": {"id": "B123", "app_id": "APINNED"}}
        return {"ok": False, "error": "unexpected_method"}

    monkeypatch.setattr(svc, "slack_api", api_with_app_id)
    db = SessionLocal()
    try:
        row = _a.run(svc.verify_connection(db))
        assert row.verified is True and row.app_id == "APINNED"
        db.rollback()
    finally:
        db.close()

    async def api_without_app_id(method, payload=None, **kw):
        if method == "auth.test":
            return {"ok": True, "team_id": "T1", "team": "WEKA",
                    "url": "https://w.slack.com/", "user_id": "UBOT", "user": "askweka"}
        return {"ok": False, "error": "method_not_supported"}

    monkeypatch.setattr(svc, "slack_api", api_without_app_id)
    db = SessionLocal()
    try:
        row = _a.run(svc.verify_connection(db))
        assert row.verified is False
        assert row.app_id == ""  # stale pin cleared on failed verification
        assert "app ID" in row.last_verify_error
        db.rollback()
    finally:
        db.close()


def test_events_rejected_when_unverified(monkeypatch):
    # Inbound admission requires a verified integration, not just the
    # master switch — stale state can't keep accepting events.
    _enable(monkeypatch, app_id="ATEST")
    db = SessionLocal()
    try:
        row = db.get(SlackIntegration, 1)
        row.verified = False
        db.commit()
    finally:
        db.close()
    r = _signed_post({
        "type": "event_callback",
        "event_id": "EvUv1",
        "event": {
            "type": "message", "channel_type": "im", "channel": "D1",
            "user": "U1", "text": "hi",
        },
    })
    assert r.status_code == 404


def test_app_id_match_accepted(monkeypatch):
    _enable(monkeypatch, app_id="A111")
    _patch_provider(monkeypatch)
    calls = []
    _patch_api(monkeypatch, calls)
    r = _signed_post({
        "type": "event_callback",
        "api_app_id": "A111",
        "event_id": "EvApp2",
        "event": {
            "type": "message", "channel_type": "im", "channel": "DAPP",
            "user": "U1", "text": "vpn?", "ts": "1.1",
        },
    })
    assert r.status_code == 200
    assert _wait_for(calls, "chat.postMessage")


def _patch_provider(monkeypatch):
    import backend.providers as providers_module

    monkeypatch.setattr(providers_module, "get_provider", lambda: FakeProvider())


# ---------- Independent feature gating ----------


def test_mentions_gated_independently_of_dm(monkeypatch):
    # dm_chat off, channel_mentions on: mention answered, DM not answered.
    _enable(monkeypatch, features={"dm_chat": False, "channel_mentions": True})
    _patch_provider(monkeypatch)
    calls = []
    _patch_api(monkeypatch, calls)
    r = _signed_post({
        "type": "event_callback",
        "event_id": "EvM1",
        "event": {
            "type": "app_mention", "channel": "C42", "user": "U1",
            "text": "<@UBOT> My VPN keeps dropping", "ts": "111.222",
        },
    })
    assert r.status_code == 200
    assert _wait_for(calls, "chat.postMessage")
    posts = [p for m, p in calls if m == "chat.postMessage"]
    answer_posts = [p for p in posts if "VPN" in p.get("text", "")]
    assert answer_posts and answer_posts[0]["thread_ts"] == "111.222"


def test_mention_ignored_when_feature_disabled(monkeypatch):
    _enable(monkeypatch, features={"dm_chat": True, "channel_mentions": False})
    calls = []
    _patch_api(monkeypatch, calls)
    r = _signed_post({
        "type": "event_callback",
        "event_id": "EvM2",
        "event": {
            "type": "app_mention", "channel": "C42", "user": "U1",
            "text": "<@UBOT> hello", "ts": "1.2",
        },
    })
    assert r.status_code == 200
    time.sleep(0.3)
    assert calls == []


# ---------- Threaded mentions: parsing, fresh context, persistence ----------


def test_mention_strips_bot_and_uses_fresh_context(monkeypatch):
    _enable(monkeypatch, features={"dm_chat": True, "channel_mentions": True})
    captured = {}

    class CapturingProvider:
        async def stream_chat(self, system, messages):
            captured["messages"] = messages
            yield "Try restarting the VPN client."

    import backend.providers as providers_module

    monkeypatch.setattr(providers_module, "get_provider", lambda: CapturingProvider())
    calls = []
    _patch_api(monkeypatch, calls)

    # Pre-existing DM history for the same employee must NOT leak into the
    # mention context.
    asyncio_run = __import__("asyncio").run
    db = SessionLocal()
    try:
        conv = Conversation(title="Slack chat", username="jane@weka.io")
        db.add(conv)
        db.flush()
        from backend.models import Message

        db.add(Message(conversation_id=conv.id, role="user", content="OLD DM QUESTION"))
        db.commit()
    finally:
        db.close()

    asyncio_run(
        slack_module._answer_mention(
            "C7", "U1", "<@UBOT> How do I reset Okta?  ", "55.66", "", "UBOT"
        )
    )
    assert captured["messages"] == [{"role": "user", "content": "How do I reset Okta?"}]

    posts = [p for m, p in calls if m == "chat.postMessage"]
    assert posts and posts[-1]["thread_ts"] == "55.66"

    db = SessionLocal()
    try:
        conv = (
            db.query(Conversation)
            .filter(Conversation.title == "Slack mention")
            .order_by(Conversation.created_at.desc())
            .first()
        )
        assert conv and conv.username == "jane@weka.io"
        assert [m.role for m in conv.messages] == ["user", "assistant"]
    finally:
        db.close()


def test_mention_preserves_other_user_mentions():
    # Only the configured bot's mention is stripped; references to other
    # people are legitimate question content.
    out = slack_module._strip_mentions("<@UBOT> can <@UOTHER> access the VPN?", "UBOT")
    assert out == "can <@UOTHER> access the VPN?"


def test_bot_only_mention_prompts_audits_and_records_identity(monkeypatch):
    _enable(monkeypatch, features={"channel_mentions": True})
    calls = []
    _patch_api(monkeypatch, calls, email="empty@weka.io")

    import asyncio as _a

    _a.run(slack_module._answer_mention("CE1", "U888", "<@UBOT>", "2.2", "", "UBOT"))
    posts = [p for m, p in calls if m == "chat.postMessage"]
    assert posts and "Mention me with a question" in posts[0]["text"]
    assert posts[0]["thread_ts"] == "2.2"

    db = SessionLocal()
    try:
        pref = db.get(SlackUserPref, "empty@weka.io")
        assert pref and pref.slack_user_id == "U888"  # first contact recorded
        assert svc.parse_prefs(pref) == {}  # no consent granted
        rows = (
            db.query(ActivityLog)
            .filter(ActivityLog.action == "slack.inbound",
                    ActivityLog.username == "empty@weka.io")
            .all()
        )
        assert any("type=mention_empty ok=true" in r.detail for r in rows)
    finally:
        db.close()


# ---------- Reactions ----------


def test_eyes_reaction_added_and_removed_and_errors_ignored(monkeypatch):
    _enable(monkeypatch, features={"dm_chat": True, "channel_mentions": True})
    _patch_provider(monkeypatch)
    calls = []

    async def fake(method, payload=None, **kw):
        calls.append((method, payload or {}))
        if method == "users.info":
            return {"ok": True, "user": {"profile": {"email": "jane@weka.io"}}}
        if method.startswith("reactions."):
            raise RuntimeError("reaction boom")  # must never break the answer
        return {"ok": True}

    monkeypatch.setattr(slack_module, "_slack_call", fake)
    monkeypatch.setattr(slack_module.svc, "slack_api", fake)

    import asyncio as _a

    _a.run(slack_module._answer_dm_inner("DR1", "U1", "vpn?", "9.9"))
    methods = [m for m, _ in calls]
    assert "reactions.add" in methods and "reactions.remove" in methods
    assert "chat.postMessage" in methods  # answer still delivered
    add = [p for m, p in calls if m == "reactions.add"][0]
    assert add["name"] == "eyes" and add["timestamp"] == "9.9"


# ---------- Disabled-DM daily notice ----------


def test_dm_disabled_notice_once_per_day(monkeypatch):
    _enable(monkeypatch, features={"dm_chat": False})
    calls = []
    _patch_api(monkeypatch, calls)

    event = {
        "type": "event_callback",
        "event_id": "EvD1",
        "event": {
            "type": "message", "channel_type": "im", "channel": "D77",
            "user": "U77", "text": "hello?", "ts": "1.0",
        },
    }
    assert _signed_post(event).status_code == 200
    assert _wait_for(calls, "chat.postMessage")
    posts = [p for m, p in calls if m == "chat.postMessage"]
    assert len(posts) == 1 and "turned off" in posts[0]["text"]

    # A second DM the same day stays silent.
    event2 = dict(event, event_id="EvD2")
    event2["event"] = dict(event["event"], text="anyone there?")
    assert _signed_post(event2).status_code == 200
    time.sleep(0.5)
    posts = [p for m, p in calls if m == "chat.postMessage"]
    assert len(posts) == 1

    db = SessionLocal()
    try:
        assert db.query(SlackDmNotice).filter(
            SlackDmNotice.slack_user_id == "U77"
        ).count() == 1
    finally:
        db.close()


# ---------- First-contact identity upsert ----------


def test_first_dm_upserts_identity_without_consent(monkeypatch):
    _enable(monkeypatch)
    _patch_provider(monkeypatch)
    calls = []
    _patch_api(monkeypatch, calls)

    import asyncio as _a

    _a.run(slack_module._answer_dm_inner("DI1", "U555", "vpn?", ""))
    db = SessionLocal()
    try:
        pref = db.get(SlackUserPref, "jane@weka.io")
        assert pref and pref.slack_user_id == "U555"
        assert pref.slack_email == "jane@weka.io"
        assert svc.parse_prefs(pref) == {}  # no consent granted
    finally:
        db.close()


# ---------- Formatting ----------


def test_markdown_converted_to_slack_and_button_attached():
    text = "# Title\n**Bold** and [VPN Guide](https://portal.weka.io/vpn)\n- item"
    out = svc.to_slack_mrkdwn(text)
    assert "*Title*" in out and "*Bold*" in out
    assert "<https://portal.weka.io/vpn|VPN Guide>" in out
    assert "• item" in out
    blocks = svc.answer_blocks(text)
    assert blocks[0]["type"] == "section"
    button = blocks[-1]["elements"][0]
    assert blocks[-1]["type"] == "actions" and button["type"] == "button"
    assert button["url"].startswith("https://")


# ---------- Metadata-only auditing ----------


def test_inbound_audit_has_no_question_text(monkeypatch):
    _enable(monkeypatch, features={"dm_chat": True, "channel_mentions": True})
    _patch_provider(monkeypatch)
    calls = []
    _patch_api(monkeypatch, calls)

    secret_q = "SUPERSECRETQUESTION-xyzzy"
    import asyncio as _a

    _a.run(slack_module._answer_dm_inner("DA1", "U1", secret_q, ""))
    _a.run(
        slack_module._answer_mention("CA1", "U1", f"<@UBOT> {secret_q}", "1.1", "", "UBOT")
    )

    db = SessionLocal()
    try:
        rows = db.query(ActivityLog).all()
        assert all(secret_q not in r.detail for r in rows)
        inbound = [
            r for r in rows
            if r.action == "slack.inbound" and r.username == "jane@weka.io"
        ]
        assert any("type=dm ok=true" in r.detail for r in inbound)
        assert any("type=mention ok=true" in r.detail for r in inbound)
    finally:
        db.close()


# ---------- Manifest ----------


def test_manifest_includes_mention_event_and_scopes(monkeypatch):
    _enable(monkeypatch, features={"dm_chat": True, "channel_mentions": True})
    db = SessionLocal()
    try:
        m = svc.generate_manifest(db)
    finally:
        db.close()
    scopes = m["oauth_config"]["scopes"]["bot"]
    events = m["settings"]["event_subscriptions"]["bot_events"]
    assert "app_mention" in events and "message.im" in events
    assert "app_mentions:read" in scopes and "reactions:write" in scopes

    _enable(monkeypatch, features={"dm_chat": True, "channel_mentions": False})
    db = SessionLocal()
    try:
        m2 = svc.generate_manifest(db)
    finally:
        db.close()
    assert "app_mention" not in m2["settings"]["event_subscriptions"]["bot_events"]
    assert "app_mentions:read" not in m2["oauth_config"]["scopes"]["bot"]
