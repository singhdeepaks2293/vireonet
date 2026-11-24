import { useQuery } from "@tanstack/react-query";
import { request } from "../lib/api";

export interface GiteaInfo { configured: boolean; installed: boolean; version: string; ui_url: string; embed_url: string; repos: { name: string; url: string; updated: string }[]; detail: string }

export function useGiteaInfo() {
  return useQuery({ queryKey: ["gitea-info"], queryFn: () => request<GiteaInfo>("/gitea/info"), staleTime: 60_000, retry: false });
}

export default function Gitea() {
  const q = useGiteaInfo();
  if (q.isLoading) return <p className="text-sm">Checking Gitea…</p>;
  const g = q.data;
  if (!g?.installed) {
    return (
      <div>
        <h2 className="text-lg mb-2">Gitea</h2>
        <p className="text-sm text-text-muted">{g?.detail || "Gitea is not installed or not reachable."} Install it from <strong>Adapters → Gitea setup</strong>.</p>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-2 h-[85vh]">
      <div className="flex items-center gap-3">
        <h2 className="text-lg">Gitea</h2>
        <span className="text-sm text-text-muted">version {g.version} · {g.repos.length} repositories</span>
        <a className="text-sm" href={g.ui_url} target="_blank" rel="noreferrer noopener">Open in a new tab ↗</a>
      </div>
      <iframe title="Gitea" src={g.embed_url} className="flex-1 w-full border border-border rounded bg-surface" referrerPolicy="same-origin" />
      <p className="text-[12px] text-text-muted">If the frame stays empty, Gitea refuses to be embedded from this address: serve it under the same host (e.g. <span className="mono">/gitea/</span>) and set <span className="mono">VIREONET_GITEA_EMBED_URL</span>, or use “Open in a new tab”.</p>
    </div>
  );
}
