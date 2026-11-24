/** Small dependency-free SVG charts for the ML tab. Colours come from tokens. */

const W = 520;

export function Sparkline({ a, b, marks = [], height = 90, label }: { a: number[]; b?: number[]; marks?: number[]; height?: number; label: string }) {
  const n = Math.max(a.length, 1);
  const max = Math.max(0.0001, ...a, ...(b ?? []));
  const x = (i: number) => (i / Math.max(1, n - 1)) * (W - 8) + 4;
  const y = (v: number) => height - 4 - (v / max) * (height - 8);
  const path = (vals: number[]) => vals.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  return (
    <svg role="img" aria-label={label} viewBox={`0 0 ${W} ${height}`} width="100%" height={height} className="block">
      <path d={path(a)} fill="none" stroke="var(--color-accent)" strokeWidth="1.5" />
      {b ? <path d={path(b)} fill="none" stroke="var(--color-text-muted)" strokeWidth="1" strokeDasharray="3 2" /> : null}
      {marks.map((i) => <circle key={i} cx={x(i)} cy={y(Math.max(a[i] ?? 0, b?.[i] ?? 0))} r="3.5" fill="var(--color-danger)" />)}
    </svg>
  );
}

export function Bars({ items, label }: { items: { name: string; value: number }[]; label: string }) {
  const max = Math.max(0.0001, ...items.map((i) => i.value));
  return (
    <svg role="img" aria-label={label} viewBox={`0 0 ${W} ${items.length * 20 + 4}`} width="100%" height={items.length * 20 + 4}>
      {items.map((it, i) => (
        <g key={it.name} transform={`translate(0 ${i * 20 + 2})`}>
          <text x="0" y="13" fontSize="11" fill="var(--color-text-muted)">{it.name}</text>
          <rect x="180" y="3" height="12" width={((W - 240) * it.value) / max} fill="var(--color-accent)" />
          <text x={186 + ((W - 240) * it.value) / max} y="13" fontSize="11" fill="var(--color-text)">{it.value.toFixed(3)}</text>
        </g>
      ))}
    </svg>
  );
}

const PALETTE = ["var(--color-accent)", "var(--color-danger)", "var(--color-success)", "var(--color-warning)", "var(--color-text-muted)", "var(--color-info)"];

export function Scatter({ points, label, xLabel, yLabel, diagonal = false }: { points: { x: number; y: number; group?: number; title?: string }[]; label: string; xLabel: string; yLabel: string; diagonal?: boolean }) {
  const H = 240;
  const maxX = Math.max(0.0001, ...points.map((p) => p.x));
  const maxY = Math.max(0.0001, ...points.map((p) => p.y), diagonal ? maxX : 0);
  const m = Math.max(maxX, diagonal ? maxY : maxX);
  const sx = (v: number) => 36 + (v / (diagonal ? m : maxX)) * (W - 48);
  const sy = (v: number) => H - 24 - (v / (diagonal ? m : maxY)) * (H - 36);
  return (
    <svg role="img" aria-label={label} viewBox={`0 0 ${W} ${H}`} width="100%" height={H}>
      <line x1="36" y1={H - 24} x2={W - 8} y2={H - 24} stroke="var(--color-border)" />
      <line x1="36" y1="8" x2="36" y2={H - 24} stroke="var(--color-border)" />
      {diagonal ? <line x1={sx(0)} y1={sy(0)} x2={sx(m)} y2={sy(m)} stroke="var(--color-border)" strokeDasharray="4 3" /> : null}
      {points.map((p, i) => (
        <circle key={i} cx={sx(p.x)} cy={sy(p.y)} r="4" fill={PALETTE[(p.group ?? 0) % PALETTE.length]} fillOpacity="0.8">
          {p.title ? <title>{p.title}</title> : null}
        </circle>
      ))}
      <text x={W / 2} y={H - 6} fontSize="11" textAnchor="middle" fill="var(--color-text-muted)">{xLabel}</text>
      <text x="10" y={H / 2} fontSize="11" textAnchor="middle" fill="var(--color-text-muted)" transform={`rotate(-90 10 ${H / 2})`}>{yLabel}</text>
    </svg>
  );
}

export const clusterColor = (i: number) => PALETTE[i % PALETTE.length];
