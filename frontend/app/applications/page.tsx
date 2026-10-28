"use client";

import Link from "next/link";
import { CalendarClock } from "lucide-react";
import { fmtDate } from "@/lib/format";
import type { JobSummary } from "@/lib/types";
import { useApi } from "@/lib/useApi";
import { Badge, Callout, Empty, PageHeader, ScoreBar, Spinner, TierBadge } from "@/components/ui";

interface Item { application_id: number | null; job: JobSummary; approved_at: string | null; submitted_at: string | null; deadline: string | null; needs_input: number }

const COLUMNS: { key: string[]; title: string; hint: string }[] = [
  { key: ["shortlisted", "resume_ready"], title: "Shortlisted / resume ready", hint: "Prepare a workspace when ready" },
  { key: ["ready_for_review"], title: "Ready for review", hint: "Check documents & answers, then approve" },
  { key: ["approved"], title: "Approved", hint: "Submit on the official site, then record evidence" },
  { key: ["submitted"], title: "Submitted", hint: "Confirmed with evidence" },
  { key: ["interview", "offer"], title: "Interview / offer", hint: "" },
  { key: ["rejected", "closed", "archived"], title: "Closed", hint: "Rejected, closed or archived" },
];

export default function ApplicationsPage() {
  const { data, loading, error } = useApi<{ items: Item[]; labels: Record<string, string> }>("/api/applications");
  if (loading && !data) return <Spinner />;
  if (error) return <Callout tone="danger">{error}</Callout>;
  const items = data?.items ?? [];
  return (
    <>
      <PageHeader title="Application tracker"
                  description="Statuses change only through explicit actions. “Submitted” requires your approval of the exact documents and answers, plus evidence of submission." />
      {items.length === 0 ? <Empty title="No applications yet">Shortlist a job or prepare an application from a job page.</Empty> : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {COLUMNS.map((col) => {
            const rows = items.filter((i) => col.key.includes(i.job.pipeline_status));
            return (
              <section key={col.title} aria-label={col.title} className="rounded-xl border border-ink-200 bg-white">
                <header className="flex items-center justify-between border-b border-ink-100 px-4 py-2.5">
                  <div><h2 className="text-sm font-semibold">{col.title}</h2>{col.hint && <p className="text-xs text-ink-500">{col.hint}</p>}</div>
                  <Badge>{rows.length}</Badge>
                </header>
                <ul className="divide-y divide-ink-100">
                  {rows.length === 0 && <li className="px-4 py-4 text-xs text-ink-300">Empty</li>}
                  {rows.map((r) => (
                    <li key={r.job.id} className="px-4 py-3">
                      <div className="flex items-start gap-2">
                        <TierBadge tier={r.job.tier} />
                        <div className="min-w-0 flex-1">
                          <Link href={r.application_id ? `/applications/view/?id=${r.application_id}` : `/jobs/view/?id=${r.job.id}`}
                                className="block truncate text-sm font-medium hover:underline">{r.job.title}</Link>
                          <p className="truncate text-xs text-ink-500">{r.job.company} · {r.job.pipeline_label}</p>
                          <div className="mt-1 flex flex-wrap gap-1.5">
                            {r.needs_input > 0 && <Badge tone="amber">{r.needs_input} answer(s) need input</Badge>}
                            {r.job.listing_status === "closed" && <Badge tone="red">listing closed</Badge>}
                            {r.submitted_at && <Badge tone="green">submitted {fmtDate(r.submitted_at)}</Badge>}
                            {r.deadline && <Badge tone="blue"><CalendarClock className="size-3" /> {fmtDate(r.deadline)}</Badge>}
                          </div>
                        </div>
                        <ScoreBar value={r.job.score} />
                      </div>
                    </li>
                  ))}
                </ul>
              </section>
            );
          })}
        </div>
      )}
    </>
  );
}
