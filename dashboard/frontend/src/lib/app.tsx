import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { AlertTriangle, CheckCircle2 } from "lucide-react";
import { api, ApiError, type Settings, type Status } from "../api";
import { Button, Modal } from "../components/ui";

/* ------------------------------------------------------------------ */
/* Hash router: #/library/12, #/chats, ...                              */
/* ------------------------------------------------------------------ */
export function useRoute(): string[] {
  const read = () => (window.location.hash.replace(/^#\/?/, "") || "home").split("/").filter(Boolean);
  const [parts, setParts] = useState(read);
  useEffect(() => {
    const on = () => setParts(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return parts;
}

export function go(path: string) {
  const target = `#/${path.replace(/^\/+/, "")}`;
  if (window.location.hash !== target) window.location.hash = target;
}

/* ------------------------------------------------------------------ */
/* Toasts                                                               */
/* ------------------------------------------------------------------ */
interface Toast { id: number; text: string; bad?: boolean; action?: { label: string; run: () => void } }
interface ToastApi {
  ok: (text: string, action?: Toast["action"]) => void;
  error: (err: unknown) => void;
}
const ToastCtx = createContext<ToastApi>({ ok: () => undefined, error: () => undefined });
export const useToast = () => useContext(ToastCtx);

export function errorText(err: unknown): string {
  if (err instanceof ApiError || err instanceof Error) return err.message;
  return String(err);
}

function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const seq = useRef(0);
  const push = useCallback((t: Omit<Toast, "id">) => {
    const id = ++seq.current;
    setToasts((all) => [...all.slice(-3), { ...t, id }]);
    setTimeout(() => setToasts((all) => all.filter((x) => x.id !== id)), t.action ? 7000 : t.bad ? 6000 : 3500);
  }, []);
  const value = useMemo<ToastApi>(
    () => ({
      ok: (text, action) => push({ text, action }),
      error: (err) => push({ text: errorText(err), bad: true }),
    }),
    [push],
  );
  return (
    <ToastCtx.Provider value={value}>
      {children}
      <div className="toasts" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.bad ? "toast-bad" : ""}`}>
            {t.bad ? <AlertTriangle size={17} /> : <CheckCircle2 size={17} />}
            <span>{t.text}</span>
            {t.action && (
              <button
                className="toast-action"
                onClick={() => {
                  t.action!.run();
                  setToasts((all) => all.filter((x) => x.id !== t.id));
                }}
              >
                {t.action.label}
              </button>
            )}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

/* ------------------------------------------------------------------ */
/* Confirm / prompt dialogs as promises                                 */
/* ------------------------------------------------------------------ */
interface ConfirmOpts { title: string; desc?: ReactNode; ok?: string; danger?: boolean; cancel?: string }
interface PromptOpts { title: string; desc?: ReactNode; ok?: string; value?: string; placeholder?: string; selectStem?: boolean }
interface DialogApi {
  confirm: (o: ConfirmOpts) => Promise<boolean>;
  prompt: (o: PromptOpts) => Promise<string | null>;
}
const DialogCtx = createContext<DialogApi>({ confirm: async () => false, prompt: async () => null });
export const useDialog = () => useContext(DialogCtx);

function DialogProvider({ children }: { children: ReactNode }) {
  const [confirmState, setConfirm] = useState<(ConfirmOpts & { resolve: (v: boolean) => void }) | null>(null);
  const [promptState, setPrompt] = useState<(PromptOpts & { resolve: (v: string | null) => void }) | null>(null);
  const [text, setText] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const value = useMemo<DialogApi>(
    () => ({
      confirm: (o) => new Promise((resolve) => setConfirm({ ...o, resolve })),
      prompt: (o) =>
        new Promise((resolve) => {
          setText(o.value ?? "");
          setPrompt({ ...o, resolve });
        }),
    }),
    [],
  );

  useEffect(() => {
    if (promptState && inputRef.current) {
      const el = inputRef.current;
      el.focus();
      const v = el.value;
      const dot = v.lastIndexOf(".");
      el.setSelectionRange(0, promptState.selectStem && dot > 0 ? dot : v.length);
    }
  }, [promptState]);

  const closeConfirm = (v: boolean) => {
    confirmState?.resolve(v);
    setConfirm(null);
  };
  const closePrompt = (v: string | null) => {
    promptState?.resolve(v);
    setPrompt(null);
  };

  return (
    <DialogCtx.Provider value={value}>
      {children}
      {confirmState && (
        <Modal
          title={confirmState.title}
          desc={confirmState.desc}
          onClose={() => closeConfirm(false)}
          footer={
            <>
              <Button onClick={() => closeConfirm(false)}>{confirmState.cancel ?? "Cancel"}</Button>
              <Button variant={confirmState.danger ? "danger-solid" : "primary"} onClick={() => closeConfirm(true)} autoFocus>
                {confirmState.ok ?? "OK"}
              </Button>
            </>
          }
        />
      )}
      {promptState && (
        <Modal
          title={promptState.title}
          desc={promptState.desc}
          onClose={() => closePrompt(null)}
          footer={
            <>
              <Button onClick={() => closePrompt(null)}>Cancel</Button>
              <Button variant="primary" disabled={!text.trim()} onClick={() => closePrompt(text.trim())}>
                {promptState.ok ?? "OK"}
              </Button>
            </>
          }
        >
          <input
            ref={inputRef}
            className="input"
            value={text}
            placeholder={promptState.placeholder}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && text.trim() && closePrompt(text.trim())}
          />
        </Modal>
      )}
    </DialogCtx.Provider>
  );
}

/* ------------------------------------------------------------------ */
/* Live status (polled) + settings                                      */
/* ------------------------------------------------------------------ */
interface AppState {
  status: Status | null;
  refreshStatus: () => Promise<void>;
  settings: Settings | null;
  setSettings: (s: Settings) => void;
  saveSettings: (changes: Partial<Settings>) => Promise<Settings | null>;
  bump: number; // increments whenever a background task finishes, so pages can reload
}
const AppCtx = createContext<AppState>({} as AppState);
export const useApp = () => useContext(AppCtx);

function StatusProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [bump, setBump] = useState(0);
  const activeIds = useRef<string>("");
  const toast = useToast();

  const refreshStatus = useCallback(async () => {
    try {
      const s = await api.get<Status>("/status");
      const ids = s.active_jobs.map((j) => j.id).join(",");
      if (ids !== activeIds.current) {
        if (activeIds.current) setBump((b) => b + 1);
        activeIds.current = ids;
      }
      setStatus(s);
    } catch {
      /* keep the last known status */
    }
  }, []);

  useEffect(() => {
    api.get<{ settings: Settings }>("/settings").then((r) => setSettings(r.settings)).catch(() => undefined);
    refreshStatus();
  }, [refreshStatus]);

  useEffect(() => {
    const busy = (status?.active_jobs.length ?? 0) > 0 || status?.instagram.state === "connecting";
    const t = setTimeout(refreshStatus, busy ? 1500 : 5000);
    return () => clearTimeout(t);
  }, [status, refreshStatus]);

  const saveSettings = useCallback(
    async (changes: Partial<Settings>) => {
      try {
        const r = await api.put<{ settings: Settings }>("/settings", changes);
        setSettings(r.settings);
        return r.settings;
      } catch (e) {
        toast.error(e);
        return null;
      }
    },
    [toast],
  );

  // Theme
  useEffect(() => {
    const pref = settings?.theme ?? "system";
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const apply = () => {
      const dark = pref === "dark" || (pref === "system" && mq.matches);
      document.documentElement.dataset.theme = dark ? "dark" : "light";
    };
    apply();
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, [settings?.theme]);

  return <AppCtx.Provider value={{ status, refreshStatus, settings, setSettings, saveSettings, bump }}>{children}</AppCtx.Provider>;
}

export function Providers({ children }: { children: ReactNode }) {
  return (
    <ToastProvider>
      <DialogProvider>{children}</DialogProvider>
    </ToastProvider>
  );
}

export function SignedInProviders({ children }: { children: ReactNode }) {
  return <StatusProvider>{children}</StatusProvider>;
}

/** Start a background task and give feedback. */
export function useJobStarter() {
  const toast = useToast();
  const { refreshStatus } = useApp();
  return useCallback(
    async (fn: () => Promise<{ job_id?: number }>, message: string) => {
      try {
        await fn();
        toast.ok(message);
        await refreshStatus();
        return true;
      } catch (e) {
        toast.error(e);
        return false;
      }
    },
    [toast, refreshStatus],
  );
}
