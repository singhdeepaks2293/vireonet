import { useMemo, useState } from "react";
import { getCoreRowModel, useReactTable, type ColumnDef } from "@tanstack/react-table";
import { useAdapterSettings } from "../lib/api";
import type { AdapterSettingResponse } from "../lib/api-types";
import { Link } from "react-router-dom";
import { Button } from "../components/ui/Button";
import { DataTable } from "../components/ui/DataTable";

export default function Adapters() {
  const [density, setDensity] = useState<"comfortable" | "compact">(() => (localStorage.getItem("vireonet-density") as "comfortable" | "compact" | null) ?? "comfortable");
  const query = useAdapterSettings();
  const rows = query.data ?? [];

  const columns = useMemo<ColumnDef<AdapterSettingResponse>[]>(() => [
    { accessorKey: "name", header: "Adapter", size: 140, cell: ({ row }) => <span className="mono">{row.original.name}</span> },
    { accessorKey: "enabled", header: "Enabled", size: 90, cell: ({ row }) => (row.original.enabled ? "yes" : "no") },
    { accessorKey: "mode", header: "Mode", size: 100, cell: ({ row }) => <span className="mono">{row.original.mode}</span> },
    { accessorKey: "endpoint", header: "Endpoint", size: 240, cell: ({ row }) => <span className="mono">{row.original.endpoint || "—"}</span> },
    { accessorKey: "timeout_seconds", header: "Timeout", size: 100, cell: ({ row }) => row.original.timeout_seconds != null ? `${row.original.timeout_seconds}s` : "—" },
    { accessorKey: "retry_max_attempts", header: "Retries", size: 90, cell: ({ row }) => row.original.retry_max_attempts ?? "—" },
    { accessorKey: "rate_limit_per_second", header: "Rate limit", size: 120, cell: ({ row }) => row.original.rate_limit_per_second != null ? `${row.original.rate_limit_per_second}/s` : "—" },
    { accessorKey: "capabilities", header: "Capabilities", size: 300, cell: ({ row }) => row.original.capabilities.join(", ") || "—" },
  ], []);
  const table = useReactTable({ data: rows, columns, getCoreRowModel: getCoreRowModel(), columnResizeMode: "onChange", enableColumnResizing: true });

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg">Adapters</h2>
        <Button variant="secondary" onClick={() => { const next = density === "comfortable" ? "compact" : "comfortable"; setDensity(next); localStorage.setItem("vireonet-density", next); }}>
          Density: {density}
        </Button>
      </div>
      <p className="text-sm text-text-muted mb-4">
        Read-only. Adapter settings come from config and environment, and secrets come from Vault. No secrets are shown on this page.
        To change an adapter, update its config or Vault entry and restart the API and worker. Using Gitea? Set it up here: <Link to="/setup/gitea" className="underline">Gitea setup</Link>.
      </p>
      {query.isLoading ? <p className="text-sm">Loading adapter settings…</p> : null}
      {query.isError ? (
        <div>
          <p className="text-sm text-danger" role="alert">Failed to load adapter settings: {(query.error as Error).message}</p>
          <Button variant="secondary" onClick={() => query.refetch()}>Retry</Button>
        </div>
      ) : null}
      {!query.isLoading && !query.isError ? (
        <>
          <DataTable table={table} density={density} emptyText="No adapters configured." />
          <p className="text-sm text-text-muted mt-2">{rows.length} adapter{rows.length === 1 ? "" : "s"} configured. Endpoint shows scheme, host and port only.</p>
        </>
      ) : null}
    </div>
  );
}
