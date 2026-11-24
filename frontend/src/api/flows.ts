import { useQuery } from "@tanstack/react-query";
import { request } from "../lib/api";

export type Json = null | boolean | number | string | Json[] | { [k: string]: Json };

export interface FieldSpec {
  name: string; label: string; kind: string; required?: boolean; default?: Json; help?: string; options?: string[];
}
export interface StepSpec {
  type: string; category: string; label: string; icon: string; description: string; fields: FieldSpec[];
  containers?: string[]; branches?: boolean; cases?: boolean; parallel_branches?: boolean; admin_only?: boolean; output?: string;
}
export interface TriggerSpec { type: string; label: string; description: string; fields: FieldSpec[]; event: Record<string, Json>; adapter?: boolean }
export interface Catalog {
  categories: { id: string; label: string; color: string }[];
  steps: StepSpec[];
  triggers: TriggerSpec[];
  operators: { op: string; label: string }[];
  step_settings: FieldSpec[];
  input_types: string[];
  vireonet_events: string[];
  examples: { key: string; name: string; description: string; trigger_type: string }[];
  sources: string[];
}

export type Condition = Record<string, Json>;
export interface Step { id: string; type: string; name?: string; [k: string]: Json | undefined }
export interface FlowInput { name: string; label?: string; type: string; required?: boolean; default?: Json; options?: string[] }
export interface Definition { inputs: FlowInput[]; variables: Record<string, Json>; steps: Step[]; outputs: Record<string, Json> }
export interface Trigger { type: string; conditions?: Condition; [k: string]: Json | undefined }

export interface FlowSummary {
  id: string; name: string; description: string; enabled: boolean; trigger_type: string; latest_version: number;
  published_version: number | null; trigger: Trigger; steps: number; runs: number; last_run_at: string | null; updated_at: string | null;
}
export interface Flow extends Omit<FlowSummary, "steps" | "runs" | "last_run_at"> {
  version: number; definition: Definition; comment: string; webhook_url: string; webhook_token?: string;
}
export interface LogEntry {
  id: string; type: string; name?: string; path: string; status: string; attempt?: number; iteration?: Json;
  started_at?: string; finished_at?: string; output?: Json; error?: string; reason?: string; message?: string; params?: Json;
}
export interface Run {
  id: string; flow_id: string; flow_name: string; version: number; parent_run_id: string | null; trigger_type: string;
  status: string; error: string; started_by: string; waiting: Record<string, { step_id: string; message: string; role: string; since: string }>;
  created_at: string; finished_at: string | null; steps_logged: number;
  trigger_event?: Record<string, Json>; inputs?: Record<string, Json>; log?: LogEntry[]; variables?: Record<string, Json>; outputs?: Record<string, Json>;
}

const post = <T,>(path: string, body?: unknown) => request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const flowsApi = {
  catalog: () => request<Catalog>("/flows/catalog"),
  list: () => request<{ items: FlowSummary[] }>("/flows"),
  get: (id: string, version?: number) => request<Flow>(`/flows/${id}${version ? `?version=${version}` : ""}`),
  create: (body: { name: string; description: string; trigger: Trigger; definition: Definition; comment?: string }) => post<Flow>("/flows", body),
  update: (id: string, body: Partial<{ name: string; description: string; trigger: Trigger; definition: Definition; comment: string }>) =>
    request<Flow>(`/flows/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  remove: (id: string) => request<void>(`/flows/${id}`, { method: "DELETE" }),
  versions: (id: string) => request<{ items: { version: number; comment: string; created_by: string; created_at: string; published: boolean; steps: number; trigger_type: string }[] }>(`/flows/${id}/versions`),
  publish: (id: string, version?: number) => post<Flow>(`/flows/${id}/publish${version ? `?version=${version}` : ""}`),
  enable: (id: string, enabled: boolean) => post<Flow>(`/flows/${id}/enable?enabled=${enabled}`),
  run: (id: string, inputs: Record<string, Json>, version?: number) => post<Run>(`/flows/${id}/run`, { inputs, version }),
  exportFlow: (id: string) => request<Record<string, Json>>(`/flows/${id}/export`),
  importFlow: (data: unknown, name?: string) => post<Flow>("/flows/import", { data, name }),
  rotateToken: (id: string) => post<{ webhook_url: string; webhook_token: string }>(`/flows/${id}/token`),
  validate: (definition: Definition, trigger: Trigger) => post<{ valid: boolean; errors: string[]; warnings: string[] }>("/flows/validate", { definition, trigger }),
  testTrigger: (trigger: Trigger, event?: unknown) => post<{ valid: boolean; errors: string[]; matched: boolean; reason?: string; captures?: Record<string, Json>; dedup_key?: string; event?: Json }>("/flows/test-trigger", { trigger, event }),
  simulate: (definition: Definition, trigger_event: unknown, inputs: Record<string, Json>) =>
    post<{ valid: boolean; errors: string[]; result?: { status: string; error: string; message: string; vars: Json; outputs: Json }; log?: LogEntry[] }>("/flows/simulate", { definition, trigger_event, inputs }),
  example: (key: string) => request<{ name: string; description: string; trigger: Trigger; definition: Definition }>(`/flows/examples/${key}`),
  runs: (flowId?: string) => request<{ items: Run[] }>(`/flows/runs${flowId ? `?flow_id=${flowId}` : ""}`),
  getRun: (id: string) => request<Run>(`/flows/runs/${id}`),
  decide: (runId: string, stepId: string, approved: boolean, comment: string) => post(`/flows/runs/${runId}/decide/${stepId}`, { approved, comment }),
  cancel: (runId: string) => post(`/flows/runs/${runId}/cancel`),
};

export function useCatalog() {
  return useQuery({ queryKey: ["flow-catalog"], queryFn: flowsApi.catalog, staleTime: Infinity });
}
export function useFlows() {
  return useQuery({ queryKey: ["flows"], queryFn: flowsApi.list, refetchInterval: 15000 });
}
export function useRuns(flowId?: string) {
  return useQuery({ queryKey: ["flow-runs", flowId ?? "all"], queryFn: () => flowsApi.runs(flowId), refetchInterval: 5000 });
}
export function useRun(id: string) {
  return useQuery({
    queryKey: ["flow-run", id],
    queryFn: () => flowsApi.getRun(id),
    refetchInterval: (q) => (q.state.data && ["succeeded", "failed", "cancelled"].includes(q.state.data.status) ? false : 2000),
  });
}

export const TERMINAL = ["succeeded", "failed", "cancelled"];

export function emptyDefinition(): Definition {
  return { inputs: [], variables: {}, steps: [], outputs: {} };
}

/** A new step of a type, with its defaults and empty containers. */
export function newStep(spec: StepSpec, taken: Set<string>): Step {
  let n = 1;
  const base = spec.type.replace(/[^a-z]/g, "").slice(0, 8) || "step";
  while (taken.has(`${base}${n}`)) n += 1;
  const step: Step = { id: `${base}${n}`, type: spec.type };
  for (const f of spec.fields) if (f.default !== undefined && f.name !== "save_as") step[f.name] = f.default;
  for (const c of spec.containers ?? []) step[c] = [];
  if (spec.branches) step.branches = [{ condition: { field: "", op: "equals", value: "" }, steps: [] }];
  if (spec.cases) step.cases = [{ value: "", steps: [] }];
  if (spec.parallel_branches) step.branches = [{ name: "Branch 1", steps: [] }, { name: "Branch 2", steps: [] }];
  return step;
}

/** Every step id in a step tree. */
export function allIds(steps: Step[], out = new Set<string>()): Set<string> {
  for (const s of steps) {
    out.add(s.id);
    for (const list of childLists(s)) allIds(list.steps, out);
  }
  return out;
}

export interface ChildList { key: string; label: string; steps: Step[]; set: (steps: Step[]) => Step }

/** The nested step lists of a step, with setters that return an updated copy. */
export function childLists(s: Step): ChildList[] {
  const out: ChildList[] = [];
  const branches = (s.branches as { condition?: Json; name?: string; steps: Step[] }[] | undefined) ?? [];
  if (s.type === "if") {
    branches.forEach((b, i) => out.push({ key: `b${i}`, label: i === 0 ? "If" : "Else if", steps: b.steps ?? [], set: (steps) => ({ ...s, branches: branches.map((x, j) => (j === i ? { ...x, steps } : x)) as unknown as Json }) }));
    out.push({ key: "else", label: "Otherwise", steps: (s.else as Step[] | undefined) ?? [], set: (steps) => ({ ...s, else: steps as unknown as Json }) });
  } else if (s.type === "switch") {
    const cases = (s.cases as { value: Json; steps: Step[] }[] | undefined) ?? [];
    cases.forEach((c, i) => out.push({ key: `c${i}`, label: "Case", steps: c.steps ?? [], set: (steps) => ({ ...s, cases: cases.map((x, j) => (j === i ? { ...x, steps } : x)) as unknown as Json }) }));
    out.push({ key: "default", label: "Default", steps: (s.default as Step[] | undefined) ?? [], set: (steps) => ({ ...s, default: steps as unknown as Json }) });
  } else if (s.type === "parallel") {
    branches.forEach((b, i) => out.push({ key: `p${i}`, label: String(b.name ?? `Branch ${i + 1}`), steps: b.steps ?? [], set: (steps) => ({ ...s, branches: branches.map((x, j) => (j === i ? { ...x, steps } : x)) as unknown as Json }) }));
  } else {
    const labels: Record<string, string> = { steps: s.type === "try" ? "Try" : "Do", catch: "On error", finally: "Finally" };
    for (const key of ["steps", "catch", "finally"]) {
      if (Array.isArray(s[key])) out.push({ key, label: labels[key], steps: s[key] as unknown as Step[], set: (steps) => ({ ...s, [key]: steps as unknown as Json }) });
    }
  }
  return out;
}

// --- addressing nested step lists (drag and drop) ---------------------------------------

/** Path to a step list: each segment is (step index, child list key). [] = the top level. */
export type ListPath = { i: number; key: string }[];

export function getList(root: Step[], path: ListPath): Step[] {
  let list = root;
  for (const seg of path) {
    const child = childLists(list[seg.i]).find((c) => c.key === seg.key);
    if (!child) return [];
    list = child.steps;
  }
  return list;
}

export function setList(root: Step[], path: ListPath, next: Step[]): Step[] {
  if (path.length === 0) return next;
  const [seg, ...rest] = path;
  const step = root[seg.i];
  const child = childLists(step).find((c) => c.key === seg.key);
  if (!child) return root;
  const out = [...root];
  out[seg.i] = child.set(setList(child.steps, rest, next));
  return out;
}

const samePrefix = (a: ListPath, b: ListPath) => a.every((s, k) => b[k] && b[k].i === s.i && b[k].key === s.key);

/** Move a step from (from, index) to (to, toIndex). Null when the move is impossible
 * (into itself or one of its own blocks). */
export function moveStep(root: Step[], from: ListPath, index: number, to: ListPath, toIndex: number): Step[] | null {
  // Dropping inside the moved step's own blocks?
  if (to.length > from.length && samePrefix(from, to) && to[from.length].i === index) return null;
  const item = getList(root, from)[index];
  if (!item) return null;
  const src = [...getList(root, from)];
  src.splice(index, 1);
  const removed = setList(root, from, src);
  const target = to.map((s) => ({ ...s }));
  let at = toIndex;
  if (target.length === from.length && samePrefix(from, target)) {
    if (toIndex > index) at -= 1; // same list, later position
  } else if (target.length > from.length && samePrefix(from, target) && target[from.length].i > index) {
    target[from.length].i -= 1; // the target list sits after the removed step
  }
  const dest = [...getList(removed, target)];
  dest.splice(at, 0, item);
  return setList(removed, target, dest);
}
