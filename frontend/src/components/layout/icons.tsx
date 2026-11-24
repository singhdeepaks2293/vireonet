/** Simple 16px line icons for the sidebar (original, Breeze-like weight). */
import type { ReactNode } from "react";

const P: Record<string, ReactNode> = {
  changes: <><path d="M3 4h10M3 8h7M3 12h10" /><path d="M12 6l2 2-2 2" /></>,
  approvals: <><path d="M3 8.5l3 3 7-7" /></>,
  locks: <><rect x="3.5" y="7" width="9" height="6.5" rx="1" /><path d="M5.5 7V5a2.5 2.5 0 015 0v2" /></>,
  drift: <><path d="M2 10c2-4 4 2 6-2s4 2 6-2" /><path d="M2 13.5h12" /></>,
  exceptions: <><circle cx="8" cy="8" r="5.5" /><path d="M8 5v3.5M8 10.8v.2" /></>,
  topology: <><circle cx="8" cy="3.5" r="1.8" /><circle cx="3.5" cy="12" r="1.8" /><circle cx="12.5" cy="12" r="1.8" /><path d="M7.2 5l-2.8 5.3M8.8 5l2.8 5.3M5.3 12h5.4" /></>,
  ztp: <><rect x="2.5" y="4" width="11" height="6" rx="1" /><path d="M5 12.5h6M8 10v2.5M5 7h.01M7 7h.01" /></>,
  repos: <><circle cx="4.5" cy="4" r="1.5" /><circle cx="4.5" cy="12" r="1.5" /><circle cx="11.5" cy="7" r="1.5" /><path d="M4.5 5.5v5M6 4.5c3 0 5.5 0 5.5 1M11.5 8.5c0 2-4 2-5.5 3" /></>,
  checks: <><rect x="3" y="2.5" width="10" height="11" rx="1" /><path d="M5.5 6.5l1.2 1.2L9 5.5M5.5 10.5h5" /></>,
  render: <><path d="M5.5 4.5L2.5 8l3 3.5M10.5 4.5l3 3.5-3 3.5M9 3l-2 10" /></>,
  scripts: <><rect x="2.5" y="3" width="11" height="10" rx="1" /><path d="M5 6.5l2 1.5-2 1.5M8.5 10h2.5" /></>,
  ml: <><circle cx="4" cy="4.5" r="1.4" /><circle cx="4" cy="11.5" r="1.4" /><circle cx="12" cy="8" r="1.4" /><circle cx="8" cy="8" r="1.4" /><path d="M5.3 5.2l1.5 2M5.3 10.8l1.5-2M9.4 8h1.2" /></>,
  health: <><path d="M2 8.5h3l1.5-4 3 8 1.5-4H14" /></>,
  flows: <><rect x="2" y="2" width="5" height="4" rx="1" /><rect x="9" y="10" width="5" height="4" rx="1" /><path d="M4.5 6v3.5a1 1 0 001 1H9" /></>,
  help: <><circle cx="8" cy="8" r="5.5" /><path d="M6.4 6.3a1.7 1.7 0 113 1.1c-.6.5-1.4.8-1.4 1.8M8 11.3v.1" /></>,
  adapters: <><path d="M6 2.5v3M10 2.5v3M4.5 5.5h7v2.5a3.5 3.5 0 01-7 0V5.5zM8 11.5v2" /></>,
  iam: <><circle cx="8" cy="5.5" r="2.5" /><path d="M3 13.5c.8-2.6 2.7-3.8 5-3.8s4.2 1.2 5 3.8" /></>,
  style: <><circle cx="8" cy="8" r="5.5" /><circle cx="6" cy="6.5" r=".6" /><circle cx="9.5" cy="5.8" r=".6" /><circle cx="10.5" cy="9" r=".6" /><path d="M7 11.5a1.5 1.5 0 01-1.5-1.5" /></>,
  excel: <><rect x="2.5" y="2.5" width="11" height="11" rx="1" /><path d="M2.5 6h11M2.5 9.5h11M6.5 2.5v11" /></>,
  textfsm: <><path d="M3 4h10M3 8h4M3 12h6" /><path d="M10 7.5l1.5 1.5L14 6.5" /></>,
  app: <><circle cx="8" cy="8" r="6" /><path d="M4.5 8.5l2.2 2.2 4.8-5" /></>,
};

export function Icon({ name, className = "" }: { name: keyof typeof P | string; className?: string }) {
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className={className}>
      {P[name] ?? P.app}
    </svg>
  );
}
