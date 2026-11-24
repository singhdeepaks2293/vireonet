import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { request } from "../lib/api";
import { Button } from "./ui/Button";

interface Field { name: string; env: string; kind: string; options: string[]; secret: boolean; value: unknown; is_set: boolean | null; source: string; default: unknown }
interface Group { id: string; label: string; fields: Field[] }
interface ConfigResponse { groups: Group[]; note: string }

const INPUT = "w-full px-2 py-1 text-sm bg-surface text-text border border-border rounded-[3px]";
const asText = (v: unknown) => (v === null || v === undefined ? "" : typeof v === "string" ? v : Array.isArray(v) ? v.join(", ") : JSON.stringify(v));

export function AdapterConfig() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["adapter-config"], queryFn: () => request<ConfigResponse>("/adapters/config") });
  const [group, setGroup] = useState("servicenow");
  const [edits, setEdits] = useState<Record<string, unknown>>({});
  const [clear, setClear] = useState<string[]>([]);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => { setEdits({}); setClear([]); setMsg(null); }, [group]);
  if (q.isLoading) return <p className="text-sm">Loading adapter settings…</p>;
  if (q.isError) return <p className="text-sm text-danger" role="alert">Only admins can configure adapters: {(q.error as Error).message}</p>;
  const groups = q.data?.groups ?? [];
  const g = groups.find((x) => x.id === group) ?? groups[0];
  if (!g) return null;
  const value = (f: Field) => (f.name in edits ? edits[f.name] : f.secret ? "" : f.value);
  const set = (f: Field, v: unknown) => setEdits({ ...edits, [f.name]: v });

  const save = async () => {
    setMsg(null);
    try {
      const r = await request<{ message: string; saved: string[] }>("/adapters/config", { method: "PUT", body: JSON.stringify({ values: edits, clear }) });
      setMsg({ ok: true, text: `${r.message} (${r.saved.length + clear.length} setting(s))` });
      setEdits({}); setClear([]);
      await qc.invalidateQueries({ queryKey: ["adapter-config"] });
    } catch (e) { setMsg({ ok: false, text: (e as Error).message }); }
  };
  const test = async () => {
    setMsg(null);
    try {
      const r = await request<{ ok: boolean; detail: unknown }>("/adapters/config/test", { method: "POST", body: JSON.stringify({ group: g.id, values: edits }) });
      setMsg({ ok: r.ok, text: `${r.ok ? "Connection OK" : "Connection failed"}: ${typeof r.detail === "string" ? r.detail : JSON.stringify(r.detail)}` });
    } catch (e) { setMsg({ ok: false, text: (e as Error).message }); }
  };

  return (
    <section aria-label="Configure adapters" className="flex gap-4 items-start">
      <nav aria-label="Adapter groups" className="w-[240px] shrink-0">
        <ul>{groups.map((x) => (
          <li key={x.id}><button type="button" aria-current={x.id === g.id ? "page" : undefined} onClick={() => setGroup(x.id)}
            className={`w-full text-left text-sm px-2 py-1 rounded ${x.id === g.id ? "bg-highlight-soft" : "hover:bg-muted"}`}>{x.label}</button></li>
        ))}</ul>
      </nav>
      <div className="flex-1 min-w-0">
        <p className="text-sm text-text-muted mb-2">{q.data?.note} Environment variables still work; a value saved here wins. Secrets are encrypted and never shown: leave a secret empty to keep it.</p>
        <table aria-label={`${g.label} settings`}>
          <thead><tr><th>Setting</th><th>Value</th><th>Source</th></tr></thead>
          <tbody>{g.fields.map((f) => (
            <tr key={f.name}>
              <td><label htmlFor={`cfg-${f.name}`} className="mono text-sm">{f.name}</label><div className="text-[12px] text-text-muted mono">{f.env}</div></td>
              <td className="min-w-[320px]">
                {f.kind === "bool" ? (
                  <input id={`cfg-${f.name}`} type="checkbox" checked={Boolean(value(f))} onChange={(e) => set(f, e.target.checked)} />
                ) : f.kind === "select" ? (
                  <select id={`cfg-${f.name}`} className={INPUT} value={asText(value(f))} onChange={(e) => set(f, e.target.value)}>{f.options.map((o) => <option key={o}>{o}</option>)}</select>
                ) : f.kind === "json" ? (
                  <textarea id={`cfg-${f.name}`} className={`${INPUT} mono`} rows={5} spellCheck={false} value={asText(value(f))} onChange={(e) => set(f, e.target.value)} />
                ) : (
                  <input id={`cfg-${f.name}`} className={`${INPUT} ${f.kind === "secret" ? "" : "mono"}`} type={f.kind === "secret" ? "password" : f.kind === "number" ? "number" : "text"} autoComplete="off"
                    placeholder={f.secret ? (f.is_set ? "•••••• set (leave empty to keep)" : "not set") : ""}
                    value={asText(value(f))} onChange={(e) => set(f, f.kind === "number" ? Number(e.target.value) : e.target.value)} />
                )}
              </td>
              <td className="text-sm">
                <span className={f.source === "ui" ? "text-accent" : "text-text-muted"}>{f.source}</span>
                {f.source === "ui" ? <label className="block text-[12px]"><input type="checkbox" checked={clear.includes(f.name)} onChange={(e) => setClear(e.target.checked ? [...clear, f.name] : clear.filter((c) => c !== f.name))} /> reset to env</label> : null}
              </td>
            </tr>
          ))}</tbody>
        </table>
        <div className="flex gap-2 mt-2">
          <Button onClick={test}>Test connection</Button>
          <Button variant="primary" onClick={save} disabled={!Object.keys(edits).length && !clear.length}>Save</Button>
        </div>
        {msg ? <p role={msg.ok ? "status" : "alert"} className={`text-sm mt-2 break-all ${msg.ok ? "text-success" : "text-danger"}`}>{msg.text}</p> : null}
      </div>
    </section>
  );
}
