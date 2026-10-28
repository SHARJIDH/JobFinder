"use client";

import Link from "next/link";
import { useEffect } from "react";
import { ArrowRight, ExternalLink, Play, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import { fmtDate, relTime } from "@/lib/format";
import type { BudgetStatus, JobSummary, RunInfo } from "@/lib/types";
import { useApi } from "@/lib/useApi";
import { JobCard } from "@/components/jobs";
import { Badge, Button, Callout, Card, Empty, PageHeader, Spinner, TierBadge, useToast } from "@/components/ui";

interface Overview {
  counts: { open: number; new_24h: number; closed: number; duplicates: number; excluded: number; leads: number };
  tiers: Record<string, number>;
  pipeline: Record<string, number>;
  top: JobSummary[];
  fresh: JobSummary[];
  leads: JobSummary[];
  attention: string[];
  last_run: RunInfo | null;
  run_in_progress: boolean;
  llm: BudgetStatus;
  manual_watch: { name: string; careers_url: string; notes: string }[];
}

function Stat({ label, value, hint }: { label: string; value: number | string; hint?: string }) {
  return (
    <div className="rounded-xl border border-ink-200 bg-white px-4 py-3">
      <p className="text-xs text-ink-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
      {hint && <p className="text-xs text-ink-500">{hint}</p>}
    </div>
  );
}

export default function OverviewPage() {
  const { data, loading, error, reload } = useApi<Overview>("/api/overview");
  const toast = useToast();

  useEffect(() => {
    if (!data?.run_in_progress) return;
    const t = setInterval(reload, 5000);
    return () => clearInterval(t);
  }, [data?.run_in_progress, reload]);

  async function start(kind: "daily" | "discover" | "rank") {
    try {
      await api.post(`/api/runs/${kind}`);
      toast("info", `${kind === "daily" ? "Daily run" : kind === "discover" ? "Discovery" : "Ranking"} started`);
      setTimeout(reload, 800);
    } catch (e) {
      toast("danger", e instanceof Error ? e.message : String(e));
    }
  }

  if (loading && !data) return <Spinner />;
  if (error) return <Callout tone="danger">{error}</Callout>;
  if (!data) return null;
  const budgetPct = data.llm.budget_usd ? Math.min(100, (data.llm.est_cost_today_usd / data.llm.budget_usd) * 100) : 0;

  return (
    <>
      <PageHeader
        title="Today"
        description="Fresh openings at your target companies, ranked by suitability. Every link goes to the official posting."
        actions={<>
          <Button icon={<RefreshCw className="size-4" />} onClick={() => start("discover")} disabled={data.run_in_progress}>Discover now</Button>
          <Button variant="primary" icon={<Play className="size-4" />} onClick={() => start("daily")} disabled={data.run_in_progress}>
            Run full daily workflow
          </Button>
        </>}
      />
      {data.run_in_progress && <div className="mb-4"><Callout tone="info">A run is in progress — this page refreshes automatically.</Callout></div>}

      <div className="mb-5 grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Stat label="New in last 24h" value={data.counts.new_24h} />
        <Stat label="Open, ranked" value={data.counts.open} />
        <Stat label="Tier A / B" value={`${data.tiers.A ?? 0} / ${data.tiers.B ?? 0}`} />
        <Stat label="Duplicates merged" value={data.counts.duplicates} />
        <Stat label="Closed detected" value={data.counts.closed} />
      </div>

      <div className="grid gap-5 lg:grid-cols-3">
        <div className="flex flex-col gap-5 lg:col-span-2">
          <Card title="Strongest opportunities" subtitle="Tier A and B, ordered by suitability and opportunity quality"
                actions={<Link href="/jobs/?tier=A,B" className="inline-flex items-center gap-1 text-xs text-accent-600 hover:underline">All jobs <ArrowRight className="size-3" /></Link>}>
            {data.top.length ? <ul className="divide-y divide-ink-100">{data.top.map((j) => <JobCard key={j.id} job={j} />)}</ul>
              : <Empty title="No Tier A/B matches yet">Verify your skills in Profile and run discovery. Scores rise as verified evidence is added.</Empty>}
          </Card>
          <Card title="Discovered in the last 24 hours">
            {data.fresh.length ? <ul className="divide-y divide-ink-100">{data.fresh.map((j) => <JobCard key={j.id} job={j} />)}</ul>
              : <Empty title="Nothing new in the last day" />}
          </Card>
          <Card title={`Web search leads (${data.counts.leads})`}
                subtitle="Found by Google Search on official career sites that block automated reading. Open the posting and paste its description to rank it."
                actions={<Link href="/jobs/?listing=unverified" className="inline-flex items-center gap-1 text-xs text-accent-600 hover:underline">All leads <ArrowRight className="size-3" /></Link>}>
            {data.leads.length ? (
              <ul className="divide-y divide-ink-100">
                {data.leads.map((j) => (
                  <li key={j.id} className="flex items-center justify-between gap-3 py-2.5 text-sm">
                    <div className="min-w-0">
                      <Link href={`/jobs/view/?id=${j.id}`} className="block truncate font-medium hover:underline">{j.title}</Link>
                      <p className="truncate text-xs text-ink-500">{j.company} · {j.location || "location not stated"}</p>
                    </div>
                    <a href={j.apply_url} target="_blank" rel="noopener noreferrer" className="inline-flex shrink-0 items-center gap-1 text-xs text-accent-600 hover:underline">
                      Official page <ExternalLink className="size-3" aria-hidden /></a>
                  </li>
                ))}
              </ul>
            ) : <Empty title="No unverified leads">Leads appear when web search finds postings on sites we cannot read automatically.</Empty>}
          </Card>
        </div>

        <div className="flex flex-col gap-5">
          <Card title="Needs your attention">
            {data.attention.length ? (
              <ul className="space-y-2 text-sm">{data.attention.map((a) => <li key={a} className="flex gap-2"><span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-amber-500" />{a}</li>)}</ul>
            ) : <p className="text-sm text-ink-500">Nothing waiting on you.</p>}
            <Link href="/applications/" className="mt-3 inline-flex items-center gap-1 text-xs text-accent-600 hover:underline">Open tracker <ArrowRight className="size-3" /></Link>
          </Card>

          <Card title="Pipeline">
            <dl className="grid grid-cols-2 gap-y-1.5 text-sm">
              {["shortlisted", "resume_ready", "ready_for_review", "approved", "submitted", "interview", "offer", "rejected"].map((s) => (
                <div key={s} className="contents">
                  <dt className="text-ink-500">{s.replace(/_/g, " ")}</dt>
                  <dd className="text-right font-medium tabular-nums">{data.pipeline[s] ?? 0}</dd>
                </div>
              ))}
            </dl>
          </Card>

          <Card title="Last run">
            {data.last_run ? (
              <div className="space-y-1 text-sm">
                <p className="flex items-center gap-2"><Badge tone={data.last_run.status === "success" ? "green" : data.last_run.status === "partial" ? "amber" : data.last_run.status === "running" ? "blue" : "red"}>{data.last_run.status}</Badge>
                  <span className="text-ink-500">{data.last_run.kind} · {relTime(data.last_run.started_at)}</span></p>
                <p className="text-xs text-ink-500">Finished {fmtDate(data.last_run.finished_at)}</p>
                <Link href="/activity/" className="inline-flex items-center gap-1 text-xs text-accent-600 hover:underline">Logs & errors <ArrowRight className="size-3" /></Link>
              </div>
            ) : <p className="text-sm text-ink-500">No runs yet.</p>}
          </Card>

          <Card title="AI usage today" subtitle={`Provider: ${data.llm.provider}`}>
            <p className="text-sm"><span className="font-semibold tabular-nums">{data.llm.calls_today}</span> / {data.llm.call_limit} calls ·{" "}
              <span className="tabular-nums">${data.llm.est_cost_today_usd.toFixed(3)}</span> of ${data.llm.budget_usd.toFixed(2)} est.</p>
            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-ink-100" role="progressbar" aria-valuenow={budgetPct} aria-valuemin={0} aria-valuemax={100}>
              <div className="h-full bg-accent-500" style={{ width: `${budgetPct}%` }} />
            </div>
            <p className="mt-2 text-xs text-ink-500">Estimate only — your Google Cloud bill is authoritative.</p>
          </Card>

          {data.manual_watch.length > 0 && (
            <Card title="Covered by web search" subtitle="No readable official feed — found daily via Google Search on their official career sites.">
              <ul className="space-y-1.5 text-sm">
                {data.manual_watch.map((c) => (
                  <li key={c.name}>
                    <a href={c.careers_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-accent-700 hover:underline">
                      {c.name} <ExternalLink className="size-3" aria-hidden />
                    </a>
                  </li>
                ))}
              </ul>
            </Card>
          )}
          <div className="flex flex-wrap items-center gap-2 text-xs text-ink-500">
            Tier legend: <TierBadge tier="A" /> apply soon <TierBadge tier="B" /> review <TierBadge tier="C" /> investigate
          </div>
        </div>
      </div>
    </>
  );
}
