import { useState } from "react";
import { useConfigRepos } from "../lib/api";
import { Button } from "../components/ui/Button";

export default function ConfigRepos() {
  const repos = useConfigRepos();
  const [filter, setFilter] = useState("");
  const rows = (repos.data ?? []).filter((r) => r.hostname.includes(filter) || r.repo.includes(filter));

  return (
    <div>
      <h2 className="text-lg mb-2">Config version control</h2>
      <p className="text-sm text-text-muted mb-4">Three stores, kept separate. Templates (Jinja only) in netops/templates. Vars (YAML only) in netops/vars. One repo per device in netops/config-&lt;hostname&gt;. NetBox Jinja lives in NetBox only and is exported read-only — it is never the render source.</p>
      <div className="border border-border bg-surface rounded p-4 mb-4 text-sm">
        <p><span className="mono">netops/templates</span> — Jinja2 templates, no device values, no secrets</p>
        <p><span className="mono">netops/vars</span> — YAML vars by site / role / device-type, no Jinja logic</p>
        <p><span className="mono">netops/config-&lt;hostname&gt;</span> — one repo per device: rendered config, backup, vars snapshot, metadata</p>
        <p><span className="mono">netops/netbox-export</span> — NetBox Jinja, read-only audit copy, never rendered from</p>
      </div>
      <div className="mb-4">
        <label htmlFor="config-filter" className="text-sm">Filter by hostname or repo</label>
        <input id="config-filter" className="w-full px-2 py-1 text-sm bg-surface border border-border rounded mono" placeholder="leaf-01" value={filter} onChange={(e) => setFilter(e.target.value)} />
      </div>
      {repos.isLoading ? <p className="text-sm">Loading config repos…</p> : null}
      {repos.isError ? (
        <div>
          <p className="text-sm text-danger" role="alert">Failed to load config repos: {(repos.error as Error).message}</p>
          <Button variant="secondary" onClick={() => repos.refetch()}>Retry</Button>
        </div>
      ) : null}
      {!repos.isLoading && !repos.isError ? (
        <table aria-label="Per-device config repos">
          <thead><tr><th>Hostname</th><th>Repo</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.hostname}>
                <td className="mono">{r.hostname}</td>
                <td className="mono">{r.repo}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {!repos.isLoading && !repos.isError && rows.length === 0 ? <p className="text-sm text-text-muted">No device repos match this filter.</p> : null}
    </div>
  );
}
