import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { Button } from "../components/ui/Button";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { Input, Label } from "../components/ui/Input";
import { useAuth } from "../lib/auth";

export default function GitSetup() {
  const { can } = useAuth();
  const [mode, setMode] = useState<"choose" | "manual" | "auto">("choose");
  const [form, setForm] = useState({ admin_user: "vireonet-admin", repo: "netops/intent", base_url: "http://localhost:3000" });
  const [confirm, setConfirm] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState("");

  const manual = useQuery({ queryKey: ["gitea-manual"], queryFn: () => api.giteaManualSteps(), enabled: mode === "manual" });
  const job = useQuery({ queryKey: ["gitea-job", jobId], queryFn: () => api.giteaInstallStatus(jobId!), enabled: jobId !== null, refetchInterval: jobId ? 3000 : false });

  if (!can("admin")) {
    return (
      <div>
        <h2 className="text-lg mb-2">Gitea setup</h2>
        <p className="text-sm text-text-muted">Admin role is required to install and integrate Gitea.</p>
      </div>
    );
  }

  const startAuto = async () => {
    setError("");
    try {
      const job = await api.giteaInstall(form);
      setJobId(job.id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setConfirm(false);
    }
  };

  return (
    <div>
      <h2 className="text-lg mb-2">Gitea setup</h2>
      <p className="text-sm text-text-muted mb-4">Install Gitea and connect it to VireoNet. Choose how: follow the manual steps yourself, or let VireoNet install and integrate it automatically.</p>

      {mode === "choose" ? (
        <div className="flex gap-4">
          <div className="border border-border bg-surface rounded p-4 w-[320px]">
            <h3 className="text-sm mb-1">Manual install</h3>
            <p className="text-sm text-text-muted mb-4">Exact steps with copyable commands. You run Docker Compose, create the admin and repo, and store the token in Vault yourself.</p>
            <Button variant="secondary" onClick={() => setMode("manual")}>Show manual steps</Button>
          </div>
          <div className="border border-border bg-surface rounded p-4 w-[320px]">
            <h3 className="text-sm mb-1">Automatic install</h3>
            <p className="text-sm text-text-muted mb-4">VireoNet runs the installer, creates the admin and repo, creates the token, stores it in Vault, and verifies the connection. The token is never shown.</p>
            <Button variant="primary" onClick={() => setMode("auto")}>Install automatically</Button>
          </div>
        </div>
      ) : null}

      {mode === "manual" ? (
        <div>
          <Button variant="ghost" onClick={() => setMode("choose")}>Back</Button>
          {manual.isLoading ? <p className="text-sm">Loading steps…</p> : null}
          {manual.isError ? <p className="text-sm text-danger">Failed to load steps: {(manual.error as Error).message}</p> : null}
          <ol className="text-sm mt-2">
            {(manual.data?.steps ?? []).map((s, i) => <li key={i} className="mb-1 mono">{s}</li>)}
          </ol>
        </div>
      ) : null}

      {mode === "auto" ? (
        <div>
          <Button variant="ghost" onClick={() => setMode("choose")}>Back</Button>
          <div className="border border-border bg-surface rounded p-4 mt-2 flex flex-wrap gap-4">
            <div>
              <Label htmlFor="gitea-admin">Admin username</Label>
              <Input id="gitea-admin" value={form.admin_user} onChange={(e) => setForm({ ...form, admin_user: e.target.value })} />
            </div>
            <div>
              <Label htmlFor="gitea-repo">Repo (owner/repo)</Label>
              <Input id="gitea-repo" value={form.repo} onChange={(e) => setForm({ ...form, repo: e.target.value })} />
            </div>
            <div>
              <Label htmlFor="gitea-url">Gitea URL</Label>
              <Input id="gitea-url" value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })} />
            </div>
          </div>
          <p className="text-sm text-text-muted mt-2">Requires Docker on this host, and GITEA_ADMIN_PASSWORD + GITEA_DB_PASSWORD set in the VireoNet environment (12+ chars each). The token is stored in Vault, never shown here.</p>
          {error ? <p className="text-sm text-danger" role="alert">{error}</p> : null}
          <div className="mt-2">
            <ConfirmDialog
              open={confirm} onOpenChange={setConfirm}
              title="Install Gitea automatically"
              description={`Install Gitea with Docker Compose at ${form.base_url}, create admin ${form.admin_user} and repo ${form.repo}, create an API token and store it in Vault. This starts containers on this host.`}
              confirmLabel="Install Gitea"
              onConfirm={startAuto}
            >
              <Button variant="primary">Install Gitea automatically</Button>
            </ConfirmDialog>
          </div>
          {job.data ? (
            <div className="mt-4">
              <p className="text-sm">Status: {job.data.status}</p>
              <ul className="text-sm">
                {job.data.steps.map((s, i) => <li key={i}>{s.step}: {s.status} — {s.detail}</li>)}
              </ul>
              {job.data.status === "done" ? <p className="text-sm">Done. Restart the VireoNet API + worker with VIREONET_GIT_PROVIDER=gitea, then check System health for the git adapter.</p> : null}
              {job.data.status === "failed" ? <p className="text-sm text-danger" role="alert">Failed: {job.data.error}</p> : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
