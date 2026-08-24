import { useEffect, useState } from "react";
import Qa from "./Qa.jsx";
import Knowledge from "./Knowledge.jsx";

async function api(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

const NAV = [
  { key: "dashboard", label: "Dashboard" },
  { key: "qa", label: "QA" },
  { key: "knowledge", label: "Knowledge" },
  { key: "apikeys", label: "API Keys" },
  { key: "mcp", label: "MCP" },
  { key: "audit", label: "Audit" },
];

function Dashboard() {
  const [stats, setStats] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api("/api/admin/summary").then(setStats).catch((e) => setError(String(e.message || e)));
  }, []);
  if (error) return <div className="admin-error">Failed to load: {error}</div>;
  if (!stats) return <div className="admin-loading">Loading…</div>;
  const cards = [
    ["Conversations", stats.conversations],
    ["Messages", stats.messages],
    ["Feedback entries", stats.feedback],
    ["👍 Thumbs up", stats.thumbs_up],
    ["👎 Thumbs down", stats.thumbs_down],
    ["Audit events", stats.audit_events],
  ];
  return (
    <div>
      <h1>Dashboard</h1>
      <div className="admin-cards">
        {cards.map(([label, value]) => (
          <div className="admin-card" key={label}>
            <div className="admin-card-value">{value}</div>
            <div className="admin-card-label">{label}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function AuditPage() {
  const [data, setData] = useState(null);
  const [actions, setActions] = useState([]);
  const [error, setError] = useState("");
  const [filters, setFilters] = useState({ username: "", action: "", from: "", to: "" });
  const [page, setPage] = useState(1);

  const load = () => {
    const params = new URLSearchParams({ page: String(page), page_size: "50" });
    for (const [k, v] of Object.entries(filters)) if (v) params.set(k, v);
    api(`/api/admin/audit?${params}`).then(setData).catch((e) => setError(String(e.message || e)));
  };

  useEffect(() => {
    api("/api/admin/actions").then(setActions).catch(() => {});
  }, []);
  useEffect(load, [page]); // eslint-disable-line react-hooks/exhaustive-deps

  const applyFilters = () => {
    setPage(1);
    load();
  };

  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  return (
    <div>
      <h1>Audit log</h1>
      <div className="admin-filters">
        <input
          placeholder="User"
          value={filters.username}
          onChange={(e) => setFilters({ ...filters, username: e.target.value })}
        />
        <select
          value={filters.action}
          onChange={(e) => setFilters({ ...filters, action: e.target.value })}
        >
          <option value="">All actions</option>
          {actions.map((a) => (
            <option key={a} value={a}>{a}</option>
          ))}
        </select>
        <input type="date" value={filters.from} onChange={(e) => setFilters({ ...filters, from: e.target.value })} />
        <input type="date" value={filters.to} onChange={(e) => setFilters({ ...filters, to: e.target.value })} />
        <button onClick={applyFilters}>Apply</button>
      </div>
      {error && <div className="admin-error">{error}</div>}
      {data && (
        <>
          <table className="admin-table">
            <thead>
              <tr><th>Time (UTC)</th><th>User</th><th>Action</th><th>Detail</th></tr>
            </thead>
            <tbody>
              {data.events.map((e) => (
                <tr key={e.id}>
                  <td>{e.created_at.replace("T", " ").slice(0, 19)}</td>
                  <td>{e.username}</td>
                  <td><code>{e.action}</code></td>
                  <td className="admin-detail">{e.detail}</td>
                </tr>
              ))}
              {data.events.length === 0 && (
                <tr><td colSpan={4} className="admin-empty">No events match.</td></tr>
              )}
            </tbody>
          </table>
          <div className="admin-pager">
            <button disabled={page <= 1} onClick={() => setPage(page - 1)}>‹ Prev</button>
            <span>Page {data.page} of {pages} · {data.total} events</span>
            <button disabled={page >= pages} onClick={() => setPage(page + 1)}>Next ›</button>
          </div>
        </>
      )}
    </div>
  );
}

function ComingSoon({ title }) {
  return (
    <div>
      <h1>{title}</h1>
      <p className="admin-soon">Coming soon — this section is planned as a follow-up.</p>
    </div>
  );
}

export default function Admin({ user }) {
  const [section, setSection] = useState("dashboard");

  return (
    <div className="admin">
      <aside className="admin-nav">
        <a className="admin-back" href="/">← Ask WEKA</a>
        <div className="admin-title">Admin</div>
        {NAV.map((n) => (
          <button
            key={n.key}
            className={`admin-nav-item ${section === n.key ? "active" : ""}`}
            onClick={() => setSection(n.key)}
          >
            {n.label}
          </button>
        ))}
        <div className="admin-nav-footer" title={user.email}>{user.name || user.username}</div>
      </aside>
      <main className="admin-main">
        {section === "dashboard" && <Dashboard />}
        {section === "audit" && <AuditPage />}
        {section === "qa" && <Qa />}
        {section === "knowledge" && <Knowledge />}
        {section === "apikeys" && <ComingSoon title="API key management" />}
        {section === "mcp" && <ComingSoon title="MCP endpoint" />}
      </main>
    </div>
  );
}
