import { useEffect, useRef, useState } from "react";

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

function SectionEditor({ section, onSaved, onClose }) {
  const [form, setForm] = useState(null);
  const [versions, setVersions] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api(`/api/admin/kb/sections/${section.id}`).then(setForm).catch((e) => setError(String(e.message || e)));
    api(`/api/admin/kb/sections/${section.id}/versions`).then(setVersions).catch(() => {});
  }, [section.id]);

  const save = async () => {
    setBusy(true);
    setError("");
    try {
      await api(`/api/admin/kb/sections/${section.id}`, json("PATCH", {
        title: form.title, domain: form.domain, body: form.body,
      }));
      onSaved();
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setBusy(false);
    }
  };

  const revert = async (version) => {
    if (!confirm(`Revert to version ${version}? The current content is kept in history.`)) return;
    setBusy(true);
    try {
      await api(`/api/admin/kb/sections/${section.id}/revert`, json("POST", { version }));
      onSaved();
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setBusy(false);
    }
  };

  if (!form) return <div className="admin-loading">Loading…</div>;
  return (
    <div className="kb-editor">
      <div className="kb-editor-head">
        <h2>Edit section <span className="admin-detail">(v{form.version})</span></h2>
        <button className="qa-mini" onClick={onClose}>Close</button>
      </div>
      {error && <div className="admin-error">{error}</div>}
      <div className="kb-editor-row">
        <input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
        <select value={form.domain} onChange={(e) => setForm({ ...form, domain: e.target.value })}>
          <option value="">No domain</option>
          <option value="IT">IT</option>
          <option value="HR">HR</option>
        </select>
        <button className="kb-save" disabled={busy || !form.title.trim()} onClick={save}>
          {busy ? "Saving…" : "Save"}
        </button>
      </div>
      <textarea
        className="kb-body"
        value={form.body}
        onChange={(e) => setForm({ ...form, body: e.target.value })}
        rows={18}
      />
      {versions.length > 0 && (
        <>
          <h3>History</h3>
          <table className="admin-table">
            <thead><tr><th>Version</th><th>Edited by</th><th>When</th><th>Size</th><th></th></tr></thead>
            <tbody>
              {versions.map((v) => (
                <tr key={v.id}>
                  <td>v{v.version}</td>
                  <td>{v.edited_by}</td>
                  <td>{v.created_at.replace("T", " ").slice(0, 16)}</td>
                  <td>{v.body_chars.toLocaleString()} chars</td>
                  <td><button className="qa-mini" disabled={busy} onClick={() => revert(v.version)}>Revert to this</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}

function Sections() {
  const [sections, setSections] = useState([]);
  const [editing, setEditing] = useState(null);
  const [error, setError] = useState("");
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState({ title: "", domain: "", body: "" });

  const load = () =>
    api("/api/admin/kb/sections").then(setSections).catch((e) => setError(String(e.message || e)));
  useEffect(load, []);

  const toggleActive = async (s) => {
    await api(`/api/admin/kb/sections/${s.id}`, json("PATCH", { active: !s.active }));
    load();
  };

  const add = async () => {
    try {
      await api("/api/admin/kb/sections", json("POST", form));
      setForm({ title: "", domain: "", body: "" });
      setAdding(false);
      load();
    } catch (e) {
      setError(String(e.message || e));
    }
  };

  if (editing)
    return (
      <SectionEditor
        section={editing}
        onClose={() => setEditing(null)}
        onSaved={() => { setEditing(null); load(); }}
      />
    );

  return (
    <div>
      {error && <div className="admin-error">{error}</div>}
      <div className="kb-toolbar">
        <span className="admin-detail">
          Active sections are injected into the assistant prompt — changes take effect immediately.
        </span>
        <button className="kb-save" onClick={() => setAdding(!adding)}>{adding ? "Cancel" : "+ New section"}</button>
      </div>
      {adding && (
        <div className="kb-add">
          <div className="kb-editor-row">
            <input placeholder="Section title" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
            <select value={form.domain} onChange={(e) => setForm({ ...form, domain: e.target.value })}>
              <option value="">No domain</option>
              <option value="IT">IT</option>
              <option value="HR">HR</option>
            </select>
            <button className="kb-save" disabled={!form.title.trim()} onClick={add}>Create</button>
          </div>
          <textarea className="kb-body" rows={8} placeholder="Markdown content…" value={form.body} onChange={(e) => setForm({ ...form, body: e.target.value })} />
        </div>
      )}
      <table className="admin-table">
        <thead>
          <tr><th>Title</th><th>Domain</th><th>Size</th><th>Version</th><th>Updated</th><th>By</th><th>Active</th><th></th></tr>
        </thead>
        <tbody>
          {sections.map((s) => (
            <tr key={s.id} className={s.active ? "" : "qa-inactive"}>
              <td>{s.title}</td>
              <td>{s.domain}</td>
              <td>{s.body_chars.toLocaleString()}</td>
              <td>v{s.version}</td>
              <td>{s.updated_at.replace("T", " ").slice(0, 16)}</td>
              <td>{s.updated_by}</td>
              <td><button className="qa-mini" onClick={() => toggleActive(s)}>{s.active ? "Active ✓" : "Inactive"}</button></td>
              <td><button className="qa-mini" onClick={() => setEditing(s)}>Edit</button></td>
            </tr>
          ))}
          {sections.length === 0 && (
            <tr><td colSpan={8} className="admin-empty">No sections yet — create one or sync a source.</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function Sources() {
  const [sources, setSources] = useState([]);
  const [error, setError] = useState("");
  const [form, setForm] = useState({ name: "", type: "upload", url: "", notes: "", domain: "" });
  const fileInputs = useRef({});

  const load = () =>
    api("/api/admin/kb/sources").then(setSources).catch((e) => setError(String(e.message || e)));
  useEffect(load, []);

  const add = async () => {
    try {
      await api("/api/admin/kb/sources", json("POST", form));
      setForm({ name: "", type: "upload", url: "", notes: "", domain: "" });
      load();
    } catch (e) {
      setError(String(e.message || e));
    }
  };

  const remove = async (id) => {
    if (!confirm("Remove this source? Synced KB sections are kept.")) return;
    await api(`/api/admin/kb/sources/${id}`, { method: "DELETE" });
    load();
  };

  const syncFile = async (src, file) => {
    setError("");
    const fd = new FormData();
    fd.append("file", file);
    try {
      await api(`/api/admin/kb/sources/${src.id}/sync`, { method: "POST", body: fd });
    } catch (e) {
      setError(String(e.message || e));
    }
    load();
  };

  return (
    <div>
      {error && <div className="admin-error">{error}</div>}
      <div className="kb-add">
        <div className="kb-editor-row">
          <input placeholder="Source name, e.g. HR Handbook export" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <select value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value })}>
            <option value="upload">File upload</option>
            <option value="notion">Notion (manual)</option>
            <option value="gdrive">Google Drive (manual)</option>
            <option value="manual">Other (manual)</option>
          </select>
          <select value={form.domain} onChange={(e) => setForm({ ...form, domain: e.target.value })}>
            <option value="">No domain</option>
            <option value="IT">IT</option>
            <option value="HR">HR</option>
          </select>
        </div>
        <div className="kb-editor-row">
          <input placeholder="URL / location (optional)" value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} />
          <input placeholder="Notes (optional)" value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
          <button className="kb-save" disabled={!form.name.trim()} onClick={add}>Add source</button>
        </div>
      </div>
      <table className="admin-table">
        <thead>
          <tr><th>Name</th><th>Type</th><th>Domain</th><th>Last sync</th><th>Status</th><th>Detail</th><th>Actions</th></tr>
        </thead>
        <tbody>
          {sources.map((s) => (
            <tr key={s.id}>
              <td>{s.name}{s.url && <div className="admin-detail"><a href={s.url} target="_blank" rel="noreferrer">{s.url.slice(0, 50)}</a></div>}</td>
              <td>{s.type}{s.type !== "upload" && <div className="admin-detail">manual until IT approves a service account</div>}</td>
              <td>{s.domain}</td>
              <td>{s.last_sync_at ? s.last_sync_at.replace("T", " ").slice(0, 16) : "never"}</td>
              <td><span className={`qa-status ${s.last_sync_status === "ok" ? "resolved" : s.last_sync_status === "error" ? "open" : "reviewed"}`}>{s.last_sync_status}</span></td>
              <td className="admin-detail">{s.last_sync_detail}</td>
              <td>
                {s.type === "upload" && (
                  <>
                    <input
                      type="file"
                      accept=".md,.txt,.markdown,text/plain,text/markdown"
                      style={{ display: "none" }}
                      ref={(el) => (fileInputs.current[s.id] = el)}
                      onChange={(e) => {
                        if (e.target.files[0]) syncFile(s, e.target.files[0]);
                        e.target.value = "";
                      }}
                    />
                    <button className="qa-mini" onClick={() => fileInputs.current[s.id]?.click()}>Sync now</button>
                  </>
                )}
                <button className="qa-mini" onClick={() => remove(s.id)}>Remove</button>
              </td>
            </tr>
          ))}
          {sources.length === 0 && (
            <tr><td colSpan={7} className="admin-empty">No sources registered yet.</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

export default function Knowledge() {
  const [tab, setTab] = useState("sections");
  return (
    <div>
      <h1>Knowledge management</h1>
      <div className="qa-tabs">
        {[["sections", "KB sections"], ["sources", "Sources"]].map(([k, label]) => (
          <button key={k} className={`qa-tab ${tab === k ? "active" : ""}`} onClick={() => setTab(k)}>
            {label}
          </button>
        ))}
      </div>
      {tab === "sections" && <Sections />}
      {tab === "sources" && <Sources />}
    </div>
  );
}
