import { StateBadge, CheckBadge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Input, Label } from "../components/ui/Input";

const STATES = ["received", "validated", "planned", "ci_passed", "approved", "pre_checked", "applied", "post_checked", "closed", "rejected", "ci_failed", "pre_check_failed", "rolled_back", "failed"] as const;

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mb-6 border border-border bg-surface rounded p-4">
      <h2 className="text-lg mb-2">{title}</h2>
      {children}
    </section>
  );
}

export default function StyleReference() {
  return (
    <div>
      <p className="text-text-muted mb-4">
        Tokens, primitives and states for VireoNet. One accent. Status colours only on badges and check results. No gradients, no shadows, no emoji.
      </p>

      <Section title="1. Palette — tokens only">
        <div className="flex flex-wrap gap-2">
          {[
            ["bg", "bg-bg"], ["surface", "bg-surface"], ["muted", "bg-muted"],
            ["border", "bg-border"], ["text", "bg-text"], ["accent", "bg-accent"],
          ].map(([label, cls]) => (
            <span key={label} className="inline-flex items-center gap-1 text-sm">
              <span className={`inline-block w-4 h-4 border border-border rounded-sm ${cls}`} aria-hidden="true" />
              <span className="mono">{label}</span>
            </span>
          ))}
        </div>
        <p className="text-sm text-text-muted mt-2">Dark theme uses the same token names with different values. Toggle Theme in the header to check contrast.</p>
      </Section>

      <Section title="2. Typography — three sizes">
        <p className="text-lg">Heading 16px — Change detail</p>
        <p className="text-base">Body 14px — A change moves through validation, planning, CI, approval, pre-check, apply and post-check.</p>
        <p className="text-sm text-text-muted">Small 13px, muted — requester, timestamps, secondary text.</p>
        <p className="mono">Monospace 13px — RITM0001234, leaf-01, 10.0.0.11, vlan 100</p>
      </Section>

      <Section title="3. Buttons">
        <div className="flex flex-wrap gap-2">
          <Button variant="primary">Approve</Button>
          <Button variant="secondary">Cancel</Button>
          <Button variant="danger">Roll back</Button>
          <Button variant="ghost">View</Button>
          <Button variant="primary" disabled>Approve (disabled)</Button>
        </div>
      </Section>

      <Section title="4. State badges — status colours only here">
        <div className="flex flex-wrap gap-1">
          {STATES.map((s) => <StateBadge key={s} state={s} />)}
        </div>
        <div className="flex gap-1 mt-2">
          <CheckBadge status="pass" /><CheckBadge status="fail" /><CheckBadge status="skip" />
        </div>
      </Section>

      <Section title="5. Table — full width, sticky header, 1px borders">
        <table>
          <thead>
            <tr><th>Ticket</th><th>Type</th><th>Devices</th><th>State</th><th>Started</th></tr>
          </thead>
          <tbody>
            <tr>
              <td className="mono">RITM0001234</td><td className="mono">vlan_add</td>
              <td className="mono">leaf-01, leaf-02</td><td><StateBadge state="approved" /></td><td>2026-11-23 11:00</td>
            </tr>
            <tr>
              <td className="mono">RITM0001235</td><td className="mono">static_route_add</td>
              <td className="mono">leaf-01</td><td><StateBadge state="pre_check_failed" /></td><td>2026-11-23 11:04</td>
            </tr>
          </tbody>
        </table>
        <p className="text-sm text-text-muted mt-2">Pre-check failed: neighbour 10.1.1.2 down</p>
      </Section>

      <Section title="6. Forms — labelled inputs, visible focus">
        <div className="max-w-md">
          <Label htmlFor="style-ticket">Ticket</Label>
          <Input id="style-ticket" defaultValue="RITM0001234" />
        </div>
      </Section>

      <Section title="7. Loading, empty and error states">
        <p className="text-sm">Loading: Loading changes…</p>
        <p className="text-sm">Empty: No changes match these filters.</p>
        <p className="text-sm text-danger">Error: Failed to load changes: Network error. Retry.</p>
        <Button variant="secondary">Retry</Button>
      </Section>

      <Section title="8. Forbidden — none of these appear on this page">
        <p className="text-sm text-text-muted">No gradients, glassmorphism, glow, neon, purple-blue, large shadows, emoji, illustrations, hero sections, marketing copy, oversized rounded cards, centred welcome screens, lorem ipsum, or fake charts.</p>
      </Section>
    </div>
  );
}
