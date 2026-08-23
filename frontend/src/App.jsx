import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";

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
  const scrollRef = useRef(null);

  const refreshConversations = () =>
    api("/api/conversations").then(setConversations).catch(console.error);

  useEffect(() => {
    refreshConversations();
  }, []);

  useEffect(() => {
    if (scrollRef.current)
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages]);

  const openConversation = async (id) => {
    setActiveId(id);
    setMessages(await api(`/api/conversations/${id}/messages`));
  };

  const newConversation = () => {
    setActiveId(null);
    setMessages([]);
  };

  const deleteConversation = async (id, e) => {
    e.stopPropagation();
    await fetch(`/api/conversations/${id}`, { method: "DELETE" });
    if (id === activeId) newConversation();
    refreshConversations();
  };

  const send = async () => {
    const text = input.trim();
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
        body: JSON.stringify({ conversation_id: activeId, message: text }),
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
              };
              return copy;
            });
        }
      }
    } catch (err) {
      setMessages((m) => [
        ...m.slice(0, -1),
        { role: "assistant", content: `⚠️ Request failed: ${err.message}` },
      ]);
    } finally {
      setStreaming(false);
      refreshConversations();
    }
  };

  return (
    <div className="app">
      <aside className="sidebar">
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
        <div className="sidebar-footer">Ask WEKA · POC</div>
      </aside>

      <main className="chat">
        <div className="messages" ref={scrollRef}>
          {messages.length === 0 && (
            <div className="empty">
              <h1>Ask WEKA</h1>
              <p>Ask anything about internal docs, processes, and tools.</p>
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={`msg ${m.role}`}>
              <div className="msg-content">
                {m.content ? (
                  <ReactMarkdown>{m.content}</ReactMarkdown>
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
