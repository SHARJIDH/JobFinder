"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import CodeMirror from "@uiw/react-codemirror";
import { StreamLanguage } from "@codemirror/language";
import { stex } from "@codemirror/legacy-modes/mode/stex";
import { EditorView } from "@codemirror/view";
import { Bot, Download, ExternalLink, FileWarning, Maximize2, Play, RotateCcw, Sparkles } from "lucide-react";
import { api } from "@/lib/api";
import { cx } from "@/lib/format";
import { Badge, Button, Callout, Dialog, Input, Spinner, useToast } from "@/components/ui";

interface LatexDoc {
  id: number;
  job_id: number;
  source: string;
  origin: "builtin" | "template" | "edited" | "";
  page_count: number | null;
  compile_log: string;
  compiler_available: boolean;
  compile_ok?: boolean;
  validation: {
    latex_notes?: string[];
    latex_unverified?: { numbers?: string[]; skills?: string[] };
    warnings?: string[];
  };
}

const EXTENSIONS = [StreamLanguage.define(stex), EditorView.lineWrapping];

const ORIGIN: Record<string, string> = { builtin: "built-in template", template: "your LaTeX template", edited: "edited by you" };

function EditorInner() {
  const id = useSearchParams().get("id");
  const toast = useToast();
  const [doc, setDoc] = useState<LatexDoc | null>(null);
  const [source, setSource] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const [instruction, setInstruction] = useState("");
  const [overleaf, setOverleaf] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const overleafForm = useRef<HTMLFormElement>(null);

  const accept = useCallback((d: LatexDoc) => { setDoc(d); setSource(d.source); setVersion((v) => v + 1); }, []);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    api.get<LatexDoc>(`/api/documents/${id}/latex`).then((d) => { if (!cancelled) accept(d); },
      (e) => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)); });
    return () => { cancelled = true; };
  }, [id, accept]);

  async function act(key: string, fn: () => Promise<LatexDoc>, ok: string) {
    setBusy(key);
    try {
      const d = await fn();
      accept(d);
      if (d.compile_ok === false) toast("danger", "Saved, but LaTeX did not compile — see the log below.");
      else toast("ok", ok);
    } catch (e) {
      toast("danger", e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  const save = () => act("save", () => api.put<LatexDoc>(`/api/documents/${id}/latex`, { source }), "Compiled");

  if (!id) return <Callout tone="danger">Missing document id.</Callout>;
  if (error) return <Callout tone="danger">{error}</Callout>;
  if (!doc) return <Spinner label="Loading LaTeX" />;

  const dirty = source !== doc.source;
  const unverified = doc.validation.latex_unverified;
  const hasUnverified = !!(unverified && ((unverified.numbers?.length ?? 0) + (unverified.skills?.length ?? 0)));

  return (
    <div className="flex h-[calc(100vh-3rem)] flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <Link href={`/jobs/view/?id=${doc.job_id}`} className="text-xs text-ink-500 hover:underline">← Back to job</Link>
        <h1 className="text-base font-semibold">LaTeX resume</h1>
        <Badge>{ORIGIN[doc.origin] ?? "LaTeX"}</Badge>
        {doc.page_count != null && <Badge tone={doc.page_count === 1 ? "green" : "amber"}>{doc.page_count} page{doc.page_count === 1 ? "" : "s"}</Badge>}
        {dirty && <Badge tone="blue">unsaved changes</Badge>}
        <div className="ml-auto flex flex-wrap gap-2">
          <Button variant="primary" size="sm" icon={<Play className="size-3.5" />} loading={busy === "save"} disabled={!doc.compiler_available} onClick={save}
                  title="Save and recompile (⌘/Ctrl+S)">Recompile</Button>
          <Button size="sm" icon={<Maximize2 className="size-3.5" />} loading={busy === "fit"} disabled={dirty || doc.page_count === 1}
                  onClick={() => act("fit", () => api.post<LatexDoc>(`/api/documents/${id}/latex/fit`), "Fitted to one page")}>Fit to one page</Button>
          <Button size="sm" variant="ghost" icon={<RotateCcw className="size-3.5" />} disabled={!dirty} onClick={() => setSource(doc.source)}>Discard edits</Button>
          <a href={api.url(`/api/documents/${doc.id}/download?format=tex`)} className="inline-flex items-center gap-1 rounded-lg border border-ink-200 bg-white px-2.5 py-1 text-xs font-medium hover:bg-ink-100"><Download className="size-3.5" />.tex</a>
          <a href={api.url(`/api/documents/${doc.id}/download?format=pdf&inline=true`)} target="_blank" rel="noopener" className="inline-flex items-center gap-1 rounded-lg border border-ink-200 bg-white px-2.5 py-1 text-xs font-medium hover:bg-ink-100"><Download className="size-3.5" />PDF</a>
          <Button size="sm" variant="ghost" icon={<ExternalLink className="size-3.5" />} onClick={() => setOverleaf(true)}>Open in Overleaf</Button>
        </div>
      </div>

      <form onSubmit={(e) => { e.preventDefault(); if (instruction.trim().length >= 3) act("ai", () => api.post<LatexDoc>(`/api/documents/${id}/latex/ai-edit`, { instruction }), "Gemini applied your request").then(() => setInstruction("")); }}
            className="flex gap-2">
        <div className="relative flex-1">
          <Bot className="pointer-events-none absolute left-2.5 top-2.5 size-4 text-violet-500" aria-hidden />
          <Input aria-label="Ask Gemini to change the resume" value={instruction} onChange={(e) => setInstruction(e.target.value)} className="pl-8"
                 placeholder="Ask Gemini: e.g. “put the RAG project first”, “shorten the summary”, “tighten spacing” — it can't add new facts" disabled={dirty} />
        </div>
        <Button type="submit" size="sm" icon={<Sparkles className="size-3.5" />} loading={busy === "ai"} disabled={dirty || instruction.trim().length < 3}>Apply</Button>
      </form>

      {!doc.compiler_available && <Callout tone="warn">No LaTeX compiler on the server. Install Tectonic (<code>brew install tectonic</code>) or use “Open in Overleaf”.</Callout>}

      <div className="grid min-h-0 flex-1 gap-3 lg:grid-cols-2">
        <div className="flex min-h-0 flex-col overflow-hidden rounded-xl border border-ink-200 bg-white"
             onKeyDownCapture={(e) => {
               if ((e.metaKey || e.ctrlKey) && e.key === "s") { e.preventDefault(); if (doc.compiler_available && !busy) save(); }
             }}>
          <CodeMirror value={source} onChange={setSource} height="100%" className="min-h-0 flex-1 overflow-auto text-[13px]"
                      extensions={EXTENSIONS}
                      basicSetup={{ foldGutter: false, highlightActiveLine: true }} aria-label="LaTeX source" />
          <div className={cx("max-h-40 overflow-auto border-t border-ink-100 p-2 text-xs", doc.compile_log ? "bg-ink-50" : "")}>
            {(doc.validation.latex_notes ?? []).slice(-6).map((n, i) => <p key={i} className="text-ink-700">• {n}</p>)}
            {hasUnverified && (
              <p className="mt-1 flex items-start gap-1 text-amber-800"><FileWarning className="mt-0.5 size-3.5 shrink-0" />
                Not found in your profile/resumes/notes — remove unless true:
                {[...(unverified?.numbers ?? []), ...(unverified?.skills ?? [])].join(", ")}</p>
            )}
            {doc.compile_log && <pre className="mt-1 whitespace-pre-wrap font-mono text-[11px] text-rose-800">{doc.compile_log}</pre>}
            {!doc.compile_log && !hasUnverified && !(doc.validation.latex_notes ?? []).length && <p className="text-ink-500">Compiled without errors.</p>}
          </div>
        </div>
        <div className="min-h-0 space-y-3 overflow-auto rounded-xl border border-ink-200 bg-ink-100 p-3" aria-label="PDF preview">
          {Array.from({ length: Math.min(doc.page_count ?? 1, 4) }, (_, i) => (
            // eslint-disable-next-line @next/next/no-img-element
            <img key={`${version}-${i}`} src={api.url(`/api/documents/${doc.id}/preview/${i + 1}?v=${version}`)}
                 alt={`Page ${i + 1} of the compiled resume`} className="mx-auto w-full max-w-[720px] bg-white shadow" />
          ))}
        </div>
      </div>

      <form ref={overleafForm} action="https://www.overleaf.com/docs" method="post" target="_blank" className="hidden">
        <textarea name="snip" readOnly value={source} />
        <input name="engine" readOnly value="xelatex" />
      </form>
      <Dialog open={overleaf} onClose={() => setOverleaf(false)} title="Open this resume in Overleaf?"
              footer={<><Button onClick={() => setOverleaf(false)}>Cancel</Button>
                <Button variant="primary" onClick={() => { overleafForm.current?.submit(); setOverleaf(false); }}>Send to Overleaf</Button></>}>
        <p>The LaTeX source (including your name and contact details) will be sent to overleaf.com and opened as a new project in your Overleaf account. Edits made there are not synced back — paste them here and recompile if you want to keep them.</p>
      </Dialog>
    </div>
  );
}

export default function LatexEditorPage() {
  return <Suspense fallback={<Spinner />}><EditorInner /></Suspense>;
}
