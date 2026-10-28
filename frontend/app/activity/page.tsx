"use client";

import { useState } from "react";
import { Bell, Send } from "lucide-react";
import { api, qs } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import type { BudgetStatus, RunInfo } from "@/lib/types";
import { useApi } from "@/lib/useApi";
import { Badge, Button, Callout, Card, Dialog, PageHeader, Select, Spinner, useToast } from "@/components/ui";

interface EventRow { id: number; at: string; level: string; category: string; message: string; job_id: number | null }
interface Usage { items: { day: string; task: string; model: string; calls: number; input_tokens: number; output_tokens: number; est_cost_usd: number; cached: number; failed: number }[];
  total_est_cost_usd: number; budget: BudgetStatus; note: string }

export default function ActivityPage() {
  const [level, setLevel] = useState("");
  const runs = useApi<{ items: RunInfo[]; in_progress: boolean }>("/api/runs");
  const events = useApi<{ items: EventRow[] }>(`/api/events${qs({ level, limit: 200 })}`);
  const usage = useApi<Usage>("/api/usage");
  const toast = useToast();
  const [preview, setPreview] = useState<{ subject: string; text: string } | null>(null);

  return (
    <>
      <PageHeader title="Activity, errors & costs" description="Every run, source error, model call and notification is logged here."
                  actions={<Button icon={<Bell className="size-4" />} onClick={async () => {
                    try { setPreview(await api.post("/api/notify/preview")); } catch (e) { toast("danger", String(e)); } }}>Preview daily digest</Button>} />
      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="Runs" subtitle={runs.data?.in_progress ? "A run is in progress" : undefined}>
          {runs.loading && !runs.data ? <Spinner /> : (
            <ul className="divide-y divide-ink-100 text-sm">
              {(runs.data?.items ?? []).map((r) => (
                <li key={r.id} className="py-2">
                  <div className="flex items-center gap-2"><Badge tone={r.status === "success" ? "green" : r.status === "partial" ? "amber" : r.status === "running" ? "blue" : "red"}>{r.status}</Badge>
                    <span className="font-medium">{r.kind}</span><span className="text-xs text-ink-500">{fmtDate(r.started_at)}</span></div>
                  <details className="mt-1"><summary className="cursor-pointer text-xs text-ink-500">stats</summary>
                    <pre className="mt-1 max-h-56 overflow-auto rounded bg-ink-50 p-2 text-xs">{JSON.stringify(r.stats, null, 2)}</pre></details>
                </li>
              ))}
              {runs.data?.items.length === 0 && <li className="py-2 text-ink-500">No runs yet.</li>}
            </ul>
          )}
        </Card>

        <Card title="AI usage (last 14 days)" subtitle={usage.data?.note}>
          {usage.data && (
            <>
              <p className="mb-2 text-sm">Estimated total: <b>${usage.data.total_est_cost_usd.toFixed(4)}</b> · today {usage.data.budget.calls_today}/{usage.data.budget.call_limit} calls, ${usage.data.budget.est_cost_today_usd.toFixed(4)} of ${usage.data.budget.budget_usd.toFixed(2)}</p>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <caption className="sr-only">Model usage</caption>
                  <thead className="text-left text-ink-500"><tr><th className="py-1 font-medium">Day</th><th className="font-medium">Task</th><th className="font-medium">Model</th><th className="text-right font-medium">Calls</th><th className="text-right font-medium">Cached</th><th className="text-right font-medium">Failed</th><th className="text-right font-medium">Tokens in/out</th><th className="text-right font-medium">Est. $</th></tr></thead>
                  <tbody className="divide-y divide-ink-100">
                    {usage.data.items.map((u, i) => (
                      <tr key={i}><td className="py-1">{u.day}</td><td>{u.task}</td><td className="font-mono">{u.model}</td><td className="text-right">{u.calls}</td><td className="text-right">{u.cached}</td>
                        <td className="text-right">{u.failed}</td><td className="text-right tabular-nums">{u.input_tokens}/{u.output_tokens}</td><td className="text-right tabular-nums">{u.est_cost_usd.toFixed(4)}</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </Card>

        <Card title="Event log" className="lg:col-span-2" actions={
          <Select aria-label="Filter level" value={level} onChange={(e) => setLevel(e.target.value)} className="w-40 py-1 text-xs">
            <option value="">All levels</option><option value="error">Errors</option><option value="warn,error">Warnings + errors</option><option value="info">Info</option>
          </Select>}>
          {events.loading && !events.data ? <Spinner /> : (
            <ul className="max-h-[60vh] divide-y divide-ink-100 overflow-y-auto text-sm">
              {(events.data?.items ?? []).map((e) => (
                <li key={e.id} className="flex gap-3 py-1.5">
                  <span className="w-36 shrink-0 text-xs text-ink-500">{fmtDate(e.at)}</span>
                  <Badge tone={e.level === "error" ? "red" : e.level === "warn" ? "amber" : "neutral"}>{e.category}</Badge>
                  <span className={e.level === "error" ? "text-rose-800" : ""}>{e.message}</span>
                </li>
              ))}
              {events.data?.items.length === 0 && <li className="py-2 text-ink-500">No events.</li>}
            </ul>
          )}
        </Card>
      </div>

      <Dialog open={!!preview} onClose={() => setPreview(null)} title={preview?.subject ?? "Digest"}
              footer={<><Button onClick={() => setPreview(null)}>Close</Button>
                <Button variant="primary" icon={<Send className="size-4" />} onClick={async () => {
                  try { const r = await api.post<{ sent: string[] }>("/api/notify/send"); toast(r.sent.length ? "ok" : "info", r.sent.length ? `Sent via ${r.sent.join(", ")}` : "Nothing sent (no channel configured or nothing new)"); setPreview(null); }
                  catch (e) { toast("danger", String(e)); } }}>Send now</Button></>}>
        <Callout tone="info">Only new Tier A/B (configurable) jobs that were not sent before are included.</Callout>
        <pre className="mt-3 max-h-[50vh] overflow-auto whitespace-pre-wrap text-xs">{preview?.text}</pre>
      </Dialog>
    </>
  );
}
