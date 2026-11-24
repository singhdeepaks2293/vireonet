import { useQuery } from "@tanstack/react-query";
import { request } from "../lib/api";

export type Platform = string;

export interface Expectation {
  contains?: string;
  not_contains?: string;
  regex?: string;
  not_regex?: string;
  /** parse: textfsm — field -> regex; a parsed row must (row) / must not (no_row) match */
  row?: Record<string, string>;
  no_row?: Record<string, string>;
}

export interface CheckView {
  kind: "builtin" | "command";
  name: string;
  description?: string;
  on_fail: "block" | "warn";
  on_missing_platform?: "skip" | "fail";
  /** platform -> command template */
  command?: Record<Platform, string>;
  /** platform (or "*" for all) -> expectation */
  expect?: Record<string, Expectation>;
}

export interface TemplatesSummary {
  source: string;
  allowed_command_prefixes: string[];
  change_types: Record<string, { pre: CheckView[]; post: CheckView[] }>;
}

export interface TemplatesResponse {
  valid: boolean;
  errors: string[];
  templates: TemplatesSummary | null;
  yaml?: string;
}

export interface PreviewRow {
  name: string;
  kind: "builtin" | "command";
  on_fail: "block" | "warn";
  command?: string | null;
  expect?: Expectation;
  error?: string;
}

export interface PreviewResponse {
  valid: boolean;
  errors: string[];
  change_type?: string;
  platform?: string;
  preview: { pre: PreviewRow[]; post: PreviewRow[] } | null;
}

export const checksApi = {
  get: () => request<TemplatesResponse>("/checks/templates"),
  sampleIntents: () => request<Record<string, Record<string, unknown>>>("/checks/sample-intents"),
  validate: (yaml: string) =>
    request<TemplatesResponse>("/checks/templates/validate", { method: "POST", body: JSON.stringify({ yaml }) }),
  preview: (body: { intent: unknown; platform: string; yaml?: string }) =>
    request<PreviewResponse>("/checks/templates/preview", { method: "POST", body: JSON.stringify(body) }),
};

export function useCheckTemplates() {
  return useQuery({ queryKey: ["check-templates"], queryFn: () => checksApi.get() });
}
export function useSampleIntents() {
  return useQuery({ queryKey: ["check-sample-intents"], queryFn: () => checksApi.sampleIntents() });
}

export interface RenderTemplatesResponse {
  valid: boolean;
  errors: string[];
  builtin_platforms?: string[];
  platforms: string[];
  aliases: Record<string, string>;
  source?: string;
  yaml: string;
  templates?: Record<string, string[]>;
}

export interface RenderedConfig {
  device: string;
  platform: string;
  format: "cli" | "set";
  source: "builtin" | "template";
  lines: string[];
}

export interface RenderPreviewResponse {
  valid: boolean;
  errors: string[];
  rendered: RenderedConfig | null;
}

export const renderApi = {
  get: () => request<RenderTemplatesResponse>("/render/templates"),
  preview: (body: { intent: unknown; platform: string; yaml?: string }) =>
    request<RenderPreviewResponse>("/render/preview", { method: "POST", body: JSON.stringify(body) }),
};

export function useRenderTemplates() {
  return useQuery({ queryKey: ["render-templates"], queryFn: () => renderApi.get() });
}
