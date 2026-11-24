import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { controllersApi, useControllers } from "../api/controllers";

export default function Controllers() {
  const q = useControllers();
  const [sel, setSel] = useState<string | null>(null);
  const name = sel ?? q.data?.items[0]?.name ?? null;
  const devices = useQuery({ queryKey: ["controller-devices", name], queryFn: () => controllersApi.devices(name!), enabled: !!name });
  const images = useQuery({ queryKey: ["controller-images", name], queryFn: () => controllersApi.images(name!), enabled: !!name });
  if (q.isLoading) return <p className="text-sm">Loading controllers…</p>;
  if (q.isError) return <p className="text-sm text-danger" role="alert">Could not load controllers: {(q.error as Error).message}</p>;
  const items = q.data?.items ?? [];
  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-lg">Vendor controllers</h2>
      <p className="text-sm text-text-muted">
        Catalyst Center, CloudVision, Mist, Apstra, FortiManager, Panorama, Nokia NSP, ExtremeCloud IQ, Meraki and Aruba Central.
        This page is read-only: software upgrades and controller API calls run inside <strong>Flows</strong> (controller actions), where they are versioned, approved and logged.
        Configure controllers with <span className="mono">VIREONET_CONTROLLERS</span>.
      </p>
      {items.length === 0 ? <p className="text-sm text-text-muted">No controllers configured.</p> : (
        <table aria-label="Controllers">
          <thead><tr><th>Name</th><th>Vendor</th><th>Platforms</th><th>Upgrade</th><th>Status</th><th>Detail</th></tr></thead>
          <tbody>{items.map((c) => (
            <tr key={c.name} className={c.name === name ? "bg-highlight-soft" : ""}>
              <td><button type="button" className="text-accent mono" onClick={() => setSel(c.name)}>{c.name}</button>{c.fake ? <span className="text-[12px] text-text-muted"> (fake)</span> : null}</td>
              <td>{c.vendor}</td><td className="text-sm">{c.platforms.join(", ")}</td>
              <td className="text-sm">{c.native_upgrade ? "one step" : "distribute, then activate"}</td>
              <td><span className={c.status === "ok" ? "text-success" : "text-danger"}>{c.status}</span></td>
              <td className="text-sm text-text-muted">{c.detail}</td>
            </tr>
          ))}</tbody>
        </table>
      )}
      {name ? (
        <div className="grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))" }}>
          <section aria-label={`Devices on ${name}`}>
            <h3 className="text-sm font-semibold mb-1">Devices on {name}</h3>
            {devices.isError ? <p className="text-sm text-danger" role="alert">{(devices.error as Error).message}</p> : (
              <table><thead><tr><th>Device</th><th>Platform</th><th>Version</th><th>Model</th><th>Reachable</th></tr></thead>
                <tbody>{(devices.data?.items ?? []).map((d) => <tr key={d.id}><td className="mono">{d.name}</td><td>{d.platform}</td><td className="mono">{d.version}</td><td className="text-sm">{d.model}</td><td>{d.reachable ? "yes" : "no"}</td></tr>)}</tbody></table>
            )}
          </section>
          <section aria-label={`Software on ${name}`}>
            <h3 className="text-sm font-semibold mb-1">Software versions offered</h3>
            {images.isError ? <p className="text-sm text-danger" role="alert">{(images.error as Error).message}</p> : (
              <table><thead><tr><th>Version</th><th>Platform</th><th>Image</th></tr></thead>
                <tbody>{(images.data?.items ?? []).map((i) => <tr key={i.id}><td className="mono">{i.version}</td><td>{i.platform}</td><td className="mono text-sm">{i.name}</td></tr>)}</tbody></table>
            )}
          </section>
        </div>
      ) : null}
    </div>
  );
}
