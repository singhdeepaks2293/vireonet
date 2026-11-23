import { useMemo, useState } from "react";
import { getCoreRowModel, useReactTable, type ColumnDef } from "@tanstack/react-table";
import { useAdaptersHealth, useSystemHealth } from "../lib/api";
import { useDriftSummary } from "../api/drift";
import type { AdapterHealth } from "../lib/api-types";
import { Button } from "../components/ui/Button";
import { DataTable } from "../components/ui/DataTable";

function statusBadge(status: string) {
  const map: Record<string, string> = {
    ok: "bg-success-bg text-success border-success",
    up: "bg-success-bg text-success border-success",
    down: "bg-danger-bg text-danger border-danger",
    degraded: "bg-warning-bg text-warning border-warning",
    not_configured: "bg-muted text-text-muted border-border",
    unknown: "bg-muted text-text-muted border-border",
  };
  const cls = map[status] ?? map.unknown;
  return <span className={`px-2 py-0.5 text-sm border rounded ${cls}`}>{status}</span>;
}

export default function SystemHealth() {
  const [density, setDensity] = useState<"comfortable" | "compact">(() => (localStorage.getItem("vireonet-density") as "comfortable" | "compact" | null) ?? "comfortable");
  const system = useSystemHealth();
  const adapters = useAdaptersHealth();
  const drift = useDriftSummary();

  const columns = useMemo<ColumnDef<AdapterHealth>[]>(() => [
    { accessorKey: "name", header: "Adapter", size: 140, cell: ({ row }) => <span className="mono">{row.original.name}</span> },
    { accessorKey: "status", header: "Status", size: 120, cell: ({ row }) => statusBadge(row.original.status) },
    { accessorKey: "latency_ms", header: "Latency", size: 100, cell: ({ row }) => row.original.latency_ms != null ? `${row.original.latency_ms} ms` : "—" },
    { accessorKey: "last_success", header: "Last success", size: 180, cell: ({ row }) => row.original.last_success ? new Date(row.original.last_success).toLocaleString() : "never" },
    { accessorKey: "capabilities", header: "Capabilities", size: 300, cell: ({ row }) => row.original.capabilities.join(", ") || "—" },
    { accessorKey: "detail", header: "Detail", size: 300, cell: ({ row }) => row.original.detail || "—" },
  ], []);
  const table = useReactTable({ data: adapters.data?.adapters ?? [], columns, getCoreRowModel: getCoreRowModel(), columnResizeMode: "onChange", enableColumnResizing: true });

  const downCount = (adapters.data?.adapters ?? []).filter((a) => a.status === "down").length;

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg">System health</h2>
        <div className="flex gap-2">
          <Button variant="secondary" onClick={() => { const next = density === "comfortable" ? "compact" : "comfortable"; setDensity(next); localStorage.setItem("vireonet-density", next); }}>
            Density: {density}
          </Button>
          <Button variant="secondary" onClick={() => { void system.refetch(); void adapters.refetch(); }}>Refresh</Button>
        </div>
      </div>

      <h3 className="text-sm mb-2">Components</h3>
      {system.isLoading ? <p className="text-sm">Loading system health…</p> : null}
      {system.isError ? (
        <div>
          <p className="text-sm text-danger" role="alert">Failed to load system health: {(system.error as Error).message}</p>
          <Button variant="secondary" onClick={() => system.refetch()}>Retry</Button>
        </div>
      ) : null}
      {!system.isLoading && !system.isError && system.data ? (
        <div className="mb-4">
          <p className="text-sm mb-2">Overall: {statusBadge(system.data.status)}</p>
          <table aria-label="System components">
            <thead><tr><th>Component</th><th>Status</th><th>Detail</th></tr></thead>
            <tbody>
              {system.data.components.map((c) => (
                <tr key={c.name}>
                  <td className="mono">{c.name}</td>
                  <td>{statusBadge(c.status)}</td>
                  <td>{c.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      <h3 className="text-sm mb-2">AWX and Execution Environment</h3>
      {!system.isLoading && !system.isError && system.data ? (
        <div className="mb-4">
          <table aria-label="AWX and Execution Environment">
            <thead><tr><th>Item</th><th>Status</th><th>Detail</th></tr></thead>
            <tbody>
              {system.data.awx ? (
                <tr>
                  <td className="mono">awx</td>
                  <td>{statusBadge(system.data.awx.status)}</td>
                  <td>{system.data.awx.detail}</td>
                </tr>
              ) : (
                <tr><td className="mono">awx</td><td>{statusBadge("unknown")}</td><td>No AWX status reported.</td></tr>
              )}
              {system.data.execution_environment ? (
                <tr>
                  <td className="mono">execution environment: {system.data.execution_environment.name}</td>
                  <td>{statusBadge(system.data.execution_environment.status)}</td>
                  <td>{system.data.execution_environment.detail}</td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      ) : null}

      <h3 className="text-sm mb-2">Lab backends</h3>
      {!system.isLoading && !system.isError && system.data ? (
        system.data.lab_backends && system.data.lab_backends.length > 0 ? (
          <div className="mb-4">
            <table aria-label="Lab backends">
              <thead><tr><th>Backend</th><th>Status</th><th>Capabilities</th><th>Detail</th></tr></thead>
              <tbody>
                {system.data.lab_backends.map((b) => (
                  <tr key={b.name}>
                    <td className="mono">{b.name}</td>
                    <td>{statusBadge(b.status)}</td>
                    <td>{(b.capabilities ?? []).join(", ") || "—"}</td>
                    <td>{b.detail || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-text-muted mb-4">No lab backends enabled.</p>
        )
      ) : null}

      <h3 className="text-sm mb-2">Drift scans</h3>
      {drift.isLoading ? <p className="text-sm">Loading drift summary…</p> : null}
      {drift.isError ? (
        <div>
          <p className="text-sm text-danger" role="alert">Failed to load drift summary: {(drift.error as Error).message}</p>
          <Button variant="secondary" onClick={() => drift.refetch()}>Retry</Button>
        </div>
      ) : null}
      {!drift.isLoading && !drift.isError && drift.data ? (
        <div className="mb-4">
          <p className="text-sm mb-2">{drift.data.total_open} open drift record{drift.data.total_open === 1 ? "" : "s"}.</p>
          {drift.data.last_scan_per_site.length > 0 ? (
            <table aria-label="Last drift scan per site">
              <thead><tr><th>Site</th><th>Status</th><th>Started</th><th>Scanned</th><th>With drift</th><th>Failed</th></tr></thead>
              <tbody>
                {drift.data.last_scan_per_site.map((s) => (
                  <tr key={s.site}>
                    <td className="mono">{s.site}</td>
                    <td>{s.status ?? "—"}</td>
                    <td>{s.started_at ? new Date(s.started_at).toLocaleString() : "—"}</td>
                    <td>{s.devices_scanned}</td>
                    <td>{s.devices_with_drift}</td>
                    <td>{s.devices_failed}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="text-sm text-text-muted mb-2">No drift scan has run yet.</p>
          )}
          <p className="text-sm text-text-muted">
            Never-scanned devices: {drift.data.never_scanned_devices.length > 0 ? drift.data.never_scanned_devices.join(", ") : "none"}
            {drift.data.never_scanned_best_effort ? " (best-effort: NetBox device list unavailable, so this is the scanned-device list only)" : ""}
          </p>
        </div>
      ) : null}

      <h3 className="text-sm mb-2">Adapters</h3>
      <p className="text-sm text-text-muted mb-2">{downCount > 0 ? `${downCount} adapter${downCount === 1 ? "" : "s"} down.` : "All adapters responding."} Refreshed every 30 seconds.</p>
      {adapters.isLoading ? <p className="text-sm">Loading adapter health…</p> : null}
      {adapters.isError ? (
        <div>
          <p className="text-sm text-danger" role="alert">Failed to load adapter health: {(adapters.error as Error).message}</p>
          <Button variant="secondary" onClick={() => adapters.refetch()}>Retry</Button>
        </div>
      ) : null}
      {!adapters.isLoading && !adapters.isError ? (
        <DataTable table={table} density={density} emptyText="No adapters configured." />
      ) : null}
    </div>
  );
}
