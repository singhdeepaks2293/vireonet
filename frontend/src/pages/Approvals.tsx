import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { getCoreRowModel, useReactTable, type ColumnDef } from "@tanstack/react-table";
import { api, useChangeDetail, useChanges } from "../lib/api";
import type { ChangeResponse } from "../lib/api-types";
import { Button } from "../components/ui/Button";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { DataTable } from "../components/ui/DataTable";
import { useAuth } from "../lib/auth";

function waitingSince(createdAt: string | null | undefined): string {
  if (!createdAt) return "—";
  const ms = Date.now() - new Date(createdAt).getTime();
  const mins = Math.floor(ms / 60000);
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ${mins % 60}m`;
  return `${Math.floor(hours / 24)}d ${hours % 24}h`;
}

function Evidence({ id }: { id: string }) {
  const detail = useChangeDetail(id);
  if (detail.isLoading) return <p className="text-sm text-text-muted">Loading evidence…</p>;
  if (detail.isError) return <p className="text-sm text-danger">Failed to load evidence: {(detail.error as Error).message}</p>;
  if (!detail.data) return null;
  const batfish = detail.data.batfish as { passed?: boolean; failures?: unknown[] };
  const passedChecks = detail.data.checks.filter((c) => c.status === "pass").length;
  return (
    <div className="text-sm">
      <p>Checks: {passedChecks} of {detail.data.checks.length} passed</p>
      <p>Batfish gate: {batfish.passed === true ? "passed" : batfish.passed === false ? "failed" : "no result yet"}{Array.isArray(batfish.failures) && batfish.failures.length > 0 ? ` — ${batfish.failures.length} failure(s)` : ""}</p>
      <p>Diff: {detail.data.diff.filter((d) => d.type === "add").length} added line(s), {detail.data.diff.filter((d) => d.type === "remove").length} removed line(s)</p>
      <p><Link to={`/changes/${id}`} className="underline">Open full detail</Link></p>
    </div>
  );
}

export default function Approvals() {
  const [params, setParams] = useSearchParams();
  const { can } = useAuth();
  const [density, setDensity] = useState<"comfortable" | "compact">(() => (localStorage.getItem("vireonet-density") as "comfortable" | "compact" | null) ?? "comfortable");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<{ id: string; ticket: string; kind: "approve" | "reject" } | null>(null);
  const [reason, setReason] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const filters = useMemo(() => ({
    state: "ci_passed",
    page: Number(params.get("page") ?? "1"),
    page_size: 20,
    sort_by: "created_at",
    sort_dir: "asc" as const, // oldest waiting first
  }), [params]);
  const query = useChanges(filters);
  const rows = query.data?.items ?? [];
  const total = query.data?.total ?? 0;
  const pages = query.data?.pages ?? 0;

  const goPage = (page: number) => {
    const next = new URLSearchParams(params);
    next.set("page", String(page));
    setParams(next);
  };

  const columns = useMemo<ColumnDef<ChangeResponse>[]>(() => [
    { accessorKey: "ticket_number", header: "Ticket", size: 140, cell: ({ row }) => <Link to={`/changes/${row.original.id}`} className="mono underline">{row.original.ticket_number}</Link> },
    { accessorKey: "change_type", header: "Type", size: 160, cell: ({ row }) => <span className="mono">{row.original.change_type}</span> },
    { accessorKey: "devices", header: "Devices", size: 200, cell: ({ row }) => <span className="mono">{row.original.devices.join(", ")}</span> },
    { accessorKey: "requester", header: "Requester", size: 140 },
    { accessorKey: "created_at", header: "Waiting since", size: 170, cell: ({ row }) => `${row.original.created_at ? new Date(row.original.created_at).toLocaleString() : "—"} (${waitingSince(row.original.created_at)})` },
    {
      id: "evidence", header: "Evidence", size: 120,
      cell: ({ row }) => <Button variant="ghost" onClick={(e) => { e.stopPropagation(); setExpanded(expanded === row.original.id ? null : row.original.id); }}>{expanded === row.original.id ? "Hide" : "Show"}</Button>,
    },
    {
      id: "actions", header: "Actions", size: 200,
      cell: ({ row }) => (
        <span className="flex gap-1">
          <Button variant="primary" disabled={!can("approver")} onClick={(e) => { e.stopPropagation(); setReason(""); setConfirm({ id: row.original.id, ticket: row.original.ticket_number, kind: "approve" }); }}>Approve</Button>
          <Button variant="secondary" disabled={!can("approver")} onClick={(e) => { e.stopPropagation(); setReason(""); setConfirm({ id: row.original.id, ticket: row.original.ticket_number, kind: "reject" }); }}>Reject</Button>
        </span>
      ),
    },
  ], [can, expanded]);

  const table = useReactTable({ data: rows, columns, getCoreRowModel: getCoreRowModel(), columnResizeMode: "onChange", enableColumnResizing: true });

  const doConfirm = async () => {
    if (!confirm) return;
    setError(""); setNotice("");
    try {
      if (confirm.kind === "approve") {
        await api.signalApproval(confirm.id, { approved: true, reason: reason || "approved from approvals queue" });
        setNotice(`Approved ${confirm.ticket}.`);
      } else {
        await api.signalApproval(confirm.id, { approved: false, reason: reason || "rejected from approvals queue" });
        setNotice(`Rejected ${confirm.ticket}.`);
      }
      await query.refetch();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setConfirm(null);
    }
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg">Approvals</h2>
        <Button variant="secondary" onClick={() => { const next = density === "comfortable" ? "compact" : "comfortable"; setDensity(next); localStorage.setItem("vireonet-density", next); }}>
          Density: {density}
        </Button>
      </div>
      <p className="text-sm text-text-muted mb-4">Changes that passed CI and are waiting for approval, oldest first. Open the full detail for the complete diff, checks and audit trail before deciding.</p>
      {query.isLoading ? <p className="text-sm">Loading approvals…</p> : null}
      {query.isError ? (
        <div>
          <p className="text-sm text-danger" role="alert">Failed to load approvals: {(query.error as Error).message}</p>
          <Button variant="secondary" onClick={() => query.refetch()}>Retry</Button>
        </div>
      ) : null}
      {error ? <p className="text-sm text-danger" role="alert">{error}</p> : null}
      {notice ? <p className="text-sm">{notice}</p> : null}
      {!can("approver") ? <p className="text-sm text-text-muted mb-2">You are signed in as a role that cannot approve. Approve and Reject are disabled.</p> : null}
      {!query.isLoading && !query.isError ? (
        <>
          <DataTable table={table} density={density} emptyText="No changes waiting for approval." />
          {expanded ? (
            <div className="border border-border bg-surface rounded p-4 mt-2">
              <h3 className="text-sm mb-2">Evidence</h3>
              <Evidence id={expanded} />
            </div>
          ) : null}
          <div className="flex items-center justify-between mt-2 text-sm">
            <span>{total} change{total === 1 ? "" : "s"} waiting</span>
            <span className="flex gap-2">
              <Button variant="secondary" disabled={(filters.page ?? 1) <= 1} onClick={() => goPage((filters.page ?? 1) - 1)}>Previous</Button>
              <span className="py-1">Page {filters.page ?? 1} of {pages || 1}</span>
              <Button variant="secondary" disabled={(filters.page ?? 1) >= pages} onClick={() => goPage((filters.page ?? 1) + 1)}>Next</Button>
            </span>
          </div>
        </>
      ) : null}
      <ConfirmDialog
        open={confirm !== null}
        onOpenChange={(o) => { if (!o) setConfirm(null); }}
        title={confirm?.kind === "approve" ? "Approve change" : "Reject change"}
        description={
          confirm?.kind === "approve"
            ? `Approve ${confirm?.ticket}. This moves the change to approved and allows pre-check and apply to run. It does not apply config yet.`
            : `Reject ${confirm?.ticket}. This rejects the change and stops the workflow. No config will be applied.`
        }
        confirmLabel={confirm?.kind === "approve" ? "Approve" : "Reject"}
        onConfirm={doConfirm}
      >
        <span />
      </ConfirmDialog>
      {confirm !== null ? (
        <div className="mt-2">
          <label htmlFor="approval-reason" className="text-sm">Reason (optional, recorded in the audit trail)</label>
          <input id="approval-reason" className="w-full px-2 py-1 text-sm bg-surface border border-border rounded" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason" />
        </div>
      ) : null}
    </div>
  );
}
