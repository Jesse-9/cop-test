import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, type HealthResponse } from "../api/client";

export type HealthState = "checking" | "online" | "offline";

export interface HealthSnapshot {
  state: HealthState;
  health: HealthResponse | null;
  error: string;
  refresh: () => void;
}

export function useHealth(pollMs = 15_000): HealthSnapshot {
  const [state, setState] = useState<HealthState>("checking");
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [error, setError] = useState("");
  const mounted = useRef(true);
  const timer = useRef<number | null>(null);

  const load = useCallback(async () => {
    try {
      const payload = await api.health();
      if (!mounted.current) return;
      setHealth(payload);
      setError("");
      setState("online");
    } catch (exc) {
      if (!mounted.current) return;
      setState("offline");
      setHealth(null);
      setError(exc instanceof ApiError ? exc.message : String(exc));
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    void load();
    const tick = () => {
      void load();
      timer.current = window.setTimeout(tick, pollMs);
    };
    timer.current = window.setTimeout(tick, pollMs);
    return () => {
      mounted.current = false;
      if (timer.current !== null) window.clearTimeout(timer.current);
    };
  }, [load, pollMs]);

  const refresh = useCallback(() => {
    setState("checking");
    void load();
  }, [load]);

  return { state, health, error, refresh };
}
