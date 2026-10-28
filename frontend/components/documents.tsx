"use client";

import { Download, FileText, Sparkles } from "lucide-react";
import { api } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import type { DocumentInfo, ResumeContent } from "@/lib/types";
import { Badge, Callout } from "./ui";

export function DownloadLinks({ doc }: { doc: DocumentInfo }) {
  return (
    <div className="flex flex-wrap gap-2">
      {doc.has_pdf && (
        <a href={api.url(`/api/documents/${doc.id}/download?format=pdf`)} className="inline-flex items-center gap-1 rounded-lg border border-ink-200 bg-white px-2.5 py-1 text-xs font-medium hover:bg-ink-100">
          <Download className="size-3.5" aria-hidden /> PDF
        </a>
      )}
      {doc.has_docx && (
        <a href={api.url(`/api/documents/${doc.id}/download?format=docx`)} className="inline-flex items-center gap-1 rounded-lg border border-ink-200 bg-white px-2.5 py-1 text-xs font-medium hover:bg-ink-100">
          <Download className="size-3.5" aria-hidden /> DOCX
        </a>
      )}
      {doc.has_tex && (
        <>
          <a href={api.url(`/api/documents/${doc.id}/download?format=tex`)} className="inline-flex items-center gap-1 rounded-lg border border-ink-200 bg-white px-2.5 py-1 text-xs font-medium hover:bg-ink-100">
            <Download className="size-3.5" aria-hidden /> .tex
          </a>
          <a href={`/documents/latex/?id=${doc.id}`} className="inline-flex items-center gap-1 rounded-lg bg-accent-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-accent-700">
            <FileText className="size-3.5" aria-hidden /> LaTeX editor{doc.page_count ? ` · ${doc.page_count}p` : ""}
          </a>
        </>
      )}
    </div>
  );
}

export function Traceability({ doc }: { doc: DocumentInfo }) {
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
      <dt className="text-ink-500">Generated</dt><dd>{fmtDate(doc.created_at)} · id {doc.generation_id}</dd>
      <dt className="text-ink-500">Method</dt>
      <dd>{doc.generator === "llm" ? <Badge tone="violet"><Sparkles className="size-3" /> {doc.model} ({doc.prompt_version})</Badge> : <Badge>deterministic ({doc.prompt_version})</Badge>}</dd>
      <dt className="text-ink-500">Source resumes</dt>
      <dd>{doc.validation?.sources?.length ? doc.validation.sources.map((s) => s.label).join(", ") : "none uploaded"}
        {doc.source_resume_sha256 && <span className="ml-1 font-mono text-ink-500">({doc.source_resume_sha256.slice(0, 10)})</span>}</dd>
      <dt className="text-ink-500">Profile version</dt><dd className="font-mono">{doc.profile_hash.slice(0, 12)}</dd>
      <dt className="text-ink-500">Job description</dt><dd className="font-mono">{doc.job_content_hash.slice(0, 12)}</dd>
    </dl>
  );
}

export function ValidationReport({ doc }: { doc: DocumentInfo }) {
  const v = doc.validation || {};
  const items: React.ReactNode[] = [];
  (v.rejected_bullets ?? []).forEach((r, i) => items.push(
    <li key={`rb${i}`}><b>Rejected rewrite</b> ({r.source_id}): {r.reason}. Original bullet kept.<div className="mt-0.5 text-ink-500 line-through">{r.text}</div></li>));
  if (v.dropped_skills?.length) items.push(<li key="ds"><b>Skills removed</b> (no verified evidence): {v.dropped_skills.join(", ")}</li>);
  (v.dropped_cover_paragraphs ?? []).forEach((p, i) => items.push(<li key={`cp${i}`}><b>Cover-letter paragraph removed</b>: {p.reason}</li>));
  if (v.summary_fallback) items.push(<li key="sf"><b>Summary</b>: {v.summary_fallback}</li>);
  (v.warnings ?? []).forEach((w, i) => items.push(<li key={`w${i}`}>{w}</li>));
  if (v.injection_flags?.length) items.push(<li key="inj"><b>Instruction-like text in the job posting was ignored</b>: {v.injection_flags.join("; ")}</li>);
  if (!items.length) return <Callout tone="ok">All generated content passed the truthfulness checks.</Callout>;
  return <Callout tone="warn" title="Truthfulness checks"><ul className="list-disc space-y-1 pl-4">{items}</ul></Callout>;
}

export function ResumePreview({ content }: { content: ResumeContent }) {
  const c = content.contact || {};
  const contact = ["email", "phone", "location", "linkedin", "github", "portfolio"].map((k) => c[k]).filter(Boolean).join(" | ");
  return (
    <article aria-label="Tailored resume preview" className="mx-auto max-w-3xl rounded-lg border border-ink-200 bg-white px-8 py-7 font-serif text-[13px] leading-relaxed text-ink-900 shadow-sm">
      <h3 className="text-center text-xl font-bold">{c.full_name || "Your Name"}</h3>
      {contact && <p className="text-center text-xs text-ink-500">{contact}</p>}
      {content.summary && <Section title="Summary"><p>{content.summary}</p></Section>}
      {content.skills?.length > 0 && <Section title="Skills"><p>{content.skills.join(", ")}</p></Section>}
      {content.experience?.length > 0 && (
        <Section title="Experience">
          {content.experience.map((x) => (
            <div key={x.id} className="mb-2">
              <p><b>{x.title} — {x.company}</b> <span className="text-ink-500">{[x.location, x.start && `${x.start} – ${x.end || "Present"}`].filter(Boolean).join(", ")}</span></p>
              <Bullets bullets={x.bullets} />
            </div>
          ))}
        </Section>
      )}
      {content.projects?.length > 0 && (
        <Section title="Projects">
          {content.projects.map((p) => (
            <div key={p.id} className="mb-2">
              <p><b>{p.name}</b> {p.technologies?.length > 0 && <span className="text-ink-500">({p.technologies.join(", ")})</span>}</p>
              <Bullets bullets={p.bullets} />
            </div>
          ))}
        </Section>
      )}
      {content.education?.length > 0 && (
        <Section title="Education">
          {content.education.map((e, i) => <p key={i}><b>{e.degree} — {e.institution}</b> <span className="text-ink-500">{[e.end, e.grade].filter(Boolean).join(", ")}</span></p>)}
        </Section>
      )}
    </article>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className="mt-3"><h4 className="mb-1 border-b border-ink-200 font-sans text-xs font-bold uppercase tracking-wide">{title}</h4>{children}</section>;
}

function Bullets({ bullets }: { bullets: ResumeContent["experience"][number]["bullets"] }) {
  if (!bullets?.length) return <p className="text-xs italic text-ink-500">No bullets in your profile for this entry yet.</p>;
  return (
    <ul className="list-disc pl-5">
      {bullets.map((b) => (
        <li key={b.source_id} title={`Source: ${b.source_id}${b.rewritten ? " (reworded)" : " (original wording)"}`}
            className={b.rewritten ? "decoration-violet-300 decoration-2 underline-offset-4 hover:underline" : ""}>{b.text}</li>
      ))}
    </ul>
  );
}

export function CoverLetterPreview({ doc }: { doc: DocumentInfo }) {
  const paras = doc.content?.paragraphs ?? [];
  return (
    <article aria-label="Cover letter preview" className="mx-auto max-w-3xl rounded-lg border border-ink-200 bg-white px-8 py-7 font-serif text-[13px] leading-relaxed shadow-sm">
      <p className="mb-3 flex items-center gap-1 font-sans text-xs text-ink-500"><FileText className="size-3.5" /> Re: {doc.content?.title} — {doc.content?.company}</p>
      <p>Dear Hiring Team,</p>
      {paras.map((p, i) => <p key={i} className="mt-2">{p}</p>)}
      <p className="mt-3">Sincerely,<br />{doc.content?.contact?.full_name}</p>
    </article>
  );
}
