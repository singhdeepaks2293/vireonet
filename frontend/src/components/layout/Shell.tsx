import type { ReactNode } from "react";
import { Header } from "./Header";
import { Nav } from "./Nav";

export function Shell({ title, theme, onToggleTheme, children }: { title: string; theme: "light" | "dark"; onToggleTheme: () => void; children: ReactNode }) {
  return (
    <div className="min-h-screen bg-bg text-text">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:z-20 focus:px-2 focus:py-1 focus:bg-surface focus:border focus:border-highlight">
        Skip to main content
      </a>
      <div className="fixed top-0 left-0 bottom-0 flex">
        <Nav />
      </div>
      <div className="ml-[232px] min-w-0">
        <Header title={title} theme={theme} onToggleTheme={onToggleTheme} />
        {/* The page sits in a Breeze "view" frame on the window background. */}
        <main id="main" className="p-3">
          <div className="frame p-4 min-h-[calc(100vh-72px)]">{children}</div>
        </main>
      </div>
    </div>
  );
}
