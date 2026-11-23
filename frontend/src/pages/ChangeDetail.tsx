import { useState } from "react";
import { useParams } from "react-router-dom";
import { api, useChangeDetail } from "../lib/api";
import { StateBadge, CheckBadge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { StateTimeline } from "../components/StateTimeline";
import { DiffViewer } from "../components/DiffViewer";
import { AuditTrail } from "../components/AuditTrail";
import { useAuth } from "../lib/auth";
import type { ChangeState } from "../lib/tokens";

type Tab = "checks" | "diff" | "batfish" | "awx" | "audit";

const TABS: { id: Tab; label: string }[] = [
  { id: "checks", label: "Checks" },
  { id: "diff", label: "Diff" },
  { id: "batfish", label: "Batfish results" },
  { id: "awx", label: "AWX job output" },
  { id: "audit", label: "Audit trail" },
];

function legalActions(state: string): ("approve" | "abort" | "rollback")[] {
  if (state === "ci_passed") return ["approve", "abort"];
  if (["received", "validated", "planned", "approved", "pre_checked"].includes(state)) return ["abort"];
  if (["applied", "post_checked"].includes(state)) return ["rollback"];
  return [];
}

export default function ChangeDetail() {
  const { id = "" } = useParams();
  const detail = useChangeDetail(id);
  const { can } = useAuth();
  const [tab, setTab] = useState<Tab>("checks");
  const [confirm, setConfirm] = useState<"approve" | "abort" | "rollback" | null>(null);
  const [actionError, setActionError] = useState("");
  const [actionDone, setActionDone] = useState("");

  if (detail.isLoading) return <p className="text-sm">Loading change…</p>;
  if (detail.isError) {
    return (
      <div>
        <p className="text-sm text-danger" role="alert">Failed to load change: {(detail.error as Error).message}</p>
        <Button variant="secondary" onClick={() => detail.refetch()}>Retry</Button>
      </div>
    );
  }
  const data = detail.data;
  if (!data) return <p className="text-sm text-text-muted">Change not found.</p>;
  const change = data.change;
  const state = change.state as ChangeState;
  const actions = legalActions(change.state);

  const doAction = async (kind: "approve" | "abort" | "rollback") => {
    setActionError(""); setActionDone("");
    try {
      if (kind === "approve") await api.signalApproval(change.id, { approved: true, reason: "approved from UI" });
      else if (kind === "abort") await api.signalApproval(change.id, { approved: false, reason: "aborted from UI" });
      else await api.rollback(change.id);
      setActionDone(kind === "approve" ? "Approval signal sent." : kind === "abort" ? "Abort signal sent." : "Rollback requested.");
      await detail.refetch();
    } catch (e) {
      setActionError((e as Error).message);
    }
  };

  const confirmText: Record<string, string> = {
    approve: `Approve ${change.ticket_number}. This moves the change to approved and allows pre-check and apply to run. It does not apply config yet.`,
    abort: `Abort ${change.ticket_number}. This rejects the change and stops the workflow. No config will be applied.`,
    rollback: `Roll back ${change.ticket_number}. This restores the pre-change backup via the AWX rollback playbook and moves the change to rolled_back. Applied config on completed waves will be reverted.`,
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <h2 className="text-lg">
          <span className="mono">{change.ticket_number}</span> <span className="text-text-muted">— {change.change_type}</span>
        </h2>
        <StateBadge state={state} />
      </div>
      <p className="text-sm text-text-muted mb-4">
        Requester: {change.requester} · Devices: <span className="mono">{change.devices.join(", ")}</span> · Started: {change.created_at ? new Date(change.created_at).toLocaleString() : "—"}
      </p>
      <StateTimeline state={state} />

      <div className="flex gap-2 mb-4">
        {actions.includes("approve") && can("approver") ? (
          <ConfirmDialog open={confirm === "approve"} onOpenChange={(o) => setConfirm(o ? "approve" : null)} title="Approve change" description={confirmText.approve} confirmLabel="Approve" onConfirm={() => doAction("approve")}>
            <Button variant="primary">Approve</Button>
          </ConfirmDialog>
        ) : null}
        {actions.includes("abort") && can("operator") ? (
          <ConfirmDialog open={confirm === "abort"} onOpenChange={(o) => setConfirm(o ? "abort" : null)} title="Abort change" description={confirmText.abort} confirmLabel="Abort" onConfirm={() => doAction("abort")}>
            <Button variant="secondary">Abort</Button>
          </ConfirmDialog>
        ) : null}
        {actions.includes("rollback") && can("operator") ? (
          <ConfirmDialog open={confirm === "rollback"} onOpenChange={(o) => setConfirm(o ? "rollback" : null)} title="Roll back change" description={confirmText.rollback} confirmLabel="Roll back" onConfirm={() => doAction("rollback")}>
            <Button variant="danger">Roll back</Button>
          </ConfirmDialog>
        ) : null}
        {actions.length === 0 ? <p className="text-sm text-text-muted">No actions are legal for state {change.state}.</p> : null}
      </div>
      {actionError ? <p className="text-sm text-danger" role="alert">{actionError}</p> : null}
      {actionDone ? <p className="text-sm">{actionDone}</p> : null}

      <div className="flex gap-1 border-b border-border mb-4" role="tablist" aria-label="Change detail tabs">
        {TABS.map((t) => (
          <button key={t.id} type="button" role="tab" aria-selected={tab === t.id} onClick={() => setTab(t.id)} className={`px-2 py-1 text-sm border-b-2 ${tab === t.id ? "border-accent text-text" : "border-transparent text-text-muted"}`}>
            {t.label}
          </button>
        ))}
      </div>

      {tab === "checks" ? (
        <div>
          {data.checks.length === 0 ? <p className="text-sm text-text-muted">No checks recorded yet. Pre-checks run after approval; post-checks run after apply.</p> : (
            <table aria-label="Checks">
              <thead><tr><th>Check</th><th>Status</th><th>Message</th></tr></thead>
              <tbody>
                {data.checks.map((c, i) => (
                  <tr key={i}><td className="mono">{c.name}</td><td><CheckBadge status={c.status as "pass" | "fail" | "skip"} /></td><td>{c.message}</td></tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      ) : null}
      {tab === "diff" ? <DiffViewer lines={data.diff} /> : null}
      {tab === "batfish" ? (
        <div>
          {Object.keys(data.batfish).length === 0 ? <p className="text-sm text-text-muted">No Batfish results yet. The Batfish gate runs in CI before approval.</p> : (
            <pre className="mono text-sm border border-border bg-surface rounded p-4 overflow-auto">{JSON.stringify(data.batfish, null, 2)}</pre>
          )}
        </div>
      ) : null}
      {tab === "awx" ? (
        <div>
          {data.awx_jobs.length === 0 ? <p className="text-sm text-text-muted">No AWX jobs recorded yet.</p> : (
            <table aria-label="AWX jobs">
              <thead><tr><th>Job ID</th><th>Status</th><th>Step</th></tr></thead>
              <tbody>
                {data.awx_jobs.map((j, i) => (
                  <tr key={i}><td className="mono">{j.job_id ?? "—"}</td><td className="mono">{j.status ?? "—"}</td><td className="mono">{j.to_state ?? "—"}</td></tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="text-sm text-text-muted mt-2">Full stdout is in AWX. Job IDs above link the change to the AWX job.</p>
        </div>
      ) : null}
      {tab === "audit" ? <AuditTrail transitions={data.transitions} /> : null}
    </div>
  );
}
