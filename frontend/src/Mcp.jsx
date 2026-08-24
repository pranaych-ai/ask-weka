import { useState } from "react";
import { KeyTable, NewSecret } from "./ApiKeys.jsx";

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export default function Mcp() {
  const [form, setForm] = useState({ name: "", owner: "", expires_days: 30 });
  const [secret, setSecret] = useState("");
  const [error, setError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);
  const mcpUrl = `${window.location.origin}/mcp`;

  const create = async () => {
    setError("");
    try {
      const out = await api("/api/admin/keys", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...form, kind: "mcp", rate_limit_per_min: 30 }),
      });
      setSecret(out.key);
      setForm({ name: "", owner: "", expires_days: 30 });
      setReloadKey((k) => k + 1);
    } catch (e) {
      setError(String(e.message || e));
    }
  };

  return (
    <div>
      <h1>MCP endpoint</h1>
      <p className="admin-detail">
        Claude and other AI assistants can query Ask WEKA through the Model Context
        Protocol. The server exposes one read-only tool, <code>ask_weka</code>{" "}
        (question in → answer + cited sources out). Endpoint:{" "}
        <code>{mcpUrl}</code> (HTTP transport). Authenticate with an expiring
        bearer token issued below: <code>Authorization: Bearer &lt;token&gt;</code>.
      </p>
      {error && <div className="admin-error">{error}</div>}
      {secret && <NewSecret secret={secret} label="MCP token" onDismiss={() => setSecret("")} />}
      <div className="kb-add">
        <div className="kb-editor-row">
          <input placeholder="Token name, e.g. Claude Desktop — Pranay" value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <input placeholder="Owner" value={form.owner}
            onChange={(e) => setForm({ ...form, owner: e.target.value })} />
          <select value={form.expires_days}
            onChange={(e) => setForm({ ...form, expires_days: Number(e.target.value) })}>
            <option value={7}>Expires in 7 days</option>
            <option value={30}>Expires in 30 days</option>
            <option value={90}>Expires in 90 days</option>
          </select>
          <button className="kb-save" disabled={!form.name.trim()} onClick={create}>
            Issue token
          </button>
        </div>
      </div>
      <KeyTable kind="mcp" reloadKey={reloadKey} onChanged={() => setReloadKey((k) => k + 1)} />
    </div>
  );
}
