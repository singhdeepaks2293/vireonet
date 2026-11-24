import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import yaml from "js-yaml";
import { request } from "../lib/api";
import { Button } from "../components/ui/Button";
import { useAuth } from "../lib/auth";

type Any = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
const INPUT = "px-2 py-1 text-sm bg-surface text-text border border-border rounded";
const post = <T,>(path: string, body: unknown, method = "POST") => request<T>(path, { method, body: JSON.stringify(body) });
type Tab = "compare" | "snapshots" | "profiles";

function useSnapshots() {
  return useQuery({ queryKey: ["prepost-snapshots"], queryFn: () => request<{ items: Any[] }>("/prepost/snapshots"), refetchInterval: 15000 });
}
function useProfiles() {
  return useQuery({ queryKey: ["prepost-profiles"], queryFn: () => request<{ items: Any[] }>("/prepost/profiles") });
}
const snapLabel = (s: Any) => `${s.label.toUpperCase()} · ${s.profile} · ${(s.devices as string[]).join(", ")} · ${s.created_at ? new Date(s.created_at).toLocaleString() : ""}${s.ticket ? ` · ${s.ticket}` : ""}`;

function DiffView({ d }: { d: Any }) {
  const [raw, setRaw] = useState(!d.parsed.available);
  return (
    <article aria-label={`Diff ${d.device} ${d.command}`} className={`border rounded-[6px] p-2 mb-2 ${d.changed ? "border-warning" : "border-border"}`}>
      <div className="flex items-center gap-2 mb-1">
        <span className="mono text-sm font-semibold">{d.device}</span><span className="mono text-sm text-text-muted">{d.command}</span>
        {d.changed ? <span className="text-[12px] text-warning">changed</span> : <span className="text-[12px] text-success">no change</span>}
        <span className="flex-1" />
        <div role="group" aria-label="Diff view" className="inline-flex border border-border rounded overflow-hidden text-[12px]">
          <button type="button" aria-pressed={!raw} disabled={!d.parsed.available} onClick={() => setRaw(false)} className={`px-2 ${!raw ? "bg-highlight-soft" : ""}`}>Parsed (TextFSM)</button>
          <button type="button" aria-pressed={raw} onClick={() => setRaw(true)} className={`px-2 ${raw ? "bg-highlight-soft" : ""}`}>Raw</button>
        </div>
      </div>
      {!raw && d.parsed.available ? (
        <div className="text-sm">
          <p className="text-[12px] text-text-muted">Rows matched by {d.parsed.key.length ? <span className="mono">{d.parsed.key.join(" + ")}</span> : "whole row"}{d.parsed.ignored?.length ? <> · ignored: <span className="mono">{d.parsed.ignored.join(", ")}</span></> : null}{d.parsed.tolerances && Object.keys(d.parsed.tolerances).length ? <> · tolerances: <span className="mono">{JSON.stringify(d.parsed.tolerances)}</span></> : null}</p>
          {(d.parsed.rows_removed ?? []).map((r: Any, i: number) => <p key={`r${i}`} className="mono text-[12px] text-danger">− {JSON.stringify(r)}</p>)}
          {(d.parsed.rows_added ?? []).map((r: Any, i: number) => <p key={`a${i}`} className="mono text-[12px] text-success">+ {JSON.stringify(r)}</p>)}
          {d.parsed.changed?.length ? (
            <table aria-label="Changed fields" className="mt-1"><thead><tr><th>Row</th><th>Field</th><th>Pre</th><th>Post</th></tr></thead>
              <tbody>{(d.parsed.changed as Any[]).flatMap((c) => Object.entries(c.fields as Any).map(([f, v]) => (
                <tr key={`${c.key}-${f}`}><td className="mono">{c.key}</td><td className="mono">{f}</td><td className="mono text-danger">{JSON.stringify((v as Any).pre)}</td><td className="mono text-success">{JSON.stringify((v as Any).post)}</td></tr>
              )))}</tbody></table>
          ) : null}
          {!d.changed ? <p className="text-[12px] text-text-muted">Identical after ignoring the listed fields and tolerances.</p> : null}
        </div>
      ) : (
        <pre className="mono text-[12px] bg-bg p-2 overflow-x-auto">{d.raw.available ? (d.raw.diff || "(no difference after removing ignored lines)").split("\n").map((l: string, i: number) => <span key={i} className={l.startsWith("+") && !l.startsWith("+++") ? "text-success" : l.startsWith("-") && !l.startsWith("---") ? "text-danger" : ""}>{l}{"\n"}</span>) : "raw output missing in one snapshot"}</pre>
      )}
    </article>
  );
}

function CompareTab() {
  const snaps = useSnapshots();
  const [pre, setPre] = useState("");
  const [postId, setPostId] = useState("");
  const [res, setRes] = useState<Any | null>(null);
  const [err, setErr] = useState("");
  const [onlyChanged, setOnlyChanged] = useState(true);
  const items = snaps.data?.items ?? [];
  return (
    <section aria-label="Compare snapshots">
      <p className="text-sm mb-2">Pick any two saved snapshots of the same profile (normally a <strong>pre</strong> and a <strong>post</strong>). Tests run on the TextFSM rows; the diff shows rows added, removed and changed (field by field, ignoring the profile's ignored fields and tolerances) and the raw output diff (volatile lines removed).</p>
      <div className="flex flex-wrap gap-2 items-end mb-3">
        <label className="text-sm flex flex-col">Pre snapshot<select aria-label="Pre snapshot" className={INPUT} value={pre} onChange={(e) => setPre(e.target.value)}><option value="">— choose —</option>{items.map((s) => <option key={s.id} value={s.id}>{snapLabel(s)}</option>)}</select></label>
        <label className="text-sm flex flex-col">Post snapshot<select aria-label="Post snapshot" className={INPUT} value={postId} onChange={(e) => setPostId(e.target.value)}><option value="">— choose —</option>{items.map((s) => <option key={s.id} value={s.id}>{snapLabel(s)}</option>)}</select></label>
        <Button variant="primary" disabled={!pre || !postId} onClick={async () => { setErr(""); try { setRes(await post<Any>("/prepost/compare", { pre, post: postId })); } catch (e) { setErr((e as Error).message); } }}>Compare</Button>
      </div>
      {err ? <p role="alert" className="text-sm text-danger">{err}</p> : null}
      {res ? (
        <div>
          <p className="text-base mb-2">{res.ok ? <span className="text-success">All {res.summary.tests} tests passed</span> : <span className="text-danger">{res.summary.failed} of {res.summary.tests} tests failed</span>} · {res.summary.changed} command output(s) changed</p>
          <table aria-label="Test results" className="mb-3"><thead><tr><th>Device</th><th>Test</th><th>Type</th><th>Result</th><th>Detail</th></tr></thead>
            <tbody>{(res.tests as Any[]).map((t, i) => <tr key={i}><td className="mono">{t.device}</td><td className="mono">{t.name}</td><td className="text-sm">{t.type}</td><td>{t.ok ? <span className="text-success">pass</span> : <span className="text-danger">fail</span>}</td><td className="text-sm">{t.message}</td></tr>)}</tbody></table>
          <label className="text-sm"><input type="checkbox" checked={onlyChanged} onChange={(e) => setOnlyChanged(e.target.checked)} /> Show only changed outputs</label>
          <div className="mt-2">{(res.diffs as Any[]).filter((d) => !onlyChanged || d.changed).map((d) => <DiffView key={`${d.device}-${d.command}`} d={d} />)}</div>
        </div>
      ) : null}
    </section>
  );
}

function SnapshotsTab() {
  const qc = useQueryClient();
  const { can } = useAuth();
  const profiles = useProfiles();
  const snaps = useSnapshots();
  const [form, setForm] = useState({ profile: "VLAN baseline", devices: "leaf-01, spine-01", label: "pre", ticket: "", compare_with: "" });
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const take = async () => {
    setMsg(null);
    try {
      const s = await post<Any>("/prepost/snapshots", { profile: form.profile, devices: form.devices.split(",").map((d) => d.trim()).filter(Boolean), label: form.label, ticket: form.ticket, compare_with: form.compare_with || null });
      setMsg({ ok: s.failed === 0, text: `Snapshot ${s.id.slice(0, 8)} saved: ${s.results.length} tests, ${s.failed} failed.` });
      await qc.invalidateQueries({ queryKey: ["prepost-snapshots"] });
    } catch (e) { setMsg({ ok: false, text: (e as Error).message }); }
  };
  return (
    <section aria-label="Snapshots">
      {can("operator") ? (
        <div className="flex flex-wrap gap-2 items-end mb-3">
          <label className="text-sm flex flex-col">Profile<select aria-label="Profile" className={INPUT} value={form.profile} onChange={(e) => setForm({ ...form, profile: e.target.value })}>{(profiles.data?.items ?? []).map((p) => <option key={p.name}>{p.name}</option>)}</select></label>
          <label className="text-sm flex flex-col">Devices<input aria-label="Devices" className={`${INPUT} w-[260px]`} value={form.devices} onChange={(e) => setForm({ ...form, devices: e.target.value })} /></label>
          <label className="text-sm flex flex-col">Snapshot<select aria-label="Snapshot label" className={INPUT} value={form.label} onChange={(e) => setForm({ ...form, label: e.target.value })}><option>pre</option><option>post</option><option>adhoc</option></select></label>
          <label className="text-sm flex flex-col">Ticket (optional)<input aria-label="Ticket" className={`${INPUT} w-[140px]`} value={form.ticket} onChange={(e) => setForm({ ...form, ticket: e.target.value })} /></label>
          <label className="text-sm flex flex-col">Compare with (for post)<select aria-label="Compare with" className={INPUT} value={form.compare_with} onChange={(e) => setForm({ ...form, compare_with: e.target.value })}><option value="">—</option>{(snaps.data?.items ?? []).filter((s) => s.profile === form.profile).map((s) => <option key={s.id} value={s.id}>{snapLabel(s)}</option>)}</select></label>
          <Button variant="primary" onClick={take}>Take snapshot</Button>
        </div>
      ) : null}
      {msg ? <p role={msg.ok ? "status" : "alert"} className={`text-sm mb-2 ${msg.ok ? "text-success" : "text-danger"}`}>{msg.text}</p> : null}
      <table aria-label="Saved snapshots"><thead><tr><th>When</th><th>Snapshot</th><th>Profile</th><th>Devices</th><th>Ticket</th><th>Tests</th><th>By</th></tr></thead>
        <tbody>{(snaps.data?.items ?? []).map((s) => (
          <tr key={s.id}><td className="text-sm">{s.created_at ? new Date(s.created_at).toLocaleString() : ""}</td><td className="mono">{s.label} {s.id.slice(0, 8)}</td><td>{s.profile} v{s.profile_version}</td><td className="mono text-sm">{(s.devices as string[]).join(", ")}</td><td className="mono">{s.ticket || "—"}</td>
            <td>{s.failed ? <span className="text-danger">{s.failed} failed</span> : <span className="text-success">{(s.results as Any[]).length} passed</span>}</td><td>{s.created_by}</td></tr>
        ))}</tbody></table>
    </section>
  );
}

function ProfilesTab() {
  const qc = useQueryClient();
  const { can } = useAuth();
  const profiles = useProfiles();
  const types = useQuery({ queryKey: ["prepost-types"], queryFn: () => request<{ tests: Record<string, string> }>("/prepost/test-types") });
  const [name, setName] = useState("VLAN baseline");
  const [text, setText] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => {
    void request<Any>(`/prepost/profiles/${encodeURIComponent(name)}`).then((p) => { const { version: _v, builtin: _b, ...rest } = p; setText(yaml.dump(rest, { lineWidth: 120 })); setMsg(null); }).catch(() => setText(""));
  }, [name]);
  const parse = (): Any | null => { try { return yaml.load(text) as Any; } catch (e) { setMsg({ ok: false, text: `YAML: ${(e as Error).message}` }); return null; } };
  return (
    <section aria-label="Profiles" className="grid gap-4" style={{ gridTemplateColumns: "minmax(220px, 260px) minmax(0, 1fr)" }}>
      <div>
        <ul aria-label="Saved profiles">{(profiles.data?.items ?? []).map((p) => (
          <li key={p.name}><button type="button" aria-current={p.name === name ? "page" : undefined} onClick={() => setName(p.name)} className={`w-full text-left text-sm px-2 py-1 rounded ${p.name === name ? "bg-highlight-soft" : "hover:bg-muted"}`}>{p.name} <span className="text-[12px] text-text-muted">v{p.version}{p.builtin ? " · built-in" : ""} · {p.tests} tests</span></button></li>
        ))}</ul>
        <Button className="mt-2" onClick={() => { setName(""); setText(yaml.dump({ name: "My profile", description: "", commands: [{ id: "intf", command: { default: "show interfaces status" }, key: ["port"], ignore_fields: [], tolerances: {}, raw_ignore: [] }], tests: [{ name: "no-down", command: "intf", type: "no_rows", where: { status: "down" } }] })); }}>New profile</Button>
        <div className="mt-3 text-[12px] text-text-muted"><p className="font-semibold">Test types</p><ul>{Object.entries(types.data?.tests ?? {}).map(([k, v]) => <li key={k}><span className="mono">{k}</span>: {v}</li>)}</ul></div>
      </div>
      <div>
        <p className="text-sm text-text-muted mb-1">Commands are run read-only; tests use the TextFSM rows only. <span className="mono">key</span> identifies a row between snapshots; <span className="mono">ignore_fields</span>, <span className="mono">tolerances</span> ({"{pct}"} / {"{abs}"}) and <span className="mono">raw_ignore</span> regexes keep counters, uptime and timestamps out of the diff.</p>
        <textarea aria-label="Profile YAML" className={`${INPUT} w-full mono`} rows={26} spellCheck={false} value={text} onChange={(e) => setText(e.target.value)} />
        <div className="flex gap-2 mt-2">
          <Button onClick={async () => { const p = parse(); if (!p) return; const r = await post<Any>("/prepost/profiles/validate", p); setMsg({ ok: r.valid, text: r.valid ? "Valid." : r.errors.join("; ") }); }}>Validate</Button>
          {can("operator") ? <Button variant="primary" onClick={async () => { const p = parse(); if (!p) return; try { const r = await post<Any>("/prepost/profiles", p, "PUT"); setMsg({ ok: true, text: `Saved ${r.name} v${r.version}.` }); await qc.invalidateQueries({ queryKey: ["prepost-profiles"] }); setName(r.name); } catch (e) { setMsg({ ok: false, text: (e as Error).message }); } }}>Save</Button> : null}
        </div>
        {msg ? <p role={msg.ok ? "status" : "alert"} className={`text-sm mt-2 ${msg.ok ? "text-success" : "text-danger"}`}>{msg.text}</p> : null}
      </div>
    </section>
  );
}

export default function PrePost() {
  const [tab, setTab] = useState<Tab>("compare");
  const TABS: [Tab, string][] = [["compare", "Compare"], ["snapshots", "Snapshots"], ["profiles", "Profiles (saved templates)"]];
  return (
    <div>
      <h2 className="text-lg mb-1">Pre/Post checks</h2>
      <p className="text-sm text-text-muted mb-3">Saved profiles capture what an engineer checks before and after work: snapshots of show commands parsed with TextFSM, tests on the parsed values, and an intelligent pre/post diff. Use a profile on a change (<span className="mono">check_profile</span>), in a flow (<strong>Run Pre/Post check profile</strong>) or by hand here.</p>
      <div role="tablist" aria-label="Pre/Post tabs" className="flex gap-1 border-b border-border mb-3">
        {TABS.map(([t, label]) => <button key={t} type="button" role="tab" aria-selected={tab === t} onClick={() => setTab(t)} className={`px-2 py-1 text-sm border-b-2 ${tab === t ? "border-accent" : "border-transparent text-text-muted"}`}>{label}</button>)}
      </div>
      {tab === "compare" ? <CompareTab /> : tab === "snapshots" ? <SnapshotsTab /> : <ProfilesTab />}
    </div>
  );
}
