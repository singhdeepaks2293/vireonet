import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { flowsApi, TERMINAL, useRun } from "../api/flows";
import { Button } from "../components/ui/Button";
import { useAuth } from "../lib/auth";
import { StepLog } from "./FlowEditor";

const CHIP: Record<string, string> = {
  succeeded: "bg-success-bg text-success border-success",
  running: "bg-info-bg text-info border-info",
  queued: "bg-muted text-text-muted border-border",
  waiting: "bg-warning-bg text-warning border-warning",
  retrying: "bg-warning-bg text-warning border-warning",
  warning: "bg-warning-bg text-warning border-warning",
  failed: "bg-danger-bg text-danger border-danger",
  cancelled: "bg-muted text-text-muted border-border",
  skipped: "bg-muted text-text-muted border-border",
};

export function StatusChip({ status }: { status: string }) {
  return <span className={`inline-block px-2 py-[1px] text-[12px] border rounded ${CHIP[status] ?? CHIP.queued}`}>{status}</span>;
}

export default function FlowRun() {
  const { runId } = useParams();
  const q = useRun(runId!);
  const qc = useQueryClient();
  const { can } = useAuth();
  const [comment, setComment] = useState("");
  const [msg, setMsg] = useState("");
  if (q.isLoading) return <p className="text-sm">Loading run…</p>;
  if (q.isError || !q.data) return <p className="text-sm text-danger" role="alert">Run not found.</p>;
  const r = q.data;
  const decide = async (stepId: string, approved: boolean) => {
    setMsg("");
    try { await flowsApi.decide(r.id, stepId, approved, comment); setMsg(approved ? "Approved." : "Rejected."); await qc.invalidateQueries({ queryKey: ["flow-run", r.id] }); }
    catch (e) { setMsg((e as Error).message); }
  };
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-lg">Run <span className="mono">{r.id.slice(0, 8)}</span> of <Link to={`/flows/${r.flow_id}`}>{r.flow_name}</Link> v{r.version}</h2>
        <StatusChip status={r.status} />
        {!TERMINAL.includes(r.status) && can("operator") ? <Button variant="danger" onClick={async () => { await flowsApi.cancel(r.id); setMsg("Cancel requested."); }}>Cancel run</Button> : null}
      </div>
      <p className="text-sm text-text-muted">
        Trigger: {r.trigger_type} · started by {r.started_by || "—"} · {r.created_at ? new Date(r.created_at).toLocaleString() : ""}
        {r.finished_at ? ` · finished ${new Date(r.finished_at).toLocaleString()}` : ""}
        {r.parent_run_id ? <> · sub-flow of <Link to={`/flows/runs/${r.parent_run_id}`} className="mono">{r.parent_run_id.slice(0, 8)}</Link></> : null}
      </p>
      {r.error ? <p role="alert" className="text-sm text-danger">{r.error}</p> : null}
      {Object.values(r.waiting ?? {}).map((w) => (
        <section key={w.step_id} aria-label="Waiting for approval" className="border-2 border-warning bg-warning-bg rounded-[6px] p-3">
          <p className="text-sm font-semibold">Waiting for approval ({w.role} or above) since {new Date(w.since).toLocaleString()}</p>
          <p className="text-sm my-1">{w.message}</p>
          <label htmlFor="decision-comment" className="block text-sm">Comment</label>
          <input id="decision-comment" className="w-full px-2 py-1 text-sm bg-surface border border-border rounded mb-2" value={comment} onChange={(e) => setComment(e.target.value)} />
          {can(w.role as "operator") ? (
            <div className="flex gap-2"><Button variant="primary" onClick={() => decide(w.step_id, true)}>Approve</Button><Button variant="danger" onClick={() => decide(w.step_id, false)}>Reject</Button></div>
          ) : <p className="text-sm text-text-muted">Your role cannot answer this approval.</p>}
        </section>
      ))}
      {msg ? <p role="status" className="text-sm">{msg}</p> : null}
      <div className="grid gap-4" style={{ gridTemplateColumns: "minmax(0, 2fr) minmax(280px, 1fr)" }}>
        <section>
          <h3 className="text-sm font-semibold mb-1">Steps ({r.log?.length ?? 0})</h3>
          {r.log?.length ? <StepLog log={r.log} /> : <p className="text-sm text-text-muted">No steps yet.</p>}
        </section>
        <section className="flex flex-col gap-2">
          <details open><summary className="text-sm font-semibold cursor-pointer">Outputs</summary><pre className="mono text-[12px] bg-bg p-2 overflow-x-auto">{JSON.stringify(r.outputs ?? {}, null, 2)}</pre></details>
          <details><summary className="text-sm font-semibold cursor-pointer">Variables</summary><pre className="mono text-[12px] bg-bg p-2 overflow-x-auto">{JSON.stringify(r.variables ?? {}, null, 2)}</pre></details>
          <details><summary className="text-sm font-semibold cursor-pointer">Inputs</summary><pre className="mono text-[12px] bg-bg p-2 overflow-x-auto">{JSON.stringify(r.inputs ?? {}, null, 2)}</pre></details>
          <details><summary className="text-sm font-semibold cursor-pointer">Trigger event</summary><pre className="mono text-[12px] bg-bg p-2 overflow-x-auto">{JSON.stringify(r.trigger_event ?? {}, null, 2)}</pre></details>
        </section>
      </div>
    </div>
  );
}
