import { useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { Shell } from "./components/layout/Shell";
import { AuthProvider, useAuth } from "./lib/auth";
import Login from "./pages/Login";
import Adapters from "./pages/Adapters";
import Approvals from "./pages/Approvals";
import ChangeDetail from "./pages/ChangeDetail";
import ChangesList from "./pages/ChangesList";
import ConfigRepos from "./pages/ConfigRepos";
import GitSetup from "./pages/GitSetup";
import IamUsers from "./pages/IamUsers";
import Locks from "./pages/Locks";
import StyleReference from "./pages/StyleReference";
import SystemHealth from "./pages/SystemHealth";
import ZtpDevices from "./pages/ZtpDevices";

const TITLES: Record<string, string> = {
  "/changes": "Changes",
  "/ztp": "ZTP devices",
  "/approvals": "Approvals",
  "/locks": "Locks and change windows",
  "/health": "System health",
  "/adapters": "Adapters",
  "/iam": "IAM",
  "/setup/gitea": "Gitea setup",
  "/config": "Config version control",
  "/style": "Style reference",
};

function titleFor(pathname: string): string {
  if (pathname.startsWith("/changes/")) return "Change detail";
  return TITLES[pathname] ?? "VireoNet";
}

function RoutesWithShell({ theme, onToggleTheme }: { theme: "light" | "dark"; onToggleTheme: () => void }) {
  const { token } = useAuth();
  const location = useLocation();
  if (!token) return <Login />;
  return (
    <Shell title={titleFor(location.pathname)} theme={theme} onToggleTheme={onToggleTheme}>
      <Routes>
        <Route path="/" element={<Navigate to="/changes" replace />} />
        <Route path="/changes" element={<ChangesList />} />
        <Route path="/changes/:id" element={<ChangeDetail />} />
        <Route path="/ztp" element={<ZtpDevices />} />
        <Route path="/approvals" element={<Approvals />} />
        <Route path="/locks" element={<Locks />} />
        <Route path="/health" element={<SystemHealth />} />
        <Route path="/adapters" element={<Adapters />} />
        <Route path="/iam" element={<IamUsers />} />
        <Route path="/setup/gitea" element={<GitSetup />} />
        <Route path="/config" element={<ConfigRepos />} />
        <Route path="/style" element={<StyleReference />} />
        <Route path="*" element={<div><h2 className="text-lg mb-2">Not found</h2><p className="text-sm text-text-muted">This route does not exist.</p></div>} />
      </Routes>
    </Shell>
  );
}

export default function App() {
  const [theme, setTheme] = useState<"light" | "dark">(() => (localStorage.getItem("vireonet-theme") as "light" | "dark" | null) ?? "light");
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("vireonet-theme", theme);
  }, [theme]);
  return (
    <AuthProvider>
      <RoutesWithShell theme={theme} onToggleTheme={() => setTheme((t) => (t === "light" ? "dark" : "light"))} />
    </AuthProvider>
  );
}
