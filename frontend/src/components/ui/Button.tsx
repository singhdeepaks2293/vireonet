import type { ButtonHTMLAttributes, ReactNode } from "react";

type Variant = "primary" | "secondary" | "danger" | "ghost";

// Breeze push buttons: flat, 3px corners, highlight border on hover; the
// default (primary) button carries the highlight colour.
const CLASSES: Record<Variant, string> = {
  primary: "bg-highlight-soft text-text border border-highlight hover:bg-highlight",
  secondary: "bg-surface text-text border border-border hover:border-highlight",
  danger: "bg-surface text-danger border border-danger hover:bg-danger-bg",
  ghost: "bg-transparent text-text border border-transparent hover:bg-muted",
};

export function Button({ variant = "secondary", children, className = "", ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; children: ReactNode }) {
  return (
    <button type="button" className={`inline-flex items-center gap-1 px-3 py-1 min-h-[28px] text-sm rounded-[3px] shadow-[0_1px_0_rgba(0,0,0,0.05)] transition-colors ${CLASSES[variant]} disabled:opacity-50 disabled:pointer-events-none ${className}`} {...rest}>
      {children}
    </button>
  );
}
