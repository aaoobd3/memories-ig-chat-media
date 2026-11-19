import { useEffect, useRef, useState } from "react";
import { Activity, Eye, Square } from "lucide-react";
import { api, type Job, type LogLine } from "../api";
import { ago, dateTime, timeOnly, TYPE_LABEL } from "../lib/format";
import { go, useApp, useToast } from "../lib/app";
import { Button, Empty, Progress, Spinner } from "../components/ui";
import { JobStatusIcon } from "./Home";

const STATUS_TEXT: Record<Job["status"], string> = {
  queued: "Waiting", running: "Working", done: "Finished", failed: "Didn't finish", cancelled: "Stopped", interrupted: "Interrupted (app restarted)",
};

export default function ActivityPage({ jobId }: { jobId?: number }) {
  const { bump, status } = useApp();
  const toast = useToast();
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [logs, setLogs] = useState<LogLine[]>([]);
  const logRef = useRef<HTMLDivElement>(null);
  const afterRef = useRef(0);
  const activeKey = status?.active_jobs.map((j) => `${j.id}:${j.progress}`).join(",");

  useEffect(() => {
    api.get<{ jobs: Job[] }>("/jobs?limit=60").then((r) => setJobs(r.jobs)).catch(toast.error);
  }, [bump, activeKey, toast]);

  const selectedId = jobId ?? jobs?.[0]?.id;

  useEffect(() => {
    setLogs([]);
    afterRef.current = 0;
    setJob(null);
  }, [selectedId]);

  useEffect(() => {
    if (!selectedId) return;
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        const [j, l] = await Promise.all([
          api.get<Job>(`/jobs/${selectedId}`),
          api.get<{ logs: LogLine[] }>(`/jobs/${selectedId}/logs?after=${afterRef.current}`),
        ]);
        if (stop) return;
        setJob(j);
        if (l.logs.length) {
          afterRef.current = l.logs[l.logs.length - 1].id;
          setLogs((prev) => [...prev, ...l.logs]);
        }
        if (j.status === "running" || j.status === "queued" || l.logs.length === 500) timer = setTimeout(tick, 1200);
      } catch {
        /* ignore */
      }
    };
    tick();
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, [selectedId]);

  useEffect(() => {
    const el = logRef.current;
    if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 120) el.scrollTop = el.scrollHeight;
  }, [logs]);

  const cancel = async () => {
    if (!job) return;
    try {
      await api.post(`/jobs/${job.id}/cancel`);
      toast.ok("Stopping...");
    } catch (e) {
      toast.error(e);
    }
  };

  const preview = (job?.stats?.preview ?? []) as { chat: string; when: string; sender: string; kind: string; link: string | null }[];
  const statEntries = job ? Object.entries(job.stats).filter(([k, v]) => typeof v === "number" && v > 0 && k !== "chats") : [];
  const statLabel: Record<string, string> = { found: "found", new: "new items", files: "files saved", skipped: "already had", failed: "failed", listed: "only listed", purged: "emptied from trash", added_files: "files added", removed_files: "missing files forgotten", added_folders: "folders added", removed_folders: "missing folders forgotten", folders: "folders" };

  return (
    <div className="page page-wide">
      <div className="page-head">
        <div>
          <h1>Activity</h1>
          <p>Everything the app did, step by step. Tasks run one at a time to keep your Instagram account safe.</p>
        </div>
      </div>

      {jobs === null ? (
        <div className="empty"><Spinner size={28} /></div>
      ) : !jobs.length ? (
        <div className="card"><Empty icon={Activity} title="Nothing yet">When you connect, sync or import something, the steps show up here.</Empty></div>
      ) : (
        <div className="jobs-layout">
          <div className="card" style={{ padding: 8, maxHeight: "75vh", overflow: "auto" }}>
            {jobs.map((j) => (
              <div key={j.id} className={`job-item ${j.id === selectedId ? "active" : ""}`} onClick={() => go(`activity/${j.id}`)}>
                <span className="job-icon" style={{ background: "var(--bg-sunk)" }}><JobStatusIcon job={j} /></span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="strong ellipsis">{j.title}</div>
                  <div className="small muted">{STATUS_TEXT[j.status]} · {ago(j.finished_at ?? j.started_at ?? j.created_at)}</div>
                </div>
                {j.stats?.files ? <span className="badge badge-ok">+{j.stats.files}</span> : null}
              </div>
            ))}
          </div>

          <div className="card card-pad stack">
            {!job ? (
              <Spinner />
            ) : (
              <>
                <div className="row" style={{ alignItems: "flex-start" }}>
                  <JobStatusIcon job={job} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <h2>{job.title}</h2>
                    <div className="small muted" style={{ marginTop: 4 }}>
                      {STATUS_TEXT[job.status]} · started {dateTime(job.started_at ?? job.created_at)}
                      {job.finished_at && ` · finished ${dateTime(job.finished_at)}`}
                    </div>
                  </div>
                  {(job.status === "running" || job.status === "queued") && <Button variant="danger" icon={Square} onClick={cancel}>Stop</Button>}
                </div>
                {(job.status === "running" || job.status === "queued") && <Progress value={job.progress} total={job.total} indeterminate={!job.total} />}
                {job.error && <div className="callout callout-bad">{job.error}</div>}
                {statEntries.length > 0 && (
                  <div className="row-wrap">
                    {statEntries.map(([k, v]) => <span key={k} className={`badge ${k === "failed" ? "badge-bad" : k === "files" || k === "new" ? "badge-ok" : ""}`}>{v as number} {statLabel[k] ?? k}</span>)}
                  </div>
                )}
                {preview.length > 0 && (
                  <div className="stack-sm">
                    <div className="row"><Eye size={17} /><h3>Would be downloaded ({preview.length})</h3></div>
                    <div style={{ maxHeight: 280, overflow: "auto" }}>
                      <table className="list">
                        <thead><tr><th>When</th><th>From</th><th>Type</th><th>Chat</th><th>Link</th></tr></thead>
                        <tbody>
                          {preview.map((p, i) => (
                            <tr key={i} style={{ cursor: "default" }}>
                              <td className="muted" style={{ whiteSpace: "nowrap" }}>{dateTime(p.when)}</td>
                              <td>{p.sender}</td>
                              <td>{TYPE_LABEL[p.kind as "photo"] ?? p.kind}</td>
                              <td className="muted ellipsis" style={{ maxWidth: 180 }}>{p.chat}</td>
                              <td>{p.link?.startsWith("http") ? <a href={p.link} target="_blank" rel="noreferrer noopener">Open</a> : ""}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
                <div className="log" ref={logRef}>
                  {logs.length ? logs.map((l) => (
                    <div key={l.id} className={`log-line log-${l.level}`}>
                      <span className="t">{timeOnly(l.ts)}</span>
                      <span>{l.message}</span>
                    </div>
                  )) : <span className="faint">{job.status === "queued" ? "Waiting for the previous task to finish..." : "No messages."}</span>}
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
