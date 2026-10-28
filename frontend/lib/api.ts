// Thin API client. Auth is an HttpOnly session cookie; mutations carry a CSRF header.

const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown, isForm = false): Promise<T> {
  const headers: Record<string, string> = { "X-Requested-With": "jobhunter" };
  if (body !== undefined && !isForm) headers["Content-Type"] = "application/json";
  const res = await fetch(`${BASE}${path}`, {
    method,
    credentials: "include",
    headers,
    body: body === undefined ? undefined : isForm ? (body as FormData) : JSON.stringify(body),
  });
  if (res.status === 401 && typeof window !== "undefined" && !path.startsWith("/api/auth")) {
    // Outside React: a full reload to the login page is intended when the session expires.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.href = "/login/";
    throw new ApiError(401, "Not authenticated");
  }
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const data = await res.json();
      msg = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail ?? data);
    } catch {
      /* non-JSON error */
    }
    throw new ApiError(res.status, msg);
  }
  return (await res.json()) as T;
}

export const api = {
  get: <T,>(p: string) => request<T>("GET", p),
  post: <T,>(p: string, b?: unknown) => request<T>("POST", p, b ?? {}),
  put: <T,>(p: string, b: unknown) => request<T>("PUT", p, b),
  patch: <T,>(p: string, b: unknown) => request<T>("PATCH", p, b),
  del: <T,>(p: string) => request<T>("DELETE", p),
  upload: <T,>(p: string, form: FormData) => request<T>("POST", p, form, true),
  url: (p: string) => `${BASE}${p}`,
};

export function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "" && v !== false) u.set(k, String(v));
  }
  const s = u.toString();
  return s ? `?${s}` : "";
}
