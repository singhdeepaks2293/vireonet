import { useState } from "react";
import { useParams } from "react-router-dom";
import { api, downloadFile, useAdapterSettings, useChangeDetail } from "../lib/api";
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

// Must match what the workflow honours (temporal/workflow.py): abort works
// until apply starts; rollback works between apply and reconcile.
function legalActions(state: string): ("approve" | "abort" | "rollback")[] {
  if (state === "ci_passed") return ["approve", "abort"];
  if (["received", "validated", "planned", "approved", "pre_checked"].includes(state)) return ["abort"];
  if (["applied", "post_checked"].includes(state)) return ["rollback"];
  return [];
}

export default function ChangeDetail() {
  const { id = "" } = useParams();
  const detail = useChangeDetail(id);
  const adapters = useAdapterSettings();
  const fakeMode = (adapters.data ?? []).some((a) => a.mode === "fake");
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
      else if (kind === "abort") await api.abort(change.id);
      else await api.rollback(change.id);
      setActionDone(
        kind === "approve"
          ? "Approval sent. The workflow continues once the merge request is merged."
          : kind === "abort"
            ? "Abort sent. The change stops at the next checkpoint; nothing is applied. This page updates when it does."
            : "Rollback requested. The workflow restores the backups at its next checkpoint. This page updates when it finishes; check for rolled_back or rollback_failed.",
      );
      await detail.refetch();
    } catch (e) {
      setActionError((e as Error).message);
    }
  };

  const confirmText: Record<string, string> = {
    approve: `Approve ${change.ticket_number}. This moves the change to approved and allows pre-check and apply to run. It does not apply config yet.`,
    abort: `Abort ${change.ticket_number}. The workflow stops at its next checkpoint, before any device is touched, and releases its device locks. No config will be applied.`,
    rollback: `Roll back ${change.ticket_number}. The workflow runs the AWX rollback playbook to restore every device from its pre-change backup, as soon as the current step finishes. If the restore fails, the change moves to rollback_failed and needs manual action.`,
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
      {state === "rollback_failed" ? (
        <p className="text-sm text-danger mb-2" role="alert">
          Rollback failed. Devices may be partially changed. Device locks are still held; check the devices and the AWX rollback job, then release the locks from Locks and change windows.
        </p>
      ) : null}
      {fakeMode && ["planned", "ci_passed"].includes(change.state) && can("operator") ? (
        <p className="text-sm mb-2">
          <Button variant="secondary" onClick={async () => {
            setActionError(""); setActionDone("");
            try {
              await api.signalCi(change.id, { passed: true, reason: "demo: simulated CI pass" });
              await api.signalMerge(change.id);
              setActionDone("Demo: CI pass and merge sent. Approve the change when it reaches ci_passed.");
              await detail.refetch();
            } catch (e) { setActionError((e as Error).message); }
          }}>Demo: simulate CI pass + merge</Button>{" "}
          <span className="text-text-muted">Fake mode only. In production these come from your Git provider's webhooks.</span>
        </p>
      ) : null}
      {(data.intent as { window_start?: string }).window_start ? (() => {
        const w = data.intent as { window_start: string; window_end?: string; pre_approved?: boolean };
        const start = new Date(w.window_start);
        const waiting = ["approved", "ci_passed", "planned"].includes(change.state) && start.getTime() > Date.now();
        return (
          <p className="text-sm mb-2" role="status">
            Planned window: <span className="mono">{start.toLocaleString()}</span> – <span className="mono">{w.window_end ? new Date(w.window_end).toLocaleString() : "?"}</span>
            {w.pre_approved ? " · fully automatic (approved in the ITSM tool or at submission)" : " · manual approval"}
            {waiting ? <span className="text-warning"> · waiting for the planned start; nothing is pushed before it</span> : null}
          </p>
        );
      })() : null}
      {change.change_type === "config_push" ? (
        <p className="text-sm mb-2">
          Excel config push.{" "}
          <Button variant="secondary" onClick={() => downloadFile(`/changes/${change.id}/workbook`, `${change.ticket_number}-workbook.xlsx`).catch((e) => setActionError((e as Error).message))}>
            Download workbook
          </Button>{" "}
          <span className="text-text-muted">After pre-check it includes each device's pre-change running config in the backup sheet.</span>
        </p>
      ) : null}
      {actionError ? <p className="text-sm text-danger" role="alert">{actionError}</p> : null}
      {actionDone ? <p className="text-sm">{actionDone}</p> : null}

      <h3 className="text-sm mb-2">Execution</h3>
      <div className="mb-4">
        <table aria-label="Execution">
          <thead><tr><th>Item</th><th>Value</th></tr></thead>
          <tbody>
            <tr><td>Job type</td><td className="mono">{data.job_type ?? "playbook"}</td></tr>
            <tr>
              <td>AWX job</td>
              <td>
                {data.awx_job_id != null ? (
                  data.awx_job_url ? (
                    <a href={data.awx_job_url} target="_blank" rel="noreferrer" className="mono underline">{data.awx_job_id}</a>
                  ) : (
                    <span className="mono">{data.awx_job_id}</span>
                  )
                ) : "—"}
              </td>
            </tr>
            {data.script_name ? (
              <tr><td>Script</td><td><span className="mono">{data.script_name}</span>{data.script_sha ? <span className="mono text-text-muted"> @ {data.script_sha.slice(0, 12)}</span> : null}</td></tr>
            ) : null}
            <tr>
              <td>Reconciliation</td>
              <td>
                {data.reconciliation_status ?? "—"}
                {data.reconciliation_status === "reconciliation_pending" && data.drift_count != null && data.drift_count > 0 ? (
                  <span className="text-text-muted"> · {data.drift_count} drift record{data.drift_count === 1 ? "" : "s"} — an approver override is required before close.</span>
                ) : null}
              </td>
            </tr>
          </tbody>
        </table>
        {data.declared_changes && data.declared_changes.length > 0 ? (
          <table aria-label="Declared changes" className="mt-2">
            <thead><tr><th>Object</th><th>Identifier</th><th>Field</th><th>Old</th><th>New</th></tr></thead>
            <tbody>
              {data.declared_changes.map((c, i) => (
                <tr key={i}>
                  <td className="mono">{c.object_type}</td>
                  <td className="mono">{c.identifier}</td>
                  <td className="mono">{c.field}</td>
                  <td className="mono">{c.old_value || "—"}</td>
                  <td className="mono">{c.new_value || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="text-sm text-text-muted mt-2">No declared changes recorded for this change.</p>
        )}
      </div>

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
            <div>
              <p className="text-sm">
                {data.batfish.passed ? "Passed" : "Failed"} · {data.batfish.blocking ? "blocking" : "non-blocking (failures are warnings)"}
              </p>
              {data.batfish.warning ? <p className="text-sm" role="alert">{data.batfish.warning}</p> : null}
              {data.batfish.failures.length > 0 ? (
                <ul aria-label="Batfish failures">
                  {data.batfish.failures.map((f, i) => (
                    <li key={i} className="text-sm text-danger">{f}</li>
                  ))}
                </ul>
              ) : null}
              {data.batfish.differential?.reachability ? (
                <table aria-label="Reachability differential">
                  <thead><tr><th>Flows lost</th><th>Flows gained</th><th>Flows changed</th></tr></thead>
                  <tbody>
                    <tr>
                      <td className="mono">{data.batfish.differential.reachability.lost}</td>
                      <td className="mono">{data.batfish.differential.reachability.gained}</td>
                      <td className="mono">{data.batfish.differential.reachability.changed}</td>
                    </tr>
                  </tbody>
                </table>
              ) : (
                <p className="text-sm text-text-muted">No differential — no current snapshot was available.</p>
              )}
              {data.batfish.differential?.acl_impact && data.batfish.differential.acl_impact.length > 0 ? (
                <table aria-label="ACL impact">
                  <thead><tr><th>File</th><th>Changed line</th><th>Flows lost</th><th>Flows gained</th></tr></thead>
                  <tbody>
                    {data.batfish.differential.acl_impact.map((entry, i) => (
                      <tr key={i}>
                        <td className="mono">{entry.file}</td>
                        <td className="mono">{entry.line}</td>
                        <td className="mono">{entry.flows_lost}</td>
                        <td className="mono">{entry.flows_gained}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : null}
            </div>
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
