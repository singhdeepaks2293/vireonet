import { useState } from "react";
import { useAuth } from "../lib/auth";
import { Button } from "../components/ui/Button";
import { Input, Label } from "../components/ui/Input";
import { Icon } from "../components/layout/icons";

export default function Login() {
  const { login, expired } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  return (
    <div className="min-h-screen bg-bg text-text flex items-center justify-center px-4 py-8">
      <div className="frame w-[380px] max-w-full p-6">
        <div className="flex items-center gap-2 mb-2">
          <span className="inline-flex items-center justify-center w-9 h-9 rounded-[6px] bg-highlight text-[#0d2a3a]"><Icon name="app" /></span>
          <h1 className="text-lg">VireoNet</h1>
        </div>
        <p className="text-sm text-text-muted mb-4">Sign in with your username and password. Your role is set by an admin.</p>
        {expired ? <p className="text-sm text-warning mb-4" role="status">Your session expired or was revoked. Sign in again.</p> : null}
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            setError("");
            try {
              await login(username.trim(), password);
            } catch {
              setError("Invalid username or password.");
            }
          }}
        >
          <Label htmlFor="login-username">Username</Label>
          <Input id="login-username" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} />
          <div className="mt-2">
            <Label htmlFor="login-password">Password</Label>
            <Input id="login-password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </div>
          {error ? <p className="text-sm text-danger mt-2" role="alert">{error}</p> : null}
          <div className="mt-4">
            <Button variant="primary" type="submit" className="w-full justify-center">Sign in</Button>
          </div>
        </form>
      </div>
    </div>
  );
}
