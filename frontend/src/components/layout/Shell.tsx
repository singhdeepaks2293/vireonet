import type { ReactNode } from "react";
import { Header } from "./Header";
import { Nav } from "./Nav";

export function Shell({ title, theme, onToggleTheme, children }: { title: string; theme: "light" | "dark"; onToggleTheme: () => void; children: ReactNode }) {
  return (
    <div className="min-h-screen bg-bg text-text">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:px-2 focus:py-1 focus:bg-surface focus:border focus:border-border">
        Skip to main content
      </a>
      <div className="flex">
        <div className="fixed top-0 left-0 bottom-0 flex">
          <Nav />
        </div>
        <div className="flex-1 ml-[200px] min-w-0">
          <Header title={title} theme={theme} onToggleTheme={onToggleTheme} />
          <main id="main" className="px-4 py-4">
            {children}
          </main>
        </div>
      </div>
    </div>
  );
}
