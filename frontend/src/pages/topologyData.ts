/** Topology API -> NeXt UI data. Pure, so it can be tested without NeXt. */

export interface TopologyNode { id: string; name: string; platform: string; role: string; site: string; status?: string }
export interface TopologyLink {
  id: number; source: string; target: string; source_interface: string; target_interface: string;
  a_to_b_bps: number | null; b_to_a_bps: number | null; speed_bps: number | null; utilization: number | null;
}
export interface Topology { site: string | null; nodes: TopologyNode[]; links: TopologyLink[]; telemetry: string | null; errors: string[] }

export function formatBps(bps: number | null | undefined): string {
  if (bps == null) return "—";
  const units = ["bps", "Kbps", "Mbps", "Gbps", "Tbps"];
  let v = bps;
  let u = 0;
  while (v >= 1000 && u < units.length - 1) { v /= 1000; u += 1; }
  return `${v >= 100 ? v.toFixed(0) : v.toFixed(1)} ${units[u]}`;
}

/** Link colour by utilisation of the busier direction. */
export function utilColor(util: number | null): string {
  if (util == null) return "#9aa0a6";
  if (util >= 0.8) return "#d93025";
  if (util >= 0.5) return "#f29900";
  return "#1e8e3e";
}

const ICONS: Record<string, string> = {
  spine: "switch", leaf: "switch", access: "switch", switch: "switch",
  router: "router", firewall: "firewall", server: "server", host: "host",
};

export function iconFor(node: TopologyNode): string {
  if (ICONS[node.role]) return ICONS[node.role];
  if (["fortios", "panos"].includes(node.platform)) return "firewall";
  if (["sros", "junos"].includes(node.platform)) return "router";
  return "switch";
}

export function toNextData(t: Topology, positions: Record<string, { x: number; y: number }> = {}) {
  return {
    nodes: t.nodes.map((n) => ({
      id: n.id,
      name: n.name,
      iconType: iconFor(n),
      platform: n.platform,
      role: n.role,
      ...(positions[n.id] ?? {}),
    })),
    links: t.links.map((l) => ({
      id: l.id,
      source: l.source,
      target: l.target,
      color: utilColor(l.utilization),
      width: l.utilization == null ? 1 : 1 + Math.round(l.utilization * 5),
      // Source end shows traffic leaving the source (A -> B), target end B -> A.
      // The labels rotate with the link, so the arrows point along it.
      sourcelabel: l.a_to_b_bps == null ? "" : `→ ${formatBps(l.a_to_b_bps)}`,
      targetlabel: l.b_to_a_bps == null ? "" : `${formatBps(l.b_to_a_bps)} ←`,
    })),
  };
}
