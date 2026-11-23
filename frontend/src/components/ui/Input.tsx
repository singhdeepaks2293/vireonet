import type { InputHTMLAttributes } from "react";

export function Input({ className = "", ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={`w-full px-2 py-1 text-sm bg-surface text-text border border-border rounded ${className}`} {...rest} />;
}

export function Label({ htmlFor, children }: { htmlFor: string; children: string }) {
  return (
    <label htmlFor={htmlFor} className="block text-sm text-text-muted mb-1">
      {children}
    </label>
  );
}
