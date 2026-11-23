import { NavLink } from "react-router-dom";
import { useAuth } from "../../lib/auth";

const ITEMS: { to: string; label: string; end?: boolean }[] = [
  { to: "/changes", label: "Changes", end: true },
  { to: "/drift", label: "Drift" },
  { to: "/exceptions", label: "Exceptions" },
  { to: "/ztp", label: "ZTP devices" },
  { to: "/approvals", label: "Approvals" },
  { to: "/locks", label: "Locks and change windows" },
  { to: "/health", label: "System health" },
  { to: "/adapters", label: "Adapters" },
  { to: "/iam", label: "IAM" },
  { to: "/config", label: "Config repos" },
  { to: "/style", label: "Style reference" },
];

export function Nav() {
  const { can } = useAuth();
  // Scripts is admin-only (like the IAM page's content gate): hide the
  // nav entry for non-admins instead of showing a page they cannot use.
  const items = can("admin")
    ? [...ITEMS.slice(0, 9), { to: "/scripts", label: "Scripts" }, ...ITEMS.slice(9)]
    : ITEMS;
  return (
    <nav aria-label="Primary" className="w-[200px] shrink-0 border-r border-border bg-surface">
      <div className="px-4 py-2 border-b border-border">
        <span className="text-lg">VireoNet</span>
        <p className="text-sm text-text-muted">Network change orchestrator</p>
      </div>
      <ul className="py-2">
        {items.map((item) => (
          <li key={item.to}>
            <NavLink
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                `block px-4 py-1 text-sm ${isActive ? "bg-muted text-text border-l-2 border-accent" : "text-text-muted hover:bg-muted hover:text-text"}`
              }
            >
              {item.label}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}
