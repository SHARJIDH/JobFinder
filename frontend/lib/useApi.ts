"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "./api";

interface State<T> { key: string | null; data: T | null; error: string | null }

/** Minimal data hook: fetches when `path` changes; `reload()` refetches (awaitable). Keeps previous data while loading. */
export function useApi<T>(path: string | null) {
  const [state, setState] = useState<State<T>>({ key: null, data: null, error: null });

  useEffect(() => {
    if (!path) return;
    let cancelled = false;
    api.get<T>(path).then(
      (data) => { if (!cancelled) setState({ key: path, data, error: null }); },
      (e) => { if (!cancelled) setState((s) => ({ key: path, data: s.data, error: e instanceof Error ? e.message : String(e) })); },
    );
    return () => { cancelled = true; };
  }, [path]);

  const reload = useCallback(async () => {
    if (!path) return;
    try {
      const data = await api.get<T>(path);
      setState({ key: path, data, error: null });
    } catch (e) {
      setState((s) => ({ ...s, error: e instanceof Error ? e.message : String(e) }));
    }
  }, [path]);

  const setData = useCallback((data: T) => setState((s) => ({ ...s, data })), []);

  return { data: state.data, error: state.error, loading: !!path && state.key !== path, reload, setData };
}
