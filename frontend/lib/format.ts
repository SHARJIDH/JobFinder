import type { Salary } from "./types";

export function relTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso).getTime();
  const diff = (Date.now() - d) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 86400 * 30) return `${Math.floor(diff / 86400)}d ago`;
  return new Date(iso).toLocaleDateString();
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function salaryLabel(s: Salary): { text: string; verified: boolean; hint: string } {
  if (s.kind === "unknown" || (s.annual_lpa_min == null && s.annual_lpa_max == null)) {
    return { text: "Not disclosed", verified: false, hint: "No compensation published by the source" };
  }
  const kind = s.kind === "total" ? "total comp" : s.kind === "estimated" ? "estimate" : "base";
  const lo = s.annual_lpa_min, hi = s.annual_lpa_max;
  const range = lo === hi || hi == null ? `₹${lo} L` : lo == null ? `≤ ₹${hi} L` : `₹${lo}–${hi} L`;
  const approx = s.currency && s.currency !== "INR" ? ` (≈ from ${s.currency})` : "";
  return { text: `${range} ${kind}${approx}`, verified: s.kind !== "estimated", hint: `${s.source}${s.text ? `: “${s.text}”` : ""}` };
}

export const STATUS_ORDER = [
  "shortlisted", "resume_ready", "ready_for_review", "approved", "submitted", "interview", "offer", "rejected", "closed", "archived",
];

export function cx(...c: (string | false | null | undefined)[]): string {
  return c.filter(Boolean).join(" ");
}
