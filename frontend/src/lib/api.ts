import { useQuery } from "@tanstack/react-query";
import { getAccessToken } from "./token-store";
import type {
  AdapterHealth,
  AdapterSettingResponse,
  ChangeDetailResponse,
  ChangeResponse,
  ChangeWindowResponse,
  ConfigRepoResponse,
  GiteaSetupJob,
  IamRolesResponse,
  IamUser,
  LockResponse,
  LoginResponse,
  PaginatedChanges,
  PaginatedZtpDevices,
  ScriptResponse,
  SystemHealthResponse,
} from "./api-types";

const BASE = (import.meta.env.VITE_API_URL as string | undefined) ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = getAccessToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(`${BASE}${path}`, { ...init, headers });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch { /* keep statusText */ }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export interface ChangeFilters {
  state?: string;
  change_type?: string;
  device?: string;
  date_from?: string;
  date_to?: string;
  page?: number;
  page_size?: number;
  sort_by?: string;
  sort_dir?: "asc" | "desc";
}

export function buildChangeQuery(filters: ChangeFilters): string {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(filters)) {
    if (v !== undefined && v !== "" && v !== null) params.set(k, String(v));
  }
  const q = params.toString();
  return q ? `?${q}` : "";
}

export interface ZtpFilters {
  stage?: string;
  site?: string;
  platform?: string;
  page?: number;
  page_size?: number;
}

export const api = {
  health: () => request<{ status: string }>("/healthz"),
  adaptersHealth: () => request<{ adapters: AdapterHealth[] }>("/adapters/health"),
  listChanges: (filters: ChangeFilters) => request<PaginatedChanges>(`/changes${buildChangeQuery(filters)}`),
  getChange: (id: string) => request<ChangeResponse>(`/changes/${id}`),
  getDetail: (id: string) => request<ChangeDetailResponse>(`/changes/${id}/detail`),
  rollback: (id: string) =>
    request(`/changes/${id}/signals/rollback`, { method: "POST", body: JSON.stringify({}) }),
  createChange: (body: unknown) => request<ChangeResponse>("/changes", { method: "POST", body: JSON.stringify(body) }),
  signalApproval: (id: string, body: { approved: boolean; reason?: string }) =>
    request(`/changes/${id}/signals/approval`, { method: "POST", body: JSON.stringify(body) }),
  signalCi: (id: string, body: { passed: boolean; reason?: string }) =>
    request(`/changes/${id}/signals/ci`, { method: "POST", body: JSON.stringify(body) }),
  signalMerge: (id: string) =>
    request(`/changes/${id}/signals/merge`, { method: "POST", body: JSON.stringify({ merged: true }) }),
  listZtpDevices: (filters: ZtpFilters) => {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(filters)) if (v !== undefined && v !== "") q.set(k, String(v));
    const s = q.toString();
    return request<PaginatedZtpDevices>(`/ztp/devices${s ? `?${s}` : ""}`);
  },
  retryZtp: (serial: string) => request<{ serial: string; stage: string; token: string }>(`/ztp/devices/${serial}/retry`, { method: "POST", body: JSON.stringify({}) }),
  resetZtp: (serial: string) => request<{ serial: string; stage: string }>(`/ztp/devices/${serial}/reset`, { method: "POST", body: JSON.stringify({}) }),
  listLocks: () => request<LockResponse[]>("/locks"),
  releaseLock: (device: string) => request(`/locks/${encodeURIComponent(device)}/release`, { method: "POST", body: JSON.stringify({}) }),
  listChangeWindows: () => request<ChangeWindowResponse[]>("/change-windows"),
  systemHealth: () => request<SystemHealthResponse>("/system/health"),
  adapterSettings: () => request<AdapterSettingResponse[]>("/adapters/settings"),
  login: (body: { username: string; password: string }) => request<LoginResponse>("/auth/login", { method: "POST", body: JSON.stringify(body) }),
  me: () => request<{ user_id: string; username: string; role: string }>("/auth/me"),
  listIamUsers: () => request<IamUser[]>("/iam/users"),
  createIamUser: (body: { username: string; display_name?: string; password: string; role: string }) => request<IamUser>("/iam/users", { method: "POST", body: JSON.stringify(body) }),
  updateIamUser: (id: string, body: { role?: string; is_active?: boolean }) => request<IamUser>(`/iam/users/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  listIamRoles: () => request<IamRolesResponse>("/iam/roles"),
  listScripts: () => request<ScriptResponse[]>("/scripts"),
  giteaManualSteps: () => request<{ steps: string[] }>("/setup/gitea/manual"),
  giteaInstall: (body: { admin_user: string; repo: string; base_url: string }) => request<GiteaSetupJob>("/setup/gitea/install", { method: "POST", body: JSON.stringify(body) }),
  giteaInstallStatus: (jobId: string) => request<GiteaSetupJob>(`/setup/gitea/status/${jobId}`),
  listConfigRepos: () => request<ConfigRepoResponse[]>("/config/repos"),
  getConfigRepo: (hostname: string) => request<ConfigRepoResponse>(`/config/repos/${encodeURIComponent(hostname)}`),
  getConfigHistory: (hostname: string) => request<{ hostname: string; repo: string; commits: unknown[] }>(`/config/repos/${encodeURIComponent(hostname)}/history`),
};

export function useChanges(filters: ChangeFilters) {
  return useQuery({ queryKey: ["changes", filters], queryFn: () => api.listChanges(filters) });
}
export function useChange(id: string) {
  return useQuery({ queryKey: ["change", id], queryFn: () => api.getChange(id), enabled: Boolean(id) });
}
export function useChangeDetail(id: string) {
  return useQuery({ queryKey: ["change-detail", id], queryFn: () => api.getDetail(id), enabled: Boolean(id) });
}
export function useZtpDevices(filters: ZtpFilters) {
  return useQuery({ queryKey: ["ztp-devices", filters], queryFn: () => api.listZtpDevices(filters) });
}
export function useLocks() {
  return useQuery({ queryKey: ["locks"], queryFn: () => api.listLocks(), refetchInterval: 30000 });
}
export function useChangeWindows() {
  return useQuery({ queryKey: ["change-windows"], queryFn: () => api.listChangeWindows() });
}
export function useSystemHealth() {
  return useQuery({ queryKey: ["system-health"], queryFn: () => api.systemHealth(), refetchInterval: 30000 });
}
export function useAdaptersHealth() {
  return useQuery({ queryKey: ["adapters-health"], queryFn: () => api.adaptersHealth(), refetchInterval: 30000 });
}
export function useAdapterSettings() {
  return useQuery({ queryKey: ["adapter-settings"], queryFn: () => api.adapterSettings() });
}
export function useIamUsers() {
  return useQuery({ queryKey: ["iam-users"], queryFn: () => api.listIamUsers() });
}
export function useIamRoles() {
  return useQuery({ queryKey: ["iam-roles"], queryFn: () => api.listIamRoles() });
}
export function useScripts() {
  return useQuery({ queryKey: ["scripts"], queryFn: () => api.listScripts() });
}

export function useConfigRepos() {
  return useQuery({ queryKey: ["config-repos"], queryFn: () => api.listConfigRepos() });
}
