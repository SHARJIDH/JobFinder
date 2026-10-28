"use client";

import { ExternalLink, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { relTime, salaryLabel } from "@/lib/format";
import type { JobSummary } from "@/lib/types";
import { Badge, ScoreBar, TierBadge, Unverified } from "./ui";

export function SalaryCell({ job }: { job: JobSummary }) {
  const s = salaryLabel(job.salary);
  if (!s.verified && job.salary.kind === "unknown") return <Unverified hint={s.hint}>{s.text}</Unverified>;
  return <span title={s.hint} className={job.salary.kind === "estimated" ? "italic text-ink-500" : ""}>{s.text}</span>;
}

export function PostedCell({ job }: { job: JobSummary }) {
  if (job.posted_at) {
    return (
      <span title={`Posting date evidence: ${job.posted_at_evidence || "source"}`} className="inline-flex items-center gap-1">
        <ShieldCheck className="size-3.5 text-accent-500" aria-label="verified date" /> {relTime(job.posted_at)}
      </span>
    );
  }
  return <Unverified hint="Source did not publish a posting date; showing when we first saw it">seen {relTime(job.first_seen_at)}</Unverified>;
}

export function StatusBadge({ status, label }: { status: string; label: string }) {
  const tone = ({
    shortlisted: "blue", resume_ready: "violet", ready_for_review: "amber", approved: "green", submitted: "green",
    interview: "green", offer: "green", rejected: "red", closed: "neutral", archived: "neutral", discovered: "neutral",
  } as const)[status] ?? "neutral";
  return <Badge tone={tone}>{label}</Badge>;
}

export function JobCard({ job }: { job: JobSummary }) {
  return (
    <li className="flex items-start gap-3 py-3">
      <TierBadge tier={job.tier} />
      <div className="min-w-0 flex-1">
        <Link href={`/jobs/view/?id=${job.id}`} className="block truncate text-sm font-medium text-ink-900 hover:underline">
          {job.title}
        </Link>
        <p className="truncate text-xs text-ink-500">{job.company} · {job.location || "Location not stated"}</p>
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs"><SalaryCell job={job} /><PostedCell job={job} /></div>
      </div>
      <div className="flex flex-col items-end gap-1.5">
        <ScoreBar value={job.score} />
        <a href={job.apply_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-xs text-accent-600 hover:underline">
          Official posting <ExternalLink className="size-3" aria-hidden />
        </a>
      </div>
    </li>
  );
}
