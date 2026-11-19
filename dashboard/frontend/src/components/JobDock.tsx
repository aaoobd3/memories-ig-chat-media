import { useEffect, useRef, useState } from "react";
import { CheckCircle2, Loader2, Square, XCircle } from "lucide-react";
import { api, type Job, type LogLine } from "../api";
import { go, useApp, useToast } from "../lib/app";
import { Button, Progress } from "./ui";

/** Floating card that shows what the app is doing right now. */
export function JobDock() {
  const { status } = useApp();
  const toast = useToast();
  const active = status?.active_jobs.find((j) => j.status === "running") ?? status?.active_jobs[0] ?? null;
  const [line, setLine] = useState<string>("");
  const [finished, setFinished] = useState<Job | null>(null);
  const lastId = useRef<number | null>(null);
  const afterId = useRef(0);

  // Follow the log of the active job.
  useEffect(() => {
    if (!active) return;
    if (lastId.current !== active.id) {
      lastId.current = active.id;
      afterId.current = 0;
      setLine("");
      setFinished(null);
    }
    let stop = false;
    const tick = async () => {
      try {
        const r = await api.get<{ logs: LogLine[] }>(`/jobs/${active.id}/logs?after=${afterId.current}`);
        if (r.logs.length) {
          afterId.current = r.logs[r.logs.length - 1].id;
          if (!stop) setLine(r.logs[r.logs.length - 1].message);
        }
      } catch {
        /* ignore */
      }
    };
    tick();
    const t = setInterval(tick, 1500);
    return () => {
      stop = true;
      clearInterval(t);
    };
  }, [active?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  // When the job finishes, show the result for a few seconds.
  useEffect(() => {
    if (active || lastId.current === null) return;
    const id = lastId.current;
    lastId.current = null;
    api.get<Job>(`/jobs/${id}`).then((j) => {
      setFinished(j);
      setTimeout(() => setFinished((f) => (f?.id === j.id ? null : f)), 7000);
    }).catch(() => undefined);
  }, [active]);

  const cancel = async () => {
    if (!active) return;
    try {
      await api.post(`/jobs/${active.id}/cancel`);
      toast.ok("Stopping...");
    } catch (e) {
      toast.error(e);
    }
  };

  if (active) {
    const queued = (status?.active_jobs.length ?? 1) - 1;
    return (
      <div className="card dock">
        <div className="row">
          <Loader2 size={18} className="spin" style={{ color: "var(--accent)", animation: "spin 1s linear infinite" }} />
          <div className="dock-title ellipsis" style={{ flex: 1 }}>{active.status === "queued" ? `Waiting: ${active.title}` : active.title}</div>
          <Button size="sm" variant="ghost" icon={Square} onClick={cancel}>Stop</Button>
        </div>
        <Progress value={active.progress} total={active.total} indeterminate={!active.total} />
        <div className="dock-line ellipsis" title={line}>{line || "Starting..."}</div>
        <div className="row tiny faint">
          {active.stats?.files ? <span>{active.stats.files} files saved so far</span> : null}
          {queued > 0 && <span>{queued} more waiting</span>}
          <span className="spacer" />
          <a href={`#/activity/${active.id}`}>Details</a>
        </div>
      </div>
    );
  }

  if (finished) {
    const ok = finished.status === "done";
    const files = finished.stats?.files;
    return (
      <div className="card dock" onClick={() => go(`activity/${finished.id}`)} style={{ cursor: "pointer" }}>
        <div className="row">
          {ok ? <CheckCircle2 size={20} style={{ color: "var(--ok)" }} /> : <XCircle size={20} style={{ color: "var(--bad)" }} />}
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="dock-title ellipsis">{finished.title}</div>
            <div className="dock-line">
              {ok
                ? files ? `Finished: ${files} new files saved.` : "Finished."
                : finished.status === "cancelled" ? "Stopped." : finished.error ?? "Didn't finish."}
            </div>
          </div>
        </div>
      </div>
    );
  }
  return null;
}
