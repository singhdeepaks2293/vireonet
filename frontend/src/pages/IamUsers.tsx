import { useState } from "react";
import { api, useIamRoles, useIamUsers } from "../lib/api";
import { Button } from "../components/ui/Button";
import { Input, Label } from "../components/ui/Input";
import { useAuth } from "../lib/auth";
import type { Role } from "../lib/auth";

const ROLES: Role[] = ["viewer", "operator", "approver", "admin"];

export default function IamUsers() {
  const { can, username: me } = useAuth();
  const users = useIamUsers();
  const roles = useIamRoles();
  const [tab, setTab] = useState<"users" | "roles">("users");
  const [form, setForm] = useState({ username: "", display_name: "", password: "", role: "viewer" as Role });
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  if (!can("admin")) {
    return (
      <div>
        <h2 className="text-lg mb-2">IAM</h2>
        <p className="text-sm text-text-muted">Admin role is required to manage users and roles.</p>
      </div>
    );
  }

  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(""); setNotice("");
    try {
      await api.createIamUser(form);
      setNotice(`Created user ${form.username} with role ${form.role}.`);
      setForm({ username: "", display_name: "", password: "", role: "viewer" });
      await users.refetch();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const update = async (id: string, body: { role?: string; is_active?: boolean }) => {
    setError(""); setNotice("");
    try {
      await api.updateIamUser(id, body);
      setNotice("User updated.");
      await users.refetch();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  return (
    <div>
      <h2 className="text-lg mb-2">IAM</h2>
      <p className="text-sm text-text-muted mb-4">Give a user a role to give them access. Roles are fixed; permissions per role are on the Roles tab. Passwords are at least 12 characters.</p>
      <div className="flex gap-1 border-b border-border mb-4" role="tablist" aria-label="IAM tabs">
        <button type="button" role="tab" aria-selected={tab === "users"} onClick={() => setTab("users")} className={`px-2 py-1 text-sm border-b-2 ${tab === "users" ? "border-accent text-text" : "border-transparent text-text-muted"}`}>Users</button>
        <button type="button" role="tab" aria-selected={tab === "roles"} onClick={() => setTab("roles")} className={`px-2 py-1 text-sm border-b-2 ${tab === "roles" ? "border-accent text-text" : "border-transparent text-text-muted"}`}>Roles</button>
      </div>
      {error ? <p className="text-sm text-danger" role="alert">{error}</p> : null}
      {notice ? <p className="text-sm">{notice}</p> : null}
      {tab === "users" ? (
        <div>
          <form onSubmit={create} className="border border-border bg-surface rounded p-4 mb-4 flex flex-wrap gap-4">
            <div>
              <Label htmlFor="iam-username">Username</Label>
              <Input id="iam-username" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} required />
            </div>
            <div>
              <Label htmlFor="iam-display">Display name</Label>
              <Input id="iam-display" value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
            </div>
            <div>
              <Label htmlFor="iam-password">Password</Label>
              <Input id="iam-password" type="password" autoComplete="new-password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} required />
            </div>
            <div>
              <Label htmlFor="iam-role">Role</Label>
              <select id="iam-role" className="px-2 py-1 text-sm bg-surface border border-border rounded" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as Role })}>
                {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
              </select>
            </div>
            <div className="flex items-end"><Button variant="primary" type="submit">Create user</Button></div>
          </form>
          {users.isLoading ? <p className="text-sm">Loading users…</p> : null}
          {users.isError ? (
            <div>
              <p className="text-sm text-danger" role="alert">Failed to load users: {(users.error as Error).message}</p>
              <Button variant="secondary" onClick={() => users.refetch()}>Retry</Button>
            </div>
          ) : null}
          {!users.isLoading && !users.isError ? (
            <table aria-label="Users">
              <thead><tr><th>Username</th><th>Display name</th><th>Role</th><th>Active</th><th>Actions</th></tr></thead>
              <tbody>
                {(users.data ?? []).map((u) => (
                  <tr key={u.id}>
                    <td className="mono">{u.username}{u.username === me ? " (you)" : ""}</td>
                    <td>{u.display_name}</td>
                    <td>
                      <select aria-label={`Role for ${u.username}`} className="px-2 py-1 text-sm bg-surface border border-border rounded" value={u.role} onChange={(e) => void update(u.id, { role: e.target.value })}>
                        {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                      </select>
                    </td>
                    <td>{u.is_active ? "yes" : "no"}</td>
                    <td>
                      <Button variant="secondary" onClick={() => void update(u.id, { is_active: !u.is_active })}>
                        {u.is_active ? "Deactivate" : "Activate"}
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
          {!users.isLoading && !users.isError && (users.data ?? []).length === 0 ? <p className="text-sm text-text-muted">No users yet.</p> : null}
        </div>
      ) : (
        <div>
          {roles.isLoading ? <p className="text-sm">Loading roles…</p> : null}
          {roles.isError ? <p className="text-sm text-danger">Failed to load roles: {(roles.error as Error).message}</p> : null}
          {!roles.isLoading && !roles.isError && roles.data ? (
            <table aria-label="Roles and permissions">
              <thead>
                <tr><th>Permission</th>{ROLES.map((r) => <th key={r}>{r}</th>)}</tr>
              </thead>
              <tbody>
                {roles.data.permissions.map((p) => (
                  <tr key={p}>
                    <td className="mono">{p}</td>
                    {ROLES.map((r) => <td key={r}>{roles.data!.roles[r]?.includes(p) ? "yes" : "—"}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
        </div>
      )}
    </div>
  );
}
