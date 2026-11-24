import { useState } from "react";
import { useParams } from "react-router-dom";
import { driftApi, useConfigCommits, useDriftDetail } from "../api/drift";
import { Button } from "../components/ui/Button";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { Input, Label } from "../components/ui/Input";
import { severityBadge } from "./DriftList";

type Tab = "diff-baseline" | "diff-intent" | "state" | "compliance" | "batfish" | "attribution" | "decisions";
type ActionKind = "rollback" | "merge" | "exception";

const TABS: { id: Tab; label: string }[] = [
  { id: "diff-baseline", label: "Diff vs baseline" },
  { id: "diff-intent", label: "Diff vs intent" },
  { id: "state", label: "State findings" },
  { id: "compliance", label: "Compliance" },
  { id: "batfish", label: "Batfish impact" },
  { id: "attribution", label: "Attribution" },
  { id: "decisions", label: "Decisions / Audit" },
];

const CONFIRM_TEXT: Record<ActionKind, string> = {
  rollback:
    "This starts a normal change that restores the device's config from Git (the target " +
    "you chose: the main/master head by default, which is the approved running config) " +
    "(backup, pre-check, dry-run, apply, post-check; automatic rollback if apply or " +
    "post-check fails). The record resolves only when a re-collect confirms the running " +
    "config matches the baseline. The device is locked for the duration.",
  merge:
    "This creates a branch drift/<device>/<record> and a merge request adding a per-device " +
    "vars override (netops/vars/device/<device>.yaml) that records the drifted config as " +
    "the new intent. It never changes a shared template. The record stays merge-pending " +
    "until the merge request is merged, when the baseline is updated; closing the request " +
    "unmerged returns the record to under review.",
  exception:
    "This keeps the current running config and tells detection to ignore this exact diff " +
    "hash until the expiry you set. Any further change (a different running hash) creates " +
    "a new drift record. On expiry the record returns to under review.",
};

export default function DriftDetail() {
  const { id } = useParams<{ id: string }>();
  const detail = useDriftDetail(id ?? "");
  const [tab, setTab] = useState<Tab>("diff-baseline");
  const [reason, setReason] = useState("");
  const [ticketRef, setTicketRef] = useState("");
  const [owner, setOwner] = useState("");
  const [expiry, setExpiry] = useState("");
  const [confirm, setConfirm] = useState<ActionKind | null>(null);
  const [message, setMessage] = useState("");
  const [acting, setActing] = useState(false);
  const [target, setTarget] = useState<"branch" | "previous" | "commit">("branch");
  const [commitSha, setCommitSha] = useState("");

  const record = detail.data?.record;
  const commits = useConfigCommits(record?.device);

  const doAction = async (kind: ActionKind) => {
    if (!id || !record) return;
    setActing(true);
    setMessage("");
    try {
      const body: { reason: string; ticket_ref: string; owner?: string; expiry?: string } = {
        reason,
        ticket_ref: ticketRef,
      };
      if (kind === "exception") {
        body.owner = owner;
        if (expiry) body.expiry = new Date(expiry).toISOString();
      }
      if (kind === "rollback") {
        (body as { target?: string }).target = target === "commit" ? commitSha : target;
      }
      const result = await driftApi.driftAction(id, kind, body);
      setMessage(`${kind} accepted: ${String(result.status ?? "")}`);
      await detail.refetch();
    } catch (err) {
      setMessage(`${kind} failed: ${(err as Error).message}`);
    } finally {
      setActing(false);
    }
  };

  if (detail.isLoading) return <p className="text-sm">Loading drift record…</p>;
  if (detail.isError) {
    return (
      <div>
        <p className="text-sm text-danger" role="alert">Failed to load drift record: {(detail.error as Error).message}</p>
        <Button variant="secondary" onClick={() => detail.refetch()}>Retry</Button>
      </div>
    );
  }
  if (!record || !detail.data) return <p className="text-sm">Drift record not found.</p>;

  const canAct = reason.trim() !== "" && ticketRef.trim() !== "";
  const exceptionReady = canAct && owner.trim() !== "" && expiry !== "";

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg">Drift {record.device}</h2>
        {severityBadge(record.severity)}
      </div>
      <p className="text-sm text-text-muted mb-4">
        {record.kind} · {record.status} · first seen {record.first_seen ? new Date(record.first_seen).toLocaleString() : "—"}
        {record.linked_ticket ? ` · ticket ${record.linked_ticket}` : ""}
      </p>

      <h3 className="text-sm mb-2">Status timeline</h3>
      {detail.data.audit.length === 0 ? (
        <p className="text-sm text-text-muted mb-4">No state moves recorded yet.</p>
      ) : (
        <ul className="mb-4">
          {detail.data.audit.map((a) => (
            <li key={a.id} className="text-sm mb-1">
              <span className="mono">{a.from_state} → {a.to_state}</span>
              {" — "}{a.actor}{a.created_at ? ` · ${new Date(a.created_at).toLocaleString()}` : ""}
              {a.reason ? ` · ${a.reason}` : ""}
            </li>
          ))}
        </ul>
      )}

      <div className="flex flex-wrap gap-2 mb-4" role="tablist" aria-label="Drift detail tabs">
        {TABS.map((t) => (
          <Button
            key={t.id}
            variant={tab === t.id ? "primary" : "secondary"}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </Button>
        ))}
      </div>

      {tab === "diff-baseline" ? (
        <div className="mb-4">
          <p className="text-sm text-text-muted mb-2">
            Baseline {record.baseline_ref ?? "—"} ({record.baseline_hash ?? "no hash"}) vs running ({record.running_hash ?? "no hash"}).
          </p>
          <pre className="text-sm bg-surface border border-border rounded p-4 overflow-auto whitespace-pre-wrap">{record.diff || "No diff stored."}</pre>
        </div>
      ) : null}
      {tab === "diff-intent" ? (
        <div className="mb-4">
          <p className="text-sm mb-2">
            No separate rendered-intent diff is stored on this record. The diff below is the
            unmanaged diff (running config vs stored baseline); an intent diff is produced
            only when a scan renders intent for this device.
          </p>
          <pre className="text-sm bg-surface border border-border rounded p-4 overflow-auto whitespace-pre-wrap">{record.diff || "No diff stored."}</pre>
        </div>
      ) : null}
      {tab === "state" ? (
        <div className="mb-4">
          {record.kind === "state" ? (
            <pre className="text-sm bg-surface border border-border rounded p-4 overflow-auto whitespace-pre-wrap">{record.diff || "No state detail stored."}</pre>
          ) : (
            <p className="text-sm text-text-muted">This is a {record.kind} record, not a state record — there are no NetBox-vs-collected-facts findings on it.</p>
          )}
        </div>
      ) : null}
      {tab === "compliance" ? (
        <div className="mb-4">
          {record.kind === "compliance" ? (
            <pre className="text-sm bg-surface border border-border rounded p-4 overflow-auto whitespace-pre-wrap">{record.diff || "No compliance detail stored."}</pre>
          ) : (
            <p className="text-sm text-text-muted">This is a {record.kind} record, not a compliance record — no compliance rule fired on it.</p>
          )}
        </div>
      ) : null}
      {tab === "batfish" ? (
        <div className="mb-4">
          <p className="text-sm text-text-muted">
            No Batfish reachability impact is stored on this record. Batfish impact is attached
            to a finding at scan time when the Batfish adapter is reachable; when it is not,
            detection keeps the finding and records no impact rather than failing the scan.
          </p>
        </div>
      ) : null}
      {tab === "attribution" ? (
        <div className="mb-4">
          <p className="text-sm mb-1">Attributed actor: {record.attributed_actor ?? "unknown (best-effort attribution)"}</p>
          <p className="text-sm mb-1">Attributed at: {record.attributed_at ? new Date(record.attributed_at).toLocaleString() : "—"}</p>
          <p className="text-sm">Attribution is best-effort: it links a change to the actor when the scan can prove it, and says unknown otherwise.</p>
        </div>
      ) : null}
      {tab === "decisions" ? (
        <div className="mb-4">
          {detail.data.decisions.length === 0 ? (
            <p className="text-sm text-text-muted">No decisions recorded yet.</p>
          ) : (
            <table aria-label="Decisions">
              <thead><tr><th>Action</th><th>Actor</th><th>Reason</th><th>Ticket</th><th>Expiry</th><th>When</th></tr></thead>
              <tbody>
                {detail.data.decisions.map((d) => (
                  <tr key={d.id}>
                    <td className="mono">{d.action}</td>
                    <td>{d.actor}</td>
                    <td>{d.reason}</td>
                    <td className="mono">{d.ticket_ref}</td>
                    <td>{d.expiry ? new Date(d.expiry).toLocaleString() : "—"}</td>
                    <td>{d.created_at ? new Date(d.created_at).toLocaleString() : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      ) : null}

      <h3 className="text-sm mb-2">Resolve this drift</h3>
      <p className="text-sm text-text-muted mb-2">
        Approver or admin only. Whoever made the manual change cannot approve their own
        exception or merge. A reason and a ServiceNow reference are required for every action.
      </p>
      <div className="flex flex-wrap gap-4 mb-4 border border-border bg-surface rounded p-4">
        <div>
          <Label htmlFor="drift-reason">Reason (required)</Label>
          <Input id="drift-reason" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why this action is correct" />
        </div>
        <div>
          <Label htmlFor="drift-ticket">ServiceNow reference (required)</Label>
          <Input id="drift-ticket" value={ticketRef} onChange={(e) => setTicketRef(e.target.value)} placeholder="RITM0001234" />
        </div>
        <div>
          <Label htmlFor="drift-owner">Exception owner</Label>
          <Input id="drift-owner" value={owner} onChange={(e) => setOwner(e.target.value)} placeholder="netops" />
        </div>
        <div>
          <Label htmlFor="drift-expiry">Exception expiry</Label>
          <Input id="drift-expiry" type="datetime-local" value={expiry} onChange={(e) => setExpiry(e.target.value)} />
        </div>
      </div>
      <div className="mb-4 border border-border bg-surface rounded p-4">
        <Label htmlFor="drift-rb-target">Roll back to (from Git)</Label>
        <select id="drift-rb-target" className="px-2 py-1 text-sm bg-surface border border-border rounded" value={target} onChange={(e) => setTarget(e.target.value as "branch" | "previous" | "commit")}>
          <option value="branch">Head of {commits.data?.branch ?? "main"}: the approved running config</option>
          <option value="previous">Last commit before the head</option>
          <option value="commit">A specific commit…</option>
        </select>
        {target === "commit" ? (
          <div className="mt-2">
            <Label htmlFor="drift-rb-commit">Commit</Label>
            <select id="drift-rb-commit" className="px-2 py-1 text-sm bg-surface border border-border rounded mono" value={commitSha} onChange={(e) => setCommitSha(e.target.value)}>
              <option value="">Choose a commit</option>
              {(commits.data?.commits ?? []).map((c) => (
                <option key={c.sha} value={c.sha}>{c.sha.slice(0, 10)} · {c.date.slice(0, 10)} · {c.message.split("\n")[0].slice(0, 60)}</option>
              ))}
            </select>
          </div>
        ) : null}
        {commits.isError ? (
          <p className="text-sm text-warning mt-2">Git history is unavailable ({(commits.error as Error).message}). The default rollback falls back to the stored baseline; other targets are refused.</p>
        ) : commits.data ? (
          <p className="text-sm text-text-muted mt-2"><span className="mono">{commits.data.repo}</span> · <span className="mono">{commits.data.path}</span> · {commits.data.commits.length} commits on {commits.data.branch}</p>
        ) : null}
      </div>
      {message ? <p className="text-sm mb-2" role="status">{message}</p> : null}
      <div className="flex gap-2">
        <ConfirmDialog open={confirm === "rollback"} onOpenChange={(o) => setConfirm(o ? "rollback" : null)} title="Roll back drift" description={CONFIRM_TEXT.rollback} confirmLabel="Roll back" onConfirm={() => doAction("rollback")}>
          <Button variant="primary" disabled={!canAct || acting || (target === "commit" && !commitSha)}>Roll back</Button>
        </ConfirmDialog>
        <ConfirmDialog open={confirm === "merge"} onOpenChange={(o) => setConfirm(o ? "merge" : null)} title="Merge drift into Git" description={CONFIRM_TEXT.merge} confirmLabel="Merge" onConfirm={() => doAction("merge")}>
          <Button variant="secondary" disabled={!canAct || acting}>Merge</Button>
        </ConfirmDialog>
        <ConfirmDialog open={confirm === "exception"} onOpenChange={(o) => setConfirm(o ? "exception" : null)} title="Grant exception" description={CONFIRM_TEXT.exception} confirmLabel="Grant exception" onConfirm={() => doAction("exception")}>
          <Button variant="secondary" disabled={!exceptionReady || acting}>Grant exception</Button>
        </ConfirmDialog>
      </div>
    </div>
  );
}
