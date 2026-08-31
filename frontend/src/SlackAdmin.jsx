import { useEffect, useState } from "react";

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = await res.text();
    try {
      const d = JSON.parse(msg);
      if (d.detail) msg = d.detail;
    } catch {}
    throw new Error(msg);
  }
  return res.json();
}

const json = (method, body) => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

const TIMEZONES = [
  "UTC",
  "America/New_York",
  "America/Chicago",
  "America/Denver",
  "America/Los_Angeles",
  "Europe/London",
  "Europe/Berlin",
  "Asia/Jerusalem",
  "Asia/Kolkata",
  "Asia/Tokyo",
];

export default function SlackAdmin() {
  const [cfg, setCfg] = useState(null);
  const [manifest, setManifest] = useState(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");
  const [showManifest, setShowManifest] = useState(false);

  const load = () =>
    api("/api/admin/slack/config").then(setCfg).catch((e) => setError(String(e.message || e)));

  useEffect(() => {
    load();
  }, []);

  const flash = (msg) => {
    setNotice(msg);
    setTimeout(() => setNotice(""), 4000);
  };

  const save = async (patch, action = "save") => {
    setBusy(action);
    setError("");
    try {
      setCfg(await api("/api/admin/slack/config", json("PUT", patch)));
      flash("Saved.");
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setBusy("");
    }
  };

  const verify = async () => {
    setBusy("verify");
    setError("");
    try {
      const out = await api("/api/admin/slack/verify", { method: "POST" });
      setCfg(out);
      flash(out.verified ? "Connection verified ✓" : `Verification failed: ${out.last_verify_error}`);
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setBusy("");
    }
  };

  const testMessage = async () => {
    setBusy("test");
    setError("");
    try {
      await api("/api/admin/slack/test", json("POST", { channel: "" }));
      flash("Test DM sent — check your Slack.");
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setBusy("");
    }
  };

  const loadManifest = async () => {
    if (!showManifest && !manifest) {
      try {
        setManifest(await api("/api/admin/slack/manifest"));
      } catch (e) {
        setError(String(e.message || e));
        return;
      }
    }
    setShowManifest(!showManifest);
  };

  if (error && !cfg) return <div className="admin-error">Failed to load: {error}</div>;
  if (!cfg) return <div className="admin-loading">Loading…</div>;

  const setFeature = (key, value) => save({ features: { [key]: value } }, `f-${key}`);
  const setCommand = (index, patch) => {
    const commands = cfg.slash_commands.map((c, i) => (i === index ? { ...c, ...patch } : c));
    setCfg({ ...cfg, slash_commands: commands });
    save({ slash_commands: commands }, `command-${index}`);
  };

  return (
    <div>
      <h1>Slack</h1>
      <p className="admin-detail">
        Bot credentials live only in Replit Secrets (<code>SLACK_BOT_TOKEN</code>,{" "}
        <code>SLACK_SIGNING_SECRET</code>) — they are never entered, stored, or shown here.
      </p>
      {error && <div className="admin-error">{error}</div>}
      {notice && <div className="slack-notice">{notice}</div>}

      <div className="slack-status-row">
        <span className={`qa-status ${cfg.credentials_configured ? "resolved" : "open"}`}>
          {cfg.credentials_configured ? "credentials set" : "credentials missing"}
        </span>
        <span className={`qa-status ${cfg.verified ? "resolved" : "open"}`}>
          {cfg.verified ? "verified" : "not verified"}
        </span>
        <span className={`qa-status ${cfg.enabled ? "resolved" : "open"}`}>
          {cfg.enabled ? "enabled" : "disabled"}
        </span>
        {cfg.verified && (
          <span className="admin-detail">
            Workspace <b>{cfg.team_name}</b> · bot <b>@{cfg.bot_name}</b>
            {cfg.last_verified_at && ` · verified ${cfg.last_verified_at.slice(0, 16).replace("T", " ")} UTC`}
          </span>
        )}
        {!cfg.verified && cfg.last_verify_error && (
          <span className="admin-detail">Last error: {cfg.last_verify_error}</span>
        )}
      </div>

      <div className="slack-actions">
        <button className="kb-save" disabled={!!busy || !cfg.credentials_configured} onClick={verify}>
          {busy === "verify" ? "Verifying…" : "Verify connection"}
        </button>
        <button
          className="qa-mini"
          disabled={!!busy || !cfg.verified || !cfg.enabled}
          onClick={testMessage}
        >
          {busy === "test" ? "Sending…" : "Send me a test DM"}
        </button>
        <button className="qa-mini" onClick={loadManifest}>
          {showManifest ? "Hide app manifest" : "Show app manifest"}
        </button>
        <button
          className="qa-mini"
          disabled={!!busy}
          onClick={() => save({ enabled: !cfg.enabled }, "toggle")}
        >
          {cfg.enabled ? "Disable integration" : "Enable integration"}
        </button>
      </div>

      {showManifest && manifest && (
        <div className="slack-manifest">
          <p className="admin-detail">
            Paste this manifest at <code>api.slack.com/apps</code> → Create New App → From
            manifest, install it to the WEKA workspace, then put the bot token and signing
            secret into Replit Secrets and click Verify. Endpoints point at{" "}
            <code>{manifest.base_url}</code>.
          </p>
          <button
            className="qa-mini"
            onClick={() => navigator.clipboard.writeText(manifest.manifest_json)}
          >
            Copy manifest JSON
          </button>
          <pre className="slack-manifest-pre">{manifest.manifest_json}</pre>
        </div>
      )}

      <h2>Features</h2>
      <table className="admin-table">
        <thead>
          <tr><th>Feature</th><th>Description</th><th>Consent</th><th>Enabled</th></tr>
        </thead>
        <tbody>
          {Object.entries(cfg.feature_meta).map(([key, meta]) => (
            <tr key={key}>
              <td>{meta.label}</td>
              <td className="admin-detail">{meta.description}</td>
              <td className="admin-detail">{meta.user_optin ? "employee opt-in" : "admin only"}</td>
              <td>
                <input
                  type="checkbox"
                  checked={!!cfg.features[key]}
                  disabled={!!busy}
                  onChange={(e) => setFeature(key, e.target.checked)}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2>Notifications & digest</h2>
      <div className="kb-editor-row slack-config-row">
        <label>
          Notification channel
          <input
            placeholder="#askweka-alerts or C0123456789"
            defaultValue={cfg.notify_channel}
            onBlur={(e) => {
              if (e.target.value !== cfg.notify_channel) save({ notify_channel: e.target.value });
            }}
          />
        </label>
        <label>
          Digest time
          <input
            type="time"
            defaultValue={cfg.digest_time}
            onBlur={(e) => {
              if (e.target.value && e.target.value !== cfg.digest_time)
                save({ digest_time: e.target.value });
            }}
          />
        </label>
        <label>
          Timezone
          <select
            value={cfg.digest_timezone}
            onChange={(e) => save({ digest_timezone: e.target.value })}
          >
            {TIMEZONES.concat(
              TIMEZONES.includes(cfg.digest_timezone) ? [] : [cfg.digest_timezone]
            ).map((tz) => (
              <option key={tz} value={tz}>{tz}</option>
            ))}
          </select>
        </label>
        <span className="admin-detail">
          Last digest: {cfg.digest_last_run || "never"}
        </span>
      </div>

      <h2>Slash commands</h2>
      <p className="admin-detail">
        Registered in the generated manifest. Each command can scope the knowledge base and add
        administrator instructions; answers remain ephemeral until the asker explicitly posts.
      </p>
      <table className="admin-table">
        <thead>
          <tr><th>Command</th><th>Description</th><th>Usage hint</th><th>Domain</th><th>Instructions</th></tr>
        </thead>
        <tbody>
          {cfg.slash_commands.map((c, index) => (
            <tr key={c.command}>
              <td><code>{c.command}</code></td>
              <td>{c.description}</td>
              <td className="admin-detail">{c.usage_hint}</td>
              <td>
                <select
                  value={c.domain || ""}
                  disabled={!!busy}
                  onChange={(e) => setCommand(index, { domain: e.target.value })}
                >
                  <option value="">All</option>
                  <option value="IT">IT</option>
                  <option value="HR">HR</option>
                </select>
              </td>
              <td>
                <input
                  value={c.instructions || ""}
                  disabled={!!busy}
                  placeholder="Optional command-specific guidance"
                  onChange={(e) => {
                    const commands = cfg.slash_commands.map((x, i) =>
                      i === index ? { ...x, instructions: e.target.value } : x
                    );
                    setCfg({ ...cfg, slash_commands: commands });
                  }}
                  onBlur={(e) => setCommand(index, { instructions: e.target.value })}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
