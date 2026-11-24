import { useMemo, useState } from "react";
import { getCoreRowModel, useReactTable, type ColumnDef } from "@tanstack/react-table";
import { useAdapterSettings } from "../lib/api";
import type { AdapterSettingResponse } from "../lib/api-types";
import { Link } from "react-router-dom";
import { Button } from "../components/ui/Button";
import { DataTable } from "../components/ui/DataTable";
import { AdapterConfig } from "../components/AdapterConfig";
import { useAuth } from "../lib/auth";

export default function Adapters() {
  const [density, setDensity] = useState<"comfortable" | "compact">(() => (localStorage.getItem("vireonet-density") as "comfortable" | "compact" | null) ?? "comfortable");
  const query = useAdapterSettings();
  const { can } = useAuth();
  const [tab, setTab] = useState<"status" | "configure">("status");
  const rows = query.data ?? [];

  const columns = useMemo<ColumnDef<AdapterSettingResponse>[]>(() => [
    { accessorKey: "category", header: "Category", size: 100, cell: ({ row }) => ({ core: "Core", itsm: "ITSM", telemetry: "Telemetry", controller: "Controller", logs: "Logs", git: "Git", inbound: "Inbound" } as Record<string, string>)[row.original.category ?? "core"] ?? "Core" },
    { accessorKey: "name", header: "Adapter", size: 200, cell: ({ row }) => <span className="mono">{row.original.name}</span> },
    { accessorKey: "enabled", header: "Enabled", size: 90, cell: ({ row }) => (row.original.enabled ? "yes" : "no") },
    { accessorKey: "mode", header: "Mode", size: 100, cell: ({ row }) => <span className="mono">{row.original.mode}</span> },
    { accessorKey: "endpoint", header: "Endpoint", size: 240, cell: ({ row }) => <span className="mono">{row.original.endpoint || "—"}</span> },
    { accessorKey: "timeout_seconds", header: "Timeout", size: 100, cell: ({ row }) => row.original.timeout_seconds != null ? `${row.original.timeout_seconds}s` : "—" },
    { accessorKey: "retry_max_attempts", header: "Retries", size: 90, cell: ({ row }) => row.original.retry_max_attempts ?? "—" },
    { accessorKey: "rate_limit_per_second", header: "Rate limit", size: 120, cell: ({ row }) => row.original.rate_limit_per_second != null ? `${row.original.rate_limit_per_second}/s` : "—" },
    { accessorKey: "capabilities", header: "Capabilities", size: 260, cell: ({ row }) => row.original.capabilities.join(", ") || "—" },
    { accessorKey: "note", header: "Note", size: 300, cell: ({ row }) => <span className="text-sm text-text-muted">{row.original.note || "—"}</span> },
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
      <div role="tablist" aria-label="Adapters" className="flex gap-1 border-b border-border mb-3">
        <button type="button" role="tab" aria-selected={tab === "status"} onClick={() => setTab("status")} className={`px-2 py-1 text-sm border-b-2 ${tab === "status" ? "border-accent" : "border-transparent text-text-muted"}`}>Status</button>
        {can("admin") ? <button type="button" role="tab" aria-selected={tab === "configure"} onClick={() => setTab("configure")} className={`px-2 py-1 text-sm border-b-2 ${tab === "configure" ? "border-accent" : "border-transparent text-text-muted"}`}>Configure</button> : null}
      </div>
      {tab === "configure" ? <AdapterConfig /> : null}
      <div hidden={tab !== "status"}>
      <p className="text-sm text-text-muted mb-4">
        Every adapter: core systems, ITSM tools, telemetry, vendor controllers, log platforms, Git providers and inbound webhook sources. No secrets are shown.
        Configure them with environment variables, or (admins) on the <strong>Configure</strong> tab. Using Gitea? Set it up here: <Link to="/setup/gitea" className="underline">Gitea setup</Link>.
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
          <p className="text-sm text-text-muted mt-2">{rows.filter((r) => r.enabled).length} enabled of {rows.length}. Core adapters, the ITSM provider (ServiceNow, Jira, Freshservice, BMC Helix) and the telemetry backend (Zabbix, Prometheus SNMP/gNMI, LibreNMS, PRTG, SolarWinds) are listed; "available" rows show the setting that enables them. Endpoint shows scheme, host and port only.</p>
        </>
      ) : null}
      </div>
    </div>
  );
}
