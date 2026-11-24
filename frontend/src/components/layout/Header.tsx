import { useAdaptersHealth } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { Button } from "../ui/Button";

export function Header({ title, theme, onToggleTheme }: { title: string; theme: "light" | "dark"; onToggleTheme: () => void }) {
  const { role, username, logout } = useAuth();
  const health = useAdaptersHealth();
  const down = health.data?.adapters.filter((a) => a.status === "down").length ?? 0;
  const status = health.isLoading ? "Checking adapters…" : health.isError ? "Adapter health: unavailable" : down > 0 ? `Adapters down: ${down}` : "Adapters: ok";
  const dot = health.isLoading ? "bg-border" : health.isError || down > 0 ? "bg-danger" : "bg-success";
  return (
    // A Breeze toolbar: window colour, hairline separator.
    <header className="sticky top-0 z-10 flex items-center justify-between gap-4 px-4 h-12 border-b border-border bg-bg">
      <h1 className="text-lg">{title}</h1>
      <div className="flex items-center gap-2 text-sm">
        <span className="inline-flex items-center gap-2 px-2 py-[3px] rounded-[3px] border border-border bg-surface text-text-muted" aria-live="polite">
          <span className={`inline-block w-2 h-2 rounded-full ${dot}`} aria-hidden="true" />
          {status}
        </span>
        <span className="px-2 py-[3px] rounded-[3px] border border-border bg-surface text-text-muted">
          {username ? `${username} · ` : ""}Role: {role}
        </span>
        <Button variant="secondary" onClick={onToggleTheme} aria-label="Toggle theme">
          Theme: {theme}
        </Button>
        <Button variant="ghost" onClick={logout}>
          Sign out
        </Button>
      </div>
    </header>
  );
}
