import type { TransitionResponse } from "../lib/api-types";

export function AuditTrail({ transitions }: { transitions: TransitionResponse[] }) {
  if (transitions.length === 0) return <p className="text-sm text-text-muted">No transitions recorded yet.</p>;
  return (
    <table aria-label="Audit trail">
      <thead>
        <tr><th>When</th><th>From</th><th>To</th><th>Actor</th><th>Reason</th></tr>
      </thead>
      <tbody>
        {transitions.map((t, i) => (
          <tr key={i}>
            <td className="mono">{t.created_at ? new Date(t.created_at).toLocaleString() : "—"}</td>
            <td className="mono">{t.from_state}</td>
            <td className="mono">{t.to_state}</td>
            <td>{t.actor}</td>
            <td>{t.reason || "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
