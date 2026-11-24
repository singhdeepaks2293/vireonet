import { createContext, useContext, useId, useRef, useState, type ReactNode } from "react";
import type { Catalog, Condition, FieldSpec, Json } from "../../api/flows";
import { useControllers } from "../../api/controllers";

export const INPUT = "w-full px-2 py-1 text-sm bg-surface text-text border border-border rounded-[3px] focus:border-highlight outline-none";
export const SELECT = "px-2 py-1 text-sm bg-surface text-text border border-border rounded-[3px]";

// --- "dynamic content": insert a variable into the field that had focus last ---

type Inserter = (token: string) => void;
const InsertCtx = createContext<{ set: (fn: Inserter | null) => void; insert: (t: string) => void; suggestions: string[] }>({ set: () => {}, insert: () => {}, suggestions: [] });

export function InsertProvider({ suggestions, children }: { suggestions: string[]; children: ReactNode }) {
  const current = useRef<Inserter | null>(null);
  return (
    <InsertCtx.Provider value={{ set: (fn) => { current.current = fn; }, insert: (t) => current.current?.(t), suggestions }}>
      {children}
    </InsertCtx.Provider>
  );
}

export function useInsert() {
  return useContext(InsertCtx);
}

/** A text input / textarea that accepts inserted {{ variables }}. */
export function TextField({ id, value, onChange, multiline = false, mono = false, placeholder, rows = 3, ariaLabel }: {
  id?: string; value: string; onChange: (v: string) => void; multiline?: boolean; mono?: boolean; placeholder?: string; rows?: number; ariaLabel?: string;
}) {
  const ctx = useInsert();
  const ref = useRef<HTMLInputElement & HTMLTextAreaElement>(null);
  const listId = useId();
  const onFocus = () => ctx.set((token) => {
    const el = ref.current;
    const at = el?.selectionStart ?? value.length;
    const end = el?.selectionEnd ?? at;
    onChange(value.slice(0, at) + `{{ ${token} }}` + value.slice(end));
  });
  const cls = `${INPUT} ${mono ? "mono" : ""}`;
  return multiline ? (
    <textarea ref={ref} id={id} aria-label={ariaLabel} className={cls} rows={rows} spellCheck={false} value={value} placeholder={placeholder} onFocus={onFocus} onChange={(e) => onChange(e.target.value)} />
  ) : (
    <>
      <input ref={ref} id={id} aria-label={ariaLabel} list={listId} className={cls} value={value} placeholder={placeholder} onFocus={onFocus} onChange={(e) => onChange(e.target.value)} />
      <datalist id={listId}>{ctx.suggestions.slice(0, 200).map((s) => <option key={s} value={`{{ ${s} }}`} />)}</datalist>
    </>
  );
}

// --- value helpers -----------------------------------------------------------------------

export function asText(v: Json | undefined): string {
  if (v === undefined || v === null) return "";
  return typeof v === "string" ? v : JSON.stringify(v);
}

/** Text typed in a "json" field: a JSON value when it parses, else the text itself (a template). */
export function parseLoose(text: string): Json {
  const t = text.trim();
  if (t === "") return "";
  if (/^(\{|\[|-?\d|true$|false$|null$|")/.test(t) && !t.startsWith("{{")) {
    try { return JSON.parse(t) as Json; } catch { /* keep text */ }
  }
  return text;
}

function ListField({ id, value, onChange }: { id: string; value: Json | undefined; onChange: (v: Json) => void }) {
  const isTemplate = typeof value === "string";
  const text = isTemplate ? String(value) : (Array.isArray(value) ? value.map((x) => asText(x)).join("\n") : "");
  return (
    <TextField id={id} multiline rows={Math.min(6, Math.max(2, text.split("\n").length))} value={text} placeholder="one per line, or {{ expression }}"
      onChange={(t) => onChange(t.trim().startsWith("{{") ? t : t.split("\n").map((x) => x.trim()).filter(Boolean))} />
  );
}

function KvField({ value, onChange, label }: { value: Json | undefined; onChange: (v: Json) => void; label: string }) {
  const obj = (value && typeof value === "object" && !Array.isArray(value) ? value : {}) as Record<string, Json>;
  const rows = Object.entries(obj);
  const set = (next: [string, Json][]) => onChange(Object.fromEntries(next));
  return (
    <div className="flex flex-col gap-1">
      {rows.map(([k, v], i) => (
        <div key={i} className="flex gap-1">
          <input aria-label={`${label} key ${i + 1}`} className={`${INPUT} mono max-w-[180px]`} value={k} onChange={(e) => set(rows.map((r, j) => (j === i ? [e.target.value, r[1]] : r)))} />
          <TextField ariaLabel={`${label} value ${i + 1}`} value={asText(v)} onChange={(t) => set(rows.map((r, j) => (j === i ? [r[0], parseLoose(t)] : r)))} />
          <button type="button" className="text-sm text-danger px-1" aria-label={`Remove ${k || "row"}`} onClick={() => set(rows.filter((_, j) => j !== i))}>✕</button>
        </div>
      ))}
      <button type="button" className="text-sm text-left text-accent" onClick={() => set([...rows, [`key${rows.length + 1}`, ""]])}>+ Add</button>
    </div>
  );
}

export function FieldEditor({ spec, value, onChange, catalog, idPrefix }: { spec: FieldSpec; value: Json | undefined; onChange: (v: Json) => void; catalog: Catalog; idPrefix: string }) {
  const id = `${idPrefix}-${spec.name}`;
  let input: ReactNode;
  switch (spec.kind) {
    case "textarea":
      input = <TextField id={id} multiline value={asText(value)} onChange={onChange} />; break;
    case "code":
      input = <TextField id={id} multiline mono rows={8} value={asText(value)} onChange={onChange} />; break;
    case "number":
      input = <TextField id={id} value={asText(value)} onChange={(t) => onChange(t.trim() !== "" && !Number.isNaN(Number(t)) ? Number(t) : t)} />; break;
    case "bool":
      input = <input id={id} type="checkbox" checked={Boolean(value)} onChange={(e) => onChange(e.target.checked)} />; break;
    case "select":
      input = (
        <select id={id} className={SELECT} value={asText(value ?? spec.default ?? "")} onChange={(e) => onChange(e.target.value)}>
          {(spec.options ?? []).map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      ); break;
    case "list":
      input = <ListField id={id} value={value} onChange={onChange} />; break;
    case "kv":
      input = <KvField label={spec.label} value={value} onChange={onChange} />; break;
    case "json":
      input = <TextField id={id} multiline mono rows={typeof value === "object" && value ? 4 : 1} value={typeof value === "object" && value !== null ? JSON.stringify(value, null, 2) : asText(value)} onChange={(t) => onChange(parseLoose(t))} />; break;
    case "controller":
      input = <ControllerField id={id} value={asText(value ?? "auto")} onChange={onChange} />; break;
    case "condition":
      input = <ConditionEditor value={(value as Condition) ?? null} onChange={(c) => onChange(c as Json)} catalog={catalog} label={spec.label} />; break;
    default:
      input = <TextField id={id} value={asText(value)} onChange={onChange} />;
  }
  return (
    <div className={spec.kind === "bool" ? "flex items-center gap-2" : ""}>
      {spec.kind === "bool" ? input : null}
      <label htmlFor={id} className="block text-sm text-text-muted mb-[2px]">{spec.label}{spec.required ? " *" : ""}</label>
      {spec.kind === "bool" ? null : input}
      {spec.help ? <p className="text-[12px] text-text-muted mt-[2px]">{spec.help}</p> : null}
    </div>
  );
}

function ControllerField({ id, value, onChange }: { id: string; value: string; onChange: (v: Json) => void }) {
  const q = useControllers();
  return (
    <select id={id} className={SELECT} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="auto">auto (by the device's platform)</option>
      {(q.data?.items ?? []).map((c) => <option key={c.name} value={c.name}>{c.name} — {c.vendor}</option>)}
      {value !== "auto" && !(q.data?.items ?? []).some((c) => c.name === value) ? <option value={value}>{value}</option> : null}
    </select>
  );
}

// --- conditions ---------------------------------------------------------------------------

const GROUP_KEYS = ["all", "any", "none"] as const;
type Mode = (typeof GROUP_KEYS)[number];
const NO_VALUE = new Set(["exists", "missing", "is_empty", "not_empty", "is_true", "is_false"]);

function toGroup(c: Condition | null | undefined): { mode: Mode; items: Condition[] } {
  if (!c || Object.keys(c).length === 0) return { mode: "all", items: [] };
  for (const k of GROUP_KEYS) if (Array.isArray(c[k])) return { mode: k, items: c[k] as Condition[] };
  return { mode: "all", items: [c] };
}

function Rule({ rule, onChange, onRemove, catalog, n }: { rule: Condition; onChange: (c: Condition) => void; onRemove: () => void; catalog: Catalog; n: string }) {
  if ("expr" in rule) {
    return (
      <div className="flex gap-1 items-start">
        <span className="text-sm text-text-muted pt-1 w-[70px]">expression</span>
        <TextField ariaLabel={`Condition ${n} expression`} mono value={asText(rule.expr)} onChange={(t) => onChange({ expr: t })} placeholder="vars.count > 3 and 'core' in trigger.ticket.summary" />
        <button type="button" className="text-sm text-danger px-1" aria-label={`Remove condition ${n}`} onClick={onRemove}>✕</button>
      </div>
    );
  }
  const op = String(rule.op ?? "equals");
  return (
    <div className="flex flex-wrap gap-1 items-start">
      <div className="min-w-[200px] flex-1"><TextField ariaLabel={`Condition ${n} field`} mono value={asText(rule.field)} onChange={(t) => onChange({ ...rule, field: t })} placeholder="ticket.summary, vars.x, or *" /></div>
      <select aria-label={`Condition ${n} operator`} className={SELECT} value={op} onChange={(e) => onChange({ ...rule, op: e.target.value })}>
        {catalog.operators.map((o) => <option key={o.op} value={o.op}>{o.label}</option>)}
      </select>
      {op === "changed" ? (
        <>
          <div className="w-[120px]"><TextField ariaLabel={`Condition ${n} from`} value={asText(rule.from)} onChange={(t) => onChange({ ...rule, from: t })} placeholder="from (any)" /></div>
          <div className="w-[120px]"><TextField ariaLabel={`Condition ${n} to`} value={asText(rule.to)} onChange={(t) => onChange({ ...rule, to: t })} placeholder="to (any)" /></div>
        </>
      ) : NO_VALUE.has(op) ? null : (
        <div className="min-w-[180px] flex-1"><TextField ariaLabel={`Condition ${n} value`} mono={op.includes("match")} value={asText(rule.value)} onChange={(t) => onChange({ ...rule, value: ["in", "not_in", "contains_any", "contains_all"].includes(op) ? parseLoose(t) : (["gt", "gte", "lt", "lte", "length_gt"].includes(op) ? parseLoose(t) : t) })} placeholder={op.includes("match") ? "regex, e.g. (?P<device>\\S+)" : "value"} /></div>
      )}
      <label className="text-[12px] text-text-muted flex items-center gap-1 pt-1"><input type="checkbox" checked={Boolean(rule.case_sensitive)} onChange={(e) => onChange({ ...rule, case_sensitive: e.target.checked })} />Aa</label>
      <button type="button" className="text-sm text-danger px-1" aria-label={`Remove condition ${n}`} onClick={onRemove}>✕</button>
    </div>
  );
}

export function ConditionEditor({ value, onChange, catalog, label = "Condition", depth = 0 }: { value: Condition | null; onChange: (c: Condition | null) => void; catalog: Catalog; label?: string; depth?: number }) {
  const g = toGroup(value);
  const [open] = useState(true);
  const set = (mode: Mode, items: Condition[]) => onChange(items.length === 0 && depth === 0 ? null : { [mode]: items as Json });
  const isGroup = (c: Condition) => GROUP_KEYS.some((k) => Array.isArray(c[k]));
  if (!open) return null;
  return (
    <div className={`border border-border rounded-[3px] p-2 ${depth ? "bg-muted" : "bg-bg"}`} role="group" aria-label={depth ? `${label} group` : label}>
      <div className="flex items-center gap-2 mb-1 text-sm">
        <select aria-label={`${label} match mode`} className={SELECT} value={g.mode} onChange={(e) => set(e.target.value as Mode, g.items)}>
          <option value="all">ALL of these are true</option>
          <option value="any">ANY of these is true</option>
          <option value="none">NONE of these is true</option>
        </select>
        {g.items.length === 0 ? <span className="text-text-muted">{depth === 0 ? "No condition: always true." : "Empty group."}</span> : null}
      </div>
      <div className="flex flex-col gap-1">
        {g.items.map((c, i) => isGroup(c) ? (
          <div key={i} className="flex gap-1 items-start">
            <div className="flex-1"><ConditionEditor value={c} depth={depth + 1} catalog={catalog} label={label} onChange={(nc) => set(g.mode, nc ? g.items.map((x, j) => (j === i ? nc : x)) : g.items.filter((_, j) => j !== i))} /></div>
            <button type="button" className="text-sm text-danger px-1" aria-label={`Remove group ${i + 1}`} onClick={() => set(g.mode, g.items.filter((_, j) => j !== i))}>✕</button>
          </div>
        ) : (
          <Rule key={i} n={`${depth}.${i + 1}`} rule={c} catalog={catalog} onChange={(nc) => set(g.mode, g.items.map((x, j) => (j === i ? nc : x)))} onRemove={() => set(g.mode, g.items.filter((_, j) => j !== i))} />
        ))}
      </div>
      <div className="flex gap-3 mt-1 text-sm">
        <button type="button" className="text-accent" onClick={() => set(g.mode, [...g.items, { field: "", op: "contains", value: "" }])}>+ Condition</button>
        {depth < 3 ? <button type="button" className="text-accent" onClick={() => set(g.mode, [...g.items, { any: [] }])}>+ Group</button> : null}
        <button type="button" className="text-accent" onClick={() => set(g.mode, [...g.items, { expr: "" }])}>+ Expression</button>
      </div>
    </div>
  );
}
