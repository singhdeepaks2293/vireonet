import { useAdaptersHealth } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { Button } from "../ui/Button";

export function Header({ title, theme, onToggleTheme }: { title: string; theme: "light" | "dark"; onToggleTheme: () => void }) {
  const { role, logout } = useAuth();
  const health = useAdaptersHealth();
  const down = health.data?.adapters.filter((a) => a.status === "down").length ?? 0;
  return (
    <header className="flex items-center justify-between px-4 py-2 border-b border-border bg-surface">
      <h1 className="text-lg">{title}</h1>
      <div className="flex items-center gap-4 text-sm">
        <span className="text-text-muted" aria-live="polite">
          {health.isLoading ? "Checking adapters…" : health.isError ? "Adapter health: unavailable" : down > 0 ? `Adapters down: ${down}` : "Adapters: ok"}
        </span>
        <span className="text-text-muted">Role: {role}</span>
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
