import { useEffect, useState } from "react";
import { renderApi, useRenderTemplates, useSampleIntents, type RenderPreviewResponse } from "../api/checks";
import { Button } from "../components/ui/Button";
import { Label } from "../components/ui/Input";

const SELECT = "px-2 py-1 text-sm bg-surface border border-border rounded";
const TEXTAREA = "w-full mono text-sm bg-surface text-text border border-border rounded p-2";

/** Vendor names people pick from, mapped to the platform the renderer uses. */
const VENDORS: { label: string; platform: string }[] = [
  { label: "Cisco IOS", platform: "ios" },
  { label: "Cisco IOS-XE", platform: "ios-xe" },
  { label: "Cisco ISR (IOS-XE)", platform: "isr" },
  { label: "Cisco NX-OS", platform: "nxos" },
  { label: "Arista EOS", platform: "eos" },
  { label: "Juniper Junos", platform: "junos" },
  { label: "Fortinet FortiOS", platform: "fortinet" },
  { label: "Palo Alto PAN-OS", platform: "paloalto" },
  { label: "Nokia SR OS", platform: "nokia" },
  { label: "Nokia SR Linux", platform: "srlinux" },
  { label: "Extreme EXOS", platform: "extreme" },
];

export default function RenderTemplates() {
  const query = useRenderTemplates();
  const samples = useSampleIntents();
  const [changeType, setChangeType] = useState("vlan_add");
  const [vendor, setVendor] = useState("ios");
  const [intentText, setIntentText] = useState("");
  const [draft, setDraft] = useState<string | null>(null);
  const [result, setResult] = useState<RenderPreviewResponse | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const sample = samples.data?.[changeType];
    if (sample) setIntentText(JSON.stringify(sample, null, 2));
    setResult(null);
  }, [changeType, samples.data]);

  if (query.isLoading) return <p className="text-sm">Loading render templates…</p>;
  if (query.isError) return <p className="text-sm text-danger" role="alert">Failed to load render templates: {(query.error as Error).message}</p>;
  const data = query.data;
  const yaml = draft ?? data?.yaml ?? "";

  const run = async () => {
    setError(""); setResult(null);
    let intent: unknown;
    try { intent = JSON.parse(intentText); } catch { setError("Intent is not valid JSON."); return; }
    try { setResult(await renderApi.preview({ intent, platform: vendor, yaml: draft ?? undefined })); }
    catch (e) { setError((e as Error).message); }
  };

  return (
    <div>
      <h2 className="text-lg mb-2">Config templates</h2>
      <p className="text-sm text-text-muted mb-2">
        Each device's config is rendered for its platform (from NetBox). Cisco IOS / IOS-XE / NX-OS, Arista EOS and Juniper Junos
        have built-in renderers; Fortinet, Palo Alto, Nokia and Extreme come from <span className="mono">{data?.source ?? "policies/render_templates.yaml"}</span>,
        where you can also override a built-in or add a vendor. A change a platform cannot render is rejected before approval.
      </p>
      <p className="text-sm text-warning mb-4">
        Vendor templates follow documented CLI but have not been run against real devices by this project. Test each in a lab first.
      </p>
      {!data?.valid ? (
        <ul className="mb-4" role="alert">{data?.errors.map((e) => <li key={e} className="text-sm text-danger mono">{e}</li>)}</ul>
      ) : null}

      <div className="flex flex-wrap gap-4 mb-4">
        <div>
          <Label htmlFor="rt-vendor">Vendor</Label>
          <select id="rt-vendor" className={SELECT} value={vendor} onChange={(e) => { setVendor(e.target.value); setResult(null); }}>
            {VENDORS.map((v) => <option key={v.platform} value={v.platform}>{v.label}</option>)}
          </select>
        </div>
        <div>
          <Label htmlFor="rt-type">Change type</Label>
          <select id="rt-type" className={SELECT} value={changeType} onChange={(e) => setChangeType(e.target.value)}>
            {Object.keys(samples.data ?? {}).map((ct) => <option key={ct} value={ct}>{ct}</option>)}
          </select>
        </div>
      </div>

      <div className="grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))" }}>
        <div>
          <Label htmlFor="rt-intent">Intent (JSON)</Label>
          <textarea id="rt-intent" className={TEXTAREA} rows={12} spellCheck={false} value={intentText} onChange={(e) => setIntentText(e.target.value)} />
          <div className="mt-2"><Button variant="primary" onClick={run}>Render</Button></div>
          {error ? <p className="text-sm text-danger mt-2" role="alert">{error}</p> : null}
          {result && result.errors.length > 0 ? (
            <ul className="mt-2" role="alert" aria-label="Render errors">{result.errors.map((e) => <li key={e} className="text-sm text-danger mono">{e}</li>)}</ul>
          ) : null}
          {result?.rendered ? (
            <div className="mt-4">
              <p className="text-sm text-text-muted mb-1">
                <span className="mono">{result.rendered.platform}</span> · {result.rendered.format === "set" ? "set format" : "CLI"} · {result.rendered.source === "template" ? "from template" : "built-in renderer"}
              </p>
              <pre aria-label="Rendered config" className="mono text-sm border border-border rounded p-2 bg-surface overflow-x-auto">{result.rendered.lines.join("\n")}</pre>
            </div>
          ) : null}
        </div>
        <div>
          <Label htmlFor="rt-yaml">{draft !== null ? "Render templates YAML (draft, edited)" : "Render templates YAML"}</Label>
          <textarea id="rt-yaml" className={TEXTAREA} rows={24} spellCheck={false} value={yaml} onChange={(e) => setDraft(e.target.value)} />
          <p className="text-sm text-text-muted mt-1">
            Edit to preview a draft. Drafts are not saved here: commit to <span className="mono">policies/render_templates.yaml</span> through a merge request to activate.
          </p>
          {draft !== null ? <Button variant="secondary" onClick={() => setDraft(null)}>Discard draft</Button> : null}
        </div>
      </div>
    </div>
  );
}
