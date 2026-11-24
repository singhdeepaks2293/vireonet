import { useEffect, useState } from "react";
import type { Catalog, Condition, Json, Step, StepSpec } from "../../api/flows";
import { childLists } from "../../api/flows";
import { ConditionEditor, FieldEditor, INPUT, TextField, asText, parseLoose } from "./fields";

const ICONS: Record<string, string> = {
  variable: "x=", list: "[+]", log: "✎", wait: "⏱", stop: "■", fail: "✖", if: "⑂", loop: "↻", switch: "⇶", parallel: "⫴",
  try: "⛨", flow: "⧉", terminal: ">_", textfsm: "⌗", checks: "✔", push: "⇪", rollback: "↶", device: "▣", ticket: "🎫",
  comment: "💬", close: "☑", api: "⇄", script: "{}", notify: "🔔", approval: "✋",
};

interface CardProps {
  step: Step;
  path: string;
  catalog: Catalog;
  issues: string[];
  onChange: (s: Step) => void;
  onRemove: () => void;
  onMove: (dir: -1 | 1) => void;
  onDuplicate: () => void;
  renderChild: (key: string) => JSX.Element;
  defaultOpen?: boolean;
  forceOpen?: { open: boolean; n: number };
  dragHandle?: JSX.Element;
}

export function StepCard({ step, path, catalog, issues, onChange, onRemove, onMove, onDuplicate, renderChild, dragHandle, defaultOpen = true, forceOpen }: CardProps) {
  const spec: StepSpec | undefined = catalog.steps.find((s) => s.type === step.type);
  const color = catalog.categories.find((c) => c.id === spec?.category)?.color ?? "#7f8c8d";
  const [open, setOpen] = useState(defaultOpen);
  const [settings, setSettings] = useState(false);
  const set = (k: string, v: Json) => onChange({ ...step, [k]: v });
  const mine = issues.filter((e) => e.includes(`(${step.id}:`));
  useEffect(() => { if (forceOpen) setOpen(forceOpen.open); }, [forceOpen]);
  useEffect(() => { if (mine.length) setOpen(true); }, [mine.length]);
  const summary = (spec?.fields ?? [])
    .filter((f) => !["save_as"].includes(f.name) && step[f.name] !== undefined && step[f.name] !== "" && f.kind !== "condition")
    .slice(0, 2)
    .map((f) => `${f.label}: ${typeof step[f.name] === "string" ? step[f.name] : JSON.stringify(step[f.name])}`)
    .join(" · ");
  const hasSettings = catalog.step_settings.some((f) => step[f.name] !== undefined && step[f.name] !== f.default && step[f.name] !== "");

  if (!spec) return <div className="p-2 border border-danger rounded text-sm text-danger">Unknown step type {step.type}</div>;

  const branches = (step.branches as { condition?: Condition; name?: string; steps: Step[] }[] | undefined) ?? [];
  const cases = (step.cases as { value: Json; steps: Step[] }[] | undefined) ?? [];
  const lists = childLists(step);

  return (
    <article aria-label={`${spec.label} ${step.id}`} className={`rounded-[6px] border bg-surface shadow-[0_1px_2px_rgba(0,0,0,0.08)] ${mine.length ? "border-danger" : "border-border"} ${step.disabled ? "opacity-60" : ""}`}>
      <header className="flex items-center gap-2 px-2 py-[6px] rounded-t-[6px]" style={{ borderLeft: `4px solid ${color}` }}>
        {dragHandle}
        <span aria-hidden="true" className="inline-flex items-center justify-center w-[26px] h-[26px] rounded-[6px] text-[12px] font-semibold text-white" style={{ background: color }}>{ICONS[spec.icon] ?? "•"}</span>
        <button type="button" className="text-sm font-semibold text-left" aria-expanded={open} onClick={() => setOpen(!open)}>{step.name || spec.label}</button>
        <span className="mono text-[12px] text-text-muted" title="Step id: its result is steps.<id>">{step.id}</span>
        {step.disabled ? <span className="text-[12px] text-warning">disabled</span> : null}
        {hasSettings ? <span className="text-[12px] text-info">settings</span> : null}
        <span className="flex-1" />
        <button type="button" className="px-1 text-sm" aria-label={`Move ${step.id} up`} onClick={() => onMove(-1)}>↑</button>
        <button type="button" className="px-1 text-sm" aria-label={`Move ${step.id} down`} onClick={() => onMove(1)}>↓</button>
        <button type="button" className="px-1 text-sm" aria-label={`Duplicate ${step.id}`} onClick={onDuplicate}>⧉</button>
        <button type="button" className="px-1 text-sm" aria-label={`Settings for ${step.id}`} aria-pressed={settings} onClick={() => setSettings(!settings)}>⚙</button>
        <button type="button" className="px-1 text-sm text-danger" aria-label={`Delete ${step.id}`} onClick={onRemove}>✕</button>
      </header>
      <div className="px-3 pb-3 pt-1 flex flex-col gap-2">
      {open ? (
        <>
          {spec.description ? <p className="text-[12px] text-text-muted">{spec.description}</p> : null}
          <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))" }}>
            <div>
              <label className="block text-sm text-text-muted mb-[2px]" htmlFor={`${path}-name`}>Step name</label>
              <input id={`${path}-name`} className={INPUT} value={step.name ?? ""} placeholder={spec.label} onChange={(e) => set("name", e.target.value)} />
            </div>
            {spec.fields.map((f) => (
              <div key={f.name} className={["textarea", "code", "condition", "json", "kv"].includes(f.kind) ? "col-span-full" : ""}>
                <FieldEditor spec={f} value={step[f.name] as Json | undefined} onChange={(v) => set(f.name, v)} catalog={catalog} idPrefix={`${path}`} />
              </div>
            ))}
          </div>
          {settings ? (
            <fieldset className="border border-border rounded-[3px] p-2">
              <legend className="text-sm px-1">Settings (run after, retries, timeout, errors)</legend>
              <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))" }}>
                {catalog.step_settings.map((f) => (
                  <div key={f.name} className={f.kind === "condition" || f.kind === "textarea" ? "col-span-full" : ""}>
                    <FieldEditor spec={f} value={step[f.name] as Json | undefined} onChange={(v) => set(f.name, v)} catalog={catalog} idPrefix={`${path}-set`} />
                  </div>
                ))}
              </div>
            </fieldset>
          ) : null}
        </>
      ) : (
        <button type="button" className="text-left text-[12px] text-text-muted truncate" onClick={() => setOpen(true)} title="Show the fields">{summary || spec.description}</button>
      )}
          {step.type === "if" ? branches.map((b, i) => (
            <div key={i} className="flex flex-col gap-1">
              <div className="flex items-center gap-2"><span className="text-sm font-semibold" style={{ color }}>{i === 0 ? "If" : "Else if"}</span>
                {i > 0 ? <button type="button" className="text-[12px] text-danger" onClick={() => set("branches", branches.filter((_, j) => j !== i) as unknown as Json)}>remove branch</button> : null}
              </div>
              <ConditionEditor value={(b.condition as Condition) ?? null} catalog={catalog} label={`${step.id} branch ${i + 1} condition`} onChange={(c) => set("branches", branches.map((x, j) => (j === i ? { ...x, condition: c } : x)) as unknown as Json)} />
              <Nested color={color} label="Then">{renderChild(`b${i}`)}</Nested>
            </div>
          )) : null}
          {step.type === "if" ? (
            <>
              <button type="button" className="text-sm text-accent text-left" onClick={() => set("branches", [...branches, { condition: { field: "", op: "equals", value: "" }, steps: [] }] as unknown as Json)}>+ Else if</button>
              <Nested color={color} label="Otherwise">{renderChild("else")}</Nested>
            </>
          ) : null}
          {step.type === "switch" ? (
            <>
              {cases.map((c, i) => (
                <div key={i}>
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-sm font-semibold" style={{ color }}>Case</span>
                    <div className="w-[220px]"><TextField ariaLabel={`${step.id} case ${i + 1} value`} value={asText(c.value)} onChange={(t) => set("cases", cases.map((x, j) => (j === i ? { ...x, value: parseLoose(t) } : x)) as unknown as Json)} placeholder='value or ["a","b"]' /></div>
                    <button type="button" className="text-[12px] text-danger" onClick={() => set("cases", cases.filter((_, j) => j !== i) as unknown as Json)}>remove</button>
                  </div>
                  <Nested color={color} label="Do">{renderChild(`c${i}`)}</Nested>
                </div>
              ))}
              <button type="button" className="text-sm text-accent text-left" onClick={() => set("cases", [...cases, { value: "", steps: [] }] as unknown as Json)}>+ Case</button>
              <Nested color={color} label="Default">{renderChild("default")}</Nested>
            </>
          ) : null}
          {step.type === "parallel" ? (
            <>
              <div className="grid gap-2" style={{ gridTemplateColumns: `repeat(${Math.min(branches.length, 3)}, minmax(0, 1fr))` }}>
                {branches.map((b, i) => (
                  <div key={i}>
                    <input aria-label={`${step.id} branch ${i + 1} name`} className={`${INPUT} mb-1`} value={String(b.name ?? "")} onChange={(e) => set("branches", branches.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)) as unknown as Json)} />
                    <Nested color={color} label="">{renderChild(`p${i}`)}</Nested>
                  </div>
                ))}
              </div>
              <button type="button" className="text-sm text-accent text-left" onClick={() => set("branches", [...branches, { name: `Branch ${branches.length + 1}`, steps: [] }] as unknown as Json)}>+ Branch</button>
            </>
          ) : null}
          {!["if", "switch", "parallel"].includes(step.type) ? lists.map((l) => (
            <Nested key={l.key} color={color} label={l.label}>{renderChild(l.key)}</Nested>
          )) : null}
          {mine.length ? <ul role="alert" className="text-[12px] text-danger">{mine.map((e) => <li key={e}>{e.replace(/^.*?\)\S*:?\s*/, "")}</li>)}</ul> : null}
      </div>
    </article>
  );
}

function Nested({ color, label, children }: { color: string; label: string; children: JSX.Element }) {
  return (
    <div className="pl-3 border-l-2" style={{ borderColor: color }}>
      {label ? <p className="text-[12px] font-semibold uppercase tracking-wide mb-1" style={{ color }}>{label}</p> : null}
      {children}
    </div>
  );
}
