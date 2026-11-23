import type { ButtonHTMLAttributes, ReactNode } from "react";

type Variant = "primary" | "secondary" | "danger" | "ghost";

const CLASSES: Record<Variant, string> = {
  primary: "bg-accent text-accent-foreground border border-accent",
  secondary: "bg-surface text-text border border-border",
  danger: "bg-surface text-danger border border-danger",
  ghost: "bg-transparent text-text border border-transparent",
};

export function Button({ variant = "secondary", children, className = "", ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; children: ReactNode }) {
  return (
    <button type="button" className={`inline-flex items-center px-2 py-1 text-sm rounded ${CLASSES[variant]} disabled:opacity-50 ${className}`} {...rest}>
      {children}
    </button>
  );
}
