import { useState } from "react";
import { useAuth } from "../lib/auth";
import { Button } from "../components/ui/Button";
import { Input, Label } from "../components/ui/Input";

export default function Login() {
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  return (
    <div className="min-h-screen bg-bg text-text flex items-start justify-start px-4 py-8">
      <div className="w-[360px] border border-border bg-surface rounded p-4">
        <h1 className="text-lg mb-1">VireoNet</h1>
        <p className="text-sm text-text-muted mb-4">Sign in with your username and password. Your role is set by an admin.</p>
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
            <Button variant="primary" type="submit">Sign in</Button>
          </div>
        </form>
      </div>
    </div>
  );
}
