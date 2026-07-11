import { useCallback, useEffect, useRef, useState } from "react";
import { useApp } from "../app/AppContext";

const POLL_MS = 30_000;

/** Fetch + refresh-on-focus + gentle polling while the tab is visible.
 * The Activity stays useful while Discord play continues without turning E6
 * into a realtime rewrite. */
export function useApi<T>(path: string | null) {
  const { api } = useApp();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(!!path);
  const [refreshing, setRefreshing] = useState(false);
  const alive = useRef(true);

  const load = useCallback(async (isRefresh = false) => {
    if (!path) return;
    if (isRefresh) setRefreshing(true);
    try {
      const result = await api.get<T>(path);
      if (!alive.current) return;
      setData(result);
      setError(null);
    } catch (e) {
      if (!alive.current) return;
      setError(e instanceof Error ? e.message : "unknown error");
    } finally {
      if (alive.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, [api, path]);

  useEffect(() => {
    alive.current = true;
    setLoading(!!path);
    setData(null);
    void load();

    const onFocus = () => void load(true);
    window.addEventListener("focus", onFocus);
    const interval = window.setInterval(() => {
      if (document.visibilityState === "visible") void load(true);
    }, POLL_MS);

    return () => {
      alive.current = false;
      window.removeEventListener("focus", onFocus);
      window.clearInterval(interval);
    };
  }, [load, path]);

  return { data, error, loading, refreshing, refresh: () => load(true) };
}
