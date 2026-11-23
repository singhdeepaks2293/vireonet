import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { getCoreRowModel, useReactTable, type ColumnDef } from "@tanstack/react-table";
import { driftApi, useDriftList } from "../api/drift";
import type { DriftRecord } from "../lib/api-types";
import { Button } from "../components/ui/Button";
import { DataTable } from "../components/ui/DataTable";
import { severityBadge } from "./DriftList";

export function countdownTo(expiry: string | null | undefined, now: number): string {
  if (!expiry) return "—";
  const ms = new Date(expiry).getTime() - now;
  if (ms <= 0) return "expired";
  const mins = Math.floor(ms / 60000);
  if (mins < 60) return `${mins}m left`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ${mins % 60}m left`;
  return `${Math.floor(hours / 24)}d ${hours % 24}h left`;
}

export default function Exceptions() {
  const navigate = useNavigate();
  const [density, setDensity] = useState<"comfortable" | "compact">(() => (localStorage.getItem("vireonet-density") as "comfortable" | "compact" | null) ?? "comfortable");
  const [expiries, setExpiries] = useState<Record<string, string | null>>({});
  const [now, setNow] = useState(() => Date.now());
  const query = useDriftList({ status: "exception_active", limit: 100, offset: 0 });
  const rows = query.data?.items ?? [];

  // Expiry lives on the latest exception decision, not on the list row,
  // so fetch each record's detail once the list loads.
  useEffect(() => {
    let cancelled = false;
    if (rows.length === 0) return;
    void Promise.all(
      rows.map(async (r) => {
        try {
          const detail = await driftApi.getDrift(r.id);
          const latest = [...detail.decisions]
            .filter((d) => d.action === "exception")
            .sort((a, b) => String(b.created_at ?? "").localeCompare(String(a.created_at ?? "")))[0];
          return [r.id, latest?.expiry ?? null] as const;
        } catch {
          return [r.id, null] as const;
        }
      }),
    ).then((pairs) => {
      if (!cancelled) setExpiries(Object.fromEntries(pairs));
    });
    return () => {
      cancelled = true;
    };
  }, [rows]);

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 60000);
    return () => clearInterval(timer);
  }, []);

  const columns = useMemo<ColumnDef<DriftRecord>[]>(() => [
    { accessorKey: "device", header: "Device", size: 160, cell: ({ row }) => <span className="mono">{row.original.device}</span> },
    { accessorKey: "kind", header: "Kind", size: 110, cell: ({ row }) => <span className="mono">{row.original.kind}</span> },
    { accessorKey: "severity", header: "Severity", size: 110, cell: ({ row }) => severityBadge(row.original.severity) },
    { accessorKey: "attributed_actor", header: "Owner", size: 140, cell: ({ row }) => row.original.attributed_actor ?? "—" },
    { id: "expiry", header: "Expires", size: 160, cell: ({ row }) => {
      const expiry = expiries[row.original.id];
      return expiry ? new Date(expiry).toLocaleString() : "—";
    } },
    { id: "countdown", header: "Countdown", size: 130, cell: ({ row }) => countdownTo(expiries[row.original.id], now) },
    { id: "open", header: "", size: 90, cell: ({ row }) => <Button variant="secondary" onClick={() => navigate(`/drift/${row.original.id}`)}>Open</Button> },
  ], [expiries, now, navigate]);

  const table = useReactTable({ data: rows, columns, getCoreRowModel: getCoreRowModel(), columnResizeMode: "onChange", enableColumnResizing: true });

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg">Exceptions</h2>
        <Button variant="secondary" onClick={() => { const next = density === "comfortable" ? "compact" : "comfortable"; setDensity(next); localStorage.setItem("vireonet-density", next); }}>
          Density: {density}
        </Button>
      </div>
      <p className="text-sm text-text-muted mb-4">
        Active drift exceptions. Detection ignores the exact diff hash until the expiry;
        any further change creates a new drift record, and an expired exception returns
        the record to under review.
      </p>
      {query.isLoading ? <p className="text-sm">Loading exceptions…</p> : null}
      {query.isError ? (
        <div>
          <p className="text-sm text-danger" role="alert">Failed to load exceptions: {(query.error as Error).message}</p>
          <Button variant="secondary" onClick={() => query.refetch()}>Retry</Button>
        </div>
      ) : null}
      {!query.isLoading && !query.isError ? (
        <DataTable table={table} density={density} emptyText="No active exceptions." />
      ) : null}
    </div>
  );
}
