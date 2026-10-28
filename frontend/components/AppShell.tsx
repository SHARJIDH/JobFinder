"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Activity, Briefcase, ClipboardCheck, LayoutDashboard, LogOut, Settings, UserRound } from "lucide-react";
import { api } from "@/lib/api";
import { cx } from "@/lib/format";
import { ToastProvider } from "./ui";

const NAV = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/jobs/", label: "Jobs", icon: Briefcase },
  { href: "/applications/", label: "Applications", icon: ClipboardCheck },
  { href: "/profile/", label: "Profile & resume", icon: UserRound },
  { href: "/settings/", label: "Search settings", icon: Settings },
  { href: "/activity/", label: "Activity & costs", icon: Activity },
];

export default function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const isLogin = pathname?.startsWith("/login");
  const [authed, setAuthed] = useState<boolean | null>(isLogin ? true : null);

  useEffect(() => {
    if (isLogin) return;
    api.get<{ authenticated: boolean }>("/api/auth/me")
      .then((r) => (r.authenticated ? setAuthed(true) : router.replace("/login/")))
      .catch(() => router.replace("/login/"));
  }, [isLogin, router]);

  if (isLogin) return <ToastProvider>{children}</ToastProvider>;
  if (!authed) return <div className="p-8 text-sm text-ink-500" role="status">Checking session…</div>;

  const active = (href: string) => (href === "/" ? pathname === "/" : pathname?.startsWith(href.replace(/\/$/, "")));

  return (
    <ToastProvider>
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-white focus:px-3 focus:py-2">
        Skip to content
      </a>
      <div className="flex min-h-screen">
        <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r border-ink-200 bg-white md:flex">
          <div className="px-5 py-5">
            <p className="text-base font-semibold tracking-tight">AI Job Hunter</p>
            <p className="text-xs text-ink-500">Private · review-first</p>
          </div>
          <nav aria-label="Main" className="flex flex-1 flex-col gap-0.5 px-3">
            {NAV.map(({ href, label, icon: Icon }) => (
              <Link key={href} href={href} aria-current={active(href) ? "page" : undefined}
                    className={cx("flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm",
                      active(href) ? "bg-accent-50 font-medium text-accent-700" : "text-ink-700 hover:bg-ink-100")}>
                <Icon className="size-4" aria-hidden /> {label}
              </Link>
            ))}
          </nav>
          <div className="border-t border-ink-100 p-3">
            <button onClick={async () => { await api.post("/api/auth/logout"); router.replace("/login/"); }}
                    className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm text-ink-500 hover:bg-ink-100">
              <LogOut className="size-4" aria-hidden /> Sign out
            </button>
          </div>
        </aside>
        <div className="flex min-w-0 flex-1 flex-col">
          <nav aria-label="Mobile" className="flex gap-1 overflow-x-auto border-b border-ink-200 bg-white px-3 py-2 md:hidden">
            {NAV.map(({ href, label }) => (
              <Link key={href} href={href} className={cx("whitespace-nowrap rounded-md px-2.5 py-1.5 text-xs",
                active(href) ? "bg-accent-50 font-medium text-accent-700" : "text-ink-700")}>{label}</Link>
            ))}
          </nav>
          <main id="main" className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 md:px-8">{children}</main>
        </div>
      </div>
    </ToastProvider>
  );
}
