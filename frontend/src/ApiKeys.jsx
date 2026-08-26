import { Fragment, useEffect, useState } from "react";

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

const json = (method, body) => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export function NewSecret({ secret, onDismiss, label }) {
  return (
    <div className="key-secret">
      <div className="key-secret-title">
        {label} created — copy it now, it will not be shown again.
      </div>
      <div className="key-secret-row">
        <code>{secret}</code>
        <button className="qa-mini" onClick={() => navigator.clipboard.writeText(secret)}>
          Copy
        </button>
        <button className="qa-mini" onClick={onDismiss}>Done</button>
      </div>
    </div>
  );
}

function UsageLog({ keyId }) {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api(`/api/admin/keys/${keyId}/usage`).then(setRows).catch((e) => setError(String(e.message || e)));
  }, [keyId]);

  if (error) return <div className="admin-error">{error}</div>;
  if (!rows) return <div className="admin-detail">Loading…</div>;
  if (rows.length === 0) return <div className="admin-detail">No requests yet.</div>;
  return (
    <table className="admin-table key-usage-table">
      <thead>
        <tr><th>When</th><th>Endpoint</th><th>Question</th><th>On behalf of</th><th>Status</th></tr>
      </thead>
      <tbody>
        {rows.map((u) => (
          <tr key={u.id}>
            <td>{u.created_at.replace("T", " ").slice(0, 16)}</td>
            <td><code>{u.endpoint}</code></td>
            <td>{u.question || <span className="admin-detail">—</span>}</td>
            <td>{u.on_behalf_of || <span className="admin-detail">—</span>}</td>
            <td>{u.status_code}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function KeyTable({ kind, reloadKey, onChanged }) {
  const [keys, setKeys] = useState([]);
  const [error, setError] = useState("");
  const [openLog, setOpenLog] = useState("");

  useEffect(() => {
    api(`/api/admin/keys?kind=${kind}`).then(setKeys).catch((e) => setError(String(e.message || e)));
  }, [kind, reloadKey]);

  const revoke = async (k) => {
    if (!confirm(`Revoke "${k.name}"? This is permanent — clients using it stop working immediately.`)) return;
    await api(`/api/admin/keys/${k.id}/revoke`, { method: "POST" });
    onChanged();
  };

  const setEnabled = async (k, enabled) => {
    await api(`/api/admin/keys/${k.id}/enabled`, json("POST", { enabled }));
    onChanged();
  };

  const cols = kind === "mcp" ? 12 : 11;
  return (
    <>
      {error && <div className="admin-error">{error}</div>}
      <table className="admin-table">
        <thead>
          <tr>
            <th>Name</th><th>Owner</th><th>Key</th><th>Scope</th><th>Rate/min</th>
            {kind === "mcp" && <th>Expires</th>}
            <th>Uses (24h)</th><th>Total</th><th>Last used</th><th>Status</th><th></th><th></th>
          </tr>
        </thead>
        <tbody>
          {keys.map((k) => (
            <Fragment key={k.id}>
              <tr className={k.revoked || !k.enabled ? "qa-inactive" : ""}>
                <td>{k.name}</td>
                <td>{k.owner || k.created_by}</td>
                <td><code>{k.prefix}…</code></td>
                <td>{k.allowed_domains || "Full KB"}</td>
                <td>{k.rate_limit_per_min}</td>
                {kind === "mcp" && <td>{k.expires_at ? k.expires_at.slice(0, 10) : "—"}</td>}
                <td>{k.usage_24h}</td>
                <td>{k.usage_count}</td>
                <td>{k.last_used_at ? k.last_used_at.replace("T", " ").slice(0, 16) : "never"}</td>
                <td>
                  <span className={`qa-status ${k.revoked || !k.enabled ? "open" : "resolved"}`}>
                    {k.revoked ? "revoked" : k.enabled ? "active" : "disabled"}
                  </span>
                </td>
                <td>
                  <button className="qa-mini" onClick={() => setOpenLog(openLog === k.id ? "" : k.id)}>
                    {openLog === k.id ? "Hide log" : "Log"}
                  </button>
                </td>
                <td>
                  {!k.revoked && (
                    <>
                      <button className="qa-mini" onClick={() => setEnabled(k, !k.enabled)}>
                        {k.enabled ? "Disable" : "Enable"}
                      </button>{" "}
                      <button className="qa-mini" onClick={() => revoke(k)}>Revoke</button>
                    </>
                  )}
                </td>
              </tr>
              {openLog === k.id && (
                <tr>
                  <td colSpan={cols}><UsageLog keyId={k.id} /></td>
                </tr>
              )}
            </Fragment>
          ))}
          {keys.length === 0 && (
            <tr><td colSpan={cols} className="admin-empty">None yet.</td></tr>
          )}
        </tbody>
      </table>
    </>
  );
}

export default function ApiKeys() {
  const [form, setForm] = useState({ name: "", owner: "", rate_limit_per_min: 30, allowed_domains: "" });
  const [secret, setSecret] = useState("");
  const [error, setError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);

  const create = async () => {
    setError("");
    try {
      const out = await api("/api/admin/keys", json("POST", { ...form, kind: "api" }));
      setSecret(out.key);
      setForm({ name: "", owner: "", rate_limit_per_min: 30, allowed_domains: "" });
      setReloadKey((k) => k + 1);
    } catch (e) {
      setError(String(e.message || e));
    }
  };

  return (
    <div>
      <h1>API keys</h1>
      <p className="admin-detail">
        Scoped keys for other internal tools to call <code>POST /api/v1/ask</code>.
        Scope: ask-only. Keys are stored hashed and shown once at creation.
      </p>
      {error && <div className="admin-error">{error}</div>}
      {secret && <NewSecret secret={secret} label="API key" onDismiss={() => setSecret("")} />}
      <div className="kb-add">
        <div className="kb-editor-row">
          <input placeholder="Key name, e.g. Slack bot" value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <input placeholder="Owner (team or person)" value={form.owner}
            onChange={(e) => setForm({ ...form, owner: e.target.value })} />
          <input type="number" min="1" max="600" title="Requests per minute" style={{ width: 90 }}
            value={form.rate_limit_per_min}
            onChange={(e) => setForm({ ...form, rate_limit_per_min: Number(e.target.value) })} />
          <select title="Knowledge base scope" value={form.allowed_domains}
            onChange={(e) => setForm({ ...form, allowed_domains: e.target.value })}>
            <option value="">Full KB</option>
            <option value="HR">HR only</option>
            <option value="IT">IT only</option>
          </select>
          <button className="kb-save" disabled={!form.name.trim()} onClick={create}>
            Create key
          </button>
        </div>
      </div>
      <KeyTable kind="api" reloadKey={reloadKey} onChanged={() => setReloadKey((k) => k + 1)} />
    </div>
  );
}
