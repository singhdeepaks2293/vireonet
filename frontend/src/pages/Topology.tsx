import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { request } from "../lib/api";
import { Button } from "../components/ui/Button";
import { formatBps, toNextData, utilColor, type Topology as TopologyT } from "./topologyData";

const NEXT_BASE = "/vendor/next-ui/1.0.1";

declare global {
  interface Window { nx?: any } // eslint-disable-line @typescript-eslint/no-explicit-any
}

let nextLoading: Promise<void> | null = null;

/** Load NeXt UI and the traffic link class once (vendored under public/vendor). */
export function loadNext(): Promise<void> {
  if (window.nx) return Promise.resolve();
  if (nextLoading) return nextLoading;
  nextLoading = new Promise<void>((resolve, reject) => {
    const css = document.createElement("link");
    css.rel = "stylesheet";
    css.href = `${NEXT_BASE}/css/next.min.css`;
    document.head.appendChild(css);
    // Classic scripts, in order: NeXt, then the traffic link class (which
    // must not be an ES module; see public/vendor/vireonet/next-traffic-link.js).
    const add = (src: string) => new Promise<void>((ok, fail) => {
      const js = document.createElement("script");
      js.src = src;
      js.onload = () => ok();
      js.onerror = () => fail(new Error(`could not load ${src}`));
      document.head.appendChild(js);
    });
    add(`${NEXT_BASE}/js/next.min.js`)
      .then(() => add("/vendor/vireonet/next-traffic-link.js"))
      .then(resolve, (e: Error) => { nextLoading = null; reject(e); });
  });
  return nextLoading;
}

export default function Topology() {
  const [site, setSite] = useState("");
  const query = useQuery({
    queryKey: ["topology", site],
    queryFn: () => request<TopologyT>(`/topology${site ? `?site=${encodeURIComponent(site)}` : ""}`),
    refetchInterval: 30000,
  });
  const stage = useRef<HTMLDivElement>(null);
  const topo = useRef<any>(null); // eslint-disable-line @typescript-eslint/no-explicit-any
  const [nextError, setNextError] = useState("");

  useEffect(() => {
    const data = query.data;
    if (!data || !stage.current) return;
    let cancelled = false;
    loadNext().then(() => {
      if (cancelled || !stage.current) return;
      const nx = window.nx;
      // Keep node positions across refreshes so the map does not re-shuffle.
      const positions: Record<string, { x: number; y: number }> = {};
      if (topo.current) {
        for (const n of data.nodes) {
          try { const p = topo.current.getNode(n.id)?.position(); if (p) positions[n.id] = { x: p.x, y: p.y }; } catch { /* new node */ }
        }
      }
      const nextData = toNextData(data, positions);
      if (!topo.current) {
        topo.current = new nx.graphic.Topology({
          adaptive: true,
          scalable: true,
          showIcon: true,
          identityKey: "id",
          dataProcessor: "force",
          enableSmartLabel: true,
          enableGradualScaling: true,
          supportMultipleLink: true,
          linkInstanceClass: "VireoTrafficLink",
          nodeConfig: { label: "model.name", iconType: "model.iconType" },
          linkConfig: {
            linkType: "parallel",
            color: "model.color",
            width: "model.width",
            sourcelabel: "model.sourcelabel",
            targetlabel: "model.targetlabel",
          },
        });
        const app = new nx.ui.Application();
        app.container(stage.current);
        topo.current.attach(app);
      } else if (Object.keys(positions).length) {
        topo.current.dataProcessor(null);
      }
      topo.current.data(nextData);
    }).catch((e: Error) => setNextError(e.message));
    return () => { cancelled = true; };
  }, [query.data]);

  const data = query.data;
  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <h2 className="text-lg">Topology</h2>
        <div className="flex gap-2 items-center">
          <label htmlFor="topo-site" className="text-sm text-text-muted">Site</label>
          <input id="topo-site" className="px-2 py-1 text-sm bg-surface border border-border rounded" placeholder="all" value={site} onChange={(e) => setSite(e.target.value)} />
          <Button variant="secondary" onClick={() => query.refetch()}>Refresh</Button>
        </div>
      </div>
      <p className="text-sm text-text-muted mb-2">
        Devices and cables from NetBox. Link labels show traffic leaving each end, from{" "}
        <span className="mono">{data?.telemetry ?? "no telemetry backend"}</span>; colour shows utilisation of the busier direction
        (<span style={{ color: utilColor(0.1) }}>under 50%</span>, <span style={{ color: utilColor(0.6) }}>50–80%</span>, <span style={{ color: utilColor(0.9) }}>80%+</span>, <span style={{ color: utilColor(null) }}>no data</span>). Refreshes every 30 s.
      </p>
      {query.isError ? <p className="text-sm text-danger" role="alert">Failed to load topology: {(query.error as Error).message}</p> : null}
      {nextError ? <p className="text-sm text-danger" role="alert">{nextError}</p> : null}
      {data?.errors.map((e) => <p key={e} className="text-sm text-warning" role="status">{e}</p>)}
      <div ref={stage} aria-label="Topology map" style={{ height: 520 }} className="border border-border rounded bg-white mb-4" />
      {data ? (
        <table aria-label="Links">
          <thead><tr><th>A end</th><th>B end</th><th>A → B</th><th>B → A</th><th>Speed</th><th>Utilisation</th></tr></thead>
          <tbody>
            {data.links.map((l) => (
              <tr key={l.id}>
                <td className="mono">{l.source} {l.source_interface}</td>
                <td className="mono">{l.target} {l.target_interface}</td>
                <td className="mono">{formatBps(l.a_to_b_bps)}</td>
                <td className="mono">{formatBps(l.b_to_a_bps)}</td>
                <td className="mono">{formatBps(l.speed_bps)}</td>
                <td><span className="mono" style={{ color: utilColor(l.utilization) }}>{l.utilization == null ? "—" : `${Math.round(l.utilization * 100)}%`}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </div>
  );
}
