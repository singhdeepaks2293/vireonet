import { useEffect, useState } from "react";
import { textfsmApi, type ParseResponse, type TemplateList } from "../api/textfsm";
import { Button } from "../components/ui/Button";
import { Input, Label } from "../components/ui/Input";

const SELECT = "px-2 py-1 text-sm bg-surface border border-border rounded";
const AREA = "w-full mono text-sm bg-surface text-text border border-border rounded p-2";
const PLATFORMS = ["ios", "ios-xe", "nxos", "eos", "junos", "fortios", "panos", "sros", "exos"];

function cell(v: unknown): string {
  return Array.isArray(v) ? v.join(", ") : String(v ?? "");
}

export default function TextFsm() {
  const [platform, setPlatform] = useState("ios");
  const [q, setQ] = useState("");
  const [list, setList] = useState<TemplateList | null>(null);
  const [command, setCommand] = useState("show vlan brief");
  const [output, setOutput] = useState("");
  const [draft, setDraft] = useState("");
  const [useDraft, setUseDraft] = useState(false);
  const [result, setResult] = useState<ParseResponse | null>(null);
  const [check, setCheck] = useState<{ valid: boolean; errors: string[]; values: string[] } | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const t = setTimeout(() => { textfsmApi.list(platform, q).then(setList).catch((e) => setError((e as Error).message)); }, 250);
    return () => clearTimeout(t);
  }, [platform, q]);

  const openTemplate = async (source: string, name: string, cmd: string) => {
    setCommand(cmd);
    try { setDraft((await textfsmApi.text(source, name)).text); setUseDraft(false); } catch (e) { setError((e as Error).message); }
  };
  const run = async () => {
    setError(""); setResult(null);
    try { setResult(await textfsmApi.parse({ platform, command, output, template: useDraft ? draft : undefined })); } catch (e) { setError((e as Error).message); }
  };

  return (
    <div>
      <h2 className="text-lg mb-1">TextFSM</h2>
      <p className="text-sm text-text-muted mb-4">
        Turn show-command output into fields. VireoNet tries your own templates first (<span className="mono">{list?.user_dir ?? "policies/textfsm"}</span>, with an ntc-style <span className="mono">index</span>),
        then the bundled ntc-templates library ({list ? `${list.total} templates match` : "…"}). Check templates use the same lookup with <span className="mono">parse: textfsm</span>; log analytics uses it for <span className="mono">show logging</span>.
        To add your own, write and test it here, then commit the file and an index line through a merge request.
      </p>
      <div className="grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(340px, 1fr))" }}>
        <section aria-label="Templates">
          <div className="flex gap-3 mb-2">
            <div><Label htmlFor="tf-plat">Platform</Label>
              <select id="tf-plat" className={SELECT} value={platform} onChange={(e) => setPlatform(e.target.value)}>{PLATFORMS.map((p) => <option key={p}>{p}</option>)}</select></div>
            <div className="flex-1"><Label htmlFor="tf-q">Search templates</Label><Input id="tf-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="bgp, interfaces, logging…" /></div>
          </div>
          <div className="max-h-[420px] overflow-y-auto border border-border rounded">
            <table aria-label="TextFSM templates">
              <thead><tr><th>Command</th><th>Template</th><th>Source</th></tr></thead>
              <tbody>{list?.templates.map((t) => (
                <tr key={`${t.source}-${t.template}-${t.platform}`} className="cursor-pointer" onClick={() => openTemplate(t.source, t.template, t.command)}>
                  <td className="mono">{t.command}</td><td className="mono text-sm">{t.template}</td><td>{t.source === "user" ? "yours" : "ntc"}</td>
                </tr>))}</tbody>
            </table>
          </div>
        </section>
        <section aria-label="Parse">
          <div className="flex gap-3 items-end mb-2">
            <div className="flex-1"><Label htmlFor="tf-cmd">Command</Label><Input id="tf-cmd" value={command} onChange={(e) => setCommand(e.target.value)} /></div>
            <Button variant="primary" disabled={!output.trim()} onClick={run}>Parse</Button>
          </div>
          <Label htmlFor="tf-out">Device output</Label>
          <textarea id="tf-out" className={AREA} rows={8} spellCheck={false} value={output} onChange={(e) => setOutput(e.target.value)} placeholder="Paste show-command output here" />
          <div className="mt-2">
            <Label htmlFor="tf-draft">{useDraft ? "Template (draft — used for Parse)" : "Template (click a row to view; edit to test your own)"}</Label>
            <textarea id="tf-draft" className={AREA} rows={10} spellCheck={false} value={draft} onChange={(e) => { setDraft(e.target.value); setUseDraft(true); setCheck(null); }} />
            <div className="flex gap-2 mt-2 items-center">
              <Button variant="secondary" disabled={!draft.trim()} onClick={() => textfsmApi.validate(draft).then(setCheck)}>Validate template</Button>
              <label className="text-sm flex items-center gap-1"><input type="checkbox" checked={useDraft} onChange={(e) => setUseDraft(e.target.checked)} /> Parse with this template</label>
            </div>
            {check ? (check.valid ? <p className="text-sm text-success mt-1">Compiles. Values: <span className="mono">{check.values.join(", ")}</span></p>
              : <p className="text-sm text-danger mt-1 mono" role="alert">{check.errors.join("; ")}</p>) : null}
          </div>
        </section>
      </div>
      {error ? <p className="text-sm text-danger mt-2" role="alert">{error}</p> : null}
      {result ? (
        <div className="mt-4">
          {result.ok ? (
            <>
              <p className="text-sm mb-2">{result.rows.length} rows · template <span className="mono">{result.template}</span> ({result.source === "user" ? "yours" : result.source}) · platform <span className="mono">{result.platform}</span></p>
              <table aria-label="Parsed rows">
                <thead><tr>{result.headers.map((h) => <th key={h}>{h.toLowerCase()}</th>)}</tr></thead>
                <tbody>{result.rows.map((r, i) => <tr key={i}>{result.headers.map((h) => <td key={h} className="mono text-sm">{cell(r[h.toLowerCase()])}</td>)}</tr>)}</tbody>
              </table>
            </>
          ) : <p className="text-sm text-danger" role="alert">{result.error}</p>}
        </div>
      ) : null}
    </div>
  );
}
