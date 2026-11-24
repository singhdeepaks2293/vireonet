import { NavLink } from "react-router-dom";
import { useAuth } from "../../lib/auth";
import { Icon } from "./icons";
import { useGiteaInfo } from "../../pages/Gitea";

interface Item { to: string; label: string; icon: string; end?: boolean; admin?: boolean; gitea?: boolean }

// Grouped like a KDE "Places" panel.
const SECTIONS: { title: string; items: Item[] }[] = [
  {
    title: "Operations",
    items: [
      { to: "/changes", label: "Changes", icon: "changes", end: true },
      { to: "/approvals", label: "Approvals", icon: "approvals" },
      { to: "/locks", label: "Locks and change windows", icon: "locks" },
      { to: "/drift", label: "Drift", icon: "drift" },
      { to: "/exceptions", label: "Exceptions", icon: "exceptions" },
    ],
  },
  {
    title: "Network",
    items: [
      { to: "/topology", label: "Topology", icon: "topology" },
      { to: "/ztp", label: "ZTP devices", icon: "ztp" },
      { to: "/config", label: "Config repos", icon: "repos" },
      { to: "/controllers", label: "Controllers", icon: "adapters" },
      { to: "/gitea", label: "Gitea", icon: "repos", gitea: true },
    ],
  },
  {
    title: "Automation",
    items: [
      { to: "/flows", label: "Flows (workflow builder)", icon: "flows" },
      { to: "/prepost", label: "Pre/Post checks", icon: "checks" },
      { to: "/checks", label: "Check templates", icon: "checks" },
      { to: "/render", label: "Config templates", icon: "render" },
      { to: "/config-push", label: "Config push (Excel)", icon: "excel" },
      { to: "/textfsm", label: "TextFSM", icon: "textfsm" },
      { to: "/scripts", label: "Scripts", icon: "scripts", admin: true },
    ],
  },
  {
    title: "Insights",
    items: [
      { to: "/intelligence", label: "Intelligence", icon: "ml" },
      { to: "/health", label: "System health", icon: "health" },
    ],
  },
  {
    title: "Administration",
    items: [
      { to: "/adapters", label: "Adapters", icon: "adapters" },
      { to: "/iam", label: "IAM", icon: "iam" },
      { to: "/style", label: "Style reference", icon: "style" },
      { to: "/help", label: "Documentation", icon: "help" },
    ],
  },
];

export function Nav() {
  const { can } = useAuth();
  const gitea = useGiteaInfo();
  return (
    <nav aria-label="Primary" className="w-[232px] shrink-0 h-full overflow-y-auto border-r border-border bg-bg px-2 pb-4">
      <div className="flex items-center gap-2 px-2 py-3">
        <span className="inline-flex items-center justify-center w-8 h-8 rounded-[6px] bg-highlight text-[#0d2a3a]"><Icon name="app" /></span>
        <div className="leading-tight">
          <span className="block text-base font-semibold">VireoNet</span>
          <span className="block text-sm text-text-muted">Network change orchestrator</span>
        </div>
      </div>
      {SECTIONS.map((section) => {
        // The Gitea tab only appears when Gitea is installed and answers.
        const items = section.items.filter((i) => (!i.admin || can("admin")) && (!i.gitea || gitea.data?.installed));
        if (!items.length) return null;
        return (
          <div key={section.title} className="mt-3">
            <p className="px-2 mb-1 text-[12px] font-semibold uppercase tracking-wide text-text-muted">{section.title}</p>
            <ul>
              {items.map((item) => (
                <li key={item.to}>
                  <NavLink
                    to={item.to}
                    end={item.end}
                    className={({ isActive }) =>
                      `flex items-center gap-2 px-2 py-[5px] my-[1px] text-sm rounded-[3px] border no-underline hover:no-underline ${
                        isActive
                          ? "bg-highlight-soft border-highlight text-text"
                          : "border-transparent text-text hover:bg-highlight-soft"
                      }`
                    }
                  >
                    <Icon name={item.icon} className="shrink-0 text-text-muted" />
                    <span>{item.label}</span>
                  </NavLink>
                </li>
              ))}
            </ul>
          </div>
        );
      })}
    </nav>
  );
}
