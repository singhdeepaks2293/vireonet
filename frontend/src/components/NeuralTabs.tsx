import { useState } from "react";
import { request } from "../lib/api";
import { Button } from "./ui/Button";
import { Sparkline } from "./MiniCharts";

const post = <T,>(path: string, body: unknown) => request<T>(path, { method: "POST", body: JSON.stringify(body) });
const INPUT = "px-2 py-1 text-sm bg-surface border border-border rounded";
const pct = (v: number) => `${(v * 100).toFixed(0)}%`;
type Any = Record<string, unknown> & { [k: string]: any }; // eslint-disable-line @typescript-eslint/no-explicit-any

function useRun() {
  const [data, setData] = useState<Any | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const run = async (fn: () => Promise<Any>) => { setBusy(true); setError(""); try { setData(await fn()); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } };
  return { data, error, busy, run };
}

function ModelLine({ d, metric }: { d: Any; metric: string }) {
  return <p className="text-sm text-text-muted mb-2"><span className="mono">{String(d.model)}</span> · {metric} · advisory only</p>;
}

export function LogSequencesTab() {
  const r = useRun();
  return (
    <section aria-label="Log event sequences">
      <p className="text-sm mb-2">A neural network learns, from the older part of each device's log, which event normally comes next (DeepLog-style). Recent events it did not expect, or kinds of events never seen on that device, are flagged. It catches <strong>unusual order and new event types</strong>; pure bursts of the same event are on the <em>Log anomalies</em> tab.</p>
      <Button variant="primary" disabled={r.busy} onClick={() => r.run(() => post<Any>("/ml/neural/log-sequences", {}))}>{r.busy ? "Training…" : "Train and score"}</Button>
      {r.error ? <p role="alert" className="text-sm text-danger mt-2">{r.error}</p> : null}
      {r.data ? (
        <div className="mt-3">
          <ModelLine d={r.data} metric={`top-k accuracy ${pct(r.data.top_k_accuracy)}`} />
          <table aria-label="Sequence anomalies"><thead><tr><th>Device</th><th>Status</th><th>Unexpected (recent)</th><th>Usual</th><th>Why</th></tr></thead>
            <tbody>{(r.data.devices as Any[]).map((d) => (
              <tr key={d.device}><td className="mono">{d.device}</td><td>{d.anomalous ? <span className="text-danger">anomalous</span> : <span className="text-success">normal</span>}</td>
                <td>{pct(d.rate)} of {d.scored}</td><td>{pct(d.baseline_rate)}</td><td className="text-sm">{(d.reasons as string[]).join("; ") || "—"}</td></tr>
            ))}</tbody></table>
        </div>
      ) : null}
    </section>
  );
}

export function ForecastTab() {
  const r = useRun();
  const [sat, setSat] = useState(0.8);
  return (
    <section aria-label="Traffic forecast">
      <p className="text-sm mb-2">A neural network trained on 6-hour windows of interface utilisation forecasts the next 3 hours and says when a link would cross the saturation level. Its error is compared with a naive forecast ("stays as it is").</p>
      <label className="text-sm mr-2">Saturation <input aria-label="Saturation" className={`${INPUT} w-[80px]`} type="number" step="0.05" min="0.1" max="1" value={sat} onChange={(e) => setSat(Number(e.target.value))} /></label>
      <Button variant="primary" disabled={r.busy} onClick={() => r.run(() => post<Any>("/ml/neural/forecast", { saturation: sat }))}>{r.busy ? "Training…" : "Forecast"}</Button>
      {r.error ? <p role="alert" className="text-sm text-danger mt-2">{r.error}</p> : null}
      {r.data ? (
        <div className="mt-3">
          <ModelLine d={r.data} metric={`MAE ${r.data.mae} vs naive ${r.data.naive_mae} (${r.data.beats_naive ? "better" : "not better"})`} />
          <table aria-label="Forecasts"><thead><tr><th>Device</th><th>Interface</th><th>Dir</th><th>Now</th><th>Next 3 h</th><th>Peak</th><th>Saturates in</th></tr></thead>
            <tbody>{(r.data.series as Any[]).slice(0, 20).map((s, i) => (
              <tr key={i}><td className="mono">{s.device}</td><td className="mono">{s.interface}</td><td>{s.direction}</td><td>{pct(s.now)}</td>
                <td><Sparkline a={s.forecast as number[]} height={24} label={`${s.device} ${s.interface} forecast`} /></td><td>{pct(s.peak)}</td>
                <td>{s.saturates_in_minutes != null ? <span className="text-danger">{s.saturates_in_minutes} min</span> : "—"}</td></tr>
            ))}</tbody></table>
        </div>
      ) : null}
    </section>
  );
}

export function RiskTab() {
  const r = useRun();
  const [f, setF] = useState({ change_type: "vlan_add", devices: 4, platforms: 2, hour: 14, emergency: false, prior_failure_rate: 0.05 });
  const types = ["vlan_add", "vlan_remove", "acl_entry_add", "acl_entry_remove", "static_route_add", "static_route_remove", "interface_set"];
  return (
    <section aria-label="Change risk">
      <p className="text-sm mb-2">A neural network trained on past changes (or a realistic synthetic history until there are enough) scores how likely a change is to fail or roll back. Flows use it with <strong>Predict change risk</strong>, e.g. to ask for approval above 0.5.</p>
      <div className="flex flex-wrap gap-2 items-end mb-2">
        <label className="text-sm">Type <select aria-label="Change type" className={INPUT} value={f.change_type} onChange={(e) => setF({ ...f, change_type: e.target.value })}>{types.map((t) => <option key={t}>{t}</option>)}</select></label>
        {(["devices", "platforms", "hour"] as const).map((k) => <label key={k} className="text-sm">{k} <input aria-label={k} className={`${INPUT} w-[70px]`} type="number" value={f[k]} onChange={(e) => setF({ ...f, [k]: Number(e.target.value) })} /></label>)}
        <label className="text-sm">past failure rate <input aria-label="past failure rate" className={`${INPUT} w-[70px]`} type="number" step="0.01" value={f.prior_failure_rate} onChange={(e) => setF({ ...f, prior_failure_rate: Number(e.target.value) })} /></label>
        <label className="text-sm"><input type="checkbox" checked={f.emergency} onChange={(e) => setF({ ...f, emergency: e.target.checked })} /> emergency</label>
        <Button variant="primary" disabled={r.busy} onClick={() => r.run(() => post<Any>("/ml/neural/change-risk", f))}>{r.busy ? "Scoring…" : "Score risk"}</Button>
      </div>
      {r.error ? <p role="alert" className="text-sm text-danger">{r.error}</p> : null}
      {r.data ? (
        <div>
          <ModelLine d={r.data} metric={`held-out AUC ${r.data.auc} · base failure rate ${pct(r.data.base_rate)} · ${r.data.trained_on}`} />
          <p className="text-base">Risk <strong className={r.data.level === "high" ? "text-danger" : r.data.level === "medium" ? "text-warning" : "text-success"}>{pct(r.data.risk)} ({r.data.level})</strong></p>
          <p className="text-sm mt-1">What drives risk most: {(r.data.importances as Any[]).slice(0, 5).map((x) => x.feature).join(", ")}</p>
        </div>
      ) : null}
    </section>
  );
}

export function RootCauseTab() {
  const r = useRun();
  const [alarms, setAlarms] = useState("leaf-01, leaf-02, leaf-03");
  return (
    <section aria-label="Root cause">
      <p className="text-sm mb-2">Graph neural network (simplified graph convolution): alarms are spread over the topology links and a neural network, trained on simulated failures of <em>your</em> topology, ranks which device most likely caused them. A dead device is often silent: its neighbours alarm.</p>
      <label className="text-sm">Devices with alarms <input aria-label="Devices with alarms" className={`${INPUT} w-[360px]`} value={alarms} onChange={(e) => setAlarms(e.target.value)} /></label>{" "}
      <Button variant="primary" disabled={r.busy} onClick={() => r.run(() => post<Any>("/ml/neural/root-cause", { alarms: Object.fromEntries(alarms.split(",").map((d) => d.trim()).filter(Boolean).map((d) => [d, 1])) }))}>{r.busy ? "Ranking…" : "Find root cause"}</Button>
      {r.error ? <p role="alert" className="text-sm text-danger mt-2">{r.error}</p> : null}
      {r.data ? (
        <div className="mt-3">
          <ModelLine d={r.data} metric={`AUC ${r.data.auc} · ${r.data.trained_on}`} />
          <ol aria-label="Root-cause ranking" className="list-decimal pl-6">{(r.data.ranking as Any[]).slice(0, 5).map((x) => <li key={x.device} className="text-sm"><span className="mono">{x.device}</span> — {pct(x.probability)}{x.alarm ? " (alarming)" : " (silent)"}</li>)}</ol>
        </div>
      ) : null}
    </section>
  );
}

export function TicketTab() {
  const r = useRun();
  const [text, setText] = useState("pls add vlan 300 on leaf 1 and leaf 3 for the new web tier");
  return (
    <section aria-label="Ticket text">
      <p className="text-sm mb-2">Reads a free-text request and suggests the change type, the devices (it understands "leaf 1", "LEAF01", "leaf-01") and fields such as a VLAN id or version. Flows use it with <strong>Classify ticket text</strong>.</p>
      <textarea aria-label="Ticket text" className={`${INPUT} w-full`} rows={3} value={text} onChange={(e) => setText(e.target.value)} />
      <Button variant="primary" disabled={r.busy} onClick={() => r.run(() => post<Any>("/ml/neural/ticket", { text }))}>{r.busy ? "Reading…" : "Classify"}</Button>
      {r.error ? <p role="alert" className="text-sm text-danger mt-2">{r.error}</p> : null}
      {r.data ? (
        <div className="mt-3">
          <ModelLine d={r.data} metric={`held-out accuracy ${pct(r.data.accuracy)}`} />
          <p className="text-sm">Change type: <strong className="mono">{r.data.change_type}</strong> ({pct(r.data.confidence)}) · devices: <span className="mono">{(r.data.devices as string[]).join(", ") || "none found"}</span> · fields: <span className="mono">{JSON.stringify(r.data.fields)}</span></p>
        </div>
      ) : null}
    </section>
  );
}
