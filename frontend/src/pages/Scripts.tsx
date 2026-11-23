import { useScripts } from "../lib/api";
import { Button } from "../components/ui/Button";
import { useAuth } from "../lib/auth";

export default function Scripts() {
  const { can } = useAuth();
  const scripts = useScripts();

  if (!can("admin")) {
    return (
      <div>
        <h2 className="text-lg mb-2">Scripts</h2>
        <p className="text-sm text-text-muted">Admin role is required to view the script manifest.</p>
      </div>
    );
  }

  return (
    <div>
      <h2 className="text-lg mb-2">Scripts</h2>
      <p className="text-sm text-text-muted mb-4">
        Custom scripts that may run, at exactly the listed SHA, for the listed change types and
        platforms. Read-only: the manifest is edited by merge request (policies/scripts.yaml).
      </p>
      {scripts.isLoading ? <p className="text-sm">Loading scripts…</p> : null}
      {scripts.isError ? (
        <div>
          <p className="text-sm text-danger" role="alert">Failed to load scripts: {(scripts.error as Error).message}</p>
          <Button variant="secondary" onClick={() => scripts.refetch()}>Retry</Button>
        </div>
      ) : null}
      {!scripts.isLoading && !scripts.isError && scripts.data ? (
        scripts.data.length === 0 ? (
          <p className="text-sm text-text-muted">No scripts listed in the manifest.</p>
        ) : (
          <table aria-label="Scripts">
            <thead>
              <tr>
                <th>Name</th>
                <th>SHA</th>
                <th>Allowed change types</th>
                <th>Platforms</th>
                <th>Writes scope</th>
                <th>Last run</th>
              </tr>
            </thead>
            <tbody>
              {scripts.data.map((s) => (
                <tr key={s.name}>
                  <td className="mono">{s.name}</td>
                  <td className="mono" title={s.sha}>{s.sha.slice(0, 12)}</td>
                  <td>{s.allowed_change_types.join(", ") || "—"}</td>
                  <td>{s.allowed_platforms.join(", ") || "—"}</td>
                  <td className="mono">{s.writes.join(", ") || "—"}</td>
                  <td>{s.last_run ? new Date(s.last_run).toLocaleString() : "never"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )
      ) : null}
    </div>
  );
}
