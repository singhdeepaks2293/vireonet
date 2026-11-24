import { request } from "../lib/api";

export interface TemplateRow { template: string; platform: string; command: string; source: "user" | "ntc" }
export interface TemplateList { total: number; templates: TemplateRow[]; platform_map: Record<string, string[]>; user_dir: string }
export interface ParseResponse { ok: boolean; error?: string; template?: string; source?: string; platform?: string; headers: string[]; rows: Record<string, unknown>[] }

const post = <T,>(path: string, body: unknown) => request<T>(path, { method: "POST", body: JSON.stringify(body) });

export const textfsmApi = {
  list: (platform: string, q: string) => {
    const p = new URLSearchParams();
    if (platform) p.set("platform", platform);
    if (q) p.set("q", q);
    return request<TemplateList>(`/textfsm/templates?${p.toString()}`);
  },
  text: (source: string, name: string) => request<{ text: string }>(`/textfsm/templates/${source}/${encodeURIComponent(name)}`),
  parse: (body: { platform: string; command: string; output: string; template?: string }) => post<ParseResponse>("/textfsm/parse", body),
  validate: (template: string) => post<{ valid: boolean; errors: string[]; values: string[] }>("/textfsm/validate", { template }),
};
