import { useQuery } from "@tanstack/react-query";
import { request } from "../lib/api";
import type {
  DriftDetail,
  DriftIgnoreRule,
  DriftList,
  DriftSummary,
} from "../lib/api-types";

export interface DriftFilters {
  device?: string;
  kind?: string;
  severity?: string;
  status?: string;
  site?: string;
  limit?: number;
  offset?: number;
}

export interface DriftActionBody {
  reason: string;
  ticket_ref: string;
  owner?: string;
  expiry?: string;
  /** Rollback only: "branch" (main/master head), "previous", or a commit SHA. */
  target?: string;
}

export interface ConfigCommit {
  sha: string;
  message: string;
  author: string;
  date: string;
}

export interface ConfigCommits {
  hostname: string;
  repo: string;
  path: string;
  branch: string;
  commits: ConfigCommit[];
}

export function useConfigCommits(device: string | undefined) {
  return useQuery({
    queryKey: ["config-commits", device],
    queryFn: () => request<ConfigCommits>(`/config/devices/${encodeURIComponent(device ?? "")}/commits`),
    enabled: Boolean(device),
    retry: false,
  });
}

export function buildDriftQuery(filters: DriftFilters): string {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(filters)) {
    if (v !== undefined && v !== "" && v !== null) params.set(k, String(v));
  }
  const q = params.toString();
  return q ? `?${q}` : "";
}

export const driftApi = {
  listDrift: (filters: DriftFilters) =>
    request<DriftList>(`/drift${buildDriftQuery(filters)}`),
  getDrift: (id: string) => request<DriftDetail>(`/drift/${encodeURIComponent(id)}`),
  driftAction: (id: string, action: "rollback" | "merge" | "exception", body: DriftActionBody) =>
    request<Record<string, unknown>>(`/drift/${encodeURIComponent(id)}/${action}`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  emergencyAccept: (id: string, body: { reason: string; ticket_ref: string; deadline_hours?: number }) =>
    request<Record<string, unknown>>(`/drift/${encodeURIComponent(id)}/emergency-accept`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  mrMerged: (id: string, body: { merged: boolean; branch: string }) =>
    request<Record<string, unknown>>(`/drift/${encodeURIComponent(id)}/mr-merged`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  startScan: (body: { device?: string; site?: string }) =>
    request<{ workflow_id?: string; scan_id?: string; site: string }>(`/drift/scan`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  getSummary: () => request<DriftSummary>(`/drift/summary`),
  listIgnoreRules: () => request<DriftIgnoreRule[]>(`/drift/ignore-rules`),
  updateIgnoreRules: (rules: { pattern: string; platform?: string; scope?: string; target?: string }[]) =>
    request<DriftIgnoreRule[]>(`/drift/ignore-rules`, {
      method: "PUT",
      body: JSON.stringify({ rules }),
    }),
};

export function useDriftList(filters: DriftFilters) {
  return useQuery({ queryKey: ["drift", filters], queryFn: () => driftApi.listDrift(filters) });
}

export function useDriftDetail(id: string) {
  return useQuery({ queryKey: ["drift-detail", id], queryFn: () => driftApi.getDrift(id), enabled: Boolean(id) });
}

export function useDriftSummary() {
  return useQuery({ queryKey: ["drift-summary"], queryFn: () => driftApi.getSummary(), refetchInterval: 30000 });
}

export function useIgnoreRules() {
  return useQuery({ queryKey: ["drift-ignore-rules"], queryFn: () => driftApi.listIgnoreRules() });
}
