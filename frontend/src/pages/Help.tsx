import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { request } from "../lib/api";
import { Markdown, headings, slugify } from "../components/Markdown";

// A Confluence-style documentation space: pick a document, browse its page
// tree (parts → chapters), search every page, follow breadcrumbs, and read one
// chapter at a time with its own "On this page" list and previous / next links.

interface DocItem { name: string; title: string }
interface Doc { name: string; title: string; markdown: string }
export interface Page { id: string; title: string; part: string; body: string; anchors: string[] }

/** Split a Markdown document into pages: one per "## " chapter; "# " headings are parts. */
export function splitPages(md: string, docTitle: string): Page[] {
  const pages: Page[] = [];
  let part = "";
  let cur: Page = { id: "overview", title: "Overview", part: "", body: "", anchors: [] };
  let fenced = false;
  const push = () => { if (cur.body.trim()) pages.push(cur); };
  for (const line of md.split("\n")) {
    if (line.startsWith("```")) fenced = !fenced;
    const h1 = !fenced && /^#\s+(.*)$/.exec(line);
    const h2 = !fenced && /^##\s+(.*)$/.exec(line);
    if (h1) {
      if (pages.length === 0 && !cur.body.trim()) { cur.title = docTitle; cur.body += line + "\n"; continue; }
      part = h1[1];
      continue;
    }
    if (h2) {
      push();
      cur = { id: slugify(h2[1]), title: h2[1], part, body: "", anchors: [] };
    }
    cur.body += line + "\n";
  }
  push();
  for (const p of pages) p.anchors = [p.id, ...headings(p.body).map((h) => h.id)];
  return pages;
}

function snippet(text: string, q: string): string {
  const i = text.toLowerCase().indexOf(q.toLowerCase());
  if (i < 0) return "";
  const s = Math.max(0, i - 60);
  return (s > 0 ? "…" : "") + text.slice(s, i + q.length + 80).replace(/[#*`|>]/g, "").replace(/\s+/g, " ") + "…";
}

export default function Help() {
  const [params, setParams] = useSearchParams();
  const docsQ = useQuery({ queryKey: ["help-docs"], queryFn: () => request<{ items: DocItem[] }>("/help/docs"), staleTime: Infinity });
  const docName = params.get("doc") ?? "USER_MANUAL";
  const docQ = useQuery({ queryKey: ["help-doc", docName], queryFn: () => request<Doc>(`/help/docs/${docName}`), staleTime: Infinity });
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const article = useRef<HTMLElement>(null);
  const pages = useMemo(() => (docQ.data ? splitPages(docQ.data.markdown, docQ.data.title) : []), [docQ.data]);
  const pageId = params.get("page") ?? pages[0]?.id ?? "";
  const idx = Math.max(0, pages.findIndex((p) => p.id === pageId));
  const page = pages[idx];

  const go = (id: string, anchor?: string) => {
    const next = new URLSearchParams(params);
    next.set("doc", docName); next.set("page", id);
    setParams(next);
    setTimeout(() => { (anchor ? document.getElementById(anchor) : article.current)?.scrollIntoView({ block: "start" }); }, 0);
  };
  const openAnchor = (anchor: string) => {
    const target = pages.find((p) => p.anchors.includes(anchor));
    if (target) go(target.id, anchor);
  };

  // Old links (/help#45-excel-config-push) and in-page links jump to the right page.
  useEffect(() => {
    const h = window.location.hash.slice(1);
    if (h && pages.length) openAnchor(decodeURIComponent(h));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pages.length]);

  const onArticleClick = (e: React.MouseEvent) => {
    const a = (e.target as HTMLElement).closest("a");
    const href = a?.getAttribute("href") ?? "";
    if (href.startsWith("#")) { e.preventDefault(); openAnchor(href.slice(1)); }
  };

  const results = q.trim().length >= 2
    ? pages
        .filter((p) => p.title !== "Contents")
        .map((p) => ({ p, s: snippet(p.body, q.trim()), t: p.title.toLowerCase().includes(q.trim().toLowerCase()), n: p.body.toLowerCase().split(q.trim().toLowerCase()).length - 1 }))
        .filter((r) => r.s || r.t)
        // Title matches first, then the pages that mention it most.
        .sort((a, b) => Number(b.t) - Number(a.t) || b.n - a.n)
        .slice(0, 30)
    : [];
  const parts = Array.from(new Set(pages.map((p) => p.part)));

  if (docQ.isLoading) return <p className="text-sm">Loading documentation…</p>;
  if (docQ.isError || !page) return <p className="text-sm text-danger" role="alert">Could not load the documentation: {(docQ.error as Error | null)?.message ?? "empty document"}</p>;
  const sub = headings(page.body).filter((h) => h.level === 3);

  return (
    <div className="flex gap-6 items-start">
      <aside aria-label="Documentation" className="w-[270px] shrink-0 sticky top-0 max-h-[88vh] overflow-y-auto">
        <label htmlFor="help-space" className="text-[12px] uppercase text-text-muted">Space</label>
        <select id="help-space" className="w-full px-2 py-1 text-sm bg-surface border border-border rounded mb-2" value={docName}
          onChange={(e) => { setParams(new URLSearchParams({ doc: e.target.value })); setQ(""); }}>
          {(docsQ.data?.items ?? [{ name: "USER_MANUAL", title: "User manual" }]).map((d) => <option key={d.name} value={d.name}>{d.title}</option>)}
        </select>
        <label htmlFor="help-filter" className="text-[12px] uppercase text-text-muted">Search this space</label>
        <input id="help-filter" className="w-full px-2 py-1 text-sm bg-surface border border-border rounded mb-2" value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. webhook, rollback, Zabbix" />
        {results.length ? (
          <ul aria-label="Search results" className="mb-3 flex flex-col gap-1">
            {results.map(({ p, s }) => (
              <li key={p.id}><button type="button" className="text-left w-full rounded px-1 hover:bg-highlight-soft" onClick={() => go(p.id)}>
                <span className="block text-sm text-accent">{p.title}</span>{s ? <span className="block text-[12px] text-text-muted">{s}</span> : null}
              </button></li>
            ))}
          </ul>
        ) : q.trim().length >= 2 ? <p className="text-[12px] text-text-muted mb-2">No page mentions “{q}”.</p> : null}
        <nav aria-label="Page tree">
          {parts.map((part) => {
            const list = pages.filter((p) => p.part === part);
            const isOpen = open[part] ?? (part === page.part || part === "");
            return (
              <div key={part || "_"} className="mb-1">
                {part ? <button type="button" aria-expanded={isOpen} className="w-full text-left text-[12px] font-semibold uppercase text-text-muted py-1" onClick={() => setOpen({ ...open, [part]: !isOpen })}>{isOpen ? "▾" : "▸"} {part}</button> : null}
                {isOpen ? (
                  <ul className={part ? "pl-3 border-l border-border" : ""}>
                    {list.map((p) => (
                      <li key={p.id}>
                        <button type="button" aria-current={p.id === page.id ? "page" : undefined} onClick={() => go(p.id)}
                          className={`block w-full text-left text-sm py-[2px] px-1 rounded ${p.id === page.id ? "bg-highlight-soft text-text" : "text-accent hover:bg-muted"}`}>{p.title}</button>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </div>
            );
          })}
        </nav>
      </aside>
      <article ref={article} className="min-w-0 flex-1 max-w-[920px]" aria-label="Documentation page" onClick={onArticleClick}>
        <nav aria-label="Breadcrumb" className="text-[12px] text-text-muted mb-1">
          Docs › {docQ.data?.title}{page.part ? ` › ${page.part}` : ""} › <span className="text-text">{page.title}</span>
        </nav>
        <Markdown source={page.body} />
        <div className="flex justify-between border-t border-border mt-6 pt-2">
          {idx > 0 ? <button type="button" className="text-sm text-accent" onClick={() => go(pages[idx - 1].id)}>← {pages[idx - 1].title}</button> : <span />}
          {idx < pages.length - 1 ? <button type="button" className="text-sm text-accent" onClick={() => go(pages[idx + 1].id)}>{pages[idx + 1].title} →</button> : <span />}
        </div>
        <p className="text-[12px] text-text-muted mt-2">Source: <span className="mono">docs/{docName}.md</span> · page {idx + 1} of {pages.length}</p>
      </article>
      {sub.length ? (
        <aside aria-label="On this page" className="w-[220px] shrink-0 sticky top-0 hidden xl:block">
          <p className="text-[12px] uppercase text-text-muted mb-1">On this page</p>
          <ul>{sub.map((h) => <li key={h.id}><a className="block text-sm py-[2px]" href={`#${h.id}`} onClick={(e) => { e.preventDefault(); document.getElementById(h.id)?.scrollIntoView(); }}>{h.text}</a></li>)}</ul>
        </aside>
      ) : null}
    </div>
  );
}
