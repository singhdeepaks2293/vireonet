import type { ChangeState } from "../lib/tokens";

const HAPPY = ["received", "validated", "planned", "ci_passed", "approved", "pre_checked", "applied", "post_checked", "closed"];

export function StateTimeline({ state }: { state: ChangeState }) {
  const isTerminalFailure = ["rejected", "ci_failed", "pre_check_failed", "rolled_back", "rollback_failed", "failed"].includes(state);
  const idx = HAPPY.indexOf(state);
  return (
    <ol className="flex flex-wrap gap-0 mb-4" aria-label="Change state timeline">
      {HAPPY.map((s, i) => {
        const done = idx >= 0 && i < idx;
        const current = s === state;
        return (
          <li key={s} className="flex items-center" aria-current={current ? "step" : undefined}>
            <span className={`px-2 py-1 text-sm border border-border rounded ${current ? "bg-accent text-accent-foreground border-accent" : done ? "bg-success-bg text-success" : "bg-surface text-text-muted"}`}>
              {s}
            </span>
            {i < HAPPY.length - 1 ? <span className="px-1 text-text-muted" aria-hidden="true">→</span> : null}
          </li>
        );
      })}
      {isTerminalFailure ? (
        <li className="flex items-center">
          <span className="px-1 text-text-muted" aria-hidden="true">→</span>
          <span className="px-2 py-1 text-sm border border-danger bg-danger-bg text-danger rounded">{state}</span>
        </li>
      ) : null}
    </ol>
  );
}
