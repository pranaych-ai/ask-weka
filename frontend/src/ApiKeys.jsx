import { useEffect, useState } from "react";

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

export function KeyTable({ kind, reloadKey, onChanged }) {
  const [keys, setKeys] = useState([]);
  const [error, setError] = useState("");

  useEffect(() => {
    api(`/api/admin/keys?kind=${kind}`).then(setKeys).catch((e) => setError(String(e.message || e)));
  }, [kind, reloadKey]);

  const revoke = async (k) => {
    if (!confirm(`Revoke "${k.name}"? Clients using it stop working immediately.`)) return;
    await api(`/api/admin/keys/${k.id}/revoke`, { method: "POST" });
    onChanged();
  };

  return (
    <>
      {error && <div className="admin-error">{error}</div>}
      <table className="admin-table">
        <thead>
          <tr>
            <th>Name</th><th>Owner</th><th>Key</th><th>Rate/min</th>
            {kind === "mcp" && <th>Expires</th>}
            <th>Uses (24h)</th><th>Total</th><th>Last used</th><th>Status</th><th></th>
          </tr>
        </thead>
        <tbody>
          {keys.map((k) => (
            <tr key={k.id} className={k.revoked ? "qa-inactive" : ""}>
              <td>{k.name}</td>
              <td>{k.owner || k.created_by}</td>
              <td><code>{k.prefix}…</code></td>
              <td>{k.rate_limit_per_min}</td>
              {kind === "mcp" && <td>{k.expires_at ? k.expires_at.slice(0, 10) : "—"}</td>}
              <td>{k.usage_24h}</td>
              <td>{k.usage_count}</td>
              <td>{k.last_used_at ? k.last_used_at.replace("T", " ").slice(0, 16) : "never"}</td>
              <td>
                <span className={`qa-status ${k.revoked ? "open" : "resolved"}`}>
                  {k.revoked ? "revoked" : "active"}
                </span>
              </td>
              <td>
                {!k.revoked && (
                  <button className="qa-mini" onClick={() => revoke(k)}>Revoke</button>
                )}
              </td>
            </tr>
          ))}
          {keys.length === 0 && (
            <tr><td colSpan={kind === "mcp" ? 10 : 9} className="admin-empty">None yet.</td></tr>
          )}
        </tbody>
      </table>
    </>
  );
}

export default function ApiKeys() {
  const [form, setForm] = useState({ name: "", owner: "", rate_limit_per_min: 30 });
  const [secret, setSecret] = useState("");
  const [error, setError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);

  const create = async () => {
    setError("");
    try {
      const out = await api("/api/admin/keys", json("POST", { ...form, kind: "api" }));
      setSecret(out.key);
      setForm({ name: "", owner: "", rate_limit_per_min: 30 });
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
          <button className="kb-save" disabled={!form.name.trim()} onClick={create}>
            Create key
          </button>
        </div>
      </div>
      <KeyTable kind="api" reloadKey={reloadKey} onChanged={() => setReloadKey((k) => k + 1)} />
    </div>
  );
}
