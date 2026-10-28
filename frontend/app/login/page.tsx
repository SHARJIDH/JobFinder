"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Lock } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { Button, Callout, Field, Input } from "@/components/ui";

export default function LoginPage() {
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      await api.post("/api/auth/login", { password });
      router.replace("/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <form onSubmit={submit} className="w-full max-w-sm rounded-xl border border-ink-200 bg-white p-6 shadow-sm">
        <div className="mb-5 flex items-center gap-2">
          <span className="flex size-9 items-center justify-center rounded-lg bg-accent-50 text-accent-700"><Lock className="size-4" aria-hidden /></span>
          <div>
            <h1 className="text-base font-semibold">AI Job Hunter</h1>
            <p className="text-xs text-ink-500">Private dashboard — owner access only</p>
          </div>
        </div>
        <Field label="Password" htmlFor="pw">
          <Input id="pw" type="password" autoComplete="current-password" required value={password}
                 onChange={(e) => setPassword(e.target.value)} autoFocus />
        </Field>
        {error && <div className="mt-3"><Callout tone="danger">{error}</Callout></div>}
        <Button type="submit" variant="primary" loading={loading} className="mt-4 w-full">Sign in</Button>
      </form>
    </main>
  );
}
