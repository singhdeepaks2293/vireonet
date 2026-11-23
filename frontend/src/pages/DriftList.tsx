import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { getCoreRowModel, getSortedRowModel, useReactTable, type ColumnDef, type SortingState } from "@tanstack/react-table";
import { driftApi, useDriftList, type DriftFilters } from "../api/drift";
import type { DriftRecord } from "../lib/api-types";
import { Button } from "../components/ui/Button";
import { DataTable } from "../components/ui/DataTable";
import { Input, Label } from "../components/ui/Input";

const KINDS = ["", "unmanaged", "intent", "state", "compliance"];
const SEVERITIES = ["", "low", "medium", "high", "critical"];
const STATUSES = ["", "detected", "under_review", "reverting", "merge_pending", "exception_active", "exception_expired", "resolved"];

export function severityBadge(severity: string) {
  const map: Record<string, string> = {
    critical: "bg-danger-bg text-danger border-danger",
    high: "bg-warning-bg text-warning border-warning",
    medium: "bg-info-bg text-info border-info",
    low: "bg-muted text-text-muted border-border",
  };
  const cls = map[severity] ?? map.low;
  return <span className={`px-2 py-0.5 text-sm border rounded ${cls}`}>{severity}</span>;
}

export function ageOf(firstSeen: string | null | undefined): string {
  if (!firstSeen) return "—";
  const ms = Date.now() - new Date(firstSeen).getTime();
  if (ms < 0) return "—";
  const mins = Math.floor(ms / 60000);
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ${mins % 60}m`;
  return `${Math.floor(hours / 24)}d ${hours % 24}h`;
}

export function driftToCsv(rows: DriftRecord[]): string {
  const header = "id,device,kind,severity,status,first_seen,last_seen,attributed_actor,linked_ticket\n";
  const esc = (v: string) => `"${v.replace(/"/g, '""')}"`;
  return header + rows.map((r) => [
    r.id, r.device, r.kind, r.severity, r.status,
    r.first_seen ?? "", r.last_seen ?? "", r.attributed_actor ?? "", r.linked_ticket ?? "",
  ].map(esc).join(",")).join("\n");
}

export default function DriftList() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const [density, setDensity] = useState<"comfortable" | "compact">(() => (localStorage.getItem("vireonet-density") as "comfortable" | "compact" | null) ?? "comfortable");
  const [sorting, setSorting] = useState<SortingState>([]);
  const [exporting, setExporting] = useState(false);
  const [scanMessage, setScanMessage] = useState("");

  const filters: DriftFilters = useMemo(() => ({
    device: params.get("device") ?? undefined,
    kind: params.get("kind") ?? undefined,
    severity: params.get("severity") ?? undefined,
    status: params.get("status") ?? undefined,
    site: params.get("site") ?? undefined,
    limit: Number(params.get("limit") ?? "50"),
    offset: Number(params.get("offset") ?? "0"),
  }), [params]);

  const query = useDriftList(filters);
  const rows = query.data?.items ?? [];
  const total = query.data?.total ?? 0;

  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value); else next.delete(key);
    next.set("offset", "0");
    setParams(next);
  };

  const columns = useMemo<ColumnDef<DriftRecord>[]>(() => [
    { accessorKey: "device", header: "Device", size: 160, cell: ({ row }) => <span className="mono">{row.original.device}</span> },
    { accessorKey: "kind", header: "Kind", size: 110, cell: ({ row }) => <span className="mono">{row.original.kind}</span> },
    { accessorKey: "severity", header: "Severity", size: 110, cell: ({ row }) => severityBadge(row.original.severity) },
    { accessorKey: "status", header: "Status", size: 150, cell: ({ row }) => <span className="mono">{row.original.status}</span> },
    { accessorKey: "first_seen", header: "First seen", size: 180, cell: ({ row }) => row.original.first_seen ? new Date(row.original.first_seen).toLocaleString() : "—" },
    { accessorKey: "last_seen", header: "Last seen", size: 180, cell: ({ row }) => row.original.last_seen ? new Date(row.original.last_seen).toLocaleString() : "—" },
    { accessorKey: "attributed_actor", header: "Owner", size: 140, cell: ({ row }) => row.original.attributed_actor ?? "—" },
    { id: "age", header: "Age", size: 90, cell: ({ row }) => ageOf(row.original.first_seen) },
  ], []);

  const table = useReactTable({
    data: rows, columns, getCoreRowModel: getCoreRowModel(), getSortedRowModel: getSortedRowModel(),
    state: { sorting }, onSortingChange: setSorting, manualSorting: true,
    columnResizeMode: "onChange", enableColumnResizing: true,
  });

  const exportCsv = async () => {
    setExporting(true);
    try {
      const all = await driftApi.listDrift({ ...filters, limit: 1000, offset: 0 });
      const blob = new Blob([driftToCsv(all.items)], { type: "text/csv" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = "drift.csv"; a.click();
      URL.revokeObjectURL(url);
    } finally { setExporting(false); }
  };

  const startScan = async () => {
    setScanMessage("");
    try {
      const result = await driftApi.startScan({ site: filters.site });
      setScanMessage(result.workflow_id ? `Scan started: ${result.workflow_id}` : `Scan queued: ${result.scan_id ?? ""}`);
      await query.refetch();
    } catch (err) {
      setScanMessage(`Scan failed: ${(err as Error).message}`);
    }
  };

  const goPage = (offset: number) => {
    const next = new URLSearchParams(params);
    next.set("offset", String(offset));
    setParams(next);
  };

  const onRowKeyDown = (e: React.KeyboardEvent, id: string) => {
    if (e.key === "Enter") navigate(`/drift/${id}`);
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg">Drift</h2>
        <div className="flex gap-2">
          <Button variant="secondary" onClick={() => { const next = density === "comfortable" ? "compact" : "comfortable"; setDensity(next); localStorage.setItem("vireonet-density", next); }}>
            Density: {density}
          </Button>
          <Button variant="secondary" onClick={exportCsv} disabled={exporting}>Export CSV</Button>
          <Button variant="primary" onClick={startScan}>Start scan</Button>
        </div>
      </div>
      {scanMessage ? <p className="text-sm mb-2" role="status">{scanMessage}</p> : null}

      <div className="flex flex-wrap gap-4 mb-4 border border-border bg-surface rounded p-4" role="search" aria-label="Filter drift">
        <div>
          <Label htmlFor="filter-device">Device</Label>
          <Input id="filter-device" value={params.get("device") ?? ""} onChange={(e) => setFilter("device", e.target.value)} placeholder="leaf-01" />
        </div>
        <div>
          <Label htmlFor="filter-site">Site</Label>
          <Input id="filter-site" value={params.get("site") ?? ""} onChange={(e) => setFilter("site", e.target.value)} placeholder="dc1" />
        </div>
        <div>
          <Label htmlFor="filter-kind">Kind</Label>
          <select id="filter-kind" className="px-2 py-1 text-sm bg-surface border border-border rounded" value={params.get("kind") ?? ""} onChange={(e) => setFilter("kind", e.target.value)}>
            {KINDS.map((k) => <option key={k} value={k}>{k === "" ? "All kinds" : k}</option>)}
          </select>
        </div>
        <div>
          <Label htmlFor="filter-severity">Severity</Label>
          <select id="filter-severity" className="px-2 py-1 text-sm bg-surface border border-border rounded" value={params.get("severity") ?? ""} onChange={(e) => setFilter("severity", e.target.value)}>
            {SEVERITIES.map((s) => <option key={s} value={s}>{s === "" ? "All severities" : s}</option>)}
          </select>
        </div>
        <div>
          <Label htmlFor="filter-status">Status</Label>
          <select id="filter-status" className="px-2 py-1 text-sm bg-surface border border-border rounded" value={params.get("status") ?? ""} onChange={(e) => setFilter("status", e.target.value)}>
            {STATUSES.map((s) => <option key={s} value={s}>{s === "" ? "All statuses" : s}</option>)}
          </select>
        </div>
      </div>

      {query.isLoading ? <p className="text-sm">Loading drift…</p> : null}
      {query.isError ? (
        <div>
          <p className="text-sm text-danger" role="alert">Failed to load drift: {(query.error as Error).message}</p>
          <Button variant="secondary" onClick={() => query.refetch()}>Retry</Button>
        </div>
      ) : null}
      {!query.isLoading && !query.isError ? (
        <div
          onClick={(e) => {
            const target = e.target as HTMLElement;
            const row = target.closest("tr[data-id]");
            if (row) navigate(`/drift/${row.getAttribute("data-id") ?? ""}`);
          }}
          onKeyDown={(e) => {
            const target = e.target as HTMLElement;
            const row = target.closest("tr[data-id]");
            if (row && e.key === "Enter") onRowKeyDown(e, row.getAttribute("data-id") ?? "");
          }}
        >
          <DataTable table={table} density={density} emptyText="No drift records match these filters." />
        </div>
      ) : null}
      <div className="flex items-center gap-2 mt-2">
        <p className="text-sm text-text-muted">{total} record{total === 1 ? "" : "s"}</p>
        <Button variant="secondary" disabled={(filters.offset ?? 0) === 0} onClick={() => goPage(Math.max(0, (filters.offset ?? 0) - (filters.limit ?? 50)))}>Previous</Button>
        <Button variant="secondary" disabled={(filters.offset ?? 0) + rows.length >= total} onClick={() => goPage((filters.offset ?? 0) + (filters.limit ?? 50))}>Next</Button>
      </div>
    </div>
  );
}
