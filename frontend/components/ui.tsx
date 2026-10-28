"use client";

import { createContext, useCallback, useContext, useEffect, useId, useRef, useState } from "react";
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from "react";
import { AlertTriangle, CheckCircle2, Info, Loader2, ShieldQuestion, X } from "lucide-react";
import { cx } from "@/lib/format";
import type { Tier } from "@/lib/types";

// ---------------------------------------------------------------- Button

type Variant = "primary" | "secondary" | "ghost" | "danger";
const VARIANTS: Record<Variant, string> = {
  primary: "bg-accent-600 text-white hover:bg-accent-700 disabled:bg-ink-300",
  secondary: "bg-white text-ink-900 border border-ink-200 hover:bg-ink-100 disabled:text-ink-500",
  ghost: "text-ink-700 hover:bg-ink-100 disabled:text-ink-300",
  danger: "bg-white text-rose-700 border border-rose-200 hover:bg-rose-50",
};

export function Button({
  variant = "secondary", size = "md", loading, icon, children, className, ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: "sm" | "md"; loading?: boolean; icon?: ReactNode }) {
  return (
    <button
      {...rest}
      disabled={rest.disabled || loading}
      aria-busy={loading || undefined}
      className={cx(
        "inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition-colors disabled:cursor-not-allowed",
        size === "sm" ? "px-2.5 py-1 text-xs" : "px-3.5 py-2 text-sm",
        VARIANTS[variant], className,
      )}
    >
      {loading ? <Loader2 className="size-4 animate-spin" aria-hidden /> : icon}
      {children}
    </button>
  );
}

export function LinkButton({ href, children, variant = "secondary", icon, external, className }: {
  href: string; children: ReactNode; variant?: Variant; icon?: ReactNode; external?: boolean; className?: string;
}) {
  return (
    <a href={href} target={external ? "_blank" : undefined} rel={external ? "noopener noreferrer" : undefined}
       className={cx("inline-flex items-center gap-1.5 rounded-lg px-3.5 py-2 text-sm font-medium transition-colors", VARIANTS[variant], className)}>
      {icon}{children}
    </a>
  );
}

// ---------------------------------------------------------------- Card / layout

export function Card({ title, actions, children, className, subtitle }: {
  title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; subtitle?: ReactNode;
}) {
  return (
    <section className={cx("rounded-xl border border-ink-200 bg-white", className)}>
      {(title || actions) && (
        <header className="flex flex-wrap items-start justify-between gap-2 border-b border-ink-100 px-4 py-3">
          <div>
            {title && <h2 className="text-sm font-semibold text-ink-900">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-ink-500">{subtitle}</p>}
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink-900">{title}</h1>
        {description && <p className="mt-1 max-w-3xl text-sm text-ink-500">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-ink-200 px-4 py-8 text-center">
      <p className="text-sm font-medium text-ink-700">{title}</p>
      {children && <div className="mt-1 text-sm text-ink-500">{children}</div>}
    </div>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div role="status" className="flex items-center gap-2 py-8 text-sm text-ink-500">
      <Loader2 className="size-4 animate-spin" aria-hidden /> {label}…
    </div>
  );
}

export function Callout({ tone = "info", title, children }: { tone?: "info" | "warn" | "danger" | "ok"; title?: string; children: ReactNode }) {
  const styles = {
    info: "border-sky-200 bg-sky-50 text-sky-900", warn: "border-amber-200 bg-amber-50 text-amber-900",
    danger: "border-rose-200 bg-rose-50 text-rose-900", ok: "border-accent-100 bg-accent-50 text-accent-700",
  }[tone];
  const Icon = tone === "ok" ? CheckCircle2 : tone === "info" ? Info : AlertTriangle;
  return (
    <div className={cx("flex gap-2 rounded-lg border px-3 py-2.5 text-sm", styles)} role={tone === "danger" ? "alert" : undefined}>
      <Icon className="mt-0.5 size-4 shrink-0" aria-hidden />
      <div>{title && <p className="font-medium">{title}</p>}<div className={title ? "mt-0.5" : ""}>{children}</div></div>
    </div>
  );
}

// ---------------------------------------------------------------- Badges

export function Badge({ children, tone = "neutral", title }: { children: ReactNode; tone?: "neutral" | "green" | "blue" | "amber" | "red" | "violet"; title?: string }) {
  const t = {
    neutral: "bg-ink-100 text-ink-700", green: "bg-accent-50 text-accent-700 ring-accent-100",
    blue: "bg-sky-50 text-sky-800", amber: "bg-amber-50 text-amber-800", red: "bg-rose-50 text-rose-700", violet: "bg-violet-50 text-violet-800",
  }[tone];
  return <span title={title} className={cx("inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs font-medium", t)}>{children}</span>;
}

const TIER_STYLE: Record<string, string> = {
  A: "bg-accent-600 text-white", B: "bg-sky-600 text-white", C: "bg-amber-400 text-ink-900",
  D: "bg-ink-200 text-ink-700", X: "bg-rose-100 text-rose-700", "": "bg-ink-100 text-ink-500",
};
const TIER_LABEL: Record<string, string> = {
  A: "Tier A — exceptional match, apply soon", B: "Tier B — strong match, worth reviewing",
  C: "Tier C — possible match, investigate", D: "Below tier thresholds", X: "Hard-excluded by an explicit constraint", "": "Not scored yet",
};

export function TierBadge({ tier }: { tier: Tier }) {
  return (
    <span title={TIER_LABEL[tier]} aria-label={TIER_LABEL[tier]}
          className={cx("inline-flex size-6 items-center justify-center rounded-md text-xs font-bold", TIER_STYLE[tier])}>
      {tier || "–"}
    </span>
  );
}

export function ScoreBar({ value, label }: { value: number | null; label?: string }) {
  const v = value ?? 0;
  const color = v >= 78 ? "bg-accent-500" : v >= 65 ? "bg-sky-500" : v >= 50 ? "bg-amber-400" : "bg-ink-300";
  return (
    <div className="flex items-center gap-2" aria-label={label ?? `Match score ${v.toFixed(0)} of 100`}>
      <span className="w-7 text-right text-sm font-semibold tabular-nums">{value == null ? "–" : v.toFixed(0)}</span>
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-ink-100"><div className={cx("h-full", color)} style={{ width: `${v}%` }} /></div>
    </div>
  );
}

export function Unverified({ children, hint }: { children: ReactNode; hint?: string }) {
  return (
    <span className="inline-flex items-center gap-1 text-ink-500" title={hint}>
      <ShieldQuestion className="size-3.5" aria-hidden /> {children}
    </span>
  );
}

// ---------------------------------------------------------------- Form fields

export function Field({ label, hint, children, htmlFor }: { label: string; hint?: ReactNode; children: ReactNode; htmlFor?: string }) {
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={htmlFor} className="text-xs font-medium text-ink-700">{label}</label>
      {children}
      {hint && <p className="text-xs text-ink-500">{hint}</p>}
    </div>
  );
}

const inputCls = "w-full rounded-lg border border-ink-200 bg-white px-3 py-2 text-sm placeholder:text-ink-300 focus:border-accent-500 focus:outline-none focus:ring-2 focus:ring-accent-100";

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={cx(inputCls, props.className)} />;
}
export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={cx(inputCls, "pr-8", props.className)} />;
}
export function Textarea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={cx(inputCls, "font-mono text-xs leading-relaxed", props.className)} />;
}

export function Checkbox({ checked, onChange, label, description, id }: {
  checked: boolean; onChange: (v: boolean) => void; label: ReactNode; description?: ReactNode; id?: string;
}) {
  const gen = useId();
  const cid = id ?? gen;
  return (
    <div className="flex items-start gap-2.5">
      <input id={cid} type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)}
             className="mt-0.5 size-4 rounded border-ink-300 accent-accent-600" />
      <label htmlFor={cid} className="text-sm text-ink-900">
        {label}
        {description && <span className="block text-xs text-ink-500">{description}</span>}
      </label>
    </div>
  );
}

// ---------------------------------------------------------------- Tabs (WAI-ARIA)

export function Tabs({ tabs, value, onChange }: { tabs: { id: string; label: ReactNode }[]; value: string; onChange: (id: string) => void }) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  return (
    <div role="tablist" className="flex gap-1 border-b border-ink-200">
      {tabs.map((t, i) => (
        <button key={t.id} ref={(el) => { refs.current[i] = el; }} role="tab" aria-selected={value === t.id}
                tabIndex={value === t.id ? 0 : -1}
                onKeyDown={(e) => {
                  if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
                  const n = (i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length;
                  onChange(tabs[n].id);
                  refs.current[n]?.focus();
                }}
                onClick={() => onChange(t.id)}
                className={cx("-mb-px border-b-2 px-3 py-2 text-sm font-medium",
                  value === t.id ? "border-accent-600 text-accent-700" : "border-transparent text-ink-500 hover:text-ink-900")}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- Dialog (native <dialog>)

export function Dialog({ open, onClose, title, children, footer }: {
  open: boolean; onClose: () => void; title: string; children: ReactNode; footer?: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog ref={ref} onClose={onClose} aria-labelledby="dlg-title"
            className="m-auto w-[min(560px,92vw)] rounded-xl border border-ink-200 p-0 shadow-xl">
      <div className="flex items-center justify-between border-b border-ink-100 px-4 py-3">
        <h2 id="dlg-title" className="text-sm font-semibold">{title}</h2>
        <button onClick={onClose} aria-label="Close" className="rounded p-1 hover:bg-ink-100"><X className="size-4" /></button>
      </div>
      <div className="px-4 py-4 text-sm">{children}</div>
      {footer && <div className="flex justify-end gap-2 border-t border-ink-100 px-4 py-3">{footer}</div>}
    </dialog>
  );
}

// ---------------------------------------------------------------- Toasts

type Toast = { id: number; tone: "ok" | "danger" | "info"; text: string };
const ToastCtx = createContext<(tone: Toast["tone"], text: string) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((tone: Toast["tone"], text: string) => {
    const id = Date.now() + Math.random();
    setItems((x) => [...x, { id, tone, text }]);
    setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), tone === "danger" ? 8000 : 4000);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div aria-live="polite" className="fixed bottom-4 right-4 z-50 flex w-80 flex-col gap-2">
        {items.map((t) => (
          <div key={t.id} className={cx("rounded-lg border px-3 py-2 text-sm shadow-md",
            t.tone === "ok" ? "border-accent-100 bg-accent-50 text-accent-700" : t.tone === "danger" ? "border-rose-200 bg-rose-50 text-rose-800" : "border-ink-200 bg-white")}>
            {t.text}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export const useToast = () => useContext(ToastCtx);
