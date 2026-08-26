import { useEffect, useState } from "react";

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

const patch = (path, body) =>
  api(path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

function BarChart({ data }) {
  const max = Math.max(1, ...data.map((d) => d.up + d.down));
  return (
    <div className="qa-chart">
      {data.map((d) => (
        <div className="qa-bar-col" key={d.date} title={`${d.date}: 👍${d.up} 👎${d.down}`}>
          <div className="qa-bar">
            <div className="qa-bar-up" style={{ height: `${(d.up / max) * 100}%` }} />
            <div className="qa-bar-down" style={{ height: `${(d.down / max) * 100}%` }} />
          </div>
          <div className="qa-bar-label">{d.date.slice(5)}</div>
        </div>
      ))}
      {data.length === 0 && <div className="admin-soon">No feedback yet in this period.</div>}
    </div>
  );
}

function Overview() {
  const [stats, setStats] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api("/api/admin/qa/stats?days=30").then(setStats).catch((e) => setError(String(e.message || e)));
  }, []);
  if (error) return <div className="admin-error">{error}</div>;
  if (!stats) return <div className="admin-loading">Loading…</div>;
  return (
    <div>
      <h2>Feedback — last 30 days</h2>
      <div className="qa-legend">
        <span><i className="qa-dot up" /> Thumbs up</span>
        <span><i className="qa-dot down" /> Thumbs down</span>
        <span className="qa-open">{stats.open_reviews} feedback items awaiting review</span>
      </div>
      <BarChart data={stats.by_day} />
      <h2>By domain</h2>
      <div className="admin-cards">
        {stats.by_domain.map((d) => (
          <div className="admin-card" key={d.domain}>
            <div className="admin-card-value">👍 {d.up} · 👎 {d.down}</div>
            <div className="admin-card-label">{d.domain} · {d.total} total</div>
          </div>
        ))}
        {stats.by_domain.length === 0 && <div className="admin-soon">No feedback yet.</div>}
      </div>
      <h2>Recent negative / written feedback</h2>
      <table className="admin-table">
        <thead>
          <tr><th>Time</th><th>Domain</th><th>Question</th><th>Feedback</th></tr>
        </thead>
        <tbody>
          {stats.recent_negative.map((f) => (
            <tr key={f.id}>
              <td>{f.logged_time.replace("T", " ").slice(0, 16)}</td>
              <td>{f.domain}</td>
              <td>{f.question.slice(0, 80)}</td>
              <td className="admin-detail">{f.thumbs === "down" ? "👎 " : ""}{f.feedback_text}</td>
            </tr>
          ))}
          {stats.recent_negative.length === 0 && (
            <tr><td colSpan={4} className="admin-empty">No negative feedback. 🎉</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function FeedbackReview() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [filters, setFilters] = useState({ thumbs: "", domain: "", status: "", from: "", to: "" });
  const [applied, setApplied] = useState(filters);
  const [page, setPage] = useState(1);
  const [expanded, setExpanded] = useState(null);
  const [reloadKey, setReloadKey] = useState(0);

  // Single declarative loader keyed on (page, applied filters); stale
  // responses are ignored so pagination and filter changes cannot race.
  useEffect(() => {
    let stale = false;
    const params = new URLSearchParams({ page: String(page), page_size: "25" });
    for (const [k, v] of Object.entries(applied)) if (v) params.set(k, v);
    api(`/api/admin/qa/feedback?${params}`)
      .then((d) => { if (!stale) setData(d); })
      .catch((e) => { if (!stale) setError(String(e.message || e)); });
    return () => { stale = true; };
  }, [page, applied, reloadKey]);

  const setStatus = async (id, review_status) => {
    try {
      await patch(`/api/admin/qa/feedback/${id}`, { review_status });
      setReloadKey((k) => k + 1);
    } catch (e) {
      setError(String(e.message || e));
    }
  };

  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  return (
    <div>
      <div className="admin-filters">
        <select value={filters.thumbs} onChange={(e) => setFilters({ ...filters, thumbs: e.target.value })}>
          <option value="">All ratings</option>
          <option value="up">👍 Up</option>
          <option value="down">👎 Down</option>
          <option value="none">No rating</option>
        </select>
        <select value={filters.domain} onChange={(e) => setFilters({ ...filters, domain: e.target.value })}>
          <option value="">All domains</option>
          <option value="HR">HR</option>
          <option value="IT">IT</option>
        </select>
        <select value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value })}>
          <option value="">All statuses</option>
          <option value="open">Open</option>
          <option value="reviewed">Reviewed</option>
          <option value="resolved">Resolved</option>
        </select>
        <input type="date" value={filters.from} onChange={(e) => setFilters({ ...filters, from: e.target.value })} />
        <input type="date" value={filters.to} onChange={(e) => setFilters({ ...filters, to: e.target.value })} />
        <button onClick={() => { setPage(1); setApplied({ ...filters }); }}>Apply</button>
      </div>
      {error && <div className="admin-error">{error}</div>}
      {data && (
        <>
          <table className="admin-table">
            <thead>
              <tr><th>Time</th><th></th><th>Domain</th><th>Question</th><th>Status</th><th>Actions</th></tr>
            </thead>
            <tbody>
              {data.items.map((f) => (
                <>
                  <tr key={f.id} className="qa-row" onClick={() => setExpanded(expanded === f.id ? null : f.id)}>
                    <td>{f.logged_time.replace("T", " ").slice(0, 16)}</td>
                    <td>{f.thumbs === "up" ? "👍" : f.thumbs === "down" ? "👎" : "—"}</td>
                    <td>{f.domain}</td>
                    <td>{f.question.slice(0, 70)}</td>
                    <td><span className={`qa-status ${f.review_status}`}>{f.review_status}</span></td>
                    <td onClick={(e) => e.stopPropagation()}>
                      {f.review_status !== "reviewed" && (
                        <button className="qa-mini" onClick={() => setStatus(f.id, "reviewed")}>Reviewed</button>
                      )}
                      {f.review_status !== "resolved" && (
                        <button className="qa-mini" onClick={() => setStatus(f.id, "resolved")}>Resolved</button>
                      )}
                      {f.review_status !== "open" && (
                        <button className="qa-mini" onClick={() => setStatus(f.id, "open")}>Reopen</button>
                      )}
                    </td>
                  </tr>
                  {expanded === f.id && (
                    <tr key={`${f.id}-x`}>
                      <td colSpan={6} className="qa-expand">
                        <p><b>Question:</b> {f.question}</p>
                        <p><b>Answer summary:</b> {f.answer_summary}</p>
                        {f.feedback_text && <p><b>Feedback:</b> {f.feedback_text}</p>}
                        <p><b>Sources:</b> {f.sources.length ? f.sources.map((s) => (
                          <a key={s} href={s} target="_blank" rel="noreferrer">{s} </a>
                        )) : "none cited"}</p>
                      </td>
                    </tr>
                  )}
                </>
              ))}
              {data.items.length === 0 && (
                <tr><td colSpan={6} className="admin-empty">No feedback matches.</td></tr>
              )}
            </tbody>
          </table>
          <div className="admin-pager">
            <button disabled={page <= 1} onClick={() => setPage(page - 1)}>‹ Prev</button>
            <span>Page {data.page} of {pages} · {data.total} items</span>
            <button disabled={page >= pages} onClick={() => setPage(page + 1)}>Next ›</button>
          </div>
        </>
      )}
    </div>
  );
}

function Golden() {
  const [questions, setQuestions] = useState([]);
  const [runs, setRuns] = useState([]);
  const [runDetail, setRunDetail] = useState(null);
  const [error, setError] = useState("");
  const [form, setForm] = useState({ question: "", expected_topic: "", domain: "" });
  const [running, setRunning] = useState(false);

  const load = () => {
    api("/api/admin/qa/golden").then(setQuestions).catch((e) => setError(String(e.message || e)));
    api("/api/admin/qa/golden/runs").then(setRuns).catch(() => {});
  };
  useEffect(load, []);

  const add = async () => {
    if (!form.question.trim()) return;
    try {
      await api("/api/admin/qa/golden", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });
      setForm({ question: "", expected_topic: "", domain: "" });
      load();
    } catch (e) {
      setError(String(e.message || e));
    }
  };

  const toggleActive = async (q) => {
    await patch(`/api/admin/qa/golden/${q.id}`, { active: !q.active });
    load();
  };

  const runSuite = async () => {
    setRunning(true);
    setError("");
    try {
      const result = await api("/api/admin/qa/golden/run", { method: "POST" });
      setRunDetail(result);
      load();
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setRunning(false);
    }
  };

  const openRun = async (id) => {
    setRunDetail(await api(`/api/admin/qa/golden/runs/${id}`));
  };

  const setVerdict = async (resultId, verdict) => {
    await patch(`/api/admin/qa/golden/results/${resultId}`, { verdict });
    if (runDetail) openRun(runDetail.id);
  };

  return (
    <div>
      {error && <div className="admin-error">{error}</div>}
      <h2>Golden questions</h2>
      <div className="qa-add">
        <input
          placeholder="Test question, e.g. How do I request a new laptop?"
          value={form.question}
          onChange={(e) => setForm({ ...form, question: e.target.value })}
        />
        <input
          placeholder="Expected topic / what a good answer covers"
          value={form.expected_topic}
          onChange={(e) => setForm({ ...form, expected_topic: e.target.value })}
        />
        <select value={form.domain} onChange={(e) => setForm({ ...form, domain: e.target.value })}>
          <option value="">Auto domain</option>
          <option value="IT">IT</option>
          <option value="HR">HR</option>
        </select>
        <button onClick={add} disabled={!form.question.trim()}>Add</button>
      </div>
      <table className="admin-table">
        <thead>
          <tr><th>Question</th><th>Expected topic</th><th>Domain</th><th>Active</th><th>By</th></tr>
        </thead>
        <tbody>
          {questions.map((q) => (
            <tr key={q.id} className={q.active ? "" : "qa-inactive"}>
              <td>{q.question}</td>
              <td className="admin-detail">{q.expected_topic}</td>
              <td>{q.domain}</td>
              <td><button className="qa-mini" onClick={() => toggleActive(q)}>{q.active ? "Active ✓" : "Inactive"}</button></td>
              <td>{q.created_by}</td>
            </tr>
          ))}
          {questions.length === 0 && (
            <tr><td colSpan={5} className="admin-empty">No golden questions yet — add one above.</td></tr>
          )}
        </tbody>
      </table>

      <div className="qa-run-row">
        <button className="qa-run-btn" onClick={runSuite} disabled={running || !questions.some((q) => q.active)}>
          {running ? "Running suite… (may take a minute)" : "▶ Run suite now"}
        </button>
      </div>

      <h2>Runs</h2>
      <table className="admin-table">
        <thead>
          <tr><th>Started</th><th>By</th><th>Status</th><th>Questions</th><th>Pass</th><th>Fail</th><th>Flagged</th><th></th></tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.id}>
              <td>{r.started_at.replace("T", " ").slice(0, 16)}</td>
              <td>{r.started_by}</td>
              <td>{r.status}</td>
              <td>{r.total}</td>
              <td>{r.passed}</td>
              <td>{r.failed}</td>
              <td>{r.flagged}</td>
              <td><button className="qa-mini" onClick={() => openRun(r.id)}>View</button></td>
            </tr>
          ))}
          {runs.length === 0 && <tr><td colSpan={8} className="admin-empty">No runs yet.</td></tr>}
        </tbody>
      </table>

      {runDetail && (
        <div className="qa-run-detail">
          <h2>Run results — {runDetail.started_at.replace("T", " ").slice(0, 16)}</h2>
          {runDetail.results.map((r) => (
            <div className={`qa-result ${r.auto_flagged ? "flagged" : ""}`} key={r.id}>
              <div className="qa-result-head">
                <b>{r.question}</b>
                <span>
                  {r.auto_flagged && (
                    <span className="qa-flag">
                      ⚠ {r.error ? "error" : r.ai_verdict === "fail" ? "AI fail" : "no sources"}
                    </span>
                  )}
                  {r.ai_verdict && (
                    <span className={`qa-ai-verdict ${r.ai_verdict}`}>
                      AI: {r.ai_verdict}
                    </span>
                  )}
                  <button className={`qa-mini ${r.verdict === "pass" ? "on" : ""}`} onClick={() => setVerdict(r.id, r.verdict === "pass" ? "" : "pass")}>Pass</button>
                  <button className={`qa-mini ${r.verdict === "fail" ? "on" : ""}`} onClick={() => setVerdict(r.id, r.verdict === "fail" ? "" : "fail")}>Fail</button>
                </span>
              </div>
              {r.expected_topic && <div className="admin-detail">Expected: {r.expected_topic}</div>}
              {r.error ? (
                <div className="admin-error">{r.error}</div>
              ) : (
                <div className="qa-answer">{r.answer}</div>
              )}
              {r.ai_reasoning && (
                <div className={`qa-ai-reasoning ${r.ai_verdict || "none"}`}>
                  <b>AI judge:</b> {r.ai_reasoning}
                </div>
              )}
              <div className="admin-detail">
                {r.sources_count} source(s) cited
                {r.reviewed_by ? ` · verdict by ${r.reviewed_by} (overrides AI)` : ""}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function Qa() {
  const [tab, setTab] = useState("overview");
  return (
    <div>
      <h1>QA — answer quality</h1>
      <div className="qa-tabs">
        {[["overview", "Overview"], ["feedback", "Feedback review"], ["golden", "Golden questions"]].map(([k, label]) => (
          <button key={k} className={`qa-tab ${tab === k ? "active" : ""}`} onClick={() => setTab(k)}>
            {label}
          </button>
        ))}
      </div>
      {tab === "overview" && <Overview />}
      {tab === "feedback" && <FeedbackReview />}
      {tab === "golden" && <Golden />}
    </div>
  );
}
