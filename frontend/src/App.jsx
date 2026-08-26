import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import Admin from "./Admin.jsx";

function FeedbackBar({ messageId, question }) {
  const [thumbs, setThumbs] = useState(null);
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);

  const submit = async (payload) => {
    setBusy(true);
    try {
      const res = await fetch("/api/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message_id: messageId, ...payload }),
      });
      if (!res.ok) throw new Error(await res.text());
      return true;
    } catch (err) {
      console.error("Feedback failed:", err);
      alert("Could not save feedback. Please try again.");
      return false;
    } finally {
      setBusy(false);
    }
  };

  const rate = async (value) => {
    if (busy) return;
    const next = value === thumbs ? null : value;
    if (await submit({ thumbs: next })) setThumbs(next);
  };

  const sendWritten = async () => {
    if (!text.trim() || busy) return;
    if (await submit({ thumbs, feedback_text: text })) {
      setSent(true);
      setOpen(false);
    }
  };

  return (
    <div className="feedback-bar">
      <button
        className={`fb-btn ${thumbs === "up" ? "selected" : ""}`}
        title="Good answer"
        onClick={() => rate("up")}
      >
        👍
      </button>
      <button
        className={`fb-btn ${thumbs === "down" ? "selected" : ""}`}
        title="Bad answer"
        onClick={() => rate("down")}
      >
        👎
      </button>
      <button className="fb-link" onClick={() => setOpen(true)}>
        {sent ? "Feedback sent ✓" : "Write feedback"}
      </button>

      {open && (
        <div className="fb-overlay" onClick={() => setOpen(false)}>
          <div className="fb-modal" onClick={(e) => e.stopPropagation()}>
            <h2>Share feedback</h2>
            <p className="fb-question">Q: {question}</p>
            <div className="fb-thumbs-row">
              <button
                className={`fb-btn big ${thumbs === "up" ? "selected" : ""}`}
                onClick={() => setThumbs(thumbs === "up" ? null : "up")}
              >
                👍
              </button>
              <button
                className={`fb-btn big ${thumbs === "down" ? "selected" : ""}`}
                onClick={() => setThumbs(thumbs === "down" ? null : "down")}
              >
                👎
              </button>
            </div>
            <textarea
              autoFocus
              rows={6}
              placeholder="What was helpful or missing? What should improve?"
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
            <div className="fb-actions">
              <button className="fb-cancel" onClick={() => setOpen(false)}>
                Cancel
              </button>
              <button
                className="fb-submit"
                disabled={!text.trim() || busy}
                onClick={sendWritten}
              >
                {busy ? "Sending…" : "Submit feedback"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function ResolutionBar({ conversationId, resolution, onResolved, disabled }) {
  const [modal, setModal] = useState(false);
  const [draft, setDraft] = useState(null); // {title, body} once loaded
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [jira, setJira] = useState(null); // {enabled, connected, email, site_url}
  const [created, setCreated] = useState(null); // ticket response after filing

  if (resolution === "solved")
    return <div className="resolution-note">✅ Marked as solved — no ticket needed.</div>;
  if (resolution === "ticket")
    return (
      <div className="resolution-note">
        🎫 Ticket created — the team will follow up.
        {created?.jira_key && (
          <>
            {" "}
            <a href={created.jira_url} target="_blank" rel="noreferrer">
              {created.jira_key} in Jira
            </a>
          </>
        )}
      </div>
    );
  if (disabled) return null;

  const markSolved = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const res = await fetch("/api/tickets/solved", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conversation_id: conversationId }),
      });
      if (!res.ok) throw new Error(await res.text());
      onResolved("solved");
    } catch (e) {
      setError("Could not save. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  const openTicket = async () => {
    setModal(true);
    setError("");
    fetch("/api/jira/status")
      .then((r) => (r.ok ? r.json() : null))
      .then(setJira)
      .catch(() => {});
    if (draft) return;
    setLoading(true);
    try {
      const res = await fetch("/api/tickets/draft", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conversation_id: conversationId }),
      });
      if (!res.ok) throw new Error(await res.text());
      setDraft(await res.json());
    } catch (e) {
      setError("Could not draft the ticket. You can still write it yourself below.");
      setDraft({ title: "", body: "" });
    } finally {
      setLoading(false);
    }
  };

  const submitTicket = async () => {
    if (busy || !draft?.title.trim()) return;
    setBusy(true);
    setError("");
    try {
      const res = await fetch("/api/tickets", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          conversation_id: conversationId,
          title: draft.title,
          body: draft.body,
        }),
      });
      if (!res.ok) {
        let msg = "Could not create the ticket. Please try again.";
        try {
          const d = await res.json();
          if (d.detail) msg = d.detail;
        } catch {}
        throw new Error(msg);
      }
      setCreated(await res.json());
      setModal(false);
      onResolved("ticket");
    } catch (e) {
      setError(e.message || "Could not create the ticket. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="resolution-bar">
      <span className="resolution-q">Did this solve your problem?</span>
      <button className="res-btn solved" disabled={busy} onClick={markSolved}>
        ✓ Yes, solved
      </button>
      <button className="res-btn ticket" disabled={busy} onClick={openTicket}>
        No — create a ticket
      </button>
      {error && !modal && <span className="res-error">{error}</span>}

      {modal && (
        <div className="fb-overlay" onClick={() => !busy && setModal(false)}>
          <div className="fb-modal ticket-modal" onClick={(e) => e.stopPropagation()}>
            <h2>Review your ticket</h2>
            <p className="ticket-hint">
              Drafted from this chat — including what was already tried. Edit
              anything. <b>Nothing is filed until you approve it.</b>
            </p>
            {loading ? (
              <p className="ticket-loading">Drafting from your conversation…</p>
            ) : (
              <>
                <input
                  className="ticket-title"
                  placeholder="Ticket title"
                  maxLength={200}
                  value={draft?.title || ""}
                  onChange={(e) => setDraft({ ...draft, title: e.target.value })}
                />
                <textarea
                  rows={10}
                  value={draft?.body || ""}
                  onChange={(e) => setDraft({ ...draft, body: e.target.value })}
                />
              </>
            )}
            {jira?.enabled && (
              <div className="jira-row">
                {jira.connected ? (
                  <span className="jira-connected">
                    ✅ Files in Jira as <b>{jira.email || "you"}</b>
                  </span>
                ) : (
                  <span className="jira-disconnected">
                    <a href="/api/jira/connect">Connect Jira</a> (sign in with your
                    WEKA SSO) to file this as a real Jira ticket under your name —
                    otherwise it stays in Ask WEKA only.
                  </span>
                )}
              </div>
            )}
            {error && <div className="res-error">{error}</div>}
            <div className="fb-actions">
              <button className="fb-cancel" disabled={busy} onClick={() => setModal(false)}>
                Cancel
              </button>
              <button
                className="fb-submit"
                disabled={loading || busy || !draft?.title.trim()}
                onClick={submitTicket}
              >
                {busy ? "Filing…" : "Approve & create ticket"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export default function App() {
  const [conversations, setConversations] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [user, setUser] = useState(undefined); // undefined = loading, null = signed out
  const [suggestions, setSuggestions] = useState([]);
  const [domain, setDomain] = useState(""); // "" | "HR" | "IT"
  const [resolution, setResolution] = useState(""); // "" | "solved" | "ticket"
  const [sidebarOpen, setSidebarOpen] = useState(false); // mobile drawer
  const scrollRef = useRef(null);

  const refreshConversations = () =>
    api("/api/conversations").then(setConversations).catch(console.error);

  useEffect(() => {
    fetch("/api/me")
      .then((res) => (res.ok ? res.json() : null))
      .then((me) => {
        setUser(me);
        if (me) {
          refreshConversations();
          api("/api/suggestions").then(setSuggestions).catch(() => {});
        }
      })
      .catch(() => setUser(null));
  }, []);

  useEffect(() => {
    if (scrollRef.current)
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages]);

  // Navigation is blocked while a response streams so incoming deltas can
  // never attach to a different conversation than the one that asked.
  const openConversation = async (id) => {
    if (streaming) return;
    setActiveId(id);
    setSidebarOpen(false);
    // Restore the conversation's fixed scope so the chips reflect reality —
    // the server enforces the stored scope regardless of what we send.
    const conv = conversations.find((c) => c.id === id);
    setDomain(conv?.domain || "");
    setResolution(conv?.resolution || "");
    setMessages(await api(`/api/conversations/${id}/messages`));
  };

  const newConversation = () => {
    if (streaming) return;
    setActiveId(null);
    setMessages([]);
    setResolution("");
    setSidebarOpen(false);
  };

  const deleteConversation = async (id, e) => {
    e.stopPropagation();
    if (streaming) return;
    await fetch(`/api/conversations/${id}`, { method: "DELETE" });
    if (id === activeId) newConversation();
    refreshConversations();
  };

  // Scope applies per conversation: switching it mid-conversation starts a
  // fresh chat so earlier out-of-scope turns never feed the scoped request.
  const toggleDomain = (d) => {
    if (streaming) return;
    const next = domain === d ? "" : d;
    setDomain(next);
    if (messages.length > 0) newConversation();
  };

  const send = async (preset) => {
    const text = (preset ?? input).trim();
    if (!text || streaming) return;
    setInput("");
    setStreaming(true);
    setMessages((m) => [
      ...m,
      { role: "user", content: text },
      { role: "assistant", content: "" },
    ]);

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conversation_id: activeId, message: text, domain }),
      });
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split("\n\n");
        buffer = events.pop();
        for (const evt of events) {
          if (!evt.startsWith("data: ")) continue;
          const payload = JSON.parse(evt.slice(6));
          if (payload.conversation_id && !activeId)
            setActiveId(payload.conversation_id);
          if (payload.delta)
            setMessages((m) => {
              const copy = [...m];
              copy[copy.length - 1] = {
                ...copy[copy.length - 1],
                content: copy[copy.length - 1].content + payload.delta,
              };
              return copy;
            });
          if (payload.done && payload.message_id)
            setMessages((m) => {
              const copy = [...m];
              copy[copy.length - 1] = {
                ...copy[copy.length - 1],
                id: payload.message_id,
              };
              return copy;
            });
          if (payload.error)
            setMessages((m) => {
              const copy = [...m];
              copy[copy.length - 1] = {
                role: "assistant",
                content: `⚠️ Error: ${payload.error}`,
                error: true,
              };
              return copy;
            });
        }
      }
    } catch (err) {
      setMessages((m) => [
        ...m.slice(0, -1),
        { role: "assistant", content: `⚠️ Request failed: ${err.message}`, error: true },
      ]);
    } finally {
      setStreaming(false);
      refreshConversations();
    }
  };

  if (user === undefined)
    return (
      <div className="login-page">
        <div className="login-card">
          <h1>Ask WEKA</h1>
          <p>Loading…</p>
        </div>
      </div>
    ); // still checking session

  // Always show the sign-in screen first — never bounce straight to Okta.
  const loginScreen = (message) => (
    <div className="login-page">
      <div className="login-card">
        <h1>Ask WEKA</h1>
        <p>{message}</p>
        <a className="login-btn" href="/auth/login">
          Sign in with Okta
        </a>
      </div>
    </div>
  );

  if (window.location.pathname.startsWith("/admin")) {
    if (user === null)
      return loginScreen("Sign in with your WEKA Okta account to access the admin portal.");
    if (!user.is_admin)
      return <div className="admin-denied">403 — Admin access required. <a href="/">Back to Ask WEKA</a></div>;
    return <Admin user={user} />;
  }

  if (user === null)
    return loginScreen("Sign in with your WEKA Okta account to continue.");

  return (
    <div className="app">
      {sidebarOpen && <div className="sidebar-backdrop" onClick={() => setSidebarOpen(false)} />}
      <aside className={`sidebar ${sidebarOpen ? "open" : ""}`}>
        <button className="new-chat" onClick={newConversation}>
          + New chat
        </button>
        <div className="conv-list">
          {conversations.map((c) => (
            <div
              key={c.id}
              className={`conv-item ${c.id === activeId ? "active" : ""}`}
              onClick={() => openConversation(c.id)}
            >
              <span className="conv-title">{c.title}</span>
              <button
                className="conv-delete"
                title="Delete"
                onClick={(e) => deleteConversation(c.id, e)}
              >
                ×
              </button>
            </div>
          ))}
        </div>
        <div className="sidebar-footer">
          <div className="user-line" title={user.email}>
            {user.name || user.username}
          </div>
          {user.is_admin && (
            <a className="logout-link" href="/admin">
              Admin
            </a>
          )}
          {user.auth_enabled && (
            <a className="logout-link" href="/auth/logout">
              Sign out
            </a>
          )}
          <div>Ask WEKA · POC</div>
        </div>
      </aside>

      <main className="chat">
        <div className="chat-topbar">
          <button
            className="menu-btn"
            title="Conversations"
            onClick={() => setSidebarOpen(!sidebarOpen)}
          >
            ☰
          </button>
          <span className="topbar-title">Ask WEKA</span>
          <div className="domain-chips">
            {["HR", "IT"].map((d) => (
              <button
                key={d}
                className={`chip ${domain === d ? "on" : ""}`}
                disabled={streaming}
                title={`Scope answers to ${d} topics (starts a new chat)`}
                onClick={() => toggleDomain(d)}
              >
                {d}
              </button>
            ))}
          </div>
        </div>
        <div className="messages" ref={scrollRef}>
          {messages.length === 0 && (
            <div className="empty">
              <h1>Ask WEKA</h1>
              <p>Ask anything about internal docs, processes, and tools.</p>
              {suggestions.length > 0 && (
                <div className="suggestions">
                  {suggestions
                    .filter((s) => !domain || !s.domain || s.domain === domain)
                    .slice(0, 4)
                    .map((s) => (
                      <button
                        key={s.question}
                        className="suggestion"
                        onClick={() => send(s.question)}
                      >
                        {s.domain && <span className="suggestion-tag">{s.domain}</span>}
                        {s.question}
                      </button>
                    ))}
                </div>
              )}
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={`msg ${m.role}`}>
              <div
                className={`msg-content ${
                  streaming && m.role === "assistant" && i === messages.length - 1 && m.content && !m.error
                    ? "streaming-cursor"
                    : ""
                }`}
              >
                {m.content ? (
                  <ReactMarkdown>{m.content}</ReactMarkdown>
                ) : m.role === "assistant" && streaming && i === messages.length - 1 ? (
                  <span className="thinking-row" role="status" aria-live="polite">
                    <span className="thinking-dots" aria-hidden="true">
                      <span /><span /><span />
                    </span>
                    <span className="thinking-label">Thinking…</span>
                  </span>
                ) : (
                  <span className="thinking">…</span>
                )}
              </div>
              {m.role === "assistant" &&
                m.content &&
                m.id &&
                !(streaming && i === messages.length - 1) && (
                  <FeedbackBar
                    key={m.id}
                    messageId={m.id}
                    question={messages[i - 1]?.content || ""}
                  />
                )}
            </div>
          ))}
          {activeId &&
            !streaming &&
            messages.length > 1 &&
            messages[messages.length - 1]?.role === "assistant" &&
            messages[messages.length - 1]?.content &&
            !messages[messages.length - 1]?.error && (
              <ResolutionBar
                key={`${activeId}-${resolution}`}
                conversationId={activeId}
                resolution={resolution}
                onResolved={(r) => {
                  setResolution(r);
                  refreshConversations();
                }}
              />
            )}
        </div>
        <div className="composer">
          <textarea
            value={input}
            placeholder="Ask a question…"
            rows={2}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
          />
          <button onClick={send} disabled={streaming || !input.trim()}>
            {streaming ? "…" : "Send"}
          </button>
        </div>
      </main>
    </div>
  );
}
