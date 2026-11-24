import { useState } from "react";
import { Button } from "../components/ui/Button";
import { Label } from "../components/ui/Input";
import { useAuth } from "../lib/auth";
import { Bars, Scatter, Sparkline, clusterColor } from "../components/MiniCharts";
import { ForecastTab, LogSequencesTab, RiskTab, RootCauseTab, TicketTab } from "../components/NeuralTabs";
import {
  mlApi,
  type AnomalyResponse,
  type ClassResponse,
  type LogResponse,
  type ClusterResponse,
  type RegResponse,
  type RiskResponse,
  type RLResponse,
} from "../api/ml";

type Tab = "anomaly" | "logs" | "clustering" | "classification" | "regression" | "rl" | "nn-logs" | "nn-forecast" | "nn-risk" | "nn-root" | "nn-ticket";
const TABS: { id: Tab; label: string }[] = [
  { id: "anomaly", label: "Anomaly detection" },
  { id: "logs", label: "Log anomalies (show logging)" },
  { id: "clustering", label: "Clustering (k-means)" },
  { id: "classification", label: "Classification" },
  { id: "regression", label: "Regression" },
  { id: "rl", label: "Reinforcement learning" },
  { id: "nn-logs", label: "Neural: log sequences" },
  { id: "nn-forecast", label: "Neural: traffic forecast" },
  { id: "nn-risk", label: "Neural: change risk" },
  { id: "nn-root", label: "Neural: root cause (graph)" },
  { id: "nn-ticket", label: "Neural: ticket text" },
];
const SELECT = "px-2 py-1 text-sm bg-surface border border-border rounded";
const pct = (v: number) => `${(v * 100).toFixed(1)}%`;

function useRun<T>() {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const run = async (fn: () => Promise<T>) => {
    setBusy(true); setError("");
    try { setData(await fn()); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  return { data, error, busy, run };
}

function Source({ source, notes }: { source?: string; notes?: string[] }) {
  if (!source) return null;
  return (
    <p className="text-sm text-text-muted mb-2">
      Data: <span className="mono">{source}</span>
      {source === "synthetic" ? <span className="text-warning"> — synthetic training data, not your network's history.</span> : null}
      {notes?.length ? <span> {notes.join("; ")}</span> : null}
    </p>
  );
}

function Status({ busy, error }: { busy: boolean; error: string }) {
  return (
    <>
      {busy ? <p className="text-sm" role="status">Training…</p> : null}
      {error ? <p className="text-sm text-danger" role="alert">{error}</p> : null}
    </>
  );
}

function AnomalyTab() {
  const [algorithm, setAlgorithm] = useState("isolation_forest");
  const [hours, setHours] = useState(24);
  const r = useRun<AnomalyResponse>();
  return (
    <div>
      <p className="text-sm text-text-muted mb-2">
        Unsupervised. Finds traffic points that do not fit an interface's own normal pattern.
        Isolation Forest learns "normal" across all interfaces (each scaled to its own median); z-score is a transparent per-interface baseline;
        Local Outlier Factor is density-based and tends to miss short bursts on bursty traffic.
      </p>
      <div className="flex flex-wrap gap-4 items-end mb-4">
        <div><Label htmlFor="an-alg">Algorithm</Label>
          <select id="an-alg" className={SELECT} value={algorithm} onChange={(e) => setAlgorithm(e.target.value)}>
            <option value="isolation_forest">Isolation Forest</option><option value="zscore">Robust z-score</option><option value="local_outlier_factor">Local Outlier Factor</option>
          </select></div>
        <div><Label htmlFor="an-hours">Hours of history</Label>
          <input id="an-hours" type="number" min={1} max={336} className={SELECT} value={hours} onChange={(e) => setHours(Number(e.target.value))} /></div>
        <Button variant="primary" onClick={() => r.run(() => mlApi.anomalies({ algorithm, hours }))}>Detect anomalies</Button>
      </div>
      <Status busy={r.busy} error={r.error} />
      {r.data ? (
        <>
          <Source source={r.data.source} notes={r.data.notes} />
          <p className="text-sm mb-2">{r.data.anomalous_interfaces} of {r.data.interfaces} interfaces have anomalous traffic.</p>
          {r.data.results.filter((x) => x.anomalies.length).map((x) => {
            const marks = x.anomalies.map((a) => x.series.timestamps.indexOf(a.timestamp)).filter((i) => i >= 0);
            return (
              <div key={`${x.device}-${x.interface}`} className="border border-border rounded p-2 mb-2">
                <p className="text-sm"><span className="mono">{x.device} {x.interface}</span> · {x.anomalies.length} anomalous points · peak {pct(Math.max(...x.anomalies.map((a) => Math.max(a.in_util, a.out_util))))}</p>
                <Sparkline label={`Traffic for ${x.device} ${x.interface}`} a={x.series.in_util} b={x.series.out_util} marks={marks} />
                <p className="text-sm text-text-muted">Solid: in · dashed: out · red: anomaly</p>
              </div>
            );
          })}
        </>
      ) : null}
    </div>
  );
}

const KIND_LABEL: Record<string, string> = { burst: "Burst", critical: "Critical event", new_event: "New event", unusual_mix: "Unusual mix" };

function LogsTab() {
  const { can } = useAuth();
  const [hours, setHours] = useState(24);
  const [push, setPush] = useState(false);
  const r = useRun<LogResponse>();
  return (
    <div>
      <p className="text-sm text-text-muted mb-2">
        Collects <span className="mono">show logging</span> from every device, parses it with TextFSM (your templates, then ntc-templates), and finds
        anomalies three ways: <strong>bursts</strong> of one event type against the device's own normal (robust z-score),
        <strong> unusual mixes</strong> of events (Isolation Forest over time windows), and <strong>critical or new</strong> events.
        Optionally writes one summary per anomalous device to NetBox as a journal entry — never the raw logs.
      </p>
      <div className="flex flex-wrap gap-4 items-end mb-4">
        <div><Label htmlFor="lg-hours">Hours of logs</Label><input id="lg-hours" type="number" min={1} max={168} className={SELECT} value={hours} onChange={(e) => setHours(Number(e.target.value))} /></div>
        {can("operator") ? <label className="text-sm flex items-center gap-1"><input type="checkbox" checked={push} onChange={(e) => setPush(e.target.checked)} /> Write summaries to NetBox journal</label> : null}
        <Button variant="primary" onClick={() => r.run(() => mlApi.logs({ hours, window_minutes: 30, push_to_netbox: push }))}>Analyse logs</Button>
      </div>
      <Status busy={r.busy} error={r.error} />
      {r.data ? (
        <>
          <p className="text-sm mb-2">Source: <span className="mono">{r.data.source}</span> · {r.data.events} events parsed · {r.data.windows} windows of {r.data.window_minutes} min · {r.data.devices.filter((d) => d.status === "anomalous").length} of {r.data.devices.length} devices anomalous</p>
          <table aria-label="Devices" className="mb-4">
            <thead><tr><th>Device</th><th>Status</th><th>Events</th><th>Top events</th><th>Activity</th></tr></thead>
            <tbody>{r.data.devices.map((d) => (
              <tr key={d.device}>
                <td className="mono">{d.device}</td>
                <td className={d.status === "anomalous" ? "text-danger" : "text-success"}>{d.status}{d.incidents ? ` (${d.incidents})` : ""}</td>
                <td>{d.events}</td>
                <td className="mono text-sm">{d.top_events.slice(0, 3).map(([m, c]) => `${m} ×${c}`).join(", ")}</td>
                <td style={{ width: 200 }}><Sparkline label={`Log volume for ${d.device}`} a={r.data!.timeline.devices[d.device] ?? []} height={30} /></td>
              </tr>))}</tbody>
          </table>
          <h3 className="text-sm mb-2">Anomalies</h3>
          {r.data.incidents.length === 0 ? <p className="text-sm text-text-muted">No anomalies.</p> : r.data.incidents.map((i, n) => (
            <div key={n} className="border border-border rounded p-2 mb-2">
              <p className="text-sm"><span className="mono">{i.device}</span> · <strong>{KIND_LABEL[i.kind] ?? i.kind}</strong> · {i.explanation}{i.interfaces.length ? ` · ${i.interfaces.join(", ")}` : ""} · {new Date(i.window_start).toLocaleString()}</p>
              <pre className="mono text-sm text-text-muted whitespace-pre-wrap mt-1">{i.samples.join("\n")}</pre>
            </div>
          ))}
          {r.data.netbox.length ? (
            <p className="text-sm mt-2" role="status">NetBox: {r.data.netbox.map((x) => x.error ? `${x.device}: ${x.error}` : `${x.device} → journal (${x.kind})`).join(" · ")}</p>
          ) : null}
          <details className="mt-2"><summary className="text-sm cursor-pointer">Collection details (TextFSM)</summary>
            <table aria-label="Collection"><thead><tr><th>Device</th><th>Platform</th><th>Lines</th><th>Parsed</th><th>Unparsed</th><th>Template / note</th></tr></thead>
              <tbody>{Object.entries(r.data.collection).map(([d, c]) => (
                <tr key={d}><td className="mono">{d}</td><td className="mono">{c.platform}</td><td>{c.lines ?? "—"}</td><td>{c.parsed ?? "—"}</td><td>{c.unparsed ?? "—"}</td><td className="text-sm">{c.error ?? c.note ?? c.template}</td></tr>))}</tbody>
            </table>
          </details>
        </>
      ) : null}
    </div>
  );
}

function ClusteringTab() {
  const [k, setK] = useState<string>("auto");
  const r = useRun<ClusterResponse>();
  return (
    <div>
      <p className="text-sm text-text-muted mb-2">Unsupervised. Groups interfaces by traffic profile (mean, 95th percentile, variability, in/out balance). "Auto" picks k by silhouette score.</p>
      <div className="flex gap-4 items-end mb-4">
        <div><Label htmlFor="cl-k">Clusters (k)</Label>
          <select id="cl-k" className={SELECT} value={k} onChange={(e) => setK(e.target.value)}>
            <option value="auto">auto</option>{[2, 3, 4, 5, 6].map((n) => <option key={n} value={n}>{n}</option>)}
          </select></div>
        <Button variant="primary" onClick={() => r.run(() => mlApi.clusters({ k: k === "auto" ? null : Number(k), hours: 24 }))}>Run k-means</Button>
      </div>
      <Status busy={r.busy} error={r.error} />
      {r.data ? (
        <>
          <Source source={r.data.source} />
          <p className="text-sm mb-2">k = {r.data.k}, silhouette {r.data.silhouette ?? "—"}{r.data.k_search.length ? ` (tried ${r.data.k_search.map((s) => `${s.k}: ${s.silhouette}`).join(", ")})` : ""}</p>
          <Scatter label="Interfaces by mean in/out utilisation" xLabel="mean in" yLabel="mean out"
            points={r.data.points.map((p) => ({ x: p.mean_in, y: p.mean_out, group: p.cluster, title: `${p.device} ${p.interface}` }))} />
          <table aria-label="Clusters" className="mt-2">
            <thead><tr><th>Cluster</th><th>Profile</th><th>Size</th><th>Peak in/out</th><th>Members</th></tr></thead>
            <tbody>{r.data.clusters.map((c) => (
              <tr key={c.cluster}>
                <td><span style={{ color: clusterColor(c.cluster) }}>●</span> {c.cluster}</td><td>{c.label}</td><td>{c.size}</td>
                <td className="mono">{pct(c.center.peak_in)} / {pct(c.center.peak_out)}</td>
                <td className="mono text-sm">{c.members.slice(0, 6).map((m) => `${m.device} ${m.interface}`).join(", ")}{c.members.length > 6 ? ` +${c.members.length - 6}` : ""}</td>
              </tr>))}</tbody>
          </table>
        </>
      ) : null}
    </div>
  );
}

function ClassificationTab() {
  const r = useRun<ClassResponse>();
  const risk = useRun<RiskResponse>();
  const [form, setForm] = useState({ change_type: "static_route_add", devices: 12, platforms: 2, hour: 23, emergency: false });
  return (
    <div>
      <p className="text-sm text-text-muted mb-2">Supervised. Predicts whether a change will fail (roll back) from its type, size, vendor mix, timing and the devices' history. Compared on a held-out 25%; thresholds are tuned for F1 because failures are rare.</p>
      <Button variant="primary" onClick={() => r.run(() => mlApi.classification({}))}>Train classifiers</Button>
      <Status busy={r.busy} error={r.error} />
      {r.data ? (
        <div className="mt-2">
          <Source source={r.data.source} notes={r.data.notes} />
          <p className="text-sm mb-2">{r.data.rows} changes · failure rate {pct(r.data.failure_rate)} · test set {r.data.test_rows} · best: <span className="mono">{r.data.best}</span></p>
          <table aria-label="Classifiers">
            <thead><tr><th>Algorithm</th><th>ROC AUC</th><th>Recall</th><th>Precision</th><th>F1</th><th>Accuracy</th><th>Threshold</th></tr></thead>
            <tbody>{r.data.models.map((m) => (
              <tr key={m.algorithm}><td className="mono">{m.algorithm}</td><td>{m.roc_auc}</td><td>{m.recall}</td><td>{m.precision}</td><td>{m.f1}</td><td>{m.accuracy}</td><td>{m.threshold}</td></tr>))}</tbody>
          </table>
          <h3 className="text-sm mt-4 mb-1">What drives risk ({r.data.models[0].algorithm})</h3>
          <Bars label="Feature importance" items={r.data.models[0].top_features.map((f) => ({ name: f.feature, value: f.importance }))} />
        </div>
      ) : null}
      <h3 className="text-sm mt-4 mb-2">Score a proposed change</h3>
      <div className="flex flex-wrap gap-4 items-end">
        <div><Label htmlFor="rk-type">Change type</Label>
          <select id="rk-type" className={SELECT} value={form.change_type} onChange={(e) => setForm({ ...form, change_type: e.target.value })}>
            {["vlan_add", "vlan_remove", "acl_entry_add", "acl_entry_remove", "static_route_add", "static_route_remove", "interface_set"].map((t) => <option key={t}>{t}</option>)}
          </select></div>
        <div><Label htmlFor="rk-dev">Devices</Label><input id="rk-dev" type="number" min={1} className={SELECT} value={form.devices} onChange={(e) => setForm({ ...form, devices: Number(e.target.value) })} /></div>
        <div><Label htmlFor="rk-plat">Vendors/platforms</Label><input id="rk-plat" type="number" min={1} className={SELECT} value={form.platforms} onChange={(e) => setForm({ ...form, platforms: Number(e.target.value) })} /></div>
        <div><Label htmlFor="rk-hour">Hour (0-23)</Label><input id="rk-hour" type="number" min={0} max={23} className={SELECT} value={form.hour} onChange={(e) => setForm({ ...form, hour: Number(e.target.value) })} /></div>
        <label className="text-sm flex items-center gap-1"><input type="checkbox" checked={form.emergency} onChange={(e) => setForm({ ...form, emergency: e.target.checked })} /> Emergency</label>
        <Button variant="secondary" onClick={() => risk.run(() => mlApi.risk(form))}>Score</Button>
      </div>
      <Status busy={risk.busy} error={risk.error} />
      {risk.data ? (
        <p className="text-sm mt-2" role="status">
          Failure probability <strong>{pct(risk.data.failure_probability)}</strong> ({risk.data.model}, {risk.data.source} data). Suggested wave size: <strong>{risk.data.recommended_wave_size}</strong>. Advisory only.
        </p>
      ) : null}
    </div>
  );
}

const REGRESSORS = ["linear", "ridge", "lasso", "random_forest", "gradient_boosting", "knn", "svr"];

function RegressionTab() {
  const [target, setTarget] = useState("change_duration");
  const [selected, setSelected] = useState<string[]>(REGRESSORS);
  const r = useRun<RegResponse>();
  const best = r.data?.models[0];
  return (
    <div>
      <p className="text-sm text-text-muted mb-2">Supervised regression with several algorithms side by side: how long a change will take (minutes), or interface utilisation one hour ahead. Ranked by RMSE on a held-out 25%.</p>
      <div className="flex flex-wrap gap-4 items-end mb-2">
        <div><Label htmlFor="rg-target">Predict</Label>
          <select id="rg-target" className={SELECT} value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="change_duration">Change duration</option><option value="traffic_forecast">Traffic forecast (1 h ahead)</option>
          </select></div>
        <fieldset className="flex flex-wrap gap-2 text-sm"><legend className="text-sm text-text-muted">Algorithms</legend>
          {REGRESSORS.map((a) => (
            <label key={a} className="flex items-center gap-1"><input type="checkbox" checked={selected.includes(a)} onChange={(e) => setSelected(e.target.checked ? [...selected, a] : selected.filter((s) => s !== a))} />{a}</label>
          ))}
        </fieldset>
        <Button variant="primary" disabled={!selected.length} onClick={() => r.run(() => mlApi.regression({ target, algorithms: selected }))}>Train regressors</Button>
      </div>
      <Status busy={r.busy} error={r.error} />
      {r.data && best ? (
        <>
          <Source source={r.data.source} notes={r.data.notes} />
          <table aria-label="Regressors">
            <thead><tr><th>Algorithm</th><th>RMSE</th><th>MAE</th><th>R²</th></tr></thead>
            <tbody>{r.data.models.map((m) => <tr key={m.algorithm}><td className="mono">{m.algorithm}{m.algorithm === r.data!.best ? " (best)" : ""}</td><td>{m.rmse}</td><td>{m.mae}</td><td>{m.r2}</td></tr>)}</tbody>
          </table>
          <h3 className="text-sm mt-4 mb-1">Actual vs predicted ({best.algorithm})</h3>
          <Scatter label="Actual versus predicted" xLabel="actual" yLabel="predicted" diagonal points={best.sample.map((s) => ({ x: s.actual, y: s.predicted }))} />
        </>
      ) : null}
    </div>
  );
}

function RLTab() {
  const [episodes, setEpisodes] = useState(60000);
  const [blast, setBlast] = useState(4);
  const r = useRun<RLResponse>();
  return (
    <div>
      <p className="text-sm text-text-muted mb-2">
        Reinforcement learning (tabular Q-learning) in a simulated rollout: the agent picks a wave size for a change of N devices with a given risk,
        and is rewarded for finishing fast and penalised for devices hit by a failed wave. Advisory only: VireoNet's configured wave size is never changed.
      </p>
      <div className="flex flex-wrap gap-4 items-end mb-4">
        <div><Label htmlFor="rl-ep">Episodes</Label><input id="rl-ep" type="number" min={1000} step={1000} className={SELECT} value={episodes} onChange={(e) => setEpisodes(Number(e.target.value))} /></div>
        <div><Label htmlFor="rl-blast">Blast-radius penalty</Label><input id="rl-blast" type="number" min={0} step={0.5} className={SELECT} value={blast} onChange={(e) => setBlast(Number(e.target.value))} /></div>
        <Button variant="primary" onClick={() => r.run(() => mlApi.reinforcement({ episodes, blast_weight: blast }))}>Train agent</Button>
      </div>
      <Status busy={r.busy} error={r.error} />
      {r.data ? (
        <>
          <h3 className="text-sm mb-1">Learning curve (mean reward)</h3>
          <Sparkline label="Learning curve" a={r.data.learning_curve.map((c) => c.mean_reward - Math.min(...r.data!.learning_curve.map((x) => x.mean_reward)))} />
          <h3 className="text-sm mt-4 mb-1">Learned policy: wave size</h3>
          <table aria-label="Wave size policy">
            <thead><tr><th>Devices</th>{[...new Set(r.data.policy.map((p) => p.risk))].map((rk) => <th key={rk}>risk {rk}</th>)}</tr></thead>
            <tbody>{[...new Set(r.data.policy.map((p) => p.devices))].map((d) => (
              <tr key={d}><td className="mono">{d}</td>{r.data!.policy.filter((p) => p.devices === d).map((p) => <td key={p.risk} className="mono">{p.wave_size}</td>)}</tr>
            ))}</tbody>
          </table>
        </>
      ) : null}
    </div>
  );
}

export default function MachineLearning() {
  const [tab, setTab] = useState<Tab>("anomaly");
  return (
    <div>
      <h2 className="text-lg mb-1">Intelligence</h2>
      <p className="text-sm text-text-muted mb-4">Models train on your change history and interface telemetry when there is enough of it. Results are advisory: nothing here changes a device or a workflow.</p>
      <div className="flex flex-wrap gap-1 border-b border-border mb-4" role="tablist" aria-label="Intelligence tabs">
        {TABS.map((t) => (
          <button key={t.id} type="button" role="tab" aria-selected={tab === t.id} onClick={() => setTab(t.id)}
            className={`px-2 py-1 text-sm border-b-2 ${tab === t.id ? "border-accent text-text" : "border-transparent text-text-muted"}`}>{t.label}</button>
        ))}
      </div>
      {tab === "anomaly" ? <AnomalyTab /> : tab === "logs" ? <LogsTab /> : tab === "clustering" ? <ClusteringTab /> : tab === "classification" ? <ClassificationTab /> : tab === "regression" ? <RegressionTab /> : tab === "rl" ? <RLTab />
        : tab === "nn-logs" ? <LogSequencesTab /> : tab === "nn-forecast" ? <ForecastTab /> : tab === "nn-risk" ? <RiskTab /> : tab === "nn-root" ? <RootCauseTab /> : <TicketTab />}
    </div>
  );
}
