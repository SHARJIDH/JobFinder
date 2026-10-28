"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";
import { ExternalLink, Plus, Search } from "lucide-react";
import { api, qs } from "@/lib/api";
import type { JobSummary } from "@/lib/types";
import { useApi } from "@/lib/useApi";
import { PostedCell, SalaryCell, StatusBadge } from "@/components/jobs";
import { Badge, Button, Callout, Dialog, Empty, Field, Input, PageHeader, ScoreBar, Select, Spinner, Textarea, TierBadge, useToast } from "@/components/ui";

interface JobList {
  total: number;
  page: number;
  page_size: number;
  items: JobSummary[];
  facets: { companies: string[]; statuses: string[] };
}

const FILTER_KEYS = ["q", "company", "tier", "status", "listing", "min_score", "min_lpa", "salary", "fresh_hours", "location", "include_excluded", "sort", "page"] as const;

function JobsInner() {
  const router = useRouter();
  const sp = useSearchParams();
  const filters = useMemo(() => Object.fromEntries(FILTER_KEYS.map((k) => [k, sp.get(k) ?? ""])) as Record<(typeof FILTER_KEYS)[number], string>, [sp]);
  const path = `/api/jobs${qs({ ...filters, listing: filters.listing || "open", sort: filters.sort || "rank", page_size: 50 })}`;
  const { data, loading, error, reload } = useApi<JobList>(path);
  const [q, setQ] = useState(filters.q);
  const [adding, setAdding] = useState(false);

  function set(patch: Partial<Record<(typeof FILTER_KEYS)[number], string>>) {
    const next = { ...filters, ...patch };
    if (!("page" in patch)) next.page = "";
    router.replace(`/jobs/${qs(next)}`);
  }

  const page = Number(filters.page || 1);
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  return (
    <>
      <PageHeader title="Jobs" description="All discovered openings after deduplication. Excluded jobs are hidden unless you ask for them."
                  actions={<Button variant="primary" icon={<Plus className="size-4" />} onClick={() => setAdding(true)}>Add job manually</Button>} />

      <form role="search" onSubmit={(e) => { e.preventDefault(); set({ q }); }}
            className="mb-4 grid grid-cols-2 gap-3 rounded-xl border border-ink-200 bg-white p-4 md:grid-cols-4 xl:grid-cols-8">
        <div className="col-span-2">
          <Field label="Search title, company, description" htmlFor="f-q">
            <div className="relative">
              <Search className="pointer-events-none absolute left-2.5 top-2.5 size-4 text-ink-300" aria-hidden />
              <Input id="f-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. backend, LLM, Hyderabad" className="pl-8" />
            </div>
          </Field>
        </div>
        <Field label="Company" htmlFor="f-company">
          <Select id="f-company" value={filters.company} onChange={(e) => set({ company: e.target.value })}>
            <option value="">All companies</option>
            {data?.facets.companies.map((c) => <option key={c}>{c}</option>)}
          </Select>
        </Field>
        <Field label="Tier" htmlFor="f-tier">
          <Select id="f-tier" value={filters.tier} onChange={(e) => set({ tier: e.target.value })}>
            <option value="">Any tier</option><option value="A">A only</option><option value="A,B">A + B</option>
            <option value="A,B,C">A, B, C</option><option value="D">D</option>
          </Select>
        </Field>
        <Field label="Min. match score" htmlFor="f-min">
          <Select id="f-min" value={filters.min_score} onChange={(e) => set({ min_score: e.target.value })}>
            <option value="">Any</option>{[50, 60, 65, 70, 78, 85].map((v) => <option key={v} value={v}>{v}+</option>)}
          </Select>
        </Field>
        <Field label="Salary" htmlFor="f-sal">
          <Select id="f-sal" value={filters.min_lpa ? `lpa:${filters.min_lpa}` : filters.salary}
                  onChange={(e) => { const v = e.target.value; set(v.startsWith("lpa:") ? { min_lpa: v.slice(4), salary: "" } : { salary: v, min_lpa: "" }); }}>
            <option value="">Any (incl. undisclosed)</option><option value="known">Disclosed only</option>
            <option value="unknown">Undisclosed only</option>
            {[20, 30, 40, 50].map((v) => <option key={v} value={`lpa:${v}`}>≥ ₹{v} LPA (disclosed)</option>)}
          </Select>
        </Field>
        <Field label="Posted within" htmlFor="f-fresh">
          <Select id="f-fresh" value={filters.fresh_hours} onChange={(e) => set({ fresh_hours: e.target.value })}>
            <option value="">Any time</option><option value="24">24 hours</option><option value="72">72 hours</option>
            <option value="168">7 days</option><option value="720">30 days</option>
          </Select>
        </Field>
        <Field label="Location contains" htmlFor="f-loc">
          <Input id="f-loc" defaultValue={filters.location} onBlur={(e) => e.target.value !== filters.location && set({ location: e.target.value })} placeholder="Bengaluru" />
        </Field>
        <Field label="Workflow status" htmlFor="f-status">
          <Select id="f-status" value={filters.status} onChange={(e) => set({ status: e.target.value })}>
            <option value="">Any</option>{data?.facets.statuses.map((s) => <option key={s} value={s}>{s.replace(/_/g, " ")}</option>)}
          </Select>
        </Field>
        <Field label="Listing" htmlFor="f-listing">
          <Select id="f-listing" value={filters.listing || "open"} onChange={(e) => set({ listing: e.target.value })}>
            <option value="open">Open</option><option value="unverified">Web leads (need description)</option>
            <option value="closed">Closed</option><option value="all">All</option>
          </Select>
        </Field>
        <Field label="Sort" htmlFor="f-sort">
          <Select id="f-sort" value={filters.sort || "rank"} onChange={(e) => set({ sort: e.target.value })}>
            <option value="rank">Best opportunity</option><option value="score">Match score</option>
            <option value="posted">Newest posting</option><option value="first_seen">Recently discovered</option><option value="company">Company</option>
          </Select>
        </Field>
        <label className="col-span-2 flex items-end gap-2 pb-2 text-sm text-ink-700">
          <input type="checkbox" className="size-4 accent-accent-600" checked={filters.include_excluded === "true"}
                 onChange={(e) => set({ include_excluded: e.target.checked ? "true" : "" })} />
          Show hard-excluded jobs
        </label>
      </form>

      {error && <Callout tone="danger">{error}</Callout>}
      {loading && !data ? <Spinner /> : data && (
        <div className="overflow-hidden rounded-xl border border-ink-200 bg-white">
          <div className="flex items-center justify-between border-b border-ink-100 px-4 py-2 text-xs text-ink-500">
            <span>{data.total} job{data.total === 1 ? "" : "s"}</span>
            {loading && <span role="status">Updating…</span>}
          </div>
          {data.items.length === 0 ? <div className="p-4"><Empty title="No jobs match these filters" /></div> : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <caption className="sr-only">Ranked jobs</caption>
                <thead className="bg-ink-50 text-left text-xs text-ink-500">
                  <tr>
                    <th scope="col" className="px-3 py-2 font-medium">Tier</th>
                    <th scope="col" className="px-3 py-2 font-medium">Score</th>
                    <th scope="col" className="px-3 py-2 font-medium">Role</th>
                    <th scope="col" className="px-3 py-2 font-medium">Location</th>
                    <th scope="col" className="px-3 py-2 font-medium">Compensation</th>
                    <th scope="col" className="px-3 py-2 font-medium">Posted</th>
                    <th scope="col" className="px-3 py-2 font-medium">Status</th>
                    <th scope="col" className="px-3 py-2 font-medium"><span className="sr-only">Links</span></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-ink-100">
                  {data.items.map((j) => (
                    <tr key={j.id} className="hover:bg-ink-50">
                      <td className="px-3 py-2.5"><TierBadge tier={j.tier} /></td>
                      <td className="px-3 py-2.5"><ScoreBar value={j.score} /></td>
                      <td className="max-w-md px-3 py-2.5">
                        <Link href={`/jobs/view/?id=${j.id}`} className="font-medium text-ink-900 hover:underline">{j.title}</Link>
                        <div className="text-xs text-ink-500">{j.company}{j.provider === "manual" && " · added manually"}</div>
                        {j.hard_excluded && <div className="mt-1"><Badge tone="red">{j.exclusion_reasons[0]}</Badge></div>}
                      </td>
                      <td className="px-3 py-2.5 text-xs text-ink-700">{j.location || "—"}{j.is_remote && <Badge tone="blue">remote</Badge>}</td>
                      <td className="px-3 py-2.5 text-xs"><SalaryCell job={j} /></td>
                      <td className="whitespace-nowrap px-3 py-2.5 text-xs"><PostedCell job={j} /></td>
                      <td className="px-3 py-2.5"><StatusBadge status={j.pipeline_status} label={j.pipeline_label} /></td>
                      <td className="px-3 py-2.5">
                        <a href={j.apply_url} target="_blank" rel="noopener noreferrer" aria-label={`Official posting for ${j.title} at ${j.company}`}
                           className="inline-flex rounded p-1 text-ink-500 hover:bg-ink-100 hover:text-accent-700"><ExternalLink className="size-4" /></a>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {pages > 1 && (
            <nav aria-label="Pagination" className="flex items-center justify-end gap-2 border-t border-ink-100 px-4 py-2">
              <Button size="sm" disabled={page <= 1} onClick={() => set({ page: String(page - 1) })}>Previous</Button>
              <span className="text-xs text-ink-500">Page {page} of {pages}</span>
              <Button size="sm" disabled={page >= pages} onClick={() => set({ page: String(page + 1) })}>Next</Button>
            </nav>
          )}
        </div>
      )}
      <AddJobDialog open={adding} onClose={() => setAdding(false)} onAdded={(id) => { setAdding(false); reload(); router.push(`/jobs/view/?id=${id}`); }} />
    </>
  );
}

function AddJobDialog({ open, onClose, onAdded }: { open: boolean; onClose: () => void; onAdded: (id: number) => void }) {
  const toast = useToast();
  const [form, setForm] = useState({ company: "", title: "", url: "", location: "", description: "" });
  const [busy, setBusy] = useState(false);
  const [fetching, setFetching] = useState(false);
  const upd = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setForm({ ...form, [k]: e.target.value });

  async function fetchDetails() {
    setFetching(true);
    try {
      const r = await api.post<{ title: string; location: string; description: string }>("/api/jobs/fetch-url", { url: form.url });
      setForm({ ...form, title: form.title || r.title, location: form.location || r.location, description: r.description });
      toast("ok", "Details read from the official page");
    } catch (e) {
      toast("danger", e instanceof Error ? e.message : String(e));
    } finally {
      setFetching(false);
    }
  }

  async function submit() {
    setBusy(true);
    try {
      const r = await api.post<{ id: number; duplicate_of_id: number | null }>("/api/jobs/manual", form);
      if (r.duplicate_of_id) toast("info", "This posting duplicates an existing job — opening the original.");
      onAdded(r.duplicate_of_id ?? r.id);
    } catch (e) {
      toast("danger", e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onClose={onClose} title="Add a job from an official career page"
            footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={busy} onClick={submit}>Add & rank</Button></>}>
      <p className="mb-3 text-ink-500">For companies without a permitted public feed (e.g. Google, Microsoft, Amazon). Paste the official https:// link and the description text.</p>
      <div className="grid gap-3">
        <div className="grid grid-cols-2 gap-3">
          <Field label="Company" htmlFor="m-co"><Input id="m-co" value={form.company} onChange={upd("company")} /></Field>
          <Field label="Location" htmlFor="m-loc"><Input id="m-loc" value={form.location} onChange={upd("location")} /></Field>
        </div>
        <Field label="Job title" htmlFor="m-title"><Input id="m-title" value={form.title} onChange={upd("title")} /></Field>
        <Field label="Official posting URL" htmlFor="m-url" hint="“Fetch details” reads the page’s structured job data when the site allows it.">
          <div className="flex gap-2">
            <Input id="m-url" type="url" value={form.url} onChange={upd("url")} placeholder="https://" />
            <Button type="button" loading={fetching} disabled={!form.url.startsWith("https://")} onClick={fetchDetails}>Fetch details</Button>
          </div>
        </Field>
        <Field label="Job description" htmlFor="m-desc" hint="Treated as untrusted data; never as instructions.">
          <Textarea id="m-desc" rows={8} value={form.description} onChange={upd("description")} />
        </Field>
      </div>
    </Dialog>
  );
}

export default function JobsPage() {
  return <Suspense fallback={<Spinner />}><JobsInner /></Suspense>;
}
