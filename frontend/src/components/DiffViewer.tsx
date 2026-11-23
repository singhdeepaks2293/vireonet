import type { DiffLineResponse } from "../lib/api-types";

export function DiffViewer({ lines }: { lines: DiffLineResponse[] }) {
  if (lines.length === 0) {
    return (
      <div className="border border-border bg-surface rounded p-2">
        <p className="text-sm">No diff stored for this change.</p>
        <p className="text-sm text-text-muted">The running config was not collected, so there is no stored running config to compare against.</p>
      </div>
    );
  }
  const adds = lines.filter((line) => line.type === "add").length;
  const removes = lines.filter((line) => line.type === "remove").length;
  if (adds === 0 && removes === 0) {
    return (
      <div className="border border-border bg-surface rounded p-2">
        <p className="text-sm">No changes — the candidate config is identical to the running config.</p>
      </div>
    );
  }
  return (
    <div className="border border-border bg-surface rounded overflow-auto">
      <p className="text-sm p-2">+{adds} -{removes} lines, compared against the stored running config.</p>
      <table aria-label="Config diff">
        <thead>
          <tr><th className="w-12">Old</th><th className="w-12">New</th><th>Line</th></tr>
        </thead>
        <tbody className="mono">
          {lines.map((line, i) => (
            <tr key={i} className={line.type === "add" ? "bg-success-bg" : line.type === "remove" ? "bg-danger-bg" : ""}>
              <td className="text-text-muted">{line.old_line ?? ""}</td>
              <td className="text-text-muted">{line.new_line ?? ""}</td>
              <td className="whitespace-pre">{line.type === "add" ? "+ " : line.type === "remove" ? "- " : "  "}{line.text}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
