import { useEffect, useMemo, useState } from "react";
import { ArrowRight, CalendarHeart, CheckCircle2, Clock, Copy, HardDrive, Loader2, MessagesSquare, PauseCircle, PlugZap, Timer, TriangleAlert, XCircle, Zap } from "lucide-react";
import { api, thumbUrl, type FileItem, type Job, type MediaType } from "../api";
import { ago, bytes, inTime, num, plural, TYPE_COLOR, TYPE_LABEL_PLURAL } from "../lib/format";
import { go, useApp, useJobStarter } from "../lib/app";
import { Button, Spinner, TYPE_ICON, TypeTag } from "../components/ui";
import { Lightbox } from "../components/Lightbox";

interface Stats {
  by_chat: { id: number; title: string; files: number; bytes: number }[];
  senders: { sender: string; files: number }[];
  months: { month: string; files: number; photo: number; video: number; post: number; reel: number }[];
  duplicates: { groups: number; extra_files: number; bytes: number };
  recent: FileItem[];
  on_this_day: FileItem[];
}

function greeting() {
  const h = new Date().getHours();
  return h < 5 ? "Good night" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

export default function HomePage() {
  const { status, bump } = useApp();
  const start = useJobStarter();
  const [stats, setStats] = useState<Stats | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [lb, setLb] = useState<{ list: FileItem[]; i: number } | null>(null);

  useEffect(() => {
    api.get<Stats>("/stats").then(setStats).catch(() => undefined);
    api.get<{ jobs: Job[] }>("/jobs?limit=6").then((r) => setJobs(r.jobs)).catch(() => undefined);
  }, [bump]);

  const ig = status?.instagram;
  const connected = ig?.state === "connected" || (ig?.state === "disconnected" && ig.session_saved);
  const syncing = status?.active_jobs.some((j) => j.kind === "sync");
  const paused = ig?.paused_until && new Date(ig.paused_until) > new Date();
  const steps = [
    { done: connected || ig?.state === "connected", title: "Connect your Instagram account", desc: "So the app can read your group chats.", to: "account" },
    { done: (status?.chats.selected ?? 0) > 0, title: "Choose the chats to save", desc: "Switch on one or more group chats.", to: "chats" },
    { done: (status?.library.files ?? 0) > 0, title: "Run your first sync", desc: "Photos, videos, posts and reels land in your library.", to: "chats" },
  ];
  const allDone = steps.every((s) => s.done);
  const maxMonth = useMemo(() => Math.max(1, ...(stats?.months.map((m) => m.files) ?? [1])), [stats]);
  const maxChat = Math.max(1, ...(stats?.by_chat.map((c) => c.files) ?? [1]));
  const maxSender = Math.max(1, ...(stats?.senders.map((s) => s.files) ?? [1]));

  const syncNow = () => start(() => api.post("/sync", { mode: "recent" }), "Checking your chats for new media...");

  return (
    <div className="page">
      <div className="stack" style={{ gap: 22 }}>
        <section className="hero">
          <div className="hero-deco" />
          <h1>{greeting()}{ig?.username ? `, @${ig.username}` : ""}!</h1>
          <p>
            {syncing ? "Checking your chats right now..." : (status?.library.files ?? 0) > 0
              ? `Your library has ${plural(status!.library.files, "file")} from ${plural(status!.chats.selected, "chat")}.`
              : "Let's save the photos, videos, posts and reels from your Instagram group chats."}
          </p>
          <div className="row-wrap" style={{ marginTop: 18 }}>
            <Button size="lg" className="btn-white" icon={syncing ? Loader2 : Zap} disabled={syncing || !status?.chats.selected} onClick={syncNow}>
              {syncing ? "Syncing..." : "Sync now"}
            </Button>
            <Button size="lg" className="btn-glass" icon={MessagesSquare} onClick={() => go("chats")}>Choose chats</Button>
          </div>
          <div className="hero-meta">
            <span><PlugZap size={16} />{ig?.state === "connected" ? `Connected as @${ig.username}` : ig?.session_saved ? "Saved login ready" : "Instagram not connected"}</span>
            <span><Timer size={16} />{paused ? `Paused until ${inTime(ig!.paused_until).replace(/^at /, "")}` : status?.auto_sync && status.chats.auto ? `Next automatic check ${inTime(status.next_auto_run) || "soon"}` : "Automatic checks off"}</span>
            <span><Clock size={16} />Last sync {ago(status?.last_sync?.finished_at)}</span>
          </div>
        </section>

        {status && !status.engine.alive && status.active_jobs.some((j) => j.status === "queued" && Date.now() - new Date(j.created_at).getTime() > 30000) && (
          <div className="callout callout-warn"><TriangleAlert size={18} /><span>Tasks are waiting, but the background worker isn't running right now. It usually restarts by itself within a few seconds. If this stays, close the app and start it again.</span></div>
        )}
        {paused && (
          <div className="callout callout-warn"><PauseCircle size={18} /><span>Instagram asked us to slow down, so checks are paused for a while. This keeps your account safe; nothing to do.</span></div>
        )}
        {ig?.state === "error" && (
          <div className="callout callout-bad" style={{ alignItems: "center" }}>
            <TriangleAlert size={18} />
            <span style={{ flex: 1 }}>{ig.message || "The Instagram connection needs attention."}</span>
            <Button size="sm" onClick={() => go("account")}>Fix it</Button>
          </div>
        )}

        {!allDone && (
          <div className="card card-pad">
            <div className="card-head">
              <div>
                <h2>Getting started</h2>
                <p>Three quick steps and you're set.</p>
              </div>
              <span className="badge badge-accent">{steps.filter((s) => s.done).length} of 3 done</span>
            </div>
            <div className="checklist">
              {steps.map((s, i) => (
                <a key={i} className={`check-item ${s.done ? "done" : ""}`} href={`#/${s.to}`} style={{ color: "inherit", textDecoration: "none" }}>
                  <span className="check-circle">{s.done ? <CheckCircle2 size={18} /> : i + 1}</span>
                  <div style={{ flex: 1 }}>
                    <div className="check-title">{s.title}</div>
                    <div className="small muted">{s.desc}</div>
                  </div>
                  {!s.done && <ArrowRight size={18} className="faint" />}
                </a>
              ))}
            </div>
          </div>
        )}

        <div className="grid-4">
          {(["photo", "video", "post", "reel"] as MediaType[]).map((t) => {
            const Icon = TYPE_ICON[t];
            const n = status?.library[`${t}s` as "photos"] ?? 0;
            return (
              <button key={t} className="card stat" onClick={() => go("library/view/all")}>
                <span className="stat-icon" style={{ background: `color-mix(in srgb, ${TYPE_COLOR[t]} 15%, transparent)`, color: TYPE_COLOR[t] }}><Icon size={20} /></span>
                <span className="num">{num(n)}</span>
                <span className="lbl">{TYPE_LABEL_PLURAL[t]}</span>
              </button>
            );
          })}
        </div>

        {stats && stats.on_this_day.length > 0 && (
          <div className="card card-pad">
            <div className="card-head">
              <div className="row"><CalendarHeart size={20} style={{ color: "var(--accent-2)" }} /><h2>On this day</h2></div>
              <span className="muted small">From previous years</span>
            </div>
            <Strip list={stats.on_this_day} onOpen={(i) => setLb({ list: stats.on_this_day, i })} />
          </div>
        )}

        <div className="card card-pad">
          <div className="card-head">
            <h2>Recently saved</h2>
            <Button size="sm" variant="ghost" onClick={() => go("library/view/recent")}>See all <ArrowRight size={15} /></Button>
          </div>
          {stats === null ? <Spinner /> : stats.recent.length ? <Strip list={stats.recent} onOpen={(i) => setLb({ list: stats.recent, i })} /> : <p className="muted">Nothing yet. Your newest downloads will show up here.</p>}
        </div>

        <div className="grid-2">
          <div className="card card-pad">
            <div className="card-head"><h2>Media per month</h2></div>
            {stats && stats.months.length ? (
              <>
                <div className="months">
                  {stats.months.map((m) => (
                    <div className="month" key={m.month} title={`${m.month}: ${m.files} files`}>
                      <div className="month-stack" style={{ height: `${(m.files / maxMonth) * 100}%` }}>
                        {(["photo", "video", "post", "reel"] as MediaType[]).map((t) => m[t] ? <div key={t} style={{ height: `${(m[t] / m.files) * 100}%`, background: TYPE_COLOR[t] }} /> : null)}
                      </div>
                      <span className="month-lbl">{new Date(`${m.month}-01T00:00:00`).toLocaleDateString(undefined, { month: "short" })}</span>
                    </div>
                  ))}
                </div>
                <div className="legend" style={{ marginTop: 12 }}>
                  {(["photo", "video", "post", "reel"] as MediaType[]).map((t) => <span key={t}><i style={{ background: TYPE_COLOR[t] }} />{TYPE_LABEL_PLURAL[t]}</span>)}
                </div>
              </>
            ) : <p className="muted">A chart appears once you have some media.</p>}
          </div>

          <div className="card card-pad">
            <div className="card-head"><h2>Who shares the most</h2></div>
            {stats && stats.senders.length ? (
              <div className="bars">
                {stats.senders.map((s) => (
                  <div className="bar-row" key={s.sender}>
                    <span className="ellipsis strong">{s.sender}</span>
                    <div className="bar-track"><div className="bar-fill" style={{ width: `${(s.files / maxSender) * 100}%` }} /></div>
                    <span className="muted small">{num(s.files)}</span>
                  </div>
                ))}
              </div>
            ) : <p className="muted">Nobody yet!</p>}
          </div>

          <div className="card card-pad">
            <div className="card-head"><h2>Biggest chats</h2></div>
            {stats && stats.by_chat.length ? (
              <div className="bars">
                {stats.by_chat.map((c) => (
                  <div className="bar-row" key={c.id}>
                    <span className="ellipsis strong">{c.title}</span>
                    <div className="bar-track"><div className="bar-fill" style={{ width: `${(c.files / maxChat) * 100}%` }} /></div>
                    <span className="muted small">{num(c.files)}</span>
                  </div>
                ))}
              </div>
            ) : <p className="muted">Sync a chat to see it here.</p>}
          </div>

          <div className="card card-pad stack-sm">
            <div className="card-head" style={{ marginBottom: 4 }}><h2>Storage</h2></div>
            <div className="row"><HardDrive size={18} className="faint" /><span>Library uses <strong>{bytes(status?.library.bytes)}</strong></span></div>
            <div className="progress"><div style={{ width: `${status ? Math.min(100, ((status.disk.total - status.disk.free) / status.disk.total) * 100) : 0}%` }} /></div>
            <span className="small muted">{bytes(status?.disk.free)} free on this drive</span>
            {stats && stats.duplicates.extra_files > 0 && (
              <div className="callout" style={{ marginTop: 8, alignItems: "center" }}>
                <Copy size={17} />
                <span style={{ flex: 1 }}>{plural(stats.duplicates.extra_files, "duplicate file")} could free {bytes(stats.duplicates.bytes)}.</span>
                <Button size="sm" onClick={() => go("library/view/duplicates")}>Review</Button>
              </div>
            )}
            {!!status?.trash && <span className="small muted">{plural(status.trash, "file")} in the trash. <a href="#/library/view/trash">Open trash</a></span>}
          </div>
        </div>

        <div className="card card-pad">
          <div className="card-head">
            <h2>Latest activity</h2>
            <Button size="sm" variant="ghost" onClick={() => go("activity")}>Everything <ArrowRight size={15} /></Button>
          </div>
          {jobs.length ? (
            <div className="stack-sm">
              {jobs.map((j) => (
                <a key={j.id} href={`#/activity/${j.id}`} className="row" style={{ color: "inherit", textDecoration: "none", padding: "6px 0" }}>
                  <JobStatusIcon job={j} />
                  <span className="ellipsis strong" style={{ flex: 1 }}>{j.title}</span>
                  {j.stats?.files ? <span className="badge badge-ok">+{j.stats.files} files</span> : null}
                  <span className="small faint">{ago(j.finished_at ?? j.created_at)}</span>
                </a>
              ))}
            </div>
          ) : <p className="muted">Nothing has happened yet.</p>}
        </div>
      </div>

      {lb && <Lightbox items={lb.list} index={lb.i} onIndex={(i) => setLb({ ...lb, i })} onClose={() => setLb(null)} onChanged={() => undefined} onRemoved={() => setLb(null)} />}
    </div>
  );
}

function Strip({ list, onOpen }: { list: FileItem[]; onOpen: (i: number) => void }) {
  return (
    <div className="strip">
      {list.map((f, i) => (
        <div key={f.id} className="thumb-tile" onClick={() => onOpen(i)} title={`${f.sender ?? ""} ${f.chat_title ? `in ${f.chat_title}` : ""}`}>
          <img src={thumbUrl(f.id)} alt={f.name} loading="lazy" onError={(e) => (e.currentTarget.style.visibility = "hidden")} />
          <div className="tile-shade" />
          <TypeTag type={f.media_type} />
          <div className="tile-name ellipsis">{f.sender}</div>
        </div>
      ))}
    </div>
  );
}

export function JobStatusIcon({ job }: { job: Job }) {
  const map = {
    running: { icon: Loader2, color: "var(--accent)", spin: true },
    queued: { icon: Clock, color: "var(--text-3)", spin: false },
    done: { icon: CheckCircle2, color: "var(--ok)", spin: false },
    failed: { icon: XCircle, color: "var(--bad)", spin: false },
    cancelled: { icon: PauseCircle, color: "var(--text-3)", spin: false },
    interrupted: { icon: TriangleAlert, color: "var(--warn)", spin: false },
  }[job.status];
  const Icon = map.icon;
  return <Icon size={18} style={{ color: map.color, flex: "none", animation: map.spin ? "spin 1s linear infinite" : undefined }} />;
}
