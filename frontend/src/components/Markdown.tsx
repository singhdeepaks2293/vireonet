import { Fragment, type ReactNode } from "react";

// A small Markdown renderer for the user manual. It builds React elements
// only (never raw HTML), so a manual cannot inject script. Supports what the
// manual uses: headings, paragraphs, lists, block quotes, fenced code, tables,
// horizontal rules, and inline code / bold / italic / links.

const IMAGE_BASE = (import.meta.env.VITE_API_URL as string | undefined) ?? "http://localhost:8000";

export function slugify(text: string): string {
  // GitHub-style anchors, so the manual's table of contents links work.
  return text
    .toLowerCase()
    .replace(/[`*_~]/g, "")
    .replace(/[^\p{L}\p{N}\s-]/gu, "")
    .trim()
    .replace(/\s/g, "-");
}

function safeHref(href: string): string | null {
  if (href.startsWith("#")) return href;
  if (/^https?:\/\//i.test(href)) return href;
  return null; // relative files and other schemes are shown as text
}

export function inline(text: string, key = "i"): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(!?\[[^\]]+\]\([^)\s]+\))|(\*[^*\s][^*]*\*)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let n = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    const k = `${key}-${n++}`;
    if (tok.startsWith("`")) out.push(<code key={k} className="mono text-sm bg-muted px-1 rounded">{tok.slice(1, -1)}</code>);
    else if (tok.startsWith("**")) out.push(<strong key={k} className="font-semibold">{inline(tok.slice(2, -2), k)}</strong>);
    else if (tok.startsWith("![")) {
      // Only the manual's own screenshots: images/<name>.png, served by the API.
      const [, alt, src] = /^!\[([^\]]+)\]\(([^)\s]+)\)$/.exec(tok) ?? [];
      const m = /^images\/([a-z0-9][a-z0-9-]*\.png)$/.exec(src ?? "");
      out.push(m ? <img key={k} src={`${IMAGE_BASE}/help/images/${m[1]}`} alt={alt} loading="lazy" className="max-w-full border border-border rounded my-2" /> : <span key={k} className="text-text-muted">[{alt}]</span>);
    } else if (tok.startsWith("[")) {
      const [, label, href] = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(tok) ?? [];
      const target = safeHref(href ?? "");
      out.push(target
        ? <a key={k} href={target} {...(target.startsWith("#") ? {} : { target: "_blank", rel: "noreferrer noopener" })}>{inline(label ?? "", k)}</a>
        : <span key={k} className="mono">{label}</span>);
    } else out.push(<em key={k}>{inline(tok.slice(1, -1), k)}</em>);
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function cells(row: string): string[] {
  return row.trim().replace(/^\|/, "").replace(/\|$/, "").split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|"));
}

export interface Heading { level: number; text: string; id: string }

export function headings(md: string): Heading[] {
  const out: Heading[] = [];
  let fenced = false;
  for (const line of md.split("\n")) {
    if (line.startsWith("```")) fenced = !fenced;
    const m = !fenced && /^(#{1,3})\s+(.*)$/.exec(line);
    if (m) out.push({ level: m[1].length, text: m[2], id: slugify(m[2]) });
  }
  return out;
}

export function Markdown({ source }: { source: string }) {
  const lines = source.split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  let k = 0;
  const isBlockStart = (l: string) => /^(#{1,6}\s|```|>|\s*[-*]\s|\s*\d+\.\s|\||---\s*$)/.test(l);
  while (i < lines.length) {
    const line = lines[i];
    const key = `b${k++}`;
    if (!line.trim()) { i++; continue; }
    const h = /^(#{1,6})\s+(.*)$/.exec(line);
    if (h) {
      const level = h[1].length;
      const Tag = `h${Math.min(level + 1, 6)}` as "h2";
      const size = level === 1 ? "text-xl" : level === 2 ? "text-lg" : "text-base";
      blocks.push(<Tag key={key} id={slugify(h[2])} className={`${size} font-semibold mt-6 mb-2 scroll-mt-4`}>{inline(h[2], key)}</Tag>);
      i++; continue;
    }
    if (line.startsWith("```")) {
      const body: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) body.push(lines[i++]);
      i++;
      blocks.push(<pre key={key} className="mono text-sm bg-surface border border-border rounded p-2 overflow-x-auto my-2">{body.join("\n")}</pre>);
      continue;
    }
    if (/^---\s*$/.test(line)) { blocks.push(<hr key={key} className="my-4 border-border" />); i++; continue; }
    if (line.startsWith(">")) {
      const body: string[] = [];
      while (i < lines.length && lines[i].startsWith(">")) body.push(lines[i++].replace(/^>\s?/, ""));
      blocks.push(<blockquote key={key} className="border-l-4 border-highlight pl-3 my-2 text-text-muted"><Markdown source={body.join("\n")} /></blockquote>);
      continue;
    }
    if (line.startsWith("|")) {
      const rows: string[] = [];
      while (i < lines.length && lines[i].startsWith("|")) rows.push(lines[i++]);
      const header = cells(rows[0]);
      const body = rows.slice(/^\|[\s:|-]+\|?\s*$/.test(rows[1] ?? "") ? 2 : 1).map(cells);
      blocks.push(
        <div key={key} className="overflow-x-auto my-2">
          <table>
            <thead><tr>{header.map((c, j) => <th key={j}>{inline(c, `${key}h${j}`)}</th>)}</tr></thead>
            <tbody>{body.map((r, ri) => <tr key={ri}>{r.map((c, j) => <td key={j}>{inline(c, `${key}r${ri}c${j}`)}</td>)}</tr>)}</tbody>
          </table>
        </div>,
      );
      continue;
    }
    const li = /^(\s*)([-*]|\d+\.)\s+(.*)$/.exec(line);
    if (li) {
      const ordered = /\d/.test(li[2]);
      const items: string[] = [];
      while (i < lines.length) {
        const m = /^(\s*)([-*]|\d+\.)\s+(.*)$/.exec(lines[i]);
        if (m) { items.push((m[1].length >= 2 ? "   " : "") + m[3]); i++; continue; }
        if (lines[i].trim() && /^\s{2,}\S/.test(lines[i])) { items[items.length - 1] += " " + lines[i].trim(); i++; continue; }
        break;
      }
      const List = ordered ? "ol" : "ul";
      blocks.push(<List key={key} className={`${ordered ? "list-decimal" : "list-disc"} pl-6 my-2 text-sm`}>{items.map((t, j) => <li key={j}>{inline(t, `${key}l${j}`)}</li>)}</List>);
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !isBlockStart(lines[i])) para.push(lines[i++]);
    if (!para.length) para.push(lines[i++]);
    blocks.push(<p key={key} className="text-sm my-2">{inline(para.join(" "), key)}</p>);
  }
  return <Fragment>{blocks}</Fragment>;
}
