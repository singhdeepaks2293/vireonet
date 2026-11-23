import type { DiffLineResponse } from "../lib/api-types";

export function DiffViewer({ lines }: { lines: DiffLineResponse[] }) {
  if (lines.length === 0) return <p className="text-sm text-text-muted">No diff available for this change.</p>;
  return (
    <div className="border border-border bg-surface rounded overflow-auto">
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
      <p className="text-sm text-text-muted p-2">Diff is rendered from the validated intent. Current config is not stored, so all intent lines appear as additions.</p>
    </div>
  );
}
