import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { getCoreRowModel, useReactTable, type ColumnDef } from "@tanstack/react-table";
import { api, useZtpDevices } from "../lib/api";
import type { ZtpDeviceResponse } from "../lib/api-types";
import { Button } from "../components/ui/Button";
import { Input, Label } from "../components/ui/Input";
import { DataTable } from "../components/ui/DataTable";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { useAuth } from "../lib/auth";

const STAGES = ["", "registered", "reservation_created", "callback_received", "identity_verified", "day1_applied", "active", "failed", "identity_failed", "callback_timeout", "day1_failed"];

export default function ZtpDevices() {
  const [params, setParams] = useSearchParams();
  const { can } = useAuth();
  const [density, setDensity] = useState<"comfortable" | "compact">(() => (localStorage.getItem("vireonet-density") as "comfortable" | "compact" | null) ?? "comfortable");
  const [confirm, setConfirm] = useState<{ serial: string; kind: "retry" | "reset" } | null>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const filters = useMemo(() => ({
    stage: params.get("stage") ?? undefined,
    site: params.get("site") ?? undefined,
    platform: params.get("platform") ?? undefined,
    page: Number(params.get("page") ?? "1"),
    page_size: 20,
  }), [params]);
  const query = useZtpDevices(filters);
  const rows = query.data?.items ?? [];
  const total = query.data?.total ?? 0;
  const pages = query.data?.pages ?? 0;

  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value); else next.delete(key);
    next.set("page", "1");
    setParams(next);
  };
  const goPage = (page: number) => {
    const next = new URLSearchParams(params);
    next.set("page", String(page));
    setParams(next);
  };

  const columns = useMemo<ColumnDef<ZtpDeviceResponse>[]>(() => [
    { accessorKey: "serial", header: "Serial", size: 140, cell: ({ row }) => <span className="mono">{row.original.serial}</span> },
    { accessorKey: "hostname", header: "Hostname", size: 120, cell: ({ row }) => <span className="mono">{row.original.hostname}</span> },
    { accessorKey: "model", header: "Model", size: 140, cell: ({ row }) => <span className="mono">{row.original.model}</span> },
    { accessorKey: "site", header: "Site", size: 80 },
    { accessorKey: "netbox_status", header: "NetBox status", size: 120, cell: ({ row }) => <span className="mono">{row.original.netbox_status}</span> },
    { accessorKey: "stage", header: "ZTP stage", size: 160, cell: ({ row }) => <span className="mono">{row.original.stage}</span> },
    { accessorKey: "callback_at", header: "Last callback", size: 170, cell: ({ row }) => row.original.callback_at ? new Date(row.original.callback_at).toLocaleString() : "—" },
    { accessorKey: "dhcp_reservation", header: "DHCP reservation", size: 260, cell: ({ row }) => <span className="mono">{row.original.dhcp_reservation || "—"}</span> },
    {
      id: "actions", header: "Actions", size: 160,
      cell: ({ row }) => (
        <span className="flex gap-1">
          <Button variant="secondary" disabled={!can("operator")} onClick={(e) => { e.stopPropagation(); setConfirm({ serial: row.original.serial, kind: "retry" }); }}>Retry</Button>
          <Button variant="secondary" disabled={!can("operator")} onClick={(e) => { e.stopPropagation(); setConfirm({ serial: row.original.serial, kind: "reset" }); }}>Reset</Button>
        </span>
      ),
    },
  ], [can]);

  const table = useReactTable({ data: rows, columns, getCoreRowModel: getCoreRowModel(), columnResizeMode: "onChange", enableColumnResizing: true });

  const doConfirm = async () => {
    if (!confirm) return;
    setError(""); setNotice("");
    try {
      if (confirm.kind === "retry") {
        const res = await api.retryZtp(confirm.serial);
        setNotice(`Retry started for ${confirm.serial}. New one-time token issued; give it to the device on its next boot. Token is shown once in the API response and is not stored here.`);
        void res;
      } else {
        await api.resetZtp(confirm.serial);
        setNotice(`Reset ${confirm.serial} to registered.`);
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
        <h2 className="text-lg">ZTP devices</h2>
        <Button variant="secondary" onClick={() => { const next = density === "comfortable" ? "compact" : "comfortable"; setDensity(next); localStorage.setItem("vireonet-density", next); }}>
          Density: {density}
        </Button>
      </div>
      <div className="flex flex-wrap gap-4 mb-4 border border-border bg-surface rounded p-4" role="search" aria-label="Filter ZTP devices">
        <div>
          <Label htmlFor="ztp-stage">Stage</Label>
          <select id="ztp-stage" className="px-2 py-1 text-sm bg-surface border border-border rounded" value={params.get("stage") ?? ""} onChange={(e) => setFilter("stage", e.target.value)}>
            {STAGES.map((s) => <option key={s} value={s}>{s === "" ? "All stages" : s}</option>)}
          </select>
        </div>
        <div>
          <Label htmlFor="ztp-site">Site</Label>
          <Input id="ztp-site" placeholder="dc1" value={params.get("site") ?? ""} onChange={(e) => setFilter("site", e.target.value)} />
        </div>
        <div>
          <Label htmlFor="ztp-platform">Platform</Label>
          <select id="ztp-platform" className="px-2 py-1 text-sm bg-surface border border-border rounded" value={params.get("platform") ?? ""} onChange={(e) => setFilter("platform", e.target.value)}>
            <option value="">All platforms</option>
            <option value="ios">ios</option>
            <option value="ios-xe">ios-xe</option>
            <option value="nxos">nxos</option>
          </select>
        </div>
        <div className="flex items-end"><Button variant="ghost" onClick={() => setParams({})}>Clear filters</Button></div>
      </div>
      {query.isLoading ? <p className="text-sm">Loading ZTP devices…</p> : null}
      {query.isError ? (
        <div>
          <p className="text-sm text-danger" role="alert">Failed to load ZTP devices: {(query.error as Error).message}</p>
          <Button variant="secondary" onClick={() => query.refetch()}>Retry</Button>
        </div>
      ) : null}
      {error ? <p className="text-sm text-danger" role="alert">{error}</p> : null}
      {notice ? <p className="text-sm">{notice}</p> : null}
      {!query.isLoading && !query.isError ? (
        <>
          <DataTable table={table} density={density} emptyText="No ZTP devices match these filters." />
          <div className="flex items-center justify-between mt-2 text-sm">
            <span>{total} device{total === 1 ? "" : "s"}</span>
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
        title={confirm?.kind === "retry" ? "Retry ZTP" : "Reset ZTP device"}
        description={
          confirm?.kind === "retry"
            ? `Retry ${confirm?.serial}. This issues a new one-time token and restarts the ZTP workflow for this serial. The device must boot again with the new token.`
            : `Reset ${confirm?.serial}. This returns the device to registered and clears its token, IP, version and callback. It does not delete the NetBox device.`
        }
        confirmLabel={confirm?.kind === "retry" ? "Retry" : "Reset"}
        onConfirm={doConfirm}
      >
        <span />
      </ConfirmDialog>
    </div>
  );
}
