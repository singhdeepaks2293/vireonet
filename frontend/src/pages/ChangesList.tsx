import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { getCoreRowModel, getSortedRowModel, useReactTable, type ColumnDef, type SortingState } from "@tanstack/react-table";
import { api, useChanges, type ChangeFilters } from "../lib/api";
import type { ChangeResponse } from "../lib/api-types";
import { StateBadge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Input, Label } from "../components/ui/Input";
import { DataTable } from "../components/ui/DataTable";
import type { ChangeState } from "../lib/tokens";

const STATES = ["", "received", "validated", "planned", "ci_passed", "approved", "pre_checked", "applied", "post_checked", "closed", "rejected", "ci_failed", "pre_check_failed", "rolled_back", "failed"];
const TYPES = ["", "vlan_add", "vlan_remove", "acl_entry_add", "acl_entry_remove", "static_route_add", "static_route_remove", "interface_set", "ztp_device"];

function duration(started: string | null | undefined): string {
  if (!started) return "—";
  const ms = Date.now() - new Date(started).getTime();
  if (ms < 0) return "—";
  const mins = Math.floor(ms / 60000);
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ${mins % 60}m`;
  return `${Math.floor(hours / 24)}d ${hours % 24}h`;
}

function toCsv(rows: ChangeResponse[]): string {
  const header = "ticket_number,change_type,devices,state,requester,started\n";
  const esc = (v: string) => `"${v.replace(/"/g, '""')}"`;
  return header + rows.map((r) => [r.ticket_number, r.change_type, r.devices.join(";"), r.state, r.requester, r.created_at ?? ""].map(esc).join(",")).join("\n");
}

export default function ChangesList() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const [density, setDensity] = useState<"comfortable" | "compact">(() => (localStorage.getItem("vireonet-density") as "comfortable" | "compact" | null) ?? "comfortable");
  const [sorting, setSorting] = useState<SortingState>([]);
  const [exporting, setExporting] = useState(false);

  const filters: ChangeFilters = useMemo(() => ({
    state: params.get("state") ?? undefined,
    change_type: params.get("change_type") ?? undefined,
    device: params.get("device") ?? undefined,
    date_from: params.get("date_from") ?? undefined,
    date_to: params.get("date_to") ?? undefined,
    page: Number(params.get("page") ?? "1"),
    page_size: Number(params.get("page_size") ?? "20"),
    sort_by: sorting[0]?.id ?? params.get("sort_by") ?? "created_at",
    sort_dir: sorting[0] ? (sorting[0].desc ? "desc" : "asc") : ((params.get("sort_dir") as "asc" | "desc" | null) ?? "desc"),
  }), [params, sorting]);

  const query = useChanges(filters);
  const rows = query.data?.items ?? [];
  const total = query.data?.total ?? 0;
  const pages = query.data?.pages ?? 0;

  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value); else next.delete(key);
    next.set("page", "1");
    setParams(next);
  };

  const columns = useMemo<ColumnDef<ChangeResponse>[]>(() => [
    { accessorKey: "ticket_number", header: "Ticket", size: 140, cell: ({ row }) => <span className="mono">{row.original.ticket_number}</span> },
    { accessorKey: "change_type", header: "Type", size: 160, cell: ({ row }) => <span className="mono">{row.original.change_type}</span> },
    { accessorKey: "devices", header: "Devices", size: 200, cell: ({ row }) => <span className="mono">{row.original.devices.join(", ")}</span> },
    { accessorKey: "state", header: "State", size: 140, cell: ({ row }) => <StateBadge state={row.original.state as ChangeState} /> },
    { accessorKey: "requester", header: "Requester", size: 140 },
    { accessorKey: "created_at", header: "Started", size: 180, cell: ({ row }) => row.original.created_at ? new Date(row.original.created_at).toLocaleString() : "—" },
    { id: "duration", header: "Duration", size: 100, cell: ({ row }) => duration(row.original.created_at) },
  ], []);

  const table = useReactTable({
    data: rows, columns, getCoreRowModel: getCoreRowModel(), getSortedRowModel: getSortedRowModel(),
    state: { sorting }, onSortingChange: setSorting, manualSorting: true,
    columnResizeMode: "onChange", enableColumnResizing: true,
  });

  const exportCsv = async () => {
    setExporting(true);
    try {
      const all = await api.listChanges({ ...filters, page: 1, page_size: 1000 });
      const blob = new Blob([toCsv(all.items)], { type: "text/csv" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = "changes.csv"; a.click();
      URL.revokeObjectURL(url);
    } finally { setExporting(false); }
  };

  const goPage = (page: number) => {
    const next = new URLSearchParams(params);
    next.set("page", String(page));
    setParams(next);
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg">Changes</h2>
        <div className="flex gap-2">
          <Button variant="secondary" onClick={() => { const next = density === "comfortable" ? "compact" : "comfortable"; setDensity(next); localStorage.setItem("vireonet-density", next); }}>
            Density: {density}
          </Button>
          <Button variant="secondary" onClick={exportCsv} disabled={exporting}>Export CSV</Button>
        </div>
      </div>

      <div className="flex flex-wrap gap-4 mb-4 border border-border bg-surface rounded p-4" role="search" aria-label="Filter changes">
        <div>
          <Label htmlFor="filter-state">State</Label>
          <select id="filter-state" className="px-2 py-1 text-sm bg-surface border border-border rounded" value={params.get("state") ?? ""} onChange={(e) => setFilter("state", e.target.value)}>
            {STATES.map((s) => <option key={s} value={s}>{s === "" ? "All states" : s}</option>)}
          </select>
        </div>
        <div>
          <Label htmlFor="filter-type">Type</Label>
          <select id="filter-type" className="px-2 py-1 text-sm bg-surface border border-border rounded" value={params.get("change_type") ?? ""} onChange={(e) => setFilter("change_type", e.target.value)}>
            {TYPES.map((t) => <option key={t} value={t}>{t === "" ? "All types" : t}</option>)}
          </select>
        </div>
        <div>
          <Label htmlFor="filter-device">Device</Label>
          <Input id="filter-device" className="mono" placeholder="leaf-01" value={params.get("device") ?? ""} onChange={(e) => setFilter("device", e.target.value)} />
        </div>
        <div>
          <Label htmlFor="filter-from">From</Label>
          <Input id="filter-from" type="date" value={params.get("date_from") ?? ""} onChange={(e) => setFilter("date_from", e.target.value)} />
        </div>
        <div>
          <Label htmlFor="filter-to">To</Label>
          <Input id="filter-to" type="date" value={params.get("date_to") ?? ""} onChange={(e) => setFilter("date_to", e.target.value)} />
        </div>
        <div className="flex items-end">
          <Button variant="ghost" onClick={() => setParams({})}>Clear filters</Button>
        </div>
      </div>

      {query.isLoading ? <p className="text-sm">Loading changes…</p> : null}
      {query.isError ? (
        <div>
          <p className="text-sm text-danger" role="alert">Failed to load changes: {(query.error as Error).message}</p>
          <Button variant="secondary" onClick={() => query.refetch()}>Retry</Button>
        </div>
      ) : null}
      {!query.isLoading && !query.isError ? (
        <>
          <div
            onKeyDown={(e) => {
              const target = e.target as HTMLElement;
              const row = target.closest("tr");
              if (!row) return;
              if (e.key === "Enter") {
                const id = row.getAttribute("data-id");
                if (id) navigate(`/changes/${id}`);
              }
              if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                e.preventDefault();
                const rows = Array.from(document.querySelectorAll("tbody tr"));
                const idx = rows.indexOf(row);
                const next = e.key === "ArrowDown" ? rows[idx + 1] : rows[idx - 1];
                (next as HTMLElement | undefined)?.focus();
              }
            }}
            onClick={(e) => {
              const row = (e.target as HTMLElement).closest("tr");
              const id = row?.getAttribute("data-id");
              if (id) navigate(`/changes/${id}`);
            }}
          >
            <DataTable table={table} density={density} emptyText="No changes match these filters." />
          </div>
          <div className="flex items-center justify-between mt-2 text-sm">
            <span>{total} change{total === 1 ? "" : "s"}</span>
            <span className="flex gap-2">
              <Button variant="secondary" disabled={(filters.page ?? 1) <= 1} onClick={() => goPage((filters.page ?? 1) - 1)}>Previous</Button>
              <span className="py-1">Page {filters.page ?? 1} of {pages || 1}</span>
              <Button variant="secondary" disabled={(filters.page ?? 1) >= pages} onClick={() => goPage((filters.page ?? 1) + 1)}>Next</Button>
            </span>
          </div>
        </>
      ) : null}
    </div>
  );
}
