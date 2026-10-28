"use client";

import { useRef, useState } from "react";
import { Bot, Check, FileUp, Plus, Save, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { useApi } from "@/lib/useApi";
import { Badge, Button, Callout, Card, Dialog, Field, Input, PageHeader, Spinner, Textarea, useToast } from "@/components/ui";

interface Evidence { name: string; canonical: string; declared: boolean; confirmed: boolean; in_resume: boolean; in_experience: string[];
  usable: boolean; in_notes: boolean; resumes: string[] | null }
interface ResumeInfo { id: number; label: string; filename: string; included: boolean; uploaded_at: string; sha256: string; size_bytes: number; text_preview: string; is_latex?: boolean }
interface Note { id: string; title: string; text: string }
interface ProfilePayload {
  profile: { skills: { name: string; category: string; proficiency: string; verified: boolean }[]; [k: string]: unknown };
  yaml: string;
  years_of_experience: number;
  skill_evidence: Evidence[];
  resume_suggestions: string[];
  resumes: ResumeInfo[];
  context_notes: Note[];
}

export default function ProfilePage() {
  const { data, loading, error, setData } = useApi<ProfilePayload>("/api/profile");
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  const [draft, setDraft] = useState<{ yaml: string; info: string } | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [aiConfirm, setAiConfirm] = useState(false);
  const [newLabel, setNewLabel] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  async function run(key: string, fn: () => Promise<ProfilePayload>, ok: string) {
    setBusy(key);
    try { setData(await fn()); toast("ok", ok); } catch (e) { toast("danger", e instanceof Error ? e.message : String(e)); } finally { setBusy(null); }
  }

  async function updateSkills(mut: (skills: ProfilePayload["profile"]["skills"]) => ProfilePayload["profile"]["skills"]) {
    if (!data) return;
    const profile = { ...data.profile, skills: mut([...data.profile.skills]) };
    await run("skills", () => api.put("/api/profile", { profile }), "Skills updated");
  }

  if (loading && !data) return <Spinner />;
  if (error && !data) return <Callout tone="danger">{error}</Callout>;
  if (!data) return null;
  const usable = data.skill_evidence.filter((e) => e.usable).length;
  const included = data.resumes.filter((r) => r.included).length;

  return (
    <>
      <PageHeader title="Profile, resumes & context"
                  description="The only source of facts the assistant may use. Upload several resumes (e.g. one per specialisation) and add context in your own words; tailoring then picks the most relevant projects and bullets for each job." />
      <div className="grid gap-5 lg:grid-cols-5">
        <div className="flex flex-col gap-5 lg:col-span-2">
          <Card title={`Resumes (${included} of ${data.resumes.length} included)`}
                subtitle="PDF, DOCX or LaTeX (.tex), up to 5 MB each, max 10. Included resumes are evidence for your skills and are combined when building your profile. The newest included .tex file is used as the LaTeX template for tailored resumes.">
            {data.resumes.length === 0 && <div className="mb-3"><Callout tone="warn">No resume uploaded yet.</Callout></div>}
            <ul className="divide-y divide-ink-100">
              {data.resumes.map((r) => <ResumeRow key={`${r.id}-${r.label}`} r={r} busy={busy}
                onPatch={(body, msg) => run(`r${r.id}`, () => api.patch(`/api/profile/resumes/${r.id}`, body), msg)}
                onDelete={() => run(`r${r.id}`, () => api.del(`/api/profile/resumes/${r.id}`), "Resume deleted")} />)}
            </ul>
            <input ref={fileRef} type="file" accept=".pdf,.docx,.tex,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/x-tex"
                   className="sr-only" id="resume-file" aria-label="Choose resume file"
                   onChange={(e) => { const f = e.target.files?.[0]; if (!f) return; const form = new FormData(); form.append("file", f); form.append("label", newLabel);
                     run("upload", () => api.upload("/api/profile/resume", form), "Resume added"); setNewLabel(""); e.target.value = ""; }} />
            <div className="mt-3 flex flex-wrap items-end gap-2">
              <div className="min-w-40 flex-1"><Field label="Label for the next upload (optional)" htmlFor="new-label">
                <Input id="new-label" value={newLabel} onChange={(e) => setNewLabel(e.target.value)} placeholder="e.g. AI/ML resume" /></Field></div>
              <Button icon={<FileUp className="size-4" />} loading={busy === "upload"} onClick={() => fileRef.current?.click()}>Add resume</Button>
            </div>
            <div className="mt-4 rounded-lg border border-violet-100 bg-violet-50 p-3">
              <p className="text-sm font-medium text-violet-900">Build profile from all resumes & notes</p>
              <p className="mt-0.5 text-xs text-violet-800">Gemini merges projects, experience and skills from every included resume and your notes into one profile draft. Nothing you wrote is removed; you review before saving.</p>
              <Button className="mt-2" size="sm" icon={<Bot className="size-3.5" />} loading={busy === "draft"}
                      disabled={included === 0 && data.context_notes.length === 0} onClick={() => setAiConfirm(true)}>Build draft</Button>
            </div>
          </Card>

          <NotesEditor key={JSON.stringify(data.context_notes)} initial={data.context_notes} saving={busy === "notes"}
                       onSave={(notes) => run("notes", () => api.put("/api/profile/notes", { notes }), "Context saved")} />

          <Card title="Skills & evidence" subtitle={`${usable} usable · experience computed: ~${data.years_of_experience} yrs`}>
            <table className="w-full text-sm">
              <caption className="sr-only">Skill evidence</caption>
              <thead className="text-left text-xs text-ink-500"><tr><th className="pb-1.5 font-medium">Skill</th><th className="pb-1.5 font-medium">Evidence</th><th className="pb-1.5 font-medium"><span className="sr-only">Actions</span></th></tr></thead>
              <tbody className="divide-y divide-ink-100">
                {data.skill_evidence.map((e) => (
                  <tr key={e.canonical}>
                    <td className="py-1.5">{e.name}{!e.usable && <span className="ml-1 text-xs text-ink-300">(not counted)</span>}</td>
                    <td className="py-1.5"><span className="flex flex-wrap gap-1">
                      {e.confirmed && <Badge tone="green">confirmed</Badge>}
                      {e.in_resume && <Badge tone="blue" title={(e.resumes ?? []).join(", ")}>in {e.resumes && e.resumes.length > 1 ? `${e.resumes.length} resumes` : "resume"}</Badge>}
                      {e.in_notes && <Badge tone="blue">in notes</Badge>}
                      {e.in_experience.length > 0 && <Badge tone="violet">in experience/projects</Badge>}
                      {!e.usable && <Badge tone="amber">unverified</Badge>}
                    </span></td>
                    <td className="py-1.5 text-right">
                      {e.declared && !e.confirmed && <Button size="sm" variant="ghost" icon={<Check className="size-3.5" />}
                        onClick={() => updateSkills((s) => s.map((x) => (x.name === e.name ? { ...x, verified: true } : x)))}>Confirm</Button>}
                      {e.declared && e.confirmed && <Button size="sm" variant="ghost"
                        onClick={() => updateSkills((s) => s.map((x) => (x.name === e.name ? { ...x, verified: false } : x)))}>Unconfirm</Button>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {data.resume_suggestions.length > 0 && (
              <div className="mt-3"><p className="mb-1 text-xs text-ink-500">Found in your resume but not in your profile:</p>
                <div className="flex flex-wrap gap-1.5">{data.resume_suggestions.map((s) => (
                  <button key={s} onClick={() => updateSkills((sk) => [...sk, { name: s, category: "", proficiency: "", verified: false }])}
                          className="inline-flex items-center gap-1 rounded-md border border-ink-200 px-1.5 py-0.5 text-xs hover:bg-ink-100"><Plus className="size-3" /> {s}</button>
                ))}</div></div>
            )}
          </Card>

          <Card title="Privacy & data deletion">
            <p className="mb-3 text-sm text-ink-500">Everything is stored in your DATA_DIR. Deletion is immediate and cannot be undone.</p>
            <div className="flex flex-wrap gap-2">
              {[["resume", "Delete all resumes"], ["documents", "Delete generated documents"], ["profile", "Reset profile"], ["all", "Delete all personal data"]].map(([k, l]) => (
                <Button key={k} size="sm" variant="danger" icon={<Trash2 className="size-3.5" />} onClick={() => setConfirmDelete(k)}>{l}</Button>
              ))}
            </div>
          </Card>
        </div>

        <div className="lg:col-span-3">
          <YamlEditor key={data.yaml + (draft?.yaml ?? "")} saved={data.yaml} initial={draft?.yaml ?? data.yaml} draftInfo={draft?.info ?? null}
                      saving={busy === "yaml"} onSave={(yaml) => run("yaml", async () => { const r = await api.put<ProfilePayload>("/api/profile", { yaml }); setDraft(null); return r; }, "Profile saved")} />
        </div>
      </div>

      <Dialog open={aiConfirm} onClose={() => setAiConfirm(false)} title="Build a profile draft with Gemini?"
              footer={<><Button onClick={() => setAiConfirm(false)}>Cancel</Button>
                <Button variant="primary" loading={busy === "draft"} onClick={async () => {
                  setBusy("draft");
                  try { const r = await api.post<{ yaml: string; model: string; added_projects: number; added_bullets: number }>("/api/profile/resume/draft");
                    setDraft({ yaml: r.yaml, info: `Merged by ${r.model}: +${r.added_projects} project(s), +${r.added_bullets} bullet(s). Nothing you wrote was removed. Review every line, then Save.` }); setAiConfirm(false);
                  } catch (e) { toast("danger", e instanceof Error ? e.message : String(e)); } finally { setBusy(null); }
                }}>Send & build draft</Button></>}>
        <p>The text of your <b>{included} included resume(s)</b> and <b>{data.context_notes.length} context note(s)</b> (emails and phone numbers redacted) will be sent to your Vertex AI project. Gemini merges duplicate projects and roles across them and records which document each came from. The draft loads into the editor but is <b>not saved</b> until you review it.</p>
      </Dialog>

      <Dialog open={!!confirmDelete} onClose={() => setConfirmDelete(null)} title="Confirm deletion"
              footer={<><Button onClick={() => setConfirmDelete(null)}>Cancel</Button>
                <Button variant="danger" onClick={async () => { const s = confirmDelete; setConfirmDelete(null);
                  try { const r = await api.del<{ removed: string[] }>(`/api/profile/data?scope=${s}`); toast("ok", `Deleted: ${r.removed.join(", ")}`); setData(await api.get("/api/profile")); }
                  catch (e) { toast("danger", String(e)); } }}>Delete</Button></>}>
        <p>This permanently deletes: <b>{confirmDelete}</b>.</p>
      </Dialog>
    </>
  );
}

function ResumeRow({ r, busy, onPatch, onDelete }: {
  r: ResumeInfo; busy: string | null; onPatch: (body: object, msg: string) => void; onDelete: () => void;
}) {
  const [label, setLabel] = useState(r.label);
  return (
    <li className="py-2.5">
      <div className="flex items-center gap-2">
        <input type="checkbox" aria-label={`Include ${r.label}`} checked={r.included} className="size-4 accent-accent-600"
               onChange={(e) => onPatch({ included: e.target.checked }, e.target.checked ? "Resume included" : "Resume excluded")} />
        <Input aria-label="Resume label" value={label} onChange={(e) => setLabel(e.target.value)} className="py-1"
               onBlur={() => label !== r.label && onPatch({ label }, "Label saved")} />
        <button aria-label={`Delete ${r.label}`} disabled={busy === `r${r.id}`} onClick={onDelete}
                className="rounded p-1 text-ink-500 hover:bg-rose-50 hover:text-rose-700"><Trash2 className="size-4" /></button>
      </div>
      <p className="mt-1 pl-6 text-xs text-ink-500">{r.is_latex && <span className="mr-1 rounded bg-violet-100 px-1.5 py-0.5 font-medium text-violet-800">LaTeX template</span>}{r.filename} · {fmtDate(r.uploaded_at)} · {Math.round(r.size_bytes / 1024)} KB
        {!r.included && <span className="ml-1 text-amber-700">· excluded from evidence</span>}</p>
      <details className="pl-6"><summary className="cursor-pointer text-xs text-ink-500">Extracted text</summary>
        <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-ink-50 p-2 text-xs">{r.text_preview}</pre></details>
    </li>
  );
}

function NotesEditor({ initial, saving, onSave }: { initial: Note[]; saving: boolean; onSave: (n: Note[]) => void }) {
  const [notes, setNotes] = useState<Note[]>(initial);
  const dirty = JSON.stringify(notes) !== JSON.stringify(initial);
  const upd = (i: number, patch: Partial<Note>) => setNotes(notes.map((n, j) => (j === i ? { ...n, ...patch } : n)));
  return (
    <Card title="Additional context" subtitle="Describe projects, achievements, tools you used and outcomes in your own words. Treated as facts you vouch for — used for matching, tailoring and profile building. Write only what is true."
          actions={<Button size="sm" variant="primary" icon={<Save className="size-3.5" />} disabled={!dirty} loading={saving}
                           onClick={() => onSave(notes.filter((n) => n.text.trim()))}>Save</Button>}>
      <ul className="space-y-3">
        {notes.map((n, i) => (
          <li key={n.id || i} className="rounded-lg border border-ink-200 p-2.5">
            <div className="flex gap-2">
              <Input aria-label="Note title" value={n.title} placeholder="Title, e.g. Kyndryl automation project" className="py-1"
                     onChange={(e) => upd(i, { title: e.target.value })} />
              <button aria-label="Remove note" onClick={() => setNotes(notes.filter((_, j) => j !== i))}
                      className="rounded p-1 text-ink-500 hover:bg-rose-50 hover:text-rose-700"><Trash2 className="size-4" /></button>
            </div>
            <Textarea aria-label="Note text" rows={4} value={n.text} onChange={(e) => upd(i, { text: e.target.value })}
                      className="mt-2 font-sans text-sm" placeholder="What you built, your role, the tech stack, measurable results you can verify…" />
          </li>
        ))}
      </ul>
      <Button size="sm" className="mt-3" icon={<Plus className="size-3.5" />} onClick={() => setNotes([...notes, { id: "", title: "", text: "" }])}>Add note</Button>
    </Card>
  );
}

function YamlEditor({ saved, initial, draftInfo, saving, onSave }: {
  saved: string; initial: string; draftInfo: string | null; saving: boolean; onSave: (yaml: string) => void;
}) {
  const [yaml, setYaml] = useState(initial);
  return (
    <Card title="Profile (YAML)" subtitle="Experience bullets, projects, education and application facts. Leave unknown facts blank — they will be asked, never guessed."
          actions={<Button variant="primary" size="sm" icon={<Save className="size-3.5" />} loading={saving} disabled={yaml === saved}
                           onClick={() => onSave(yaml)}>Save profile</Button>}>
      {draftInfo && <div className="mb-3"><Callout tone="warn" title="AI draft loaded — not saved">{draftInfo}</Callout></div>}
      <Textarea aria-label="Profile YAML" spellCheck={false} rows={34} value={yaml} onChange={(e) => setYaml(e.target.value)} />
    </Card>
  );
}
