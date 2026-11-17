import { ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api";
import { lookup, t } from "../i18n";

const SAVED_MS = 4000;

/** What the server says a save would open up, asked with `?dry_run=true` before the real save (P0-2). */
export interface SettingsWarning {
  kind: "sends_without_approval" | "cannot_send";
  action?: string;
  connector: string;
  text: string;
}

interface PutResult {
  warnings?: SettingsWarning[];
  /** The connectors this save turns live that could not send, each saying what is missing. */
  cannot_send?: SettingsWarning[];
}

export interface Saver {
  busy: boolean;
  error: string | null;
  saved: boolean;
  save: (body: object) => Promise<boolean>;
  /** The warnings this body would raise, without saving; null when asking failed (the error is set). */
  check: (body: object) => Promise<SettingsWarning[] | null>;
  clearError: () => void;
}

/**
 * The owner reads a failed save in owner words: a lost connection or a server error says to try again, and a refusal
 * that starts with a field key (`console_url: ...`) names the field as the screen labels it.
 */
function saveError(e: unknown): string {
  if (e instanceof ApiError && (e.kind === "offline" || e.kind === "server")) return t("settings.save_failed.offline");
  const text = (e as Error)?.message ?? String(e);
  const m = /^([a-z_]+(?:\.[a-z_]+)*):\s*(.+)$/s.exec(text);
  const field = m && lookup(`settings.field.${m[1]}`);
  return field ? `${field}: ${m![2]}` : text;
}

/** Saves one settings section. The server's refusal is kept as the sentence it sent, and "saved" fades on its own. */
export function useSave(section: string, reload: () => void): Saver {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const save = useCallback(
    async (body: object) => {
      setBusy(true);
      setError(null);
      setSaved(false);
      try {
        await api(`/settings/${section}`, { method: "PUT", body: JSON.stringify(body) });
        reload();
        setSaved(true);
        window.clearTimeout(timer.current);
        timer.current = window.setTimeout(() => setSaved(false), SAVED_MS);
        return true;
      } catch (e) {
        setError(saveError(e));
        return false;
      } finally {
        setBusy(false);
      }
    },
    [section, reload],
  );
  const check = useCallback(
    async (body: object) => {
      setBusy(true);
      setError(null);
      try {
        const r = await api<PutResult>(`/settings/${section}?dry_run=true`, { method: "PUT", body: JSON.stringify(body) });
        return [...(r.cannot_send ?? []), ...(r.warnings ?? [])];
      } catch (e) {
        setError(saveError(e));
        return null;
      } finally {
        setBusy(false);
      }
    },
    [section],
  );
  return { busy, error, saved, save, check, clearError: () => setError(null) };
}

/** A save that asks first: what the form knows it opens up (`lines`) and what the server's dry run warns about. */
export interface Guard {
  pending: { lines: ReactNode[]; body: object } | null;
  run: (body: object, opts?: { lines?: ReactNode[]; check?: boolean }) => Promise<void>;
  confirm: () => Promise<void>;
  cancel: () => void;
}

/**
 * Nothing is confirmed for the owner: with anything to say, the save waits on a confirm box until the owner presses its
 * button; with nothing to say it saves straight away. Lowering a setting never asks.
 */
export function useGuard(saver: Saver): Guard {
  const [pending, setPending] = useState<Guard["pending"]>(null);
  const run = useCallback(async (body: object, opts: { lines?: ReactNode[]; check?: boolean } = {}) => {
    let warned: string[] = [];
    if (opts.check) {
      const w = await saver.check(body);
      if (w === null) return;
      warned = w.map((x) => x.text);
    }
    const lines = [...(opts.lines ?? []), ...warned];
    if (lines.length) setPending({ lines, body });
    else await saver.save(body);
  }, [saver]);
  const confirm = useCallback(async () => {
    if (pending && (await saver.save(pending.body))) setPending(null);
  }, [pending, saver]);
  const cancel = useCallback(() => {
    setPending(null);
    saver.clearError();
  }, [saver]);
  return { pending, run, confirm, cancel };
}
