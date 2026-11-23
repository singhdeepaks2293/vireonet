import { useMemo, useState } from "react";
import { getCoreRowModel, useReactTable, type ColumnDef } from "@tanstack/react-table";
import { api, useChangeWindows, useLocks } from "../lib/api";
import type { LockResponse } from "../lib/api-types";
import { Button } from "../components/ui/Button";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { DataTable } from "../components/ui/DataTable";
import { useAuth } from "../lib/auth";

function ttlRemaining(expiresAt: string | null | undefined): string {
  if (!expiresAt) return "—";
  const ms = new Date(expiresAt).getTime() - Date.now();
  if (ms <= 0) return "expired";
  const mins = Math.floor(ms / 60000);
  if (mins < 60) return `${mins}m`;
  return `${Math.floor(mins / 60)}h ${mins % 60}m`;
}

export default function Locks() {
  const { can } = useAuth();
  const [density, setDensity] = useState<"comfortable" | "compact">(() => (localStorage.getItem("vireonet-density") as "comfortable" | "compact" | null) ?? "comfortable");
  const [confirm, setConfirm] = useState<LockResponse | null>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const locks = useLocks();
  const windows = useChangeWindows();
  const rows = locks.data ?? [];

  const columns = useMemo<ColumnDef<LockResponse>[]>(() => [
    { accessorKey: "device", header: "Device", size: 140, cell: ({ row }) => <span className="mono">{row.original.device}</span> },
    { accessorKey: "ticket_number", header: "Ticket", size: 140, cell: ({ row }) => <span className="mono">{row.original.ticket_number}</span> },
    { accessorKey: "holder", header: "Holder", size: 140 },
    { accessorKey: "acquired_at", header: "Acquired", size: 170, cell: ({ row }) => row.original.acquired_at ? new Date(row.original.acquired_at).toLocaleString() : "—" },
    { accessorKey: "expires_at", header: "Expires", size: 170, cell: ({ row }) => row.original.expires_at ? new Date(row.original.expires_at).toLocaleString() : "—" },
    { id: "ttl", header: "TTL remaining", size: 130, cell: ({ row }) => ttlRemaining(row.original.expires_at) },
    {
      id: "actions", header: "Actions", size: 120,
      cell: ({ row }) => (
        <Button variant="secondary" disabled={!can("operator")} onClick={(e) => { e.stopPropagation(); setConfirm(row.original); }}>Release</Button>
      ),
    },
  ], [can]);

  const table = useReactTable({ data: rows, columns, getCoreRowModel: getCoreRowModel(), columnResizeMode: "onChange", enableColumnResizing: true });

  const doRelease = async () => {
    if (!confirm) return;
    setError(""); setNotice("");
    try {
      await api.releaseLock(confirm.device);
      setNotice(`Released lock on ${confirm.device}.`);
      await locks.refetch();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setConfirm(null);
    }
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg">Locks and change windows</h2>
        <Button variant="secondary" onClick={() => { const next = density === "comfortable" ? "compact" : "comfortable"; setDensity(next); localStorage.setItem("vireonet-density", next); }}>
          Density: {density}
        </Button>
      </div>

      <h3 className="text-sm mb-2">Device locks</h3>
      <p className="text-sm text-text-muted mb-2">A lock is held per device while a change is applying. Locks expire after the TTL (default 30 minutes) and are stolen on the next acquire. Releasing a lock held by a running change may let that change apply without the lock.</p>
      {locks.isLoading ? <p className="text-sm">Loading locks…</p> : null}
      {locks.isError ? (
        <div>
          <p className="text-sm text-danger" role="alert">Failed to load locks: {(locks.error as Error).message}</p>
          <Button variant="secondary" onClick={() => locks.refetch()}>Retry</Button>
        </div>
      ) : null}
      {error ? <p className="text-sm text-danger" role="alert">{error}</p> : null}
      {notice ? <p className="text-sm">{notice}</p> : null}
      {!locks.isLoading && !locks.isError ? (
        <>
          <DataTable table={table} density={density} emptyText="No device locks held." />
          <p className="text-sm text-text-muted mt-2">{rows.length} lock{rows.length === 1 ? "" : "s"} held. Refreshed every 30 seconds.</p>
        </>
      ) : null}

      <h3 className="text-sm mt-4 mb-2">Change windows</h3>
      <p className="text-sm text-text-muted mb-2">Read from policies/default.yaml — the same file the validator enforces. Windows are read-only here; editing them is a config change in Git.</p>
      {windows.isLoading ? <p className="text-sm">Loading change windows…</p> : null}
      {windows.isError ? (
        <div>
          <p className="text-sm text-danger" role="alert">Failed to load change windows: {(windows.error as Error).message}</p>
          <Button variant="secondary" onClick={() => windows.refetch()}>Retry</Button>
        </div>
      ) : null}
      {!windows.isLoading && !windows.isError ? (
        <table aria-label="Change windows">
          <thead><tr><th>Days</th><th>Start</th><th>End</th><th>Timezone</th><th>In window now</th><th>Next window</th><th>Emergency bypass</th></tr></thead>
          <tbody>
            {(windows.data ?? []).map((w, i) => (
              <tr key={i}>
                <td>{w.days.join(", ")}</td>
                <td className="mono">{w.start}</td>
                <td className="mono">{w.end}</td>
                <td className="mono">{w.timezone}</td>
                <td>{w.currently_in_window ? "yes" : "no"}</td>
                <td className="mono">{w.next_window_start ? new Date(w.next_window_start).toLocaleString() : "—"}</td>
                <td>{w.emergency_bypasses_window ? "yes" : "no"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {!windows.isLoading && !windows.isError && (windows.data ?? []).length === 0 ? <p className="text-sm text-text-muted">No change windows configured.</p> : null}

      <ConfirmDialog
        open={confirm !== null}
        onOpenChange={(o) => { if (!o) setConfirm(null); }}
        title="Release lock"
        description={`Release the lock on ${confirm?.device} held by ${confirm?.ticket_number}. If that change is still running, it may then apply without the lock.`}
        confirmLabel="Release"
        onConfirm={doRelease}
      >
        <span />
      </ConfirmDialog>
    </div>
  );
}
