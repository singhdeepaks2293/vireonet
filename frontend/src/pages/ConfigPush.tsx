import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { fileToBase64, pushApi, type Automation, type ItsmTicket, type PushPreview, type PushResult, type StateResult } from "../api/configPush";
import { Button } from "../components/ui/Button";
import { Input, Label } from "../components/ui/Input";
import { downloadFile, useAdapterSettings } from "../lib/api";
import { useAuth } from "../lib/auth";

function Result({ result }: { result: PushResult }) {
  const ok = result.change.state !== "rejected";
  return (
    <div className={`mt-3 p-3 rounded-[3px] border ${ok ? "border-success bg-success-bg" : "border-danger bg-danger-bg"}`} role="status">
      <p className="text-sm">
        {result.created ? "Change created" : "Change already exists"}: <Link to={`/changes/${result.change.id}`} className="mono">{result.change.ticket_number}</Link> — state <span className="mono">{result.change.state}</span>
        {result.workflow_started ? "; the workflow has started (CI, approval, dry-run, waves, post-checks)." : ok ? "." : ". See the change's Audit trail for the reason."}
      </p>
      {result.automation === "full" ? <p className="text-sm">Fully automatic: no approval click; it runs to completion on its own{result.window?.start ? ` inside the planned window ${new Date(result.window.start).toLocaleString()} – ${new Date(result.window.end).toLocaleString()}` : ""}.</p>
        : <p className="text-sm">Manual: an approver approves it on the change page{result.window?.start ? `; it will still run only inside ${new Date(result.window.start).toLocaleString()} – ${new Date(result.window.end).toLocaleString()}` : ""}.</p>}
      {result.warnings.map((w) => <p key={w} className="text-sm text-warning">{w}</p>)}
      {result.skipped_attachments?.map((w) => <p key={w} className="text-sm text-text-muted">{w}</p>)}
    </div>
  );
}

export default function ConfigPush() {
  const { can } = useAuth();
  const [file, setFile] = useState<{ name: string; b64: string } | null>(null);
  const [preview, setPreview] = useState<PushPreview | null>(null);
  const [ticket, setTicket] = useState("");
  const [site, setSite] = useState("dc1");
  const [emergency, setEmergency] = useState(false);
  const [result, setResult] = useState<PushResult | null>(null);
  const [error, setError] = useState("");
  const [itsmKey, setItsmKey] = useState("");
  const [itsmTicket, setItsmTicket] = useState<ItsmTicket | null>(null);
  const [itsmResult, setItsmResult] = useState<PushResult | null>(null);
  const [itsmError, setItsmError] = useState("");
  const [health, setHealth] = useState("");
  const [automation, setAutomation] = useState<Automation>("manual");
  const [plannedStart, setPlannedStart] = useState("");
  const [plannedEnd, setPlannedEnd] = useState("");
  const [stateResults, setStateResults] = useState<Record<string, StateResult | string>>({});
  const adapters = useAdapterSettings();
  const fakeMode = (adapters.data ?? []).some((a) => a.mode === "fake");
  const toIso = (local: string) => (local ? new Date(local).toISOString() : "");

  useEffect(() => {
    pushApi.health().then((h) => setHealth(h.backend ? `${h.backend}: ${h.status}${h.detail ? ` (${h.detail})` : ""}` : "no ITSM provider configured")).catch(() => setHealth("unavailable"));
  }, []);

  const onFile = async (f: File | undefined) => {
    setPreview(null); setResult(null); setError("");
    if (!f) { setFile(null); return; }
    try {
      const b64 = await fileToBase64(f);
      setFile({ name: f.name, b64 });
      setPreview(await pushApi.preview(f.name, b64));
    } catch (e) { setError((e as Error).message); }
  };

  const submit = async () => {
    if (!file) return;
    setError(""); setResult(null);
    try { setResult(await pushApi.submit({ filename: file.name, content_b64: file.b64, ticket_number: ticket.trim(), site: site.trim(), emergency, automation, planned_start: toIso(plannedStart), planned_end: toIso(plannedEnd) })); }
    catch (e) { setError((e as Error).message); }
  };

  const lookUp = async () => {
    setItsmError(""); setItsmTicket(null); setItsmResult(null);
    try { setItsmTicket(await pushApi.ticket(itsmKey.trim())); } catch (e) { setItsmError((e as Error).message); }
  };
  const fromTicket = async () => {
    setItsmError(""); setItsmResult(null);
    try { setItsmResult(await pushApi.fromTicket({ ticket_number: itsmKey.trim(), site: site.trim(), emergency, automation })); }
    catch (e) { setItsmError((e as Error).message); }
  };

  return (
    <div>
      <h2 className="text-lg mb-1">Config push (Excel)</h2>
      <p className="text-sm text-text-muted mb-4">
        Push device config from an Excel workbook: sheet <span className="mono">config</span> (Device | Config, one row per device) and, optionally,
        sheet <span className="mono">backup</span> (Device | Rollback config). The config replaces rendering only — the change still goes through
        approval, locks, backup, dry-run on every device, waves, post-checks and automatic rollback. At pre-check VireoNet writes each device's
        running config into the backup sheet and attaches the updated workbook to the ticket. ITSM: <span className="mono">{health || "…"}</span>
      </p>

      <fieldset className="mb-4 border border-border rounded-[6px] p-3" aria-label="Automation">
        <legend className="text-sm px-1">Automation</legend>
        <label className="text-sm flex items-center gap-2"><input type="radio" name="automation" checked={automation === "manual"} onChange={() => setAutomation("manual")} />
          <span><strong>Manual</strong> — an approver presses Approve on the change page.</span></label>
        <label className="text-sm flex items-center gap-2 mt-1"><input type="radio" name="automation" checked={automation === "full"} disabled={!can("approver")} onChange={() => setAutomation("full")} />
          <span><strong>Fully automatic</strong> — no approval click; runs end to end{can("approver") ? "" : " (approver role required)"}. Needs a planned window: nothing is pushed before its start or after its end.</span></label>
      </fieldset>

      <div className="mb-4">
        <Button variant="secondary" onClick={() => downloadFile("/config-push/template.xlsx", "vireonet-config-push-template.xlsx").catch((e) => setError((e as Error).message))}>
          Download template workbook
        </Button>
      </div>

      <div className="grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(340px, 1fr))" }}>
        <section className="border border-border rounded-[6px] p-4" aria-label="Upload a workbook">
          <h3 className="text-sm mb-2">Upload a workbook</h3>
          <Label htmlFor="cp-file">Workbook (.xlsx)</Label>
          <input id="cp-file" type="file" accept=".xlsx,.xlsm" className="text-sm" onChange={(e) => onFile(e.target.files?.[0])} />
          {preview ? (
            <div className="mt-3">
              {preview.errors.length ? (
                <ul role="alert" aria-label="Workbook errors">{preview.errors.map((e) => <li key={e} className="text-sm text-danger mono">{e}</li>)}</ul>
              ) : <p className="text-sm text-success">Workbook is valid.</p>}
              {preview.warnings.map((w) => <p key={w} className="text-sm text-warning">{w}</p>)}
              {preview.devices.length ? (
                <table aria-label="Devices in the workbook" className="mt-2">
                  <thead><tr><th>Device</th><th>Config lines</th><th>Rollback lines</th></tr></thead>
                  <tbody>{preview.devices.map((d) => (
                    <tr key={d.device}><td className="mono">{d.device}<pre className="text-sm text-text-muted mt-1 whitespace-pre-wrap">{d.config_preview}</pre></td><td>{d.config_lines}</td><td>{d.rollback_lines || "—"}</td></tr>
                  ))}</tbody>
                </table>
              ) : null}
            </div>
          ) : null}
          <div className="flex flex-wrap gap-3 mt-3 items-end">
            <div><Label htmlFor="cp-ticket">Ticket number</Label><Input id="cp-ticket" value={ticket} onChange={(e) => setTicket(e.target.value)} placeholder="RITM0001234" /></div>
            <div><Label htmlFor="cp-site">Site</Label><Input id="cp-site" value={site} onChange={(e) => setSite(e.target.value)} /></div>
            <label className="text-sm flex items-center gap-1"><input type="checkbox" checked={emergency} onChange={(e) => setEmergency(e.target.checked)} /> Emergency (bypasses the policy change window only)</label>
          </div>
          <div className="flex flex-wrap gap-3 mt-3 items-end">
            <div><Label htmlFor="cp-start">Planned start</Label><Input id="cp-start" type="datetime-local" value={plannedStart} onChange={(e) => setPlannedStart(e.target.value)} /></div>
            <div><Label htmlFor="cp-end">Planned end</Label><Input id="cp-end" type="datetime-local" value={plannedEnd} onChange={(e) => setPlannedEnd(e.target.value)} /></div>
            <p className="text-sm text-text-muted">{automation === "full" ? "Required for fully automatic." : "Optional for manual; enforced when set."}</p>
          </div>
          <div className="mt-3">
            {can("operator") ? (
              <Button variant="primary" disabled={!file || !preview?.valid || !ticket.trim() || !site.trim() || (automation === "full" && (!plannedStart || !plannedEnd))} onClick={submit}>Create change from workbook</Button>
            ) : <p className="text-sm text-text-muted">Operator role or above is required to create a change.</p>}
          </div>
          {error ? <p className="text-sm text-danger mt-2" role="alert">{error}</p> : null}
          {result ? <Result result={result} /> : null}
        </section>

        <section className="border border-border rounded-[6px] p-4" aria-label="From an ITSM ticket">
          <h3 className="text-sm mb-2">From an ITSM ticket</h3>
          <p className="text-sm text-text-muted mb-2">VireoNet reads the newest .xlsx attached to the ticket that has a <span className="mono">config</span> sheet. Your ITSM tool can also call <span className="mono">POST /webhooks/itsm/config-push</span> to do this automatically.</p>
          <div className="flex gap-2 items-end">
            <div className="flex-1"><Label htmlFor="cp-itsm">Ticket</Label><Input id="cp-itsm" value={itsmKey} onChange={(e) => setItsmKey(e.target.value)} placeholder="RITM0005001 / NET-42 / INC000000000123" /></div>
            <Button variant="secondary" disabled={!itsmKey.trim()} onClick={lookUp}>Look up</Button>
          </div>
          {itsmTicket ? (
            <div className="mt-3 text-sm">
              <p><span className="mono">{itsmTicket.ticket.key}</span> · {itsmTicket.ticket.summary} · {itsmTicket.ticket.status} · requester {itsmTicket.ticket.requester} <span className="text-text-muted">({itsmTicket.provider})</span></p>
              <ul className="mt-1">{itsmTicket.attachments.map((a) => <li key={a.id} className="mono">{a.filename} <span className="text-text-muted">({a.size} bytes)</span></li>)}</ul>
              {itsmTicket.attachments.length === 0 ? <p className="text-text-muted">No attachments.</p> : null}
            </div>
          ) : null}
          <div className="mt-3">
            {can("operator") ? (
              <Button variant="primary" disabled={!itsmKey.trim() || !site.trim()} onClick={fromTicket}>Create change from ticket</Button>
            ) : <p className="text-sm text-text-muted">Operator role or above is required.</p>}
          </div>
          <p className="text-sm text-text-muted mt-2">The ticket's planned start/end are used; fully automatic needs them.</p>
          {itsmError ? <p className="text-sm text-danger mt-2" role="alert">{itsmError}</p> : null}
          {itsmResult ? <Result result={itsmResult} /> : null}
        </section>
      </div>

      <section className="mt-4 border border-border rounded-[6px] p-4" aria-label="End-to-end trigger">
        <h3 className="text-sm mb-2">End-to-end trigger from the ITSM tool</h3>
        <p className="text-sm text-text-muted">
          Configure your ITSM tool to call <span className="mono">POST /webhooks/servicenow</span> (or <span className="mono">/webhooks/itsm/state</span>) with
          <span className="mono"> {"{\"number\": \"CHG…\"}"}</span> whenever a ticket changes state, authenticated with the webhook secret. VireoNet re-reads the
          ticket; when it is in a trigger state (default <strong>Implement</strong>) it reads the attached workbook and runs the push fully automatically, only inside the
          ticket's planned start/end. Other states are ignored; repeats are harmless.
        </p>
        {fakeMode ? (
          <div className="mt-3">
            <p className="text-sm mb-2">Fake mode — simulate the ITSM tool moving a demo Change Request to Implement:</p>
            <div className="flex flex-wrap gap-2">
              {[["CHG0030001", "window open now"], ["CHG0030002", "window opens in 3 min (waits)"], ["CHG0030003", "no planned window (refused)"], ["CHG0030004", "window over (refused)"]].map(([key, label]) => (
                <Button key={key} variant="secondary" onClick={() => pushApi.stateEvent(key).then((r) => setStateResults((x) => ({ ...x, [key]: r }))).catch((e) => setStateResults((x) => ({ ...x, [key]: (e as Error).message })))}>
                  {key} → Implement ({label})
                </Button>
              ))}
            </div>
            <ul className="mt-2">{Object.entries(stateResults).map(([key, r]) => (
              <li key={key} className="text-sm">
                <span className="mono">{key}</span>: {typeof r === "string" ? <span className="text-danger">{r}</span>
                  : r.change ? <>{r.status} — <Link to={`/changes/${r.change.id}`} className="mono">open the change</Link> ({r.change.state})</> : <>{r.status}{r.reason ? `: ${r.reason}` : ""}</>}
              </li>))}</ul>
          </div>
        ) : null}
      </section>
    </div>
  );
}
