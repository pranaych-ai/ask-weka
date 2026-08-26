import { useEffect, useState } from "react";

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

const STATUSES = ["open", "in_progress", "resolved", "closed"];

export default function Tickets() {
  const [stats, setStats] = useState(null);
  const [tickets, setTickets] = useState(null);
  const [filter, setFilter] = useState("");
  const [expanded, setExpanded] = useState(null);
  const [error, setError] = useState("");

  const load = () => {
    const qs = filter ? `?status=${filter}` : "";
    Promise.all([api("/api/admin/tickets/stats"), api(`/api/admin/tickets${qs}`)])
      .then(([s, t]) => {
        setStats(s);
        setTickets(t);
      })
      .catch((e) => setError(String(e.message || e)));
  };
  useEffect(load, [filter]); // eslint-disable-line react-hooks/exhaustive-deps

  const setStatus = async (id, status) => {
    try {
      await api(`/api/admin/tickets/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status }),
      });
      load();
    } catch (e) {
      setError(String(e.message || e));
    }
  };

  if (error) return <div className="admin-error">{error}</div>;
  if (!stats || !tickets) return <div className="admin-loading">Loading…</div>;

  const cards = [
    ["Solved without a ticket", stats.solved_without_ticket],
    ["Tickets created", stats.tickets_created],
    ["Open tickets", stats.open_tickets],
    [
      "Deflection rate",
      stats.deflection_rate == null ? "—" : `${Math.round(stats.deflection_rate * 100)}%`,
    ],
  ];

  return (
    <div>
      <h1>Tickets</h1>
      <p className="admin-hint">
        The assistant tries to solve issues in chat first. Tickets appear here only
        after the employee approved a draft. "Deflection rate" is the share of
        resolved issues that never needed a ticket — the success number.
      </p>
      <div className="admin-cards">
        {cards.map(([label, value]) => (
          <div className="admin-card" key={label}>
            <div className="admin-card-value">{value}</div>
            <div className="admin-card-label">{label}</div>
          </div>
        ))}
      </div>
      <div className="admin-filters">
        <select value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="">All statuses</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>{s.replace("_", " ")}</option>
          ))}
        </select>
      </div>
      <table className="admin-table">
        <thead>
          <tr><th>Created</th><th>Employee</th><th>Domain</th><th>Title</th><th>Status</th><th>Actions</th></tr>
        </thead>
        <tbody>
          {tickets.map((t) => (
            <>
              <tr
                key={t.id}
                className="qa-row"
                onClick={() => setExpanded(expanded === t.id ? null : t.id)}
              >
                <td>{t.created_at.replace("T", " ").slice(0, 16)}</td>
                <td>{t.username}</td>
                <td>{t.domain || "—"}</td>
                <td>{t.title}</td>
                <td><span className={`qa-status ${t.status}`}>{t.status.replace("_", " ")}</span></td>
                <td onClick={(e) => e.stopPropagation()}>
                  <select value={t.status} onChange={(e) => setStatus(t.id, e.target.value)}>
                    {STATUSES.map((s) => (
                      <option key={s} value={s}>{s.replace("_", " ")}</option>
                    ))}
                  </select>
                </td>
              </tr>
              {expanded === t.id && (
                <tr key={`${t.id}-x`}>
                  <td colSpan={6} className="qa-expand">
                    <pre className="ticket-body">{t.body}</pre>
                  </td>
                </tr>
              )}
            </>
          ))}
          {tickets.length === 0 && (
            <tr><td colSpan={6} className="admin-empty">No tickets. 🎉</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
