import { useMemo, useState } from "react";
import { getCoreRowModel, useReactTable, type ColumnDef } from "@tanstack/react-table";
import { useAdaptersHealth, useSystemHealth } from "../lib/api";
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
