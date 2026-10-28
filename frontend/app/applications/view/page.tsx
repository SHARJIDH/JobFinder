"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { BadgeCheck, Bot, ExternalLink, Lock, Save, Send, Undo2 } from "lucide-react";
import { api } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import type { Answer, ApplicationInfo, DocumentInfo, JobSummary } from "@/lib/types";
import { useApi } from "@/lib/useApi";
import { CoverLetterPreview, DownloadLinks, ResumePreview, ValidationReport } from "@/components/documents";
import { StatusBadge } from "@/components/jobs";
import { Badge, Button, Callout, Card, Checkbox, Dialog, Field, Input, PageHeader, Select, Spinner, Tabs, Textarea, useToast } from "@/components/ui";

interface Workspace {
  application: ApplicationInfo;
  job: JobSummary;
  documents: { resume?: DocumentInfo; cover_letter?: DocumentInfo };
  checklist_labels: Record<string, string>;
  blockers: string[];
  browser_automation_enabled: boolean;
}

function WorkspaceInner() {
  const id = useSearchParams().get("id");
  const toast = useToast();
  const { data, loading, error, setData } = useApi<Workspace>(id ? `/api/applications/${id}` : null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [docTab, setDocTab] = useState("resume");
  const [submitOpen, setSubmitOpen] = useState(false);
  const [evidence, setEvidence] = useState("");
  const [method, setMethod] = useState("manual_official_site");
  const [fillReport, setFillReport] = useState<Record<string, unknown> | null>(null);

  async function call(key: string, fn: () => Promise<Workspace | unknown>, ok: string) {
    setBusy(key);
    try {
      const r = await fn();
      if (r && typeof r === "object" && "application" in (r as object)) setData(r as Workspace);
      toast("ok", ok);
      return true;
    } catch (e) {
      toast("danger", e instanceof Error ? e.message : String(e));
      return false;
    } finally {
      setBusy(null);
    }
  }

  if (!id) return <Callout tone="danger">Missing application id.</Callout>;
  if (loading && !data) return <Spinner />;
  if (error && !data) return <Callout tone="danger">{error}</Callout>;
  if (!data) return null;

  const { application: app, job, documents } = data;
  const status = job.pipeline_status;
  const editable = ["ready_for_review", "approved", "resume_ready", "shortlisted"].includes(status);

  return (
    <>
      <nav aria-label="Breadcrumb" className="mb-2 text-xs text-ink-500"><Link href="/applications/" className="hover:underline">Applications</Link> / <Link href={`/jobs/view/?id=${job.id}`} className="hover:underline">{job.company}</Link></nav>
      <PageHeader title={`${job.title} — ${job.company}`}
                  description={<span className="flex items-center gap-2"><StatusBadge status={status} label={job.pipeline_label} />
                    {app.approved_at && <span className="text-xs">approved {fmtDate(app.approved_at)}</span>}
                    {app.submitted_at && <span className="text-xs">submitted {fmtDate(app.submitted_at)}</span>}</span>}
                  actions={<a href={job.apply_url} target="_blank" rel="noopener noreferrer"
                              className="inline-flex items-center gap-1.5 rounded-lg border border-ink-200 bg-white px-3.5 py-2 text-sm font-medium hover:bg-ink-100">
                    Official application page <ExternalLink className="size-4" /></a>} />

      <div className="grid gap-5 xl:grid-cols-5">
        <div className="flex flex-col gap-5 xl:col-span-2">
          {app.concerns.length > 0 && (
            <Callout tone="warn" title="Eligibility & match concerns">
              <ul className="list-disc space-y-0.5 pl-4">{app.concerns.map((c, i) => <li key={i}>{c}</li>)}</ul>
            </Callout>
          )}

          {/* Keyed by the saved answers: unsaved edits survive checklist updates, and reset after a save. */}
          <AnswersEditor key={JSON.stringify(app.answers)} initial={app.answers} editable={editable} approved={status === "approved"}
                         onDirty={setDirty} onSave={async (answers) => { if (await call("save", () => api.put(`/api/applications/${id}`, { answers }), "Answers saved")) setDirty(false); }}
                         saving={busy === "save"} />

          <Card title="Submission checklist">
            <div className="space-y-2.5">
              {Object.entries(data.checklist_labels).map(([k, label]) => (
                <Checkbox key={k} checked={!!app.checklist[k]} label={label}
                          onChange={(v) => call(`c-${k}`, () => api.put(`/api/applications/${id}`, { checklist: { [k]: v } }), "Checklist updated")} />
              ))}
            </div>
          </Card>

          <Card title="Approval & submission">
            {status === "ready_for_review" && (
              <>
                {data.blockers.length > 0 ? (
                  <Callout tone="info" title="Before you can approve"><ul className="list-disc pl-4">{data.blockers.map((b) => <li key={b}>{b}</li>)}</ul></Callout>
                ) : <Callout tone="ok">Everything is checked. Approval locks this exact resume, cover letter and answer set.</Callout>}
                <Button variant="primary" className="mt-3" icon={<Lock className="size-4" />} disabled={data.blockers.length > 0 || dirty} loading={busy === "approve"}
                        onClick={() => call("approve", () => api.post(`/api/applications/${id}/approve`), "Approved")}>Approve this application</Button>
              </>
            )}
            {status === "approved" && (
              <div className="space-y-3">
                <Callout tone="ok" title="Approved">Submit on the official site yourself. The assistant never clicks “Submit”. Then record the confirmation here.</Callout>
                <div className="flex flex-wrap gap-2">
                  <Button variant="primary" icon={<Send className="size-4" />} onClick={() => setSubmitOpen(true)}>Record confirmed submission</Button>
                  <Button icon={<Undo2 className="size-4" />} loading={busy === "revoke"} onClick={() => call("revoke", () => api.post(`/api/applications/${id}/revoke`), "Approval revoked")}>Revoke approval</Button>
                </div>
              </div>
            )}
            {["submitted", "interview", "offer"].includes(status) && (
              <div className="space-y-3 text-sm">
                <p className="flex items-center gap-1.5"><BadgeCheck className="size-4 text-accent-600" /> Submitted {fmtDate(app.submitted_at)} ({app.submission_method.replace(/_/g, " ")})</p>
                <p className="rounded-lg bg-ink-50 p-2 text-xs text-ink-700">Evidence: {app.submission_evidence}</p>
                <div className="flex flex-wrap gap-2">
                  {status === "submitted" && <Button size="sm" onClick={() => call("o", () => api.post(`/api/applications/${id}/outcome`, { status: "interview" }), "Recorded")}>Got an interview</Button>}
                  {status !== "offer" && <Button size="sm" onClick={() => call("o", () => api.post(`/api/applications/${id}/outcome`, { status: "offer" }), "Recorded")}>Offer</Button>}
                  <Button size="sm" variant="danger" onClick={() => call("o", () => api.post(`/api/applications/${id}/outcome`, { status: "rejected" }), "Recorded")}>Rejected</Button>
                  <Button size="sm" variant="ghost" onClick={() => call("o", () => api.post(`/api/applications/${id}/outcome`, { status: "closed" }), "Recorded")}>Closed</Button>
                </div>
              </div>
            )}
            {["rejected", "closed", "archived"].includes(status) && <p className="text-sm text-ink-500">This application is {status}.</p>}
          </Card>

          {data.browser_automation_enabled && ["ready_for_review", "approved"].includes(status) && (
            <Card title="Assisted form filling (local)" subtitle="Greenhouse, Lever and Ashby forms only. Opens a visible browser; stops at CAPTCHAs; never submits.">
              <Button icon={<Bot className="size-4" />} loading={busy === "fill"}
                      onClick={async () => { setBusy("fill"); try { setFillReport(await api.post(`/api/applications/${id}/fill`)); } catch (e) { toast("danger", e instanceof Error ? e.message : String(e)); } finally { setBusy(null); } }}>
                Open & pre-fill form</Button>
              {fillReport && <pre className="mt-3 max-h-48 overflow-auto rounded bg-ink-50 p-2 text-xs">{JSON.stringify(fillReport, null, 2)}</pre>}
            </Card>
          )}

          <Card title="Notes & history">
            <NotesEditor id={id} initial={app.notes} onSaved={(w) => setData(w)} />
            <ol className="mt-3 space-y-1 text-xs text-ink-500">
              {app.history.slice().reverse().map((h, i) => <li key={i}>{fmtDate(h.at)} — {h.from.replace(/_/g, " ")} → <b className="text-ink-700">{h.to.replace(/_/g, " ")}</b>{h.note && ` (${h.note})`}</li>)}
            </ol>
          </Card>
        </div>

        <div className="xl:col-span-3">
          <Tabs value={docTab} onChange={setDocTab} tabs={[{ id: "resume", label: "Tailored resume" }, { id: "cover", label: "Cover letter" }]} />
          <div className="space-y-3 pt-4">
            {docTab === "resume" && (documents.resume ? <>
              <div className="flex items-center justify-between"><DownloadLinks doc={documents.resume} />
                <Link href={`/jobs/view/?id=${job.id}`} className="text-xs text-accent-600 hover:underline">Regenerate from job page</Link></div>
              <ValidationReport doc={documents.resume} />
              {documents.resume.content && <ResumePreview content={documents.resume.content} />}
            </> : <Callout tone="warn">No resume attached.</Callout>)}
            {docTab === "cover" && (documents.cover_letter ? <>
              <DownloadLinks doc={documents.cover_letter} /><CoverLetterPreview doc={documents.cover_letter} />
            </> : <Callout tone="info">No cover letter for this application.</Callout>)}
          </div>
        </div>
      </div>

      <Dialog open={submitOpen} onClose={() => setSubmitOpen(false)} title="Record a confirmed submission"
              footer={<><Button onClick={() => setSubmitOpen(false)}>Cancel</Button>
                <Button variant="primary" loading={busy === "submit"} disabled={evidence.trim().length < 8}
                        onClick={async () => { if (await call("submit", () => api.post(`/api/applications/${id}/submitted`, { evidence, method }), "Submission recorded")) setSubmitOpen(false); }}>
                  Record submission</Button></>}>
        <p className="mb-3 text-ink-500">Only record this after the employer site confirmed your application (on-screen confirmation or email).</p>
        <div className="grid gap-3">
          <Field label="How did you submit?" htmlFor="sub-m">
            <Select id="sub-m" value={method} onChange={(e) => setMethod(e.target.value)}>
              <option value="manual_official_site">I submitted on the official site</option>
              <option value="browser_assist_user_clicked_submit">Assisted fill — I reviewed and clicked Submit myself</option>
            </Select>
          </Field>
          <Field label="Evidence" htmlFor="sub-e" hint="e.g. confirmation message text, email subject, or reference number">
            <Input id="sub-e" value={evidence} onChange={(e) => setEvidence(e.target.value)} />
          </Field>
        </div>
      </Dialog>
    </>
  );
}

function AnswersEditor({ initial, editable, approved, onDirty, onSave, saving }: {
  initial: Answer[]; editable: boolean; approved: boolean; onDirty: (d: boolean) => void; onSave: (a: Answer[]) => void; saving: boolean;
}) {
  const [answers, setAnswers] = useState<Answer[]>(initial);
  const [dirty, setDirty] = useState(false);
  const upd = (i: number, patch: Partial<Answer>) => {
    setAnswers(answers.map((a, j) => (j === i ? { ...a, ...patch } : a)));
    setDirty(true);
    onDirty(true);
  };
  return (
    <Card title="Application answers" subtitle="Facts come only from your profile. Mark each answer verified after checking it."
          actions={editable && <Button size="sm" variant="primary" icon={<Save className="size-3.5" />} disabled={!dirty} loading={saving}
                                       onClick={() => onSave(answers)}>Save</Button>}>
      {dirty && approved && <div className="mb-3"><Callout tone="warn">Saving changes will revoke your approval.</Callout></div>}
      <ul className="space-y-4">
        {answers.map((a, i) => (
          <li key={a.key} className={a.include ? "" : "opacity-50"}>
            <div className="mb-1 flex items-center justify-between gap-2">
              <label htmlFor={`ans-${i}`} className="text-sm font-medium">{a.question}</label>
              <span className="flex gap-1">
                {a.source === "ai_draft" && <Badge tone="violet"><Bot className="size-3" /> AI draft</Badge>}
                {a.source === "profile" && <Badge>from profile</Badge>}
                {a.needs_input && <Badge tone="amber">needs your input</Badge>}
              </span>
            </div>
            <Textarea id={`ans-${i}`} rows={a.key.startsWith("narrative") ? 4 : 1} value={a.answer} disabled={!editable}
                      onChange={(e) => upd(i, { answer: e.target.value, needs_input: !e.target.value.trim(), verified: false, source: a.source === "needs_input" ? "user" : a.source })}
                      className="font-sans text-sm" />
            <div className="mt-1.5 flex flex-wrap gap-4">
              <Checkbox checked={a.verified} onChange={(v) => upd(i, { verified: v })} label={<span className="text-xs">I verified this is accurate</span>} />
              <Checkbox checked={!a.include} onChange={(v) => upd(i, { include: !v })} label={<span className="text-xs">Not asked on this form</span>} />
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function NotesEditor({ id, initial, onSaved }: { id: string; initial: string; onSaved: (w: Workspace) => void }) {
  const [notes, setNotes] = useState(initial);
  const toast = useToast();
  return (
    <div className="space-y-2">
      <Textarea aria-label="Notes" rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} className="font-sans text-sm" placeholder="Referrals, recruiter contacts, deadlines…" />
      <Button size="sm" disabled={notes === initial} onClick={async () => {
        try { onSaved(await api.put<Workspace>(`/api/applications/${id}`, { notes })); toast("ok", "Notes saved"); } catch (e) { toast("danger", String(e)); }
      }}>Save notes</Button>
    </div>
  );
}

export default function ApplicationViewPage() {
  return <Suspense fallback={<Spinner />}><WorkspaceInner /></Suspense>;
}
