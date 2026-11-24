import { useEffect, useMemo, useState } from "react";
import { checksApi, useCheckTemplates, useSampleIntents, type CheckView, type PreviewResponse } from "../api/checks";
import { Button } from "../components/ui/Button";
import { Label } from "../components/ui/Input";
import { useAuth } from "../lib/auth";

const PLATFORMS = ["ios", "ios-xe", "nxos", "eos", "junos"];
const SELECT = "px-2 py-1 text-sm bg-surface border border-border rounded";
const TEXTAREA = "w-full mono text-sm bg-surface text-text border border-border rounded p-2";

type Tab = "view" | "edit";

function expectText(e: Record<string, unknown>): string {
  return Object.entries(e)
    .filter(([, v]) => v != null)
    .map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
    .join("; ");
}

function CheckTable({ title, checks }: { title: string; checks: CheckView[] }) {
  return (
    <div className="mb-4">
      <h3 className="text-sm mb-2">{title}</h3>
      {checks.length === 0 ? <p className="text-sm text-text-muted">No checks.</p> : (
        <table aria-label={title}>
          <thead><tr><th>Check</th><th>Kind</th><th>On fail</th><th>Command per platform</th><th>Expect</th></tr></thead>
          <tbody>
            {checks.map((c) => (
              <tr key={c.name}>
                <td><span className="mono">{c.name}</span>{c.description ? <div className="text-sm text-text-muted">{c.description}</div> : null}</td>
                <td>{c.kind === "builtin" ? "built-in" : "command"}</td>
                <td>{c.on_fail === "warn" ? "warn only" : "block"}</td>
                <td>
                  {c.command ? (
                    <ul>{Object.entries(c.command).map(([p, cmd]) => <li key={p} className="text-sm"><span className="text-text-muted">{p}:</span> <span className="mono">{cmd}</span></li>)}</ul>
                  ) : <span className="text-text-muted">—</span>}
                </td>
                <td>
                  {c.expect ? (
                    <ul>{Object.entries(c.expect).map(([p, e]) => <li key={p} className="text-sm"><span className="text-text-muted">{p === "*" ? "all" : p}:</span> <span className="mono">{expectText(e as Record<string, unknown>)}</span></li>)}</ul>
                  ) : <span className="text-text-muted">—</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export default function CheckTemplates() {
  const query = useCheckTemplates();
  const samples = useSampleIntents();
  const { can } = useAuth();
  const [tab, setTab] = useState<Tab>("view");
  const [changeType, setChangeType] = useState("vlan_add");
  const [draft, setDraft] = useState<string | null>(null);
  const [validation, setValidation] = useState<{ valid: boolean; errors: string[] } | null>(null);
  const [platform, setPlatform] = useState("ios");
  const [intentText, setIntentText] = useState("");
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [error, setError] = useState("");

  const templates = query.data?.templates ?? null;
  const changeTypes = useMemo(() => Object.keys(templates?.change_types ?? {}), [templates]);
  const yaml = draft ?? query.data?.yaml ?? "";

  useEffect(() => {
    const sample = samples.data?.[changeType];
    if (sample) setIntentText(JSON.stringify(sample, null, 2));
    setPreview(null);
  }, [changeType, samples.data]);

  if (query.isLoading) return <p className="text-sm">Loading check templates…</p>;
  if (query.isError) {
    return (
      <div>
        <p className="text-sm text-danger" role="alert">Failed to load check templates: {(query.error as Error).message}</p>
        <Button variant="secondary" onClick={() => query.refetch()}>Retry</Button>
      </div>
    );
  }

  const runValidate = async () => {
    setError("");
    try {
      const res = await checksApi.validate(yaml);
      setValidation({ valid: res.valid, errors: res.errors });
    } catch (e) { setError((e as Error).message); }
  };

  const runPreview = async () => {
    setError(""); setPreview(null);
    let intent: unknown;
    try { intent = JSON.parse(intentText); } catch { setError("Intent is not valid JSON."); return; }
    try {
      setPreview(await checksApi.preview({ intent, platform, yaml: draft ?? undefined }));
    } catch (e) { setError((e as Error).message); }
  };

  const current = templates?.change_types[changeType];

  return (
    <div>
      <h2 className="text-lg mb-2">Check templates</h2>
      <p className="text-sm text-text-muted mb-4">
        Pre- and post-checks per change type, from <span className="mono">{templates?.source ?? "policies/checks.yaml"}</span>.
        Command checks pick the command for each device's platform (from NetBox) and test the output.
        Only read-only commands starting with {(templates?.allowed_command_prefixes ?? []).map((p) => `"${p.trim()}"`).join(", ")} are allowed.
        Locks and backups always happen, whatever the templates say.
      </p>
      {!query.data?.valid ? (
        <div className="mb-4" role="alert">
          <p className="text-sm text-danger">The active template file is invalid, so the worker will refuse to start:</p>
          <ul>{query.data?.errors.map((e) => <li key={e} className="text-sm text-danger mono">{e}</li>)}</ul>
        </div>
      ) : null}

      <div className="flex gap-1 border-b border-border mb-4" role="tablist" aria-label="Check template tabs">
        {(["view", "edit"] as Tab[]).map((t) => (
          <button key={t} type="button" role="tab" aria-selected={tab === t} onClick={() => setTab(t)} className={`px-2 py-1 text-sm border-b-2 ${tab === t ? "border-accent text-text" : "border-transparent text-text-muted"}`}>
            {t === "view" ? "Active templates" : "Edit and preview"}
          </button>
        ))}
      </div>

      <div className="flex flex-wrap gap-4 mb-4">
        <div>
          <Label htmlFor="ct-type">Change type</Label>
          <select id="ct-type" className={SELECT} value={changeType} onChange={(e) => setChangeType(e.target.value)}>
            {changeTypes.map((ct) => <option key={ct} value={ct}>{ct}</option>)}
          </select>
        </div>
        {tab === "edit" ? (
          <div>
            <Label htmlFor="ct-platform">Platform</Label>
            <select id="ct-platform" className={SELECT} value={platform} onChange={(e) => setPlatform(e.target.value)}>
              {PLATFORMS.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </div>
        ) : null}
      </div>

      {tab === "view" ? (
        current ? (
          <>
            <CheckTable title="Pre-checks" checks={current.pre} />
            <CheckTable title="Post-checks" checks={current.post} />
          </>
        ) : <p className="text-sm text-text-muted">No templates loaded.</p>
      ) : (
        <div>
          <p className="text-sm text-text-muted mb-2">
            Edit a draft, validate it, and preview exactly which checks and commands a change would run.
            Drafts are not saved here: to activate a template, commit it to <span className="mono">policies/checks.yaml</span> through a merge request.
          </p>
          <div className="grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))" }}>
            <div>
              <Label htmlFor="ct-yaml">{draft !== null ? "Template YAML (draft, edited)" : "Template YAML"}</Label>
              <textarea id="ct-yaml" className={TEXTAREA} rows={22} spellCheck={false} value={yaml} onChange={(e) => { setDraft(e.target.value); setValidation(null); }} />
              <div className="flex gap-2 mt-2">
                {can("operator") ? <Button variant="primary" onClick={runValidate}>Validate</Button> : <p className="text-sm text-text-muted">Operator role or above is required to validate.</p>}
                {draft !== null ? <Button variant="secondary" onClick={() => { setDraft(null); setValidation(null); }}>Discard draft</Button> : null}
              </div>
              {validation ? (
                validation.valid ? <p className="text-sm text-success mt-2" role="status">Valid. Commit it to policies/checks.yaml to activate.</p> : (
                  <ul className="mt-2" role="alert" aria-label="Validation errors">
                    {validation.errors.map((e) => <li key={e} className="text-sm text-danger mono">{e}</li>)}
                  </ul>
                )
              ) : null}
            </div>
            <div>
              <Label htmlFor="ct-intent">Sample intent (JSON)</Label>
              <textarea id="ct-intent" className={TEXTAREA} rows={12} spellCheck={false} value={intentText} onChange={(e) => setIntentText(e.target.value)} />
              <div className="mt-2"><Button variant="secondary" onClick={runPreview}>Preview checks</Button></div>
              {preview && !preview.valid ? (
                <ul className="mt-2" role="alert">{preview.errors.map((e) => <li key={e} className="text-sm text-danger mono">{e}</li>)}</ul>
              ) : null}
              {preview?.preview ? (["pre", "post"] as const).map((phase) => (
                <div key={phase} className="mt-4">
                  <h3 className="text-sm mb-2">{phase === "pre" ? "Pre-checks" : "Post-checks"} on {preview.platform}</h3>
                  <table aria-label={`Preview ${phase}`}>
                    <thead><tr><th>Check</th><th>Runs</th><th>Expect</th></tr></thead>
                    <tbody>
                      {preview.preview![phase].map((r) => (
                        <tr key={r.name}>
                          <td className="mono">{r.name}{r.on_fail === "warn" ? <span className="text-text-muted"> (warn)</span> : null}</td>
                          <td>
                            {r.error ? <span className="text-danger mono">{r.error}</span>
                              : r.kind === "builtin" ? <span className="text-text-muted">built-in check</span>
                              : r.command ? <span className="mono">{r.command}</span>
                              : <span className="text-text-muted">no command for this platform: skipped</span>}
                          </td>
                          <td className="mono text-sm">{r.expect ? expectText(r.expect as Record<string, unknown>) : "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )) : null}
            </div>
          </div>
        </div>
      )}
      {error ? <p className="text-sm text-danger mt-2" role="alert">{error}</p> : null}
    </div>
  );
}
