import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, ApiFailure, post, useLive, useLiveEvents } from "../api";
import { BusinessInfo, KickoffView, SessionStatus } from "./types";

const TABLES = ["kickoff_sessions", "kickoff_turns"];

/**
 * One kickoff session, kept fresh by the console's event stream. An owner action returns the new view, which
 * replaces the state at once; a slower refetch that started before it must not overwrite it.
 */
export function useKickoff(id: number) {
  const [view, setView] = useState<KickoffView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [failure, setFailure] = useState<ApiFailure | null>(null);
  const seq = useRef(0);
  const timer = useRef<number | undefined>(undefined);
  const businessId = useRef<number | undefined>(undefined);
  const reading = useRef(false);

  const load = useCallback(() => {
    const mine = ++seq.current;
    api<KickoffView>(`/kickoff/${id}`)
      .then((d) => {
        if (mine === seq.current) {
          businessId.current = d.session.business_id;
          reading.current = d.product?.report_state === "reading";
          setView(d);
          setError(null);
          setFailure(null);
        }
      })
      .catch((e) => {
        if (mine !== seq.current) return;
        setError(String(e.message ?? e));
        setFailure(e instanceof ApiError ? e.kind : null);
      });
  }, [id]);

  useEffect(() => {
    setView(null);
    load();
    return () => window.clearTimeout(timer.current);
  }, [load]);

  useLiveEvents((e) => {
    // the product's report is written onto the business row, not onto the session; while it is being read, a job
    // that was held (the sandbox is off) is the only change there is, and the view says why it waits
    const mine = e.table === "*" || (e.table === "kickoff_sessions" && e.id === id) || (e.table === "kickoff_turns" && e.session_id === id)
      || (e.table === "businesses" && e.id === businessId.current) || (e.table === "jobs" && reading.current);
    if (!mine) return;
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(load, 400);
  });

  const act = useCallback(
    async (path: string, body: unknown = {}) => {
      const next = await post<KickoffView>(`/kickoff/${id}${path}`, body);
      seq.current++;
      businessId.current = next.session.business_id;
      reading.current = next.product?.report_state === "reading";
      setView(next);
      return next;
    },
    [id],
  );

  return { view, error, failure, reload: load, act };
}

const BUSINESSES_WITH_ARCHIVED = "/businesses?include_archived=true&include_test=true";

/** The business a session belongs to. The kickoff view does not carry its name or one-liner. */
export function useBusiness(businessId: number | undefined) {
  const { data } = useLive<BusinessInfo[]>(businessId === undefined ? null : BUSINESSES_WITH_ARCHIVED, ["businesses", "projects"]);
  return useMemo(() => data?.find((b) => b.id === businessId) ?? null, [data, businessId]);
}
