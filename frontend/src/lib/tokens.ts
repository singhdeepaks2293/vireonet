/** Token names, for tests and for code that needs to reference a token by name.
 *  Values live in tokens.css. Do not add colour or size literals here. */
export const TOKENS = [
  "bg", "surface", "surface-muted", "border", "text", "text-muted",
  "accent", "accent-foreground",
  "success", "success-bg", "warning", "warning-bg", "danger", "danger-bg", "info", "info-bg",
] as const;
export type TokenName = (typeof TOKENS)[number];

export const CHANGE_STATES = [
  "received", "validated", "planned", "ci_passed", "approved", "pre_checked",
  "applied", "post_checked", "closed",
  "rejected", "ci_failed", "pre_check_failed", "rolled_back", "failed",
] as const;
export type ChangeState = (typeof CHANGE_STATES)[number];

/** Status colour is only for state badges and check results, never decoration. */
export function statusToken(state: ChangeState): "success" | "warning" | "danger" | "info" | "muted" {
  if (state === "closed" || state === "post_checked" || state === "applied") return "success";
  if (state === "rejected" || state === "ci_failed" || state === "pre_check_failed" || state === "failed") return "danger";
  if (state === "rolled_back") return "warning";
  return "info";
}
