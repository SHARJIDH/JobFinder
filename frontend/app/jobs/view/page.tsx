"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { Archive, ClipboardCheck, ExternalLink, FileText, RefreshCw, ShieldCheck, Sparkles, Star } from "lucide-react";
import { api } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import type { DocumentInfo, JobDetail } from "@/lib/types";
import { useApi } from "@/lib/useApi";
import { PostedCell, SalaryCell, StatusBadge } from "@/components/jobs";
import { CoverLetterPreview, DownloadLinks, ResumePreview, Traceability, ValidationReport } from "@/components/documents";
import { Badge, Button, Callout, Card, Checkbox, Empty, Input, PageHeader, ScoreBar, Spinner, Tabs, Textarea, TierBadge, useToast } from "@/components/ui";

function JobInner() {
  const sp = useSearchParams();
  const id = sp.get("id");
  const router = useRouter();
  const toast = useToast();
  const { data: job, loading, error, reload } = useApi<JobDetail>(id ? `/api/jobs/${id}` : null);
  const [tab, setTab] = useState("match");
  const [busy, setBusy] = useState<string | null>(null);
  const [useAi, setUseAi] = useState(true);

  async function act<T>(key: string, fn: () => Promise<T>, ok: string) {
    setBusy(key);
    try {
      const r = await fn();
      toast("ok", ok);
      await reload();
      return r;
    } catch (e) {
      toast("danger", e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  if (!id) return <Callout tone="danger">Missing job id.</Callout>;
  if (loading && !job) return <Spinner />;
  if (error) return <Callout tone="danger">{error}</Callout>;
  if (!job) return null;

  const bd = job.breakdown || {};
  const resumes = job.documents.filter((d) => d.kind === "resume");
  const covers = job.documents.filter((d) => d.kind === "cover_letter");
  const pre = job.listing_status !== "unverified" && ["discovered", "shortlisted", "resume_ready", "ready_for_review", "approved"].includes(job.pipeline_status);

  return (
    <>
      <nav aria-label="Breadcrumb" className="mb-2 text-xs text-ink-500"><Link href="/jobs/" className="hover:underline">Jobs</Link> / {job.company}</nav>
      <PageHeader
        title={job.title}
        description={<span className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="font-medium text-ink-700">{job.company}</span>
          <span>{job.location || "Location not stated"}</span>
          <SalaryCell job={job} /><PostedCell job={job} />
          <StatusBadge status={job.pipeline_status} label={job.pipeline_label} />
          {job.listing_status === "closed" && <Badge tone="red">Listing closed</Badge>}
        </span>}
        actions={<a href={job.apply_url} target="_blank" rel="noopener noreferrer"
                    className="inline-flex items-center gap-1.5 rounded-lg bg-accent-600 px-3.5 py-2 text-sm font-medium text-white hover:bg-accent-700">
          Open official posting <ExternalLink className="size-4" aria-hidden /></a>}
      />

      {job.hard_excluded && <div className="mb-4"><Callout tone="danger" title="Hard-excluded">{job.exclusion_reasons.join("; ")}</Callout></div>}
      {job.listing_status === "unverified" && <ImportDescription jobId={job.id} url={job.apply_url} location={job.location} onDone={reload} />}
      {job.duplicate_of_id && <div className="mb-4"><Callout tone="info">This is a duplicate of <Link className="underline" href={`/jobs/view/?id=${job.duplicate_of_id}`}>job #{job.duplicate_of_id}</Link>.</Callout></div>}

      <div className="grid gap-5 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <Tabs value={tab} onChange={setTab} tabs={[
            { id: "match", label: "Match analysis" }, { id: "description", label: "Description" },
            { id: "documents", label: `Documents (${resumes.length})` }, { id: "verification", label: "Source & verification" },
          ]} />
          <div className="pt-4" role="tabpanel">
            {tab === "match" && (
              <div className="space-y-4">
                <Card title="Score breakdown" subtitle={`Deterministic ${bd.deterministic ?? "–"}${bd.ai_score != null ? ` · AI ${bd.ai_score}` : ""} → final ${bd.final ?? "–"}`}>
                  <table className="w-full text-sm">
                    <caption className="sr-only">Score components</caption>
                    <thead className="text-left text-xs text-ink-500"><tr><th className="pb-2 font-medium">Factor</th><th className="pb-2 font-medium">Weight</th><th className="pb-2 font-medium">Fit</th><th className="pb-2 font-medium">Why</th></tr></thead>
                    <tbody className="divide-y divide-ink-100">
                      {(bd.components ?? []).map((c) => (
                        <tr key={c.key}>
                          <td className="py-2 pr-3 font-medium">{c.label}</td>
                          <td className="py-2 pr-3 tabular-nums text-ink-500">{Math.round(c.weight * 100)}%</td>
                          <td className="py-2 pr-3"><ScoreBar value={c.value * 100} label={`${c.label} ${Math.round(c.value * 100)}%`} /></td>
                          <td className="py-2 text-xs text-ink-700">{c.detail}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {bd.flags && bd.flags.length > 0 && <div className="mt-3 flex flex-wrap gap-1.5">{bd.flags.map((f) => <Badge key={f} tone="amber">{f}</Badge>)}</div>}
                </Card>
                <Card title="Skills" subtitle="Only skills you have verified (or that your resume evidences) count as matches.">
                  <div className="grid gap-3 sm:grid-cols-2">
                    <SkillList title="Required — matched" items={bd.skills?.matched_required} tone="green" />
                    <SkillList title="Required — no verified evidence" items={bd.skills?.missing_required} tone="red" />
                    <SkillList title="Preferred — matched" items={bd.skills?.matched_preferred} tone="green" />
                    <SkillList title="Preferred — missing" items={bd.skills?.missing_preferred} tone="neutral" />
                  </div>
                  {job.experience_text && <p className="mt-3 text-xs text-ink-500">Experience requirement found: “{job.experience_text}”</p>}
                </Card>
                <Card title="AI assessment" subtitle={job.ai_analysis ? `${job.ai_analysis.model} · ${fmtDate(job.ai_analysis.at)}` : "Not run for this job"}
                      actions={<Button size="sm" icon={<Sparkles className="size-3.5" />} loading={busy === "rescore"}
                                       onClick={() => act("rescore", () => api.post(`/api/jobs/${job.id}/rescore`, { use_ai: true }), "Re-scored")}>
                        {job.ai_analysis ? "Re-run" : "Run AI match"}</Button>}>
                  {job.ai_analysis ? (
                    <div className="space-y-3 text-sm">
                      <p>{job.ai_analysis.summary}</p>
                      <p className="text-xs text-ink-500">Seniority: {job.ai_analysis.seniority_assessment} · AI fit {job.ai_analysis.fit_score}/100</p>
                      <div className="grid gap-3 sm:grid-cols-2">
                        <TextList title="Strengths" items={job.ai_analysis.strengths} />
                        <TextList title="Gaps" items={job.ai_analysis.gaps} />
                        <TextList title="Missing mandatory qualifications" items={job.ai_analysis.missing_mandatory_qualifications} />
                        <TextList title="Eligibility concerns" items={job.ai_analysis.eligibility_concerns} />
                      </div>
                    </div>
                  ) : <p className="text-sm text-ink-500">The deterministic score is shown. AI matching runs automatically for promising jobs within your daily budget.</p>}
                </Card>
              </div>
            )}
            {tab === "description" && (
              <Card title="Job description" subtitle="Third-party content — shown as data only.">
                <div className="mb-3 flex flex-wrap gap-1.5">
                  {job.required_skills.map((s) => <Badge key={s} tone="blue">{s}</Badge>)}
                  {job.preferred_skills.map((s) => <Badge key={s}>{s} (preferred)</Badge>)}
                </div>
                <div className="prose-jd max-h-[70vh] overflow-y-auto text-ink-700">{job.description}</div>
              </Card>
            )}
            {tab === "documents" && <DocumentsTab resumes={resumes} covers={covers} />}
            {tab === "verification" && (
              <Card title="Source & verification" actions={<Button size="sm" icon={<RefreshCw className="size-3.5" />} loading={busy === "verify"}
                     onClick={() => act("verify", () => api.post<{ result: string }>(`/api/jobs/${job.id}/verify`), "Verification complete")}>Verify now</Button>}>
                <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
                  <dt className="text-ink-500">Source</dt><dd>{job.provider} · board “{job.board}” · id {job.source_job_id}</dd>
                  <dt className="text-ink-500">Original posting date</dt><dd>{job.posted_at ? <span className="inline-flex items-center gap-1"><ShieldCheck className="size-4 text-accent-500" />{fmtDate(job.posted_at)} <span className="text-xs text-ink-500">({job.posted_at_evidence})</span></span> : "Not published by source (unverified)"}</dd>
                  <dt className="text-ink-500">First seen by us</dt><dd>{fmtDate(job.first_seen_at)}</dd>
                  <dt className="text-ink-500">Last verified open</dt><dd>{fmtDate(job.last_verified_at)}</dd>
                  <dt className="text-ink-500">Listing status</dt><dd>{job.listing_status}{job.closed_reason && ` — ${job.closed_reason}`}</dd>
                  <dt className="text-ink-500">Posting page</dt><dd><a className="break-all text-accent-700 underline" href={job.source_url} target="_blank" rel="noopener noreferrer">{job.source_url}</a></dd>
                  <dt className="text-ink-500">Application URL</dt><dd><a className="break-all text-accent-700 underline" href={job.apply_url} target="_blank" rel="noopener noreferrer">{job.apply_url}</a></dd>
                  <dt className="text-ink-500">Salary source</dt><dd>{job.salary.kind === "unknown" ? "Not disclosed" : `${job.salary.kind} — ${job.salary.source}${job.salary.text ? `: “${job.salary.text}”` : ""}`}</dd>
                </dl>
                {job.duplicates.length > 0 && (
                  <div className="mt-4"><p className="mb-1 text-xs font-medium text-ink-500">Merged duplicates</p>
                    <ul className="text-sm">{job.duplicates.map((d) => <li key={d.id}>{d.provider}: <a className="underline" href={d.source_url} target="_blank" rel="noopener noreferrer">{d.title}</a></li>)}</ul></div>
                )}
              </Card>
            )}
          </div>
        </div>

        <aside className="flex flex-col gap-4">
          <Card title="Match">
            <div className="flex items-center gap-3"><TierBadge tier={job.tier} /><ScoreBar value={job.score} /></div>
            <p className="mt-2 text-xs text-ink-500">Ranks suitability first; prestige is only a small weight.</p>
          </Card>
          <Card title="Next step">
            <div className="flex flex-col gap-2">
              {job.application ? (
                <Button variant="primary" icon={<ClipboardCheck className="size-4" />} onClick={() => router.push(`/applications/view/?id=${job.application!.id}`)}>
                  Open application workspace</Button>
              ) : (
                <Button variant="primary" icon={<ClipboardCheck className="size-4" />} loading={busy === "prepare"} disabled={!pre}
                        onClick={async () => { const r = await act("prepare", () => api.post<{ application_id: number; warnings: string[] }>(`/api/jobs/${job.id}/prepare`), "Workspace prepared");
                          if (r) router.push(`/applications/view/?id=${r.application_id}`); }}>
                  Prepare application</Button>
              )}
              <Button icon={<FileText className="size-4" />} loading={busy === "gen"} disabled={!pre}
                      onClick={async () => {
                        const r = await act("gen", () => api.post<{ resume: DocumentInfo }>(`/api/jobs/${job.id}/generate`, { use_ai: useAi, cover_letter: true }), "Tailored resume ready");
                        if (r?.resume?.has_tex) router.push(`/documents/latex/?id=${r.resume.id}`); else if (r) setTab("documents");
                      }}>
                Tailor resume{resumes.length ? " again" : ""}</Button>
              {resumes[0]?.has_tex && <Button icon={<FileText className="size-4" />} onClick={() => router.push(`/documents/latex/?id=${resumes[0].id}`)}>Open LaTeX editor</Button>}
              <Checkbox checked={useAi} onChange={setUseAi} label="Use Gemini for wording" description="Off = deterministic reordering only (no model call)" />
              <div className="mt-1 flex gap-2">
                {job.pipeline_status === "discovered" && <Button size="sm" icon={<Star className="size-3.5" />} loading={busy === "sl"}
                  onClick={() => act("sl", () => api.post(`/api/jobs/${job.id}/status`, { status: "shortlisted" }), "Shortlisted")}>Shortlist</Button>}
                {pre && <Button size="sm" variant="ghost" icon={<Archive className="size-3.5" />} loading={busy === "ar"}
                  onClick={() => act("ar", () => api.post(`/api/jobs/${job.id}/status`, { status: "archived", note: "not interested" }), "Archived")}>Not interested</Button>}
                {job.pipeline_status === "archived" && <Button size="sm" onClick={() => act("un", () => api.post(`/api/jobs/${job.id}/status`, { status: "discovered" }), "Restored")}>Restore</Button>}
              </div>
            </div>
          </Card>
        </aside>
      </div>
    </>
  );
}

function DocumentsTab({ resumes, covers }: { resumes: DocumentInfo[]; covers: DocumentInfo[] }) {
  const [sel, setSel] = useState(0);
  if (!resumes.length) return <Empty title="No tailored documents yet">Use “Generate tailored resume”.</Empty>;
  const doc = resumes[sel];
  const cover = covers.find((c) => c.generation_id === doc.generation_id);
  return (
    <div className="space-y-4">
      <Card title="Tailored resume" subtitle={`${resumes.length} version(s)`} actions={<>
        {resumes.length > 1 && <select aria-label="Version" className="rounded border border-ink-200 px-2 py-1 text-xs" value={sel} onChange={(e) => setSel(Number(e.target.value))}>
          {resumes.map((r, i) => <option key={r.id} value={i}>{fmtDate(r.created_at)} · {r.generator}</option>)}</select>}
        <DownloadLinks doc={doc} /></>}>
        <div className="space-y-3">
          <ValidationReport doc={doc} />
          <Traceability doc={doc} />
          {doc.content?.change_log && doc.content.change_log.length > 0 && <TextList title="What changed" items={doc.content.change_log} />}
          {doc.content?.missing_qualifications && doc.content.missing_qualifications.length > 0 && <TextList title="Missing qualifications (not added)" items={doc.content.missing_qualifications} />}
          {doc.content?.questions_for_candidate && doc.content.questions_for_candidate.length > 0 && <TextList title="Questions for you" items={doc.content.questions_for_candidate} />}
        </div>
      </Card>
      {doc.content && <ResumePreview content={doc.content} />}
      {cover && <Card title="Cover letter" actions={<DownloadLinks doc={cover} />}><CoverLetterPreview doc={cover} /></Card>}
    </div>
  );
}

function ImportDescription({ jobId, url, location, onDone }: { jobId: number; url: string; location: string; onDone: () => void }) {
  const toast = useToast();
  const [text, setText] = useState("");
  const [loc, setLoc] = useState(location);
  const [busy, setBusy] = useState(false);
  return (
    <div className="mb-4">
      <Callout tone="warn" title="Unverified web-search lead">
        <p>Google Search found this posting on the official site, but the site blocks automated reading, so the
          description is not known and it is not ranked yet. <a className="underline" href={url} target="_blank" rel="noopener noreferrer">Open the official page</a>,
          copy the job description and paste it here.</p>
        <div className="mt-3 grid gap-2">
          <Input aria-label="Location" value={loc} onChange={(e) => setLoc(e.target.value)} placeholder="Location as shown on the page" />
          <Textarea aria-label="Job description" rows={6} value={text} onChange={(e) => setText(e.target.value)} className="font-sans text-sm"
                    placeholder="Paste the full job description from the official page" />
          <div><Button variant="primary" size="sm" loading={busy} disabled={text.trim().length < 50} onClick={async () => {
            setBusy(true);
            try { await api.post(`/api/jobs/${jobId}/import-description`, { description: text, location: loc }); toast("ok", "Imported and ranked"); onDone(); }
            catch (e) { toast("danger", e instanceof Error ? e.message : String(e)); }
            finally { setBusy(false); }
          }}>Import & rank</Button></div>
        </div>
      </Callout>
    </div>
  );
}

function SkillList({ title, items, tone }: { title: string; items?: string[]; tone: "green" | "red" | "neutral" }) {
  return (
    <div><p className="mb-1 text-xs font-medium text-ink-500">{title}</p>
      {items?.length ? <div className="flex flex-wrap gap-1">{items.map((s) => <Badge key={s} tone={tone}>{s}</Badge>)}</div> : <p className="text-xs text-ink-300">none</p>}
    </div>
  );
}

function TextList({ title, items }: { title: string; items?: string[] }) {
  if (!items?.length) return null;
  return <div><p className="mb-1 text-xs font-medium text-ink-500">{title}</p><ul className="list-disc space-y-0.5 pl-4 text-sm">{items.map((s, i) => <li key={i}>{s}</li>)}</ul></div>;
}

export default function JobViewPage() {
  return <Suspense fallback={<Spinner />}><JobInner /></Suspense>;
}
