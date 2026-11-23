import { statusToken, type ChangeState } from "../../lib/tokens";

const CLASSES: Record<string, string> = {
  success: "bg-success-bg text-success border border-success",
  warning: "bg-warning-bg text-warning border border-warning",
  danger: "bg-danger-bg text-danger border border-danger",
  info: "bg-info-bg text-info border border-info",
  muted: "bg-muted text-text-muted border border-border",
};

export function StateBadge({ state }: { state: ChangeState }) {
  const token = statusToken(state);
  return <span className={`inline-block px-1 py-0 text-sm rounded ${CLASSES[token]}`}>{state}</span>;
}

export function CheckBadge({ status }: { status: "pass" | "fail" | "skip" }) {
  const token = status === "pass" ? "success" : status === "fail" ? "danger" : "muted";
  return <span className={`inline-block px-1 py-0 text-sm rounded ${CLASSES[token]}`}>{status}</span>;
}
