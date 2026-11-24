import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api, UNAUTHORIZED_EVENT } from "./api";

export type Role = "viewer" | "operator" | "approver" | "admin";

const RANK: Record<Role, number> = { viewer: 1, operator: 2, approver: 3, admin: 4 };

function roleFromToken(token: string): Role {
  try {
    const payload = JSON.parse(atob(token.split(".")[1])) as { role?: string };
    return (payload.role as Role) ?? "viewer";
  } catch {
    return "viewer";
  }
}
function usernameFromToken(token: string): string {
  try {
    const payload = JSON.parse(atob(token.split(".")[1])) as { username?: string };
    return payload.username ?? "";
  } catch {
    return "";
  }
}

interface AuthState {
  token: string | null;
  /** True when the last session ended because the API rejected the token. */
  expired: boolean;
  role: Role;
  username: string;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
  can: (minimum: Role) => boolean;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem("vireonet-token"));
  const [expired, setExpired] = useState(false);
  useEffect(() => {
    const onUnauthorized = () => { setExpired(true); setToken(null); };
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, []);
  const login = async (username: string, password: string) => {
    const res = await api.login({ username, password });
    localStorage.setItem("vireonet-token", res.access_token);
    setExpired(false);
    setToken(res.access_token);
  };
  const logout = () => {
    localStorage.removeItem("vireonet-token");
    setToken(null);
  };
  const role: Role = token ? roleFromToken(token) : "viewer";
  const username = token ? usernameFromToken(token) : "";
  const can = (minimum: Role) => RANK[role] >= RANK[minimum];
  return <AuthContext.Provider value={{ token, expired, role, username, login, logout, can }}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}

export function RequireRole({ minimum, children, fallback }: { minimum: Role; children: ReactNode; fallback?: ReactNode }) {
  const { can } = useAuth();
  if (!can(minimum)) return <>{fallback ?? <p className="text-sm text-text-muted">Role {minimum} or above is required for this action.</p>}</>;
  return <>{children}</>;
}
