import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import yaml from "js-yaml";
import {
  allIds, emptyDefinition, flowsApi, getList, moveStep, newStep, setList, useCatalog, useRuns,
  type Catalog, type Condition, type Definition, type FlowInput, type Json, type ListPath, type LogEntry, type Step, type StepSpec, type Trigger,
} from "../api/flows";
import { Button } from "../components/ui/Button";
import { StepCard } from "../components/flows/StepCard";
import { ConditionEditor, FieldEditor, INPUT, InsertProvider, SELECT, TextField, asText, parseLoose, useInsert } from "../components/flows/fields";
import { useAuth } from "../lib/auth";
import { StatusChip } from "./FlowRun";

type Tab = "designer" | "inputs" | "test" | "runs" | "versions";
type Drag = { kind: "move"; from: ListPath; index: number } | { kind: "new"; type: string };
const DRAG_MIME = "application/x-vireonet-step";

// --- helpers --------------------------------------------------------------------------

function flatten(obj: Json, prefix: string, out: string[], depth = 0): void {
  if (depth > 5 || obj === null || typeof obj !== "object") return;
  for (const [k, v] of Object.entries(obj as Record<string, Json>)) {
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(k)) continue;
    const p = `${prefix}.${k}`;
    out.push(p);
    if (v && typeof v === "object" && !Array.isArray(v)) flatten(v, p, out, depth + 1);
  }
}

function regexGroups(c: Json | undefined, out: Set<string>): void {
  if (!c || typeof c !== "object") return;
  if (Array.isArray(c)) { c.forEach((x) => regexGroups(x, out)); return; }
  const o = c as Record<string, Json>;
  if (typeof o.value === "string" && String(o.op).includes("match")) for (const m of o.value.matchAll(/\(\?P<([A-Za-z_]\w*)>/g)) out.add(m[1]);
  Object.values(o).forEach((v) => regexGroups(v, out));
}

function walk(steps: Step[], fn: (s: Step) => void): void {
  for (const s of steps) {
    fn(s);
    for (const k of ["steps", "else", "catch", "finally", "default"]) if (Array.isArray(s[k])) walk(s[k] as unknown as Step[], fn);
    for (const k of ["branches", "cases"]) if (Array.isArray(s[k])) (s[k] as unknown as { steps?: Step[] }[]).forEach((b) => walk(b.steps ?? [], fn));
  }
}

function suggestions(catalog: Catalog | undefined, trigger: Trigger, def: Definition): string[] {
  const out: string[] = [];
  const sample = catalog?.triggers.find((t) => t.type === trigger.type)?.event ?? {};
  flatten(sample as Json, "trigger", out);
  const groups = new Set<string>();
  regexGroups(trigger.conditions as Json, groups);
  groups.forEach((g) => out.push(`trigger.match.${g}`));
  def.inputs.forEach((i) => out.push(`inputs.${i.name}`));
  const vars = new Set(Object.keys(def.variables));
  walk(def.steps, (s) => {
    out.push(`steps.${s.id}`);
    if ((s.type === "set_variable" || s.type === "append_to_list") && typeof s.name === "string") vars.add(s.name);
    if (typeof s.save_as === "string" && s.save_as) vars.add(s.save_as);
    if ((s.type === "for_each" || s.type === "repeat") && typeof s.as === "string") vars.add(s.as);
  });
  vars.forEach((v) => out.push(`vars.${v}`));
  out.push("vars.error.message", "match", "run.id", "run.flow", "run.started_by");
  return [...new Set(out)];
}

function clone(step: Step, taken: Set<string>): Step {
  const copy = JSON.parse(JSON.stringify(step)) as Step;
  walk([copy], (s) => {
    let n = 2;
    const base = s.id.replace(/_?\d+$/, "");
    while (taken.has(`${base}_${n}`)) n += 1;
    s.id = `${base}_${n}`;
    taken.add(s.id);
  });
  return copy;
}

// --- palette -----------------------------------------------------------------------------

function Palette({ catalog, onPick, onClose, isAdmin }: { catalog: Catalog; onPick: (spec: StepSpec) => void; onClose: () => void; isAdmin: boolean }) {
  const [q, setQ] = useState("");
  const shown = catalog.steps.filter((s) => (`${s.label} ${s.description} ${s.type}`).toLowerCase().includes(q.toLowerCase()));
  return (
    <div role="dialog" aria-label="Add an action" className="fixed inset-0 z-40 flex items-start justify-center bg-black/30 pt-[8vh]" onClick={onClose}>
      <div className="w-[min(860px,94vw)] max-h-[80vh] overflow-y-auto bg-surface border border-border rounded-[8px] p-4 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex gap-2 mb-3">
          <input autoFocus aria-label="Search actions" className={INPUT} placeholder="Search actions: api, ticket, loop, check…" value={q} onChange={(e) => setQ(e.target.value)} />
          <Button onClick={onClose}>Close</Button>
        </div>
        {catalog.categories.map((cat) => {
          const items = shown.filter((s) => s.category === cat.id);
          if (!items.length) return null;
          return (
            <section key={cat.id} className="mb-3">
              <h3 className="text-sm font-semibold mb-1" style={{ color: cat.color }}>{cat.label}</h3>
              <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(190px, 1fr))" }}>
                {items.map((s) => {
                  const blocked = s.admin_only && !isAdmin;
                  return (
                    <button key={s.type} type="button" disabled={blocked} onClick={() => onPick(s)} className="text-left p-2 rounded-[6px] border border-border hover:border-highlight bg-bg disabled:opacity-50" title={blocked ? "Admins only" : s.description}>
                      <span className="block text-sm font-semibold">{s.label}</span>
                      <span className="block text-[12px] text-text-muted line-clamp-2">{s.description}</span>
                    </button>
                  );
                })}
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
}

// --- side palette: drag actions into the flow ---------------------------------------------

function SidePalette({ catalog, isAdmin, onAdd, setDrag }: { catalog: Catalog; isAdmin: boolean; onAdd: (spec: StepSpec) => void; setDrag: (d: Drag | null) => void }) {
  const [q, setQ] = useState("");
  return (
    <aside aria-label="Actions" className="w-[210px] shrink-0 sticky top-0 self-start max-h-[85vh] overflow-y-auto border border-border rounded-[6px] p-2 bg-surface hidden lg:block">
      <h3 className="text-sm font-semibold">Actions</h3>
      <p className="text-[12px] text-text-muted mb-1">Drag into the flow, or click to add at the end.</p>
      <input aria-label="Filter actions" className={INPUT} value={q} onChange={(e) => setQ(e.target.value)} placeholder="filter" />
      {catalog.categories.map((cat) => {
        const items = catalog.steps.filter((s) => s.category === cat.id && `${s.label} ${s.type}`.toLowerCase().includes(q.toLowerCase()));
        if (!items.length) return null;
        return (
          <div key={cat.id} className="mt-2">
            <p className="text-[12px] font-semibold" style={{ color: cat.color }}>{cat.label}</p>
            <ul className="flex flex-col gap-[3px]">
              {items.map((s) => {
                const blocked = s.admin_only && !isAdmin;
                return (
                  <li key={s.type}>
                    <button type="button" disabled={blocked} draggable={!blocked} aria-label={`Add ${s.label}`}
                      onDragStart={(e) => { e.dataTransfer.setData(DRAG_MIME, s.type); e.dataTransfer.effectAllowed = "copy"; setDrag({ kind: "new", type: s.type }); }}
                      onDragEnd={() => setDrag(null)} onClick={() => onAdd(s)}
                      className="w-full text-left text-[12px] px-2 py-[3px] rounded-[4px] border border-border bg-bg hover:border-highlight cursor-grab disabled:opacity-50" style={{ borderLeft: `3px solid ${cat.color}` }}>
                      {s.label}
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })}
    </aside>
  );
}

function DropSlot({ active, onDrop, label }: { active: boolean; onDrop: () => void; label: string }) {
  const [over, setOver] = useState(false);
  if (!active) return null;
  return (
    <div role="button" aria-label={label} tabIndex={-1}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); onDrop(); }}
      className={`h-[14px] rounded-[4px] border-2 border-dashed transition-colors ${over ? "border-highlight bg-highlight-soft h-[28px]" : "border-transparent"}`} />
  );
}

// --- trigger card --------------------------------------------------------------------

function TriggerCard({ catalog, trigger, onChange, webhookUrl }: { catalog: Catalog; trigger: Trigger; onChange: (t: Trigger) => void; webhookUrl: string }) {
  const spec = catalog.triggers.find((t) => t.type === trigger.type) ?? catalog.triggers[0];
  const [sample, setSample] = useState("");
  const [result, setResult] = useState<Awaited<ReturnType<typeof flowsApi.testTrigger>> | null>(null);
  useEffect(() => { setSample(JSON.stringify(spec.event, null, 2)); setResult(null); }, [spec.type]);
  const builtin = catalog.triggers.filter((t) => !t.adapter);
  const adapters = catalog.triggers.filter((t) => t.adapter);
  const test = async () => {
    let ev: unknown;
    try { ev = JSON.parse(sample); } catch { setResult({ valid: false, errors: ["sample event is not valid JSON"], matched: false }); return; }
    setResult(await flowsApi.testTrigger(trigger, ev));
  };
  return (
    <section aria-label="Trigger" className="rounded-[8px] border-2 border-highlight bg-highlight-soft p-3">
      <div className="flex flex-wrap items-center gap-2 mb-2">
        <span className="text-base font-semibold">When</span>
        <select aria-label="Trigger type" className={SELECT} value={trigger.type} onChange={(e) => onChange({ type: e.target.value })}>
          <optgroup label="Built in">{builtin.map((t) => <option key={t.type} value={t.type}>{t.label}</option>)}</optgroup>
          <optgroup label="From adapters">{adapters.map((t) => <option key={t.type} value={t.type}>{t.label}</option>)}</optgroup>
        </select>
      </div>
      <p className="text-[12px] text-text-muted mb-2">{spec.description}</p>
      {trigger.type === "webhook" ? <p className="text-sm mb-2">URL: <span className="mono">POST {webhookUrl}</span> with header <span className="mono">X-VireoNet-Flow-Token</span>.</p> : null}
      {spec.adapter ? <p className="text-sm mb-2">Point the {spec.label.split(" ")[0]} webhook at <span className="mono">POST /webhooks/flows/source/{trigger.type}</span> (header <span className="mono">X-VireoNet-Event-Token</span>).</p> : null}
      <div className="grid gap-2 mb-2" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))" }}>
        {spec.fields.map((f) => (
          <FieldEditor key={f.name} spec={f} value={trigger[f.name] as Json | undefined} onChange={(v) => onChange({ ...trigger, [f.name]: v })} catalog={catalog} idPrefix="trigger" />
        ))}
        {trigger.type !== "manual" ? (
          <FieldEditor spec={{ name: "dedup_key", label: "Run once per (dedup key)", kind: "text", help: "Template; default per source (ITSM: ticket + state). 'none' = every event runs." }} value={trigger.dedup_key as Json} onChange={(v) => onChange({ ...trigger, dedup_key: v })} catalog={catalog} idPrefix="trigger" />
        ) : null}
      </div>
      {trigger.type !== "manual" ? (
        <>
          <p className="text-sm mb-1">Only when (fields are paths in the event, e.g. <span className="mono">ticket.summary</span>; <span className="mono">*</span> = any text; regex named groups become <span className="mono">trigger.match.&lt;name&gt;</span>):</p>
          <ConditionEditor value={(trigger.conditions as Condition) ?? null} catalog={catalog} label="Trigger conditions" onChange={(c) => onChange({ ...trigger, conditions: c ?? undefined })} />
          <details className="mt-2">
            <summary className="text-sm cursor-pointer">Test with sample data</summary>
            <textarea aria-label="Sample event" className={`${INPUT} mono mt-1`} rows={8} value={sample} onChange={(e) => setSample(e.target.value)} />
            <Button className="mt-1" onClick={test}>Test trigger</Button>
            {result ? (
              <div role="status" className={`mt-1 text-sm ${result.matched ? "text-success" : "text-danger"}`}>
                {result.errors.length ? result.errors.join("; ") : result.matched ? "Matches: the flow would start." : `No match: ${result.reason}`}
                {result.captures && Object.keys(result.captures).length ? <pre className="mono text-[12px] text-text bg-surface p-1 mt-1">{JSON.stringify(result.captures, null, 2)}</pre> : null}
                {result.dedup_key ? <p className="text-[12px] text-text-muted">dedup key: <span className="mono">{result.dedup_key}</span></p> : null}
              </div>
            ) : null}
          </details>
        </>
      ) : null}
    </section>
  );
}

// --- variables panel --------------------------------------------------------------------------

function VariablesPanel({ items }: { items: string[] }) {
  const ctx = useInsert();
  const [q, setQ] = useState("");
  const shown = items.filter((i) => i.toLowerCase().includes(q.toLowerCase()));
  const groups = ["trigger", "inputs", "vars", "steps", "other"];
  const groupOf = (i: string) => (groups.includes(i.split(".")[0]) ? i.split(".")[0] : "other");
  return (
    <aside aria-label="Variables" className="w-[250px] shrink-0 sticky top-0 self-start max-h-[85vh] overflow-y-auto border border-border rounded-[6px] p-2 bg-surface hidden xl:block">
      <h3 className="text-sm font-semibold">Dynamic content</h3>
      <p className="text-[12px] text-text-muted mb-1">Click a field, then a value to insert it as {"{{ … }}"}.</p>
      <input aria-label="Filter variables" className={INPUT} value={q} onChange={(e) => setQ(e.target.value)} placeholder="filter" />
      {groups.map((g) => {
        const list = shown.filter((i) => groupOf(i) === g);
        if (!list.length) return null;
        return (
          <div key={g} className="mt-2">
            <p className="text-[12px] font-semibold uppercase text-text-muted">{g}</p>
            <ul>{list.map((i) => <li key={i}><button type="button" className="mono text-[12px] text-left hover:text-accent break-all" onMouseDown={(e) => e.preventDefault()} onClick={() => ctx.insert(i)}>{i}</button></li>)}</ul>
          </div>
        );
      })}
    </aside>
  );
}

// --- inputs / variables / outputs ------------------------------------------------------------

function InputsTab({ def, setDef, catalog }: { def: Definition; setDef: (d: Definition) => void; catalog: Catalog }) {
  const setInput = (i: number, patch: Partial<FlowInput>) => setDef({ ...def, inputs: def.inputs.map((x, j) => (j === i ? { ...x, ...patch } : x)) });
  return (
    <div className="flex flex-col gap-4">
      <section>
        <h3 className="text-sm font-semibold mb-1">Run inputs (form shown on Run, like an NSP workflow input form)</h3>
        <table aria-label="Inputs"><thead><tr><th>Name</th><th>Label</th><th>Type</th><th>Required</th><th>Default</th><th>Options</th><th /></tr></thead>
          <tbody>{def.inputs.map((inp, i) => (
            <tr key={i}>
              <td><input aria-label={`Input ${i + 1} name`} className={`${INPUT} mono`} value={inp.name} onChange={(e) => setInput(i, { name: e.target.value })} /></td>
              <td><input aria-label={`Input ${i + 1} label`} className={INPUT} value={inp.label ?? ""} onChange={(e) => setInput(i, { label: e.target.value })} /></td>
              <td><select aria-label={`Input ${i + 1} type`} className={SELECT} value={inp.type} onChange={(e) => setInput(i, { type: e.target.value })}>{catalog.input_types.map((t) => <option key={t}>{t}</option>)}</select></td>
              <td><input aria-label={`Input ${i + 1} required`} type="checkbox" checked={Boolean(inp.required)} onChange={(e) => setInput(i, { required: e.target.checked })} /></td>
              <td><input aria-label={`Input ${i + 1} default`} className={INPUT} value={asText(inp.default)} onChange={(e) => setInput(i, { default: parseLoose(e.target.value) })} /></td>
              <td><input aria-label={`Input ${i + 1} options`} className={INPUT} value={(inp.options ?? []).join(", ")} onChange={(e) => setInput(i, { options: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })} /></td>
              <td><button type="button" className="text-danger text-sm" onClick={() => setDef({ ...def, inputs: def.inputs.filter((_, j) => j !== i) })}>Remove</button></td>
            </tr>
          ))}</tbody></table>
        <Button className="mt-1" onClick={() => setDef({ ...def, inputs: [...def.inputs, { name: `input${def.inputs.length + 1}`, type: "text" }] })}>+ Input</Button>
      </section>
      <KvSection title="Variables (initial values, vars.<name>)" value={def.variables} onChange={(v) => setDef({ ...def, variables: v })} />
      <KvSection title="Outputs (returned by the run; a sub-flow's result)" value={def.outputs} onChange={(v) => setDef({ ...def, outputs: v })} />
    </div>
  );
}

function KvSection({ title, value, onChange }: { title: string; value: Record<string, Json>; onChange: (v: Record<string, Json>) => void }) {
  const rows = Object.entries(value);
  return (
    <section>
      <h3 className="text-sm font-semibold mb-1">{title}</h3>
      {rows.map(([k, v], i) => (
        <div key={i} className="flex gap-1 mb-1">
          <input aria-label={`${title} name ${i + 1}`} className={`${INPUT} mono max-w-[200px]`} value={k} onChange={(e) => onChange(Object.fromEntries(rows.map((r, j) => (j === i ? [e.target.value, r[1]] : r))))} />
          <TextField ariaLabel={`${title} value ${i + 1}`} value={asText(v)} onChange={(t) => onChange(Object.fromEntries(rows.map((r, j) => (j === i ? [r[0], parseLoose(t)] : r))))} />
          <button type="button" className="text-danger text-sm" onClick={() => onChange(Object.fromEntries(rows.filter((_, j) => j !== i)))}>✕</button>
        </div>
      ))}
      <Button onClick={() => onChange({ ...value, [`name${rows.length + 1}`]: "" })}>+ Add</Button>
    </section>
  );
}

// --- page --------------------------------------------------------------------------------------

export default function FlowEditor() {
  const { id } = useParams();
  const isNew = !id || id === "new";
  const nav = useNavigate();
  const qc = useQueryClient();
  const { can } = useAuth();
  const catalogQ = useCatalog();
  const flowQ = useQuery({ queryKey: ["flow", id], queryFn: () => flowsApi.get(id!), enabled: !isNew });
  const [name, setName] = useState("New flow");
  const [description, setDescription] = useState("");
  const [trigger, setTrigger] = useState<Trigger>({ type: "manual" });
  const [def, setDef] = useState<Definition>(emptyDefinition());
  const [tab, setTab] = useState<Tab>("designer");
  const [palette, setPalette] = useState<{ set: (steps: Step[]) => void; steps: Step[]; index: number } | null>(null);
  const [issues, setIssues] = useState<string[]>([]);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [dirty, setDirty] = useState(false);
  const [token, setToken] = useState("");
  const [mode, setMode] = useState<"visual" | "yaml">("visual");
  const [yamlText, setYamlText] = useState("");
  const [drag, setDrag] = useState<Drag | null>(null);
  const [forceOpen, setForceOpen] = useState<{ open: boolean; n: number } | undefined>(undefined);
  const [loadedIds, setLoadedIds] = useState<Set<string>>(new Set());
  const [runOpen, setRunOpen] = useState(false);

  useEffect(() => {
    const f = flowQ.data;
    if (!f) return;
    setName(f.name); setDescription(f.description); setTrigger(f.trigger); setDef({ ...emptyDefinition(), ...f.definition }); setDirty(false);
    setLoadedIds(allIds(f.definition.steps ?? [])); // saved steps start collapsed
  }, [flowQ.data]);
  useEffect(() => {
    // New flow from a template: /flows/new?example=<key>
    const key = new URLSearchParams(window.location.search).get("example");
    if (isNew && key) void flowsApi.example(key).then((ex) => { setName(ex.name); setDescription(ex.description); setTrigger(ex.trigger); setDef({ ...emptyDefinition(), ...ex.definition }); setLoadedIds(allIds(ex.definition.steps ?? [])); setDirty(true); });
  }, [isNew]);

  const catalog = catalogQ.data;
  const sugg = useMemo(() => suggestions(catalog, trigger, def), [catalog, trigger, def]);
  const change = (d: Definition) => { setDef(d); setDirty(true); };
  const changeTrigger = (t: Trigger) => { setTrigger(t); setDirty(true); };

  if (catalogQ.isLoading || (!isNew && flowQ.isLoading)) return <p className="text-sm">Loading the flow builder…</p>;
  if (!catalog) return <p className="text-sm text-danger" role="alert">Could not load the action catalog.</p>;
  if (!isNew && flowQ.isError) return <p className="text-sm text-danger" role="alert">Flow not found.</p>;

  const flow = flowQ.data;
  const taken = allIds(def.steps);

  const setRoot = (steps: Step[]) => change({ ...def, steps });
  const dropAt = (to: ListPath, index: number) => {
    if (!drag) return;
    if (drag.kind === "new") {
      const spec = catalog.steps.find((x) => x.type === drag.type);
      if (!spec) return;
      const list = [...getList(def.steps, to)];
      list.splice(index, 0, newStep(spec, taken));
      setRoot(setList(def.steps, to, list));
    } else {
      const moved = moveStep(def.steps, drag.from, drag.index, to, index);
      if (moved) setRoot(moved);
      else setMessage({ ok: false, text: "A step cannot be moved inside itself." });
    }
    setDrag(null);
  };

  const renderList = (path: ListPath): JSX.Element => {
    const steps = getList(def.steps, path);
    const set = (next: Step[]) => setRoot(setList(def.steps, path, next));
    const where = path.length ? `in ${path.map((p) => p.key).join(" › ")}` : "at the top level";
    return (
      <div className="flex flex-col gap-1">
        <DropSlot active={drag !== null} label={`Drop here: position 1 ${where}`} onDrop={() => dropAt(path, 0)} />
        {steps.map((s, i) => (
          <div key={s.id} className="flex flex-col gap-1">
            <StepCard
              step={s} path={`${path.map((p) => `${p.i}.${p.key}`).join(".")}.${i}`} catalog={catalog} issues={issues}
              onChange={(ns) => { const out = [...steps]; out[i] = ns; set(out); }}
              onRemove={() => set(steps.filter((_, j) => j !== i))}
              onDuplicate={() => { const c = clone(s, allIds(def.steps)); const out = [...steps]; out.splice(i + 1, 0, c); set(out); }}
              onMove={(dir) => { const j = i + dir; if (j < 0 || j >= steps.length) return; const out = [...steps]; [out[i], out[j]] = [out[j], out[i]]; set(out); }}
              renderChild={(key) => renderList([...path, { i, key }])}
              defaultOpen={!loadedIds.has(s.id)} forceOpen={forceOpen}
              dragHandle={
                <span draggable role="button" tabIndex={0} aria-label={`Drag ${s.id}`} title="Drag to move" className="cursor-grab select-none text-text-muted px-1"
                  onDragStart={(e) => { e.dataTransfer.setData(DRAG_MIME, s.id); e.dataTransfer.effectAllowed = "move"; setDrag({ kind: "move", from: path, index: i }); }}
                  onDragEnd={() => setDrag(null)}>⠿</span>
              }
            />
            <DropSlot active={drag !== null} label={`Drop here: position ${i + 2} ${where}`} onDrop={() => dropAt(path, i + 1)} />
          </div>
        ))}
        <button type="button" className="self-start text-sm text-accent border border-dashed border-border rounded-[6px] px-3 py-1 hover:border-highlight" onClick={() => setPalette({ set, steps, index: steps.length })}>+ Add action</button>
      </div>
    );
  };

  const toYaml = () => yaml.dump({ trigger, definition: def }, { noRefs: true, lineWidth: 120 });
  const switchMode = (next: "visual" | "yaml") => {
    if (next === mode) return;
    if (next === "yaml") { setYamlText(toYaml()); setMode("yaml"); return; }
    try {
      const v = yaml.load(yamlText) as { trigger?: Trigger; definition?: Definition } | null;
      if (!v || typeof v !== "object" || !v.definition) throw new Error("the YAML needs a 'definition' (and usually a 'trigger')");
      setTrigger(v.trigger ?? { type: "manual" });
      change({ ...emptyDefinition(), ...v.definition });
      setMode("visual");
      setMessage(null);
      void flowsApi.validate({ ...emptyDefinition(), ...v.definition }, v.trigger ?? { type: "manual" }).then((r) => setIssues(r.errors));
    } catch (e) {
      setMessage({ ok: false, text: `YAML not applied: ${(e as Error).message}` });
    }
  };

  const save = async () => {
    setMessage(null);
    if (mode === "yaml") { setMessage({ ok: false, text: "Switch to Visual to apply the YAML, then save." }); return; }
    try {
      if (isNew) {
        const created = await flowsApi.create({ name, description, trigger, definition: def });
        setToken(created.webhook_token ?? "");
        setDirty(false);
        await qc.invalidateQueries({ queryKey: ["flows"] });
        nav(`/flows/${created.id}`, { replace: true });
        setMessage({ ok: true, text: "Saved as version 1 (draft). Publish it to make the trigger live." });
      } else {
        const updated = await flowsApi.update(id!, { name, description, trigger, definition: def });
        setDirty(false);
        await qc.invalidateQueries({ queryKey: ["flow", id] });
        setMessage({ ok: true, text: `Saved: version ${updated.version}.` });
      }
      setIssues([]);
    } catch (e) {
      const detail = (e as Error).message;
      const m = /errors?":\s*\[(.*)\]/.exec(detail);
      const list = m ? (JSON.parse(`[${m[1]}]`) as string[]) : [detail];
      setIssues(list);
      setMessage({ ok: false, text: `Not saved: ${list.length} problem(s).` });
    }
  };
  const validate = async () => {
    const r = await flowsApi.validate(def, trigger);
    setIssues(r.errors);
    setMessage({ ok: r.valid, text: r.valid ? `Valid.${r.warnings.length ? " " + r.warnings.join(" ") : ""}` : `${r.errors.length} problem(s) found.` });
  };
  const exportJson = async () => {
    const data = isNew || dirty ? { format: "vireonet-flow/1", name, description, trigger, definition: def } : await flowsApi.exportFlow(id!);
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = `${name.replace(/\W+/g, "-").toLowerCase()}.flow.json`; a.click();
  };

  const TABS: [Tab, string][] = [["designer", "Designer"], ["inputs", "Inputs, variables, outputs"], ["test", "Test run"], ["runs", "Runs"], ["versions", "Versions"]];

  return (
    <InsertProvider suggestions={sugg}>
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-end gap-2">
          <div className="flex-1 min-w-[260px]">
            <label htmlFor="flow-name" className="block text-sm text-text-muted">Flow name</label>
            <input id="flow-name" className={`${INPUT} text-base font-semibold`} value={name} onChange={(e) => { setName(e.target.value); setDirty(true); }} />
          </div>
          <div className="flex-[2] min-w-[260px]">
            <label htmlFor="flow-desc" className="block text-sm text-text-muted">Description</label>
            <input id="flow-desc" className={INPUT} value={description} onChange={(e) => { setDescription(e.target.value); setDirty(true); }} />
          </div>
          <Button variant="primary" onClick={save} disabled={!can("operator")}>{dirty ? "Save*" : "Save"}</Button>
          <Button onClick={validate}>Validate</Button>
          {!isNew ? <Button onClick={() => setRunOpen(true)} disabled={!can("operator")}>Run…</Button> : null}
          {!isNew && can("approver") ? <Button onClick={async () => { await flowsApi.publish(id!); await qc.invalidateQueries({ queryKey: ["flow", id] }); setMessage({ ok: true, text: `Version ${flow?.latest_version} published.` }); }}>Publish v{flow?.latest_version}</Button> : null}
          {!isNew && can("approver") ? (
            <Button onClick={async () => { try { await flowsApi.enable(id!, !flow?.enabled); await qc.invalidateQueries({ queryKey: ["flow", id] }); } catch (e) { setMessage({ ok: false, text: (e as Error).message }); } }}>{flow?.enabled ? "Disable trigger" : "Enable trigger"}</Button>
          ) : null}
          <Button onClick={exportJson}>Export</Button>
        </div>
        {!isNew && flow ? (
          <p className="text-sm text-text-muted">
            Editing version {flow.latest_version} · published: {flow.published_version ? `v${flow.published_version}` : "none"} · trigger {flow.enabled ? <span className="text-success">enabled</span> : "disabled"}
            {flow.published_version && flow.published_version !== flow.latest_version ? <span className="text-warning"> · the live trigger runs v{flow.published_version}</span> : null}
          </p>
        ) : null}
        {token ? <p role="status" className="text-sm p-2 border border-warning bg-warning-bg rounded">Webhook token (shown once, store it now): <span className="mono">{token}</span></p> : null}
        {message ? <p role={message.ok ? "status" : "alert"} className={`text-sm ${message.ok ? "text-success" : "text-danger"}`}>{message.text}</p> : null}
        {issues.length && tab !== "designer" ? <ul role="alert" className="text-[12px] text-danger">{issues.map((i) => <li key={i} className="mono">{i}</li>)}</ul> : null}

        <div role="tablist" aria-label="Flow editor" className="flex gap-1 border-b border-border">
          {TABS.map(([t, label]) => (
            <button key={t} type="button" role="tab" aria-selected={tab === t} className={`px-2 py-1 text-sm border-b-2 ${tab === t ? "border-accent" : "border-transparent text-text-muted"}`} onClick={() => { if (mode === "yaml" && t !== "designer") switchMode("visual"); setTab(t); }}>{label}</button>
          ))}
        </div>

        {tab === "designer" ? (
          <>
            <div className="flex items-center gap-2">
            <div role="group" aria-label="Editor mode" className="inline-flex self-start border border-border rounded-[4px] overflow-hidden">
              {(["visual", "yaml"] as const).map((m) => (
                <button key={m} type="button" aria-pressed={mode === m} onClick={() => switchMode(m)} className={`px-3 py-1 text-sm ${mode === m ? "bg-highlight-soft text-text" : "bg-surface text-text-muted"}`}>{m === "visual" ? "Visual (drag and drop)" : "YAML"}</button>
              ))}
            </div>
            {mode === "visual" ? <Button onClick={() => setForceOpen({ open: true, n: (forceOpen?.n ?? 0) + 1 })}>Expand all</Button> : null}
            {mode === "visual" ? <Button onClick={() => setForceOpen({ open: false, n: (forceOpen?.n ?? 0) + 1 })}>Collapse all</Button> : null}
            </div>
            {mode === "visual" ? (
              <div className="flex gap-4 items-start">
                <SidePalette catalog={catalog} isAdmin={can("admin")} setDrag={setDrag} onAdd={(spec) => setRoot([...def.steps, newStep(spec, taken)])} />
                <div className="flex-1 min-w-0 flex flex-col gap-3 max-w-[1100px]">
                  <TriggerCard catalog={catalog} trigger={trigger} onChange={changeTrigger} webhookUrl={flow?.webhook_url ?? "/webhooks/flows/<id>"} />
                  <div aria-hidden="true" className="self-center w-[2px] h-4 bg-border" />
                  {issues.filter((i) => !/\(\w+:\w+\)/.test(i)).length ? <ul role="alert" className="text-[12px] text-danger">{issues.filter((i) => !/\(\w+:\w+\)/.test(i)).map((i) => <li key={i}>{i}</li>)}</ul> : null}
                  {renderList([])}
                </div>
                <VariablesPanel items={sugg} />
              </div>
            ) : (
              <div>
                <p className="text-sm text-text-muted mb-1">The whole flow (trigger + definition) as YAML. Switch back to Visual to apply; errors keep you here.</p>
                <textarea aria-label="Flow YAML" className={`${INPUT} mono`} rows={32} spellCheck={false} value={yamlText} onChange={(e) => { setYamlText(e.target.value); setDirty(true); }} />
              </div>
            )}
          </>
        ) : null}
        {tab === "inputs" ? <InputsTab def={def} setDef={change} catalog={catalog} /> : null}
        {tab === "test" ? <TestRun catalog={catalog} trigger={trigger} def={def} /> : null}
        {tab === "runs" && !isNew ? <RunsTab flowId={id!} /> : null}
        {tab === "versions" && !isNew ? <VersionsTab flowId={id!} canPublish={can("approver")} onLoad={async (v) => { const f = await flowsApi.get(id!, v); setTrigger(f.trigger); change({ ...emptyDefinition(), ...f.definition }); setTab("designer"); setMessage({ ok: true, text: `Loaded version ${v}: save to make it the newest version.` }); }} /> : null}
        {(tab === "runs" || tab === "versions") && isNew ? <p className="text-sm text-text-muted">Save the flow first.</p> : null}
      </div>
      {palette ? (
        <Palette catalog={catalog} isAdmin={can("admin")} onClose={() => setPalette(null)} onPick={(spec) => { const s = newStep(spec, taken); const out = [...palette.steps]; out.splice(palette.index, 0, s); palette.set(out); setPalette(null); }} />
      ) : null}
      {runOpen && flow ? <RunDialog flowId={flow.id} inputs={def.inputs} onClose={() => setRunOpen(false)} onStarted={(rid) => nav(`/flows/runs/${rid}`)} /> : null}
    </InsertProvider>
  );
}

// --- run dialog, test run, runs, versions ------------------------------------------------------

function RunDialog({ flowId, inputs, onClose, onStarted }: { flowId: string; inputs: FlowInput[]; onClose: () => void; onStarted: (id: string) => void }) {
  const [values, setValues] = useState<Record<string, Json>>(() => Object.fromEntries(inputs.map((i) => [i.name, i.default ?? ""])));
  const [err, setErr] = useState("");
  return (
    <div role="dialog" aria-label="Run flow" className="fixed inset-0 z-40 flex items-start justify-center bg-black/30 pt-[12vh]" onClick={onClose}>
      <div className="w-[min(520px,94vw)] bg-surface border border-border rounded-[8px] p-4" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-base font-semibold mb-2">Run this flow (latest saved version)</h2>
        {inputs.length === 0 ? <p className="text-sm text-text-muted mb-2">This flow has no inputs.</p> : null}
        {inputs.map((i) => (
          <div key={i.name} className="mb-2">
            <label htmlFor={`run-${i.name}`} className="block text-sm">{i.label || i.name}{i.required ? " *" : ""} <span className="text-text-muted">({i.type})</span></label>
            {i.type === "select" ? (
              <select id={`run-${i.name}`} className={SELECT} value={asText(values[i.name])} onChange={(e) => setValues({ ...values, [i.name]: e.target.value })}>{(i.options ?? []).map((o) => <option key={o}>{o}</option>)}</select>
            ) : i.type === "bool" ? (
              <input id={`run-${i.name}`} type="checkbox" checked={Boolean(values[i.name])} onChange={(e) => setValues({ ...values, [i.name]: e.target.checked })} />
            ) : (
              <input id={`run-${i.name}`} className={INPUT} value={asText(values[i.name])} onChange={(e) => setValues({ ...values, [i.name]: ["number", "json", "list"].includes(i.type) ? parseLoose(e.target.value) : e.target.value })} />
            )}
          </div>
        ))}
        {err ? <p role="alert" className="text-sm text-danger">{err}</p> : null}
        <div className="flex gap-2 justify-end">
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" onClick={async () => { try { const r = await flowsApi.run(flowId, values); onStarted(r.id); } catch (e) { setErr((e as Error).message); } }}>Run</Button>
        </div>
      </div>
    </div>
  );
}

function TestRun({ catalog, trigger, def }: { catalog: Catalog; trigger: Trigger; def: Definition }) {
  const sample = catalog.triggers.find((t) => t.type === trigger.type)?.event ?? {};
  const [event, setEvent] = useState(JSON.stringify(sample, null, 2));
  const [inputs, setInputs] = useState(JSON.stringify(Object.fromEntries(def.inputs.map((i) => [i.name, i.default ?? ""])), null, 2));
  const [res, setRes] = useState<Awaited<ReturnType<typeof flowsApi.simulate>> | null>(null);
  const [err, setErr] = useState("");
  const run = async () => {
    setErr("");
    try {
      const ev = JSON.parse(event) as Record<string, Json>;
      const t = await flowsApi.testTrigger(trigger, ev);
      setRes(await flowsApi.simulate(def, { ...ev, match: t.captures ?? {} }, JSON.parse(inputs) as Record<string, Json>));
    } catch (e) { setErr((e as Error).message); }
  };
  return (
    <div className="grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(340px, 1fr))" }}>
      <div>
        <p className="text-sm text-text-muted mb-1">Runs the logic for real (variables, conditions, loops, errors) and <strong>simulates every action</strong>: nothing is called, pushed or changed. Approvals are approved.</p>
        <label htmlFor="test-event" className="block text-sm">Trigger event</label>
        <textarea id="test-event" className={`${INPUT} mono`} rows={12} value={event} onChange={(e) => setEvent(e.target.value)} />
        <label htmlFor="test-inputs" className="block text-sm mt-2">Inputs</label>
        <textarea id="test-inputs" className={`${INPUT} mono`} rows={4} value={inputs} onChange={(e) => setInputs(e.target.value)} />
        <Button variant="primary" className="mt-2" onClick={run}>Test run</Button>
        {err ? <p role="alert" className="text-sm text-danger">{err}</p> : null}
      </div>
      <div>
        {res && !res.valid ? <ul role="alert" className="text-sm text-danger">{res.errors.map((e) => <li key={e}>{e}</li>)}</ul> : null}
        {res?.result ? (
          <>
            <p className="text-sm mb-1">Result: <StatusChip status={res.result.status} /> {res.result.error || res.result.message}</p>
            <StepLog log={res.log ?? []} />
            <details className="mt-2"><summary className="text-sm cursor-pointer">Final variables and outputs</summary><pre className="mono text-[12px] bg-bg p-2 overflow-x-auto">{JSON.stringify({ vars: res.result.vars, outputs: res.result.outputs }, null, 2)}</pre></details>
          </>
        ) : null}
      </div>
    </div>
  );
}

export function StepLog({ log }: { log: LogEntry[] }) {
  return (
    <ol aria-label="Step log" className="flex flex-col gap-1">
      {log.map((e, i) => {
        const depth = (e.path.match(/\./g) ?? []).length - 1;
        return (
          <li key={i} className="text-sm border border-border rounded-[3px] p-1 bg-surface" style={{ marginLeft: Math.max(0, depth) * 12 }}>
            <div className="flex flex-wrap items-center gap-2">
              <StatusChip status={e.status} />
              <span className="font-semibold">{e.name ?? e.type}</span>
              <span className="mono text-[12px] text-text-muted">{e.id}{e.iteration !== undefined && e.iteration !== null ? ` #${String(e.iteration)}` : ""}{e.attempt && e.attempt > 1 ? ` attempt ${e.attempt}` : ""}</span>
              {e.reason ? <span className="text-[12px] text-text-muted">{e.reason}</span> : null}
              {e.error ? <span className="text-[12px] text-danger">{e.error}</span> : null}
              {e.message ? <span className="text-[12px] text-warning">{e.message}</span> : null}
            </div>
            {e.output !== undefined && e.output !== null ? <details><summary className="text-[12px] cursor-pointer text-text-muted">output</summary><pre className="mono text-[12px] overflow-x-auto">{JSON.stringify(e.output, null, 2)}</pre></details> : null}
          </li>
        );
      })}
    </ol>
  );
}

function RunsTab({ flowId }: { flowId: string }) {
  const q = useRuns(flowId);
  const items = q.data?.items ?? [];
  if (!items.length) return <p className="text-sm text-text-muted">No runs yet.</p>;
  return (
    <table aria-label="Runs"><thead><tr><th>Run</th><th>Status</th><th>Trigger</th><th>Version</th><th>Started by</th><th>Started</th><th>Finished</th></tr></thead>
      <tbody>{items.map((r) => (
        <tr key={r.id}>
          <td><Link to={`/flows/runs/${r.id}`} className="mono">{r.id.slice(0, 8)}</Link></td>
          <td><StatusChip status={r.status} /></td><td>{r.trigger_type}</td><td>v{r.version}</td><td>{r.started_by}</td>
          <td className="text-sm">{r.created_at ? new Date(r.created_at).toLocaleString() : ""}</td>
          <td className="text-sm">{r.finished_at ? new Date(r.finished_at).toLocaleString() : ""}</td>
        </tr>
      ))}</tbody></table>
  );
}

function VersionsTab({ flowId, canPublish, onLoad }: { flowId: string; canPublish: boolean; onLoad: (v: number) => void }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["flow-versions", flowId], queryFn: () => flowsApi.versions(flowId) });
  return (
    <table aria-label="Versions"><thead><tr><th>Version</th><th>Comment</th><th>By</th><th>When</th><th>Trigger</th><th>Steps</th><th /></tr></thead>
      <tbody>{(q.data?.items ?? []).map((v) => (
        <tr key={v.version}>
          <td>v{v.version} {v.published ? <span className="text-success text-[12px]">published</span> : null}</td>
          <td>{v.comment}</td><td>{v.created_by}</td><td className="text-sm">{v.created_at ? new Date(v.created_at).toLocaleString() : ""}</td>
          <td>{v.trigger_type}</td><td>{v.steps}</td>
          <td className="flex gap-1">
            <Button onClick={() => onLoad(v.version)}>Open in editor</Button>
            {canPublish && !v.published ? <Button onClick={async () => { await flowsApi.publish(flowId, v.version); await qc.invalidateQueries({ queryKey: ["flow-versions", flowId] }); await qc.invalidateQueries({ queryKey: ["flow", flowId] }); }}>Publish this version</Button> : null}
          </td>
        </tr>
      ))}</tbody></table>
  );
}
