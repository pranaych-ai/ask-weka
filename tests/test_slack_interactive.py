"""Slack interactive features: interactivity endpoint admission, signed
action references, feedback/post/ticket actions, consented summaries,
App Home, link unfurls, manifest, and command configuration."""

import os

for _k in ("OKTA_ISSUER", "OKTA_CLIENT_ID", "OKTA_CLIENT_SECRET", "REPLIT_DEPLOYMENT"):
    os.environ.pop(_k, None)
os.environ["DATABASE_URL"] = "sqlite:////tmp/askweka_pytest.db"

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from fastapi.testclient import TestClient

import backend.slack_app as slack_module
import backend.slack_service as svc
from backend.db import SessionLocal
from backend.main import app
from backend.models import (
    Conversation,
    Feedback,
    Message,
    SlackEventDedup,
    SlackIntegration,
    SlackUserPref,
    Ticket,
)

client = TestClient(app)

SECRET = "test-signing-secret"
EMAIL = "jane@weka.io"

ALL_INTERACTIVE = {
    "dm_chat": True,
    "channel_mentions": True,
    "answer_actions": True,
    "thread_summaries": True,
    "app_home": True,
    "link_unfurls": True,
}


def _enable(monkeypatch, features=None, app_id="ATEST"):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_SIGNING_SECRET", SECRET)
    db = SessionLocal()
    try:
        row = db.get(SlackIntegration, 1) or SlackIntegration(id=1)
        row.enabled = True
        row.verified = True
        row.app_id = app_id
        row.bot_user_id = "UBOT"
        row.features = json.dumps(features if features is not None else ALL_INTERACTIVE)
        db.merge(row)
        db.query(SlackEventDedup).delete()
        db.query(SlackUserPref).delete()
        db.commit()
    finally:
        db.close()


def _sign(body: bytes, secret=SECRET, ts=None):
    ts = ts or str(int(time.time()))
    sig = "v0=" + hmac.new(
        secret.encode(), f"v0:{ts}:{body.decode()}".encode(), hashlib.sha256
    ).hexdigest()
    return ts, sig


def _post_interaction(payload: dict, secret=SECRET, ts=None, raw_body: bytes = None):
    body = raw_body if raw_body is not None else urlencode({"payload": json.dumps(payload)}).encode()
    ts, sig = _sign(body, secret, ts)
    return client.post(
        "/api/slack/interactions",
        content=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Slack-Request-Timestamp": ts,
            "X-Slack-Signature": sig,
        },
    )


def _signed_event(payload: dict, app_id="ATEST"):
    if "api_app_id" not in payload and app_id:
        payload = {**payload, "api_app_id": app_id}
    body = json.dumps(payload).encode()
    ts, sig = _sign(body)
    return client.post(
        "/api/slack/events",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Slack-Request-Timestamp": ts,
            "X-Slack-Signature": sig,
        },
    )


def _fake_api(calls, email=EMAIL, responses=None):
    async def fake(method, payload=None, **kw):
        calls.append((method, payload or {}))
        if responses and method in responses:
            return responses[method]
        if method == "users.info":
            return {"ok": True, "user": {"profile": {"email": email}}}
        if method == "views.open":
            return {"ok": True, "view": {"id": "V123"}}
        if method == "conversations.open":
            return {"ok": True, "channel": {"id": "D999"}}
        return {"ok": True}

    return fake


def _patch_api(monkeypatch, calls, email=EMAIL, responses=None):
    fake = _fake_api(calls, email, responses)
    monkeypatch.setattr(slack_module, "_slack_call", fake)
    monkeypatch.setattr(slack_module.svc, "slack_api", fake)


def _wait(pred, timeout=5.0):
    for _ in range(int(timeout * 20)):
        if pred():
            return True
        time.sleep(0.05)
    return False


def _mk_conversation(username=EMAIL, resolution=""):
    db = SessionLocal()
    try:
        conv = Conversation(title="VPN trouble", username=username, resolution=resolution)
        db.add(conv)
        db.flush()
        conv.messages.append(Message(role="user", content="My VPN drops"))
        conv.messages.append(Message(role="assistant", content="Restart the VPN client."))
        db.commit()
        return conv.id, conv.messages[-1].id
    finally:
        db.close()


def _ephemeral_patch(monkeypatch):
    sent = []

    async def fake_eph(url, text):
        sent.append(text)

    monkeypatch.setattr(slack_module, "_respond_ephemeral", fake_eph)
    return sent


# ---------- Endpoint admission ----------


def test_interactions_signature_and_replay(monkeypatch):
    _enable(monkeypatch)
    assert client.post("/api/slack/interactions", content=b"payload=%7B%7D").status_code == 401
    assert _post_interaction({"type": "block_actions"}, secret="wrong").status_code == 401
    assert (
        _post_interaction({"type": "block_actions"}, ts=str(int(time.time()) - 3600)).status_code
        == 401
    )


def test_interactions_app_id_pinned(monkeypatch):
    _enable(monkeypatch)
    assert _post_interaction({"type": "block_actions", "api_app_id": "AEVIL"}).status_code == 403
    assert _post_interaction({"type": "block_actions"}).status_code == 403  # missing
    # Unpinned config fails closed too.
    _enable(monkeypatch, app_id="")
    assert _post_interaction({"type": "block_actions", "api_app_id": "ATEST"}).status_code == 403


def test_interactions_bad_payload(monkeypatch):
    _enable(monkeypatch)
    assert _post_interaction({}, raw_body=b"payload=notjson").status_code == 400


def test_interactions_disabled_without_creds(monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
    assert client.post("/api/slack/interactions", content=b"x").status_code == 404


# ---------- Signed action references ----------


def test_sign_action_roundtrip_and_tamper():
    v = svc.sign_action("m", "msg123")
    assert svc.parse_action(v) == ("m", "msg123")
    assert svc.parse_action(v.replace("msg123", "msg999")) is None
    assert svc.parse_action("m:msg123:deadbeef") is None
    assert svc.parse_action("") is None
    assert svc.parse_action("m:onlyone") is None


def _block_action(action_id, value, app_id="ATEST"):
    return {
        "type": "block_actions",
        "api_app_id": app_id,
        "user": {"id": "U123"},
        "trigger_id": "trig1",
        "response_url": "https://hooks.slack.com/actions/T/1/xyz",
        "actions": [{"action_id": action_id, "value": value}],
    }


# ---------- Feedback actions ----------


def test_feedback_written_anonymously_one_per_user(monkeypatch):
    _enable(monkeypatch)
    calls = []
    _patch_api(monkeypatch, calls)
    sent = _ephemeral_patch(monkeypatch)
    conv_id, msg_id = _mk_conversation()

    r = _post_interaction(_block_action("fb_up", svc.sign_action("m", msg_id)))
    assert r.status_code == 200
    assert _wait(lambda: sent)

    db = SessionLocal()
    try:
        rows = db.query(Feedback).filter(Feedback.message_id == msg_id).all()
        assert len(rows) == 1
        fb = rows[0]
        assert fb.thumbs == "up"
        # Anonymous at rest: a keyed pseudonym, never the email.
        assert fb.username != EMAIL and EMAIL not in fb.username and len(fb.username) == 64
        assert fb.question == "My VPN drops"
    finally:
        db.close()

    # Second rating from the same employee replaces, never duplicates.
    sent.clear()
    _post_interaction(_block_action("fb_down", svc.sign_action("m", msg_id)))
    assert _wait(lambda: sent)
    db = SessionLocal()
    try:
        rows = db.query(Feedback).filter(Feedback.message_id == msg_id).all()
        assert len(rows) == 1 and rows[0].thumbs == "down"
    finally:
        db.close()


def test_feedback_denied_for_non_owner(monkeypatch):
    _enable(monkeypatch)
    calls = []
    _patch_api(monkeypatch, calls, email="mallory@weka.io")
    sent = _ephemeral_patch(monkeypatch)
    conv_id, msg_id = _mk_conversation(username=EMAIL)

    _post_interaction(_block_action("fb_up", svc.sign_action("m", msg_id)))
    assert _wait(lambda: sent) and "own" in sent[0]
    db = SessionLocal()
    try:
        assert db.query(Feedback).filter(Feedback.message_id == msg_id).count() == 0
    finally:
        db.close()


def test_action_with_tampered_value_ignored(monkeypatch):
    _enable(monkeypatch)
    calls = []
    _patch_api(monkeypatch, calls)
    sent = _ephemeral_patch(monkeypatch)
    conv_id, msg_id = _mk_conversation()

    _post_interaction(_block_action("fb_up", f"m:{msg_id}:0000000000000000"))
    _post_interaction(_block_action("fb_up", "raw-text-not-signed"))
    time.sleep(0.6)
    db = SessionLocal()
    try:
        assert db.query(Feedback).filter(Feedback.message_id == msg_id).count() == 0
    finally:
        db.close()


def test_actions_gated_on_feature_flag(monkeypatch):
    _enable(monkeypatch, features={**ALL_INTERACTIVE, "answer_actions": False})
    calls = []
    _patch_api(monkeypatch, calls)
    sent = _ephemeral_patch(monkeypatch)
    conv_id, msg_id = _mk_conversation()

    _post_interaction(_block_action("fb_up", svc.sign_action("m", msg_id)))
    assert _wait(lambda: sent) and "disabled" in sent[0]
    db = SessionLocal()
    try:
        assert db.query(Feedback).filter(Feedback.message_id == msg_id).count() == 0
    finally:
        db.close()


# ---------- Post to channel ----------


def test_post_to_channel_uses_server_answer(monkeypatch):
    _enable(monkeypatch)
    calls = []
    _patch_api(monkeypatch, calls)
    _ephemeral_patch(monkeypatch)
    posted = []

    async def fake_post(url, text, blocks):
        posted.append((url, text))
        return True

    monkeypatch.setattr(slack_module, "_post_in_channel_via_response_url", fake_post)
    conv_id, msg_id = _mk_conversation()

    _post_interaction(_block_action("post_channel", svc.sign_action("m", msg_id)))
    assert _wait(lambda: posted)
    # Content is the STORED answer, loaded server-side.
    assert posted[0][1] == "Restart the VPN client."


def test_post_to_channel_denied_for_non_owner(monkeypatch):
    _enable(monkeypatch)
    calls = []
    _patch_api(monkeypatch, calls, email="mallory@weka.io")
    sent = _ephemeral_patch(monkeypatch)
    posted = []

    async def fake_post(url, text, blocks):
        posted.append(text)
        return True

    monkeypatch.setattr(slack_module, "_post_in_channel_via_response_url", fake_post)
    conv_id, msg_id = _mk_conversation(username=EMAIL)

    _post_interaction(_block_action("post_channel", svc.sign_action("m", msg_id)))
    assert _wait(lambda: sent) and not posted


# ---------- Create ticket ----------


def test_create_ticket_guides_to_connect_jira(monkeypatch):
    _enable(monkeypatch)
    calls = []
    _patch_api(monkeypatch, calls)
    sent = _ephemeral_patch(monkeypatch)
    import backend.jira_oauth as jira_module

    monkeypatch.setattr(jira_module, "jira_enabled", lambda: True)
    conv_id, msg_id = _mk_conversation()

    _post_interaction(_block_action("create_ticket", svc.sign_action("c", conv_id)))
    assert _wait(lambda: sent) and "Connect Jira" in sent[0]
    assert not any(m == "views.open" for m, _ in calls)


def test_create_ticket_modal_and_submit_files_locally(monkeypatch):
    """Without Jira configured, approval files a local ticket via the shared
    solve-first core; the modal is opened first for review/edit."""
    _enable(monkeypatch)
    calls = []
    _patch_api(monkeypatch, calls)
    _ephemeral_patch(monkeypatch)
    import backend.jira_oauth as jira_module

    monkeypatch.setattr(jira_module, "jira_enabled", lambda: False)

    # AI draft is best-effort; keep it deterministic.
    async def fake_draft(db, conv, username):
        return {"title": "VPN keeps dropping", "body": "Steps tried: restart.", "domain": ""}

    import backend.tickets as tickets_module

    monkeypatch.setattr(tickets_module, "draft_ticket_content", fake_draft)
    conv_id, msg_id = _mk_conversation()

    r = _post_interaction(_block_action("create_ticket", svc.sign_action("c", conv_id)))
    assert r.status_code == 200
    assert _wait(lambda: any(m == "views.open" for m, _ in calls))
    assert _wait(lambda: any(m == "views.update" for m, _ in calls))

    # Explicit approval: submit the (edited) modal.
    view = {
        "callback_id": "askweka_ticket_modal",
        "private_metadata": svc.sign_action("c", conv_id),
        "state": {
            "values": {
                "title": {"v": {"value": "VPN keeps dropping (edited)"}},
                "body": {"v": {"value": "My edited details."}},
            }
        },
    }
    r = _post_interaction(
        {
            "type": "view_submission",
            "api_app_id": "ATEST",
            "user": {"id": "U123"},
            "view": view,
        }
    )
    assert r.status_code == 200 and r.json()["response_action"] == "clear"

    def ticket_exists():
        db = SessionLocal()
        try:
            return db.query(Ticket).filter(Ticket.conversation_id == conv_id).count() == 1
        finally:
            db.close()

    assert _wait(ticket_exists)
    db = SessionLocal()
    try:
        t = db.query(Ticket).filter(Ticket.conversation_id == conv_id).one()
        assert t.title == "VPN keeps dropping (edited)"
        assert t.username == EMAIL and not t.jira_key
        assert db.get(Conversation, conv_id).resolution == "ticket"
    finally:
        db.close()
    # Outcome is DMed.
    assert _wait(lambda: any(m == "conversations.open" for m, _ in calls))


def test_ticket_submit_rejects_tampered_reference(monkeypatch):
    _enable(monkeypatch)
    calls = []
    _patch_api(monkeypatch, calls)
    conv_id, _ = _mk_conversation()
    r = _post_interaction(
        {
            "type": "view_submission",
            "api_app_id": "ATEST",
            "user": {"id": "U123"},
            "view": {
                "callback_id": "askweka_ticket_modal",
                "private_metadata": f"c:{conv_id}:badmac00badmac00",
                "state": {"values": {"title": {"v": {"value": "t"}}, "body": {"v": {"value": "b"}}}},
            },
        }
    )
    assert r.status_code == 200
    time.sleep(0.5)
    db = SessionLocal()
    try:
        assert db.query(Ticket).filter(Ticket.conversation_id == conv_id).count() == 0
    finally:
        db.close()


def test_create_ticket_denied_for_non_owner_conversation(monkeypatch):
    _enable(monkeypatch)
    calls = []
    _patch_api(monkeypatch, calls, email="mallory@weka.io")
    sent = _ephemeral_patch(monkeypatch)
    conv_id, _ = _mk_conversation(username=EMAIL)

    _post_interaction(_block_action("create_ticket", svc.sign_action("c", conv_id)))
    assert _wait(lambda: sent) and "own" in sent[0]
    assert not any(m == "views.open" for m, _ in calls)


# ---------- Consented summaries ----------


def _mention_event(text, thread_ts=""):
    ev = {
        "type": "event_callback",
        "event_id": f"Ev{time.time_ns()}",
        "event": {
            "type": "app_mention",
            "channel": "C555",
            "user": "U123",
            "text": text,
            "ts": "111.222",
        },
    }
    if thread_ts:
        ev["event"]["thread_ts"] = thread_ts
    return ev


def _optin(email=EMAIL):
    db = SessionLocal()
    try:
        db.merge(
            SlackUserPref(
                username=email,
                slack_user_id="U123",
                prefs=json.dumps({"thread_summaries": True}),
            )
        )
        db.commit()
    finally:
        db.close()


def test_summary_requires_admin_flag(monkeypatch):
    _enable(monkeypatch, features={**ALL_INTERACTIVE, "thread_summaries": False})
    calls = []
    _patch_api(monkeypatch, calls)
    _optin()
    _signed_event(_mention_event("<@UBOT> catch me up"))
    assert _wait(lambda: any(m == "chat.postMessage" for m, _ in calls))
    posts = [p for m, p in calls if m == "chat.postMessage"]
    assert "not enabled" in posts[0]["text"]
    assert not any(m in ("conversations.replies", "conversations.history") for m, _ in calls)


def test_summary_requires_employee_optin(monkeypatch):
    _enable(monkeypatch)
    calls = []
    _patch_api(monkeypatch, calls)
    _signed_event(_mention_event("<@UBOT> summarize this thread", thread_ts="100.1"))
    assert _wait(lambda: any(m == "chat.postMessage" for m, _ in calls))
    posts = [p for m, p in calls if m == "chat.postMessage"]
    assert "opt-in" in posts[0]["text"]
    assert not any(m in ("conversations.replies", "conversations.history") for m, _ in calls)


def test_summary_bounded_fetch_and_no_persistence(monkeypatch):
    _enable(monkeypatch)
    calls = []
    _patch_api(
        monkeypatch,
        calls,
        responses={
            "conversations.replies": {
                "ok": True,
                "messages": [
                    {"type": "message", "user": "U1", "text": "We chose option B", "ts": "1"},
                    {"type": "message", "user": "U2", "text": "Alice will send the doc", "ts": "2"},
                ],
            }
        },
    )
    _optin()

    class FakeProvider:
        async def stream_chat(self, system, messages):
            yield "**Key points**\n• Option B chosen\n**Decisions**\n• Option B\n**Action items**\n• Alice: send the doc"

    import backend.providers as providers_module

    monkeypatch.setattr(providers_module, "get_provider", lambda: FakeProvider())

    db = SessionLocal()
    try:
        conv_before = db.query(Conversation).count()
        msg_before = db.query(Message).count()
    finally:
        db.close()

    _signed_event(_mention_event("<@UBOT> summarize", thread_ts="100.1"))
    assert _wait(
        lambda: any(m == "chat.postMessage" and "Key points" in p.get("text", "") for m, p in calls)
    )
    # Bounded to the current thread only.
    replies = [p for m, p in calls if m == "conversations.replies"]
    assert replies and replies[0]["ts"] == "100.1" and int(replies[0]["limit"]) == 200
    assert not any(m == "conversations.history" for m, _ in calls)
    # Threaded reply.
    post = next(p for m, p in calls if m == "chat.postMessage" and "Key points" in p.get("text", ""))
    assert post.get("thread_ts") == "100.1"
    # Nothing persisted: no new conversations or messages.
    db = SessionLocal()
    try:
        assert db.query(Conversation).count() == conv_before
        assert db.query(Message).count() == msg_before
    finally:
        db.close()


def test_summary_works_without_channel_mentions(monkeypatch):
    """Regression: summaries are an independent capability — with
    channel_mentions disabled and thread_summaries enabled, a consented
    catch-up mention still fetches, summarizes, and replies in-thread."""
    _enable(monkeypatch, features={**ALL_INTERACTIVE, "channel_mentions": False})
    calls = []
    _patch_api(
        monkeypatch,
        calls,
        responses={
            "conversations.replies": {
                "ok": True,
                "messages": [{"type": "message", "user": "U1", "text": "Ship it Friday", "ts": "1"}],
            }
        },
    )
    _optin()

    class FakeProvider:
        async def stream_chat(self, system, messages):
            yield "**Key points**\n• Ship Friday\n**Decisions**\n• None\n**Action items**\n• None"

    import backend.providers as providers_module

    monkeypatch.setattr(providers_module, "get_provider", lambda: FakeProvider())

    _signed_event(_mention_event("<@UBOT> tl;dr", thread_ts="200.2"))
    assert _wait(
        lambda: any(m == "chat.postMessage" and "Key points" in p.get("text", "") for m, p in calls)
    )
    post = next(p for m, p in calls if m == "chat.postMessage" and "Key points" in p.get("text", ""))
    assert post.get("thread_ts") == "200.2"
    # Non-summary questions are NOT answered in this mode.
    calls.clear()
    _signed_event(_mention_event("<@UBOT> how do I request a laptop?"))
    time.sleep(0.4)
    assert not any(m == "chat.postMessage" for m, _ in calls)


def test_manifest_reactions_scope_for_summaries_only(monkeypatch):
    _enable(
        monkeypatch,
        features={"dm_chat": False, "channel_mentions": False, "thread_summaries": True},
    )
    db = SessionLocal()
    try:
        m = svc.generate_manifest(db)
    finally:
        db.close()
    assert "reactions:write" in m["oauth_config"]["scopes"]["bot"]


def test_summary_channel_window_24h(monkeypatch):
    _enable(monkeypatch)
    calls = []
    _patch_api(
        monkeypatch,
        calls,
        responses={"conversations.history": {"ok": True, "messages": []}},
    )
    _optin()
    _signed_event(_mention_event("<@UBOT> catch me up"))
    assert _wait(lambda: any(m == "conversations.history" for m, _ in calls))
    hist = next(p for m, p in calls if m == "conversations.history")
    assert int(hist["limit"]) == 200
    assert float(hist["oldest"]) >= time.time() - 24 * 3600 - 60


# ---------- App Home ----------


def test_app_home_published_with_optin_status(monkeypatch):
    _enable(monkeypatch)
    calls = []
    _patch_api(monkeypatch, calls)
    _optin()
    r = _signed_event(
        {
            "type": "event_callback",
            "event_id": f"Ev{time.time_ns()}",
            "event": {"type": "app_home_opened", "user": "U123", "tab": "home"},
        }
    )
    assert r.status_code == 200
    assert _wait(lambda: any(m == "views.publish" for m, _ in calls))
    pub = next(p for m, p in calls if m == "views.publish")
    blob = json.dumps(pub)
    assert pub["user_id"] == "U123"
    assert "Ask WEKA" in blob and "opted in" in blob
    # No conversation content in the Home tab.
    assert "VPN" not in blob


def test_app_home_gated_on_feature(monkeypatch):
    _enable(monkeypatch, features={**ALL_INTERACTIVE, "app_home": False})
    calls = []
    _patch_api(monkeypatch, calls)
    _signed_event(
        {
            "type": "event_callback",
            "event_id": f"Ev{time.time_ns()}",
            "event": {"type": "app_home_opened", "user": "U123", "tab": "home"},
        }
    )
    time.sleep(0.4)
    assert not any(m == "views.publish" for m, _ in calls)


# ---------- Link unfurls ----------


def _link_event(url):
    return {
        "type": "event_callback",
        "event_id": f"Ev{time.time_ns()}",
        "event": {
            "type": "link_shared",
            "channel": "C1",
            "message_ts": "5.5",
            "links": [{"url": url, "domain": "x"}],
        },
    }


def test_unfurl_only_own_domain_metadata_only(monkeypatch):
    _enable(monkeypatch)
    calls = []
    _patch_api(monkeypatch, calls)
    monkeypatch.setattr(svc, "public_base_url", lambda: "https://askweka.example.com")

    _signed_event(_link_event("https://askweka.example.com/c/abc123"))
    assert _wait(lambda: any(m == "chat.unfurl" for m, _ in calls))
    unf = next(p for m, p in calls if m == "chat.unfurl")
    payload = json.dumps(unf)
    # Metadata only — never conversation content or the conversation id echo
    # beyond the URL key Slack requires.
    assert "internal HR/IT assistant" in payload
    assert "VPN" not in payload

    # Foreign domains are never unfurled.
    calls.clear()
    _signed_event(_link_event("https://evil.example.com/askweka"))
    time.sleep(0.4)
    assert not any(m == "chat.unfurl" for m, _ in calls)


def test_unfurl_gated_on_feature(monkeypatch):
    _enable(monkeypatch, features={**ALL_INTERACTIVE, "link_unfurls": False})
    calls = []
    _patch_api(monkeypatch, calls)
    monkeypatch.setattr(svc, "public_base_url", lambda: "https://askweka.example.com")
    _signed_event(_link_event("https://askweka.example.com/x"))
    time.sleep(0.4)
    assert not any(m == "chat.unfurl" for m, _ in calls)


# ---------- Manifest ----------


def test_manifest_includes_interactive_capabilities(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(svc, "public_base_url", lambda: "https://askweka.example.com")
    db = SessionLocal()
    try:
        m = svc.generate_manifest(db)
    finally:
        db.close()
    scopes = m["oauth_config"]["scopes"]["bot"]
    events = m["settings"]["event_subscriptions"]["bot_events"]
    assert m["settings"]["interactivity"]["request_url"].endswith("/api/slack/interactions")
    assert "app_mention" in events and "app_home_opened" in events and "link_shared" in events
    assert {"channels:history", "groups:history", "links:read", "links:write"} <= set(scopes)
    assert m["features"]["app_home"]["home_tab_enabled"] is True
    assert m["settings"]["event_subscriptions"]["unfurl_domains"] == ["askweka.example.com"]


def test_manifest_minimal_when_features_off(monkeypatch):
    _enable(
        monkeypatch,
        features={
            "dm_chat": True,
            "channel_mentions": False,
            "thread_summaries": False,
            "app_home": False,
            "link_unfurls": False,
        },
    )
    db = SessionLocal()
    try:
        m = svc.generate_manifest(db)
    finally:
        db.close()
    scopes = set(m["oauth_config"]["scopes"]["bot"])
    events = m["settings"]["event_subscriptions"]["bot_events"]
    assert "app_home_opened" not in events and "link_shared" not in events
    assert not {"channels:history", "links:read", "links:write"} & scopes
    assert "app_home" not in m["features"]


# ---------- Command configuration ----------


def test_command_domain_and_instructions_saved(monkeypatch):
    _enable(monkeypatch)
    r = client.put(
        "/api/admin/slack/config",
        json={
            "slash_commands": [
                {
                    "command": "/askit",
                    "description": "IT questions",
                    "usage_hint": "vpn help",
                    "domain": "it",
                    "instructions": "Answer only IT questions.",
                }
            ]
        },
    )
    assert r.status_code == 200
    cmds = r.json()["slash_commands"]
    assert cmds[0]["domain"] == "IT"
    assert cmds[0]["instructions"] == "Answer only IT questions."
    # Restore defaults so later modules see the stock command set.
    r = client.put("/api/admin/slack/config", json={"slash_commands": svc.DEFAULT_COMMANDS})
    assert r.status_code == 200


def test_command_domain_validated(monkeypatch):
    _enable(monkeypatch)
    r = client.put(
        "/api/admin/slack/config",
        json={"slash_commands": [{"command": "/x", "domain": "FINANCE"}]},
    )
    assert r.status_code == 400


def test_command_config_backwards_compatible():
    """Old stored command JSON without domain/instructions still parses."""
    row = SlackIntegration(id=99)
    row.slash_commands = json.dumps([{"command": "/askweka", "description": "d", "usage_hint": "u"}])
    cmds = svc.parse_commands(row)
    assert cmds[0]["domain"] == "" and cmds[0]["instructions"] == ""
