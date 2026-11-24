import { useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { flowsApi, useCatalog, useFlows, useRuns } from "../api/flows";
import { Button } from "../components/ui/Button";
import { useAuth } from "../lib/auth";
import { StatusChip } from "./FlowRun";

export default function Flows() {
  const flows = useFlows();
  const catalog = useCatalog();
  const runs = useRuns();
  const nav = useNavigate();
  const qc = useQueryClient();
  const { can } = useAuth();
  const file = useRef<HTMLInputElement>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const triggerLabel = (t: string) => catalog.data?.triggers.find((x) => x.type === t)?.label ?? t;

  const onImport = async (f: File | undefined) => {
    if (!f) return;
    try {
      const created = await flowsApi.importFlow(JSON.parse(await f.text()));
      await qc.invalidateQueries({ queryKey: ["flows"] });
      nav(`/flows/${created.id}`);
    } catch (e) { setMsg({ ok: false, text: `Import failed: ${(e as Error).message}` }); }
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-lg flex-1">Flows</h2>
        {can("operator") ? <Button variant="primary" onClick={() => nav("/flows/new")}>New flow</Button> : null}
        {can("operator") ? <Button onClick={() => file.current?.click()}>Import…</Button> : null}
        <input ref={file} type="file" accept=".json,application/json" className="hidden" aria-label="Import flow file" onChange={(e) => onImport(e.target.files?.[0])} />
      </div>
      <p className="text-sm text-text-muted">
        Automations like iPhone Shortcuts or Power Automate: a trigger (ticket, alert, NetBox change, schedule, webhook, Kafka…) starts a flow of actions with variables, conditions, loops, error handling, API calls, scripts, checks and full VireoNet changes.
        Drafts are saved as versions; an approver publishes a version and enables its trigger.
      </p>
      {msg ? <p role={msg.ok ? "status" : "alert"} className={`text-sm ${msg.ok ? "text-success" : "text-danger"}`}>{msg.text}</p> : null}

      {flows.isLoading ? <p className="text-sm">Loading flows…</p> : (flows.data?.items.length ?? 0) === 0 ? (
        <p className="text-sm text-text-muted">No flows yet. Start from a template below or create a new flow.</p>
      ) : (
        <table aria-label="Flows">
          <thead><tr><th>Name</th><th>Trigger</th><th>Status</th><th>Version</th><th>Steps</th><th>Runs</th><th>Last run</th></tr></thead>
          <tbody>{flows.data!.items.map((f) => (
            <tr key={f.id}>
              <td><Link to={`/flows/${f.id}`}>{f.name}</Link>{f.description ? <div className="text-[12px] text-text-muted line-clamp-1">{f.description}</div> : null}</td>
              <td className="text-sm">{triggerLabel(f.trigger_type)}</td>
              <td>{f.enabled ? <span className="text-success text-sm">● on</span> : <span className="text-text-muted text-sm">○ off</span>}</td>
              <td className="text-sm">v{f.latest_version}{f.published_version ? ` (live v${f.published_version})` : " (draft)"}</td>
              <td>{f.steps}</td><td>{f.runs}</td>
              <td className="text-sm">{f.last_run_at ? new Date(f.last_run_at).toLocaleString() : "—"}</td>
            </tr>
          ))}</tbody>
        </table>
      )}

      <section aria-label="Templates">
        <h3 className="text-base font-semibold mb-2">Start from a template</h3>
        <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))" }}>
          {(catalog.data?.examples ?? []).map((ex) => (
            <article key={ex.key} className="border border-border rounded-[8px] p-3 bg-surface flex flex-col gap-1">
              <span className="text-[12px] text-text-muted uppercase">{triggerLabel(ex.trigger_type)}</span>
              <h4 className="text-sm font-semibold">{ex.name}</h4>
              <p className="text-[12px] text-text-muted flex-1">{ex.description}</p>
              {can("operator") ? <Button onClick={() => nav(`/flows/new?example=${ex.key}`)}>Use template</Button> : null}
            </article>
          ))}
        </div>
      </section>

      <section aria-label="Recent runs">
        <h3 className="text-base font-semibold mb-2">Recent runs</h3>
        {(runs.data?.items.length ?? 0) === 0 ? <p className="text-sm text-text-muted">No runs yet.</p> : (
          <table aria-label="Recent runs table"><thead><tr><th>Run</th><th>Flow</th><th>Status</th><th>Trigger</th><th>Started</th></tr></thead>
            <tbody>{runs.data!.items.slice(0, 15).map((r) => (
              <tr key={r.id}>
                <td><Link className="mono" to={`/flows/runs/${r.id}`}>{r.id.slice(0, 8)}</Link></td>
                <td>{r.flow_name}</td><td><StatusChip status={r.status} /></td><td>{r.trigger_type}</td>
                <td className="text-sm">{r.created_at ? new Date(r.created_at).toLocaleString() : ""}</td>
              </tr>
            ))}</tbody></table>
        )}
      </section>
    </div>
  );
}
