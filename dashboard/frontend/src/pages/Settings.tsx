import { useEffect, useState, type ReactNode } from "react";
import { Cpu, Download, FolderInput, FolderSearch, Gauge, HardDrive, Lock, LogOut, Monitor, Moon, Palette, ShieldCheck, Sun, Timer } from "lucide-react";
import { api, type MediaType, type Settings } from "../api";
import { TYPE_COLOR, TYPE_LABEL_PLURAL } from "../lib/format";
import { useApp, useJobStarter, useToast } from "../lib/app";
import { Button, Seg, Spinner, SwitchRow, TYPE_ICON } from "../components/ui";
import { FolderPicker, useFolders } from "../components/FolderTree";

function Section({ icon: Icon, title, desc, children }: { icon: typeof Timer; title: string; desc?: string; children: ReactNode }) {
  return (
    <section className="card card-pad">
      <div className="row" style={{ alignItems: "flex-start", marginBottom: 6 }}>
        <span className="stat-icon" style={{ width: 36, height: 36, borderRadius: 10, display: "grid", placeItems: "center", background: "var(--accent-soft)", color: "var(--accent-text)", flex: "none" }}><Icon size={18} /></span>
        <div>
          <h2>{title}</h2>
          {desc && <p className="muted small" style={{ marginTop: 2 }}>{desc}</p>}
        </div>
      </div>
      {children}
    </section>
  );
}

function Row({ title, desc, children }: { title: string; desc?: ReactNode; children: ReactNode }) {
  return (
    <div className="switch-row" style={{ flexWrap: "wrap" }}>
      <div className="text" style={{ minWidth: 220 }}>
        <div className="strong">{title}</div>
        {desc && <p>{desc}</p>}
      </div>
      <div>{children}</div>
    </div>
  );
}

export default function SettingsPage({ onSignOut }: { onSignOut: () => void }) {
  const { settings, saveSettings } = useApp();
  const toast = useToast();
  const start = useJobStarter();
  const [libraryPath, setLibraryPath] = useState("");
  const [local, setLocal] = useState<Settings | null>(null);

  useEffect(() => {
    api.get<{ settings: Settings; library_path: string }>("/settings").then((r) => setLibraryPath(r.library_path)).catch(() => undefined);
  }, []);
  useEffect(() => setLocal(settings), [settings]);

  if (!local) return <div className="page"><div className="empty"><Spinner size={28} /></div></div>;

  const save = async (changes: Partial<Settings>, quiet = false) => {
    setLocal({ ...local, ...changes });
    const r = await saveSettings(changes);
    if (r && !quiet) toast.ok("Saved.");
    if (!r) setLocal(settings);
  };
  const toggleType = (t: MediaType) => {
    const has = local.default_types.includes(t);
    const next = has ? local.default_types.filter((x) => x !== t) : [...local.default_types, t];
    if (next.length) save({ default_types: next });
  };
  const speed = local.delay_max <= 2 ? "fast" : local.delay_max <= 4 ? "normal" : "careful";

  return (
    <div className="page" style={{ maxWidth: 920 }}>
      <div className="page-head">
        <div>
          <h1>Settings</h1>
          <p>Changes save by themselves. The defaults are good for most people.</p>
        </div>
      </div>

      <div className="stack">
        <Section icon={Timer} title="Automatic checking" desc="Let the app look for new media in the background, like a watcher.">
          <SwitchRow title="Check my chats automatically" desc="Only chats where “Check automatically” is switched on (on the Chats page) are checked." on={local.auto_sync} onChange={(v) => save({ auto_sync: v })} />
          <Row title="How often" desc="Less often is gentler on your account.">
            <select className="select" style={{ width: 200 }} value={local.auto_interval_min} disabled={!local.auto_sync} onChange={(e) => save({ auto_interval_min: Number(e.target.value) })}>
              {[5, 10, 15, 30, 60, 120, 360, 720, 1440].map((m) => <option key={m} value={m}>{m < 60 ? `Every ${m} minutes` : m === 60 ? "Every hour" : m === 1440 ? "Once a day" : `Every ${m / 60} hours`}</option>)}
            </select>
          </Row>
          <Row title="Messages per check" desc="How many of the newest messages each automatic check reads.">
            <select className="select" style={{ width: 200 }} value={local.watch_batch} disabled={!local.auto_sync} onChange={(e) => save({ watch_batch: Number(e.target.value) })}>
              {[10, 20, 30, 50, 100, 200].map((n) => <option key={n} value={n}>Last {n} messages</option>)}
            </select>
          </Row>
        </Section>

        <Section icon={Download} title="Downloading" desc="What “Sync now” does. Each chat can override some of these on the Chats page.">
          <Row title="Save these kinds of media" desc="Used for chats you switch on from now on.">
            <div className="row-wrap" style={{ gap: 6 }}>
              {(["photo", "video", "post", "reel"] as MediaType[]).map((t) => {
                const Icon = TYPE_ICON[t];
                return <span key={t} className={`chip ${local.default_types.includes(t) ? "on" : ""}`} onClick={() => toggleType(t)}><Icon size={14} style={{ color: TYPE_COLOR[t] }} />{TYPE_LABEL_PLURAL[t]}</span>;
              })}
            </div>
          </Row>
          <Row title="Messages to read per sync" desc="Reading more finds older media, but takes longer.">
            <select className="select" style={{ width: 200 }} value={local.message_limit} onChange={(e) => save({ message_limit: Number(e.target.value) })}>
              {[50, 100, 200, 500, 1000, 2000, 5000].map((n) => <option key={n} value={n}>Last {n} messages</option>)}
            </select>
          </Row>
          <Row title="Only save media sent after" desc="Leave empty to save everything.">
            <div className="row">
              <input type="date" className="input" style={{ width: 180 }} value={local.since} onChange={(e) => save({ since: e.target.value })} />
              {local.since && <Button size="sm" variant="ghost" onClick={() => save({ since: "" })}>Clear</Button>}
            </div>
          </Row>
          <SwitchRow title="Sort new chats into Photos / Videos / Posts / Reels folders" desc="Otherwise each chat's media goes into one folder." on={local.split_by_type} onChange={(v) => save({ split_by_type: v })} />
          <SwitchRow title="Show one-to-one chats" desc="By default only group chats are listed." on={local.include_private} onChange={(v) => save({ include_private: v })} />
          <SwitchRow title="Download shared posts and reels from exports" desc="Exports only contain links for shared posts. This signs in to fetch them." on={local.download_shared_from_export} onChange={(v) => save({ download_shared_from_export: v })} />
          <Row title="Chats to load from Instagram" desc="How many of your most recent chats appear in the list.">
            <select className="select" style={{ width: 200 }} value={local.search_threads} onChange={(e) => save({ search_threads: Number(e.target.value) })}>
              {[20, 40, 60, 100, 200].map((n) => <option key={n} value={n}>{n} most recent</option>)}
            </select>
          </Row>
        </Section>

        <Section icon={Gauge} title="Speed and safety" desc="Pauses between requests make the app behave like a person, which keeps your account safe.">
          <Row title="Pace" desc={speed === "fast" ? "Quick, a little riskier with big downloads." : speed === "normal" ? "Balanced (recommended)." : "Slow and very gentle."}>
            <Seg value={speed} onChange={(v) => save(v === "fast" ? { delay_min: 0.5, delay_max: 1.5 } : v === "normal" ? { delay_min: 1, delay_max: 3 } : { delay_min: 3, delay_max: 8 })} options={[{ value: "fast", label: "Fast" }, { value: "normal", label: "Normal" }, { value: "careful", label: "Careful" }]} />
          </Row>
          <p className="hint">Currently waits {local.delay_min}–{local.delay_max} seconds between requests. Takes effect at the next sign-in.</p>
        </Section>

        <LibrarySection libraryPath={libraryPath} retention={local.trash_retention_days} onRetention={(d) => save({ trash_retention_days: d })} start={start} />

        <Section icon={Palette} title="Appearance">
          <Row title="Theme">
            <Seg value={local.theme} onChange={(v) => save({ theme: v }, true)} options={[{ value: "system", label: "Auto", icon: Monitor }, { value: "light", label: "Light", icon: Sun }, { value: "dark", label: "Dark", icon: Moon }]} />
          </Row>
          <Row title="Thumbnail size">
            <Seg value={local.grid_size} onChange={(v) => save({ grid_size: v }, true)} options={[{ value: "small", label: "Small" }, { value: "medium", label: "Medium" }, { value: "large", label: "Large" }]} />
          </Row>
        </Section>

        <ServerSection />

        <PasscodeSection onSignOut={onSignOut} />
      </div>
    </div>
  );
}

function LibrarySection({ libraryPath, retention, onRetention, start }: { libraryPath: string; retention: number; onRetention: (d: number) => void; start: ReturnType<typeof useJobStarter> }) {
  const { rootId, folders } = useFolders();
  const [path, setPath] = useState("");
  const [target, setTarget] = useState<number | null>(null);
  const [move, setMove] = useState(false);
  const [picking, setPicking] = useState(false);
  const targetId = target ?? rootId;
  const targetName = folders.find((f) => f.id === targetId);

  return (
    <Section icon={HardDrive} title="Library" desc="Where your files live, and bringing in files from elsewhere.">
      <Row title="Library folder on this computer" desc="You can also open this folder in Explorer/Finder. Set LIBRARY_DIR when starting the app to use another place.">
        <code className="folder-pill" style={{ maxWidth: 380, overflowWrap: "anywhere", whiteSpace: "normal" }}>{libraryPath}</code>
      </Row>
      <Row title="Keep deleted files in the trash for" desc="After that they're removed for good.">
        <select className="select" style={{ width: 200 }} value={retention} onChange={(e) => onRetention(Number(e.target.value))}>
          {[7, 14, 30, 60, 90, 365, 0].map((d) => <option key={d} value={d}>{d === 0 ? "Forever (never empty)" : `${d} days`}</option>)}
        </select>
      </Row>
      <Row title="Changed files outside the app?" desc="If you added or deleted files in the library folder with Explorer/Finder, this brings the app up to date.">
        <Button icon={FolderSearch} onClick={() => start(() => api.post("/library/rescan"), "Checking the library folder...")}>Rescan library</Button>
      </Row>
      <div className="switch-row" style={{ display: "block" }}>
        <div className="strong">Bring in files from anywhere on this computer</div>
        <p className="muted small" style={{ margin: "2px 0 12px" }}>Paste the path of a file or folder (for example your old downloads). Folders keep their structure.</p>
        <div className="stack-sm">
          <input className="input mono" placeholder="D:\Pictures\Old Instagram" value={path} onChange={(e) => setPath(e.target.value)} />
          <div className="row-wrap">
            <span className="small muted">Into</span>
            <Button size="sm" icon={FolderInput} onClick={() => setPicking(true)}>{targetName ? (targetName.role === "root" ? "Library" : targetName.name) : "Library"}</Button>
            <Seg value={move ? "move" : "copy"} onChange={(v) => setMove(v === "move")} options={[{ value: "copy", label: "Copy (keep originals)" }, { value: "move", label: "Move" }]} />
            <span className="spacer" />
            <Button variant="primary" disabled={!path.trim() || targetId == null} onClick={async () => { if (await start(() => api.post("/library/import", { path, folder_id: targetId, move }), "Importing... watch the progress in the corner.")) setPath(""); }}>Import</Button>
          </div>
        </div>
      </div>
      {picking && <FolderPicker title="Import into which folder?" okLabel="Choose" initialId={targetId} onClose={() => setPicking(false)} onPick={(id) => { setTarget(id); setPicking(false); }} />}
    </Section>
  );
}

function ServerSection() {
  const { status } = useApp();
  if (!status) return null;
  const s = status.server;
  const e = status.engine;
  const multi = s.mode === "multi-worker";
  return (
    <Section icon={Cpu} title="Server" desc="How the app runs behind the scenes. Set these when starting the app (start.bat, start.sh or docker-compose.yml).">
      <Row title="Web workers" desc={multi ? "Several processes answer the dashboard at the same time." : "Running as a single process (development mode)."}>
        <span className="badge badge-accent">{multi ? `${s.web_workers} workers` : "1 process"}</span>
      </Row>
      <Row title="Fresh worker after" desc="Each web worker is replaced with a fresh one after this many requests (plus a random extra amount, so they never all restart together). Keeps memory use low over time.">
        <span className="badge">{s.max_requests ? `${s.max_requests.toLocaleString()} requests (+0–${s.max_requests_jitter})` : "never"}</span>
      </Row>
      <Row title="Background worker" desc={<>Does all the Instagram work, one task at a time. Replaced with a fresh one after {s.engine_max_jobs ? `${s.engine_max_jobs} tasks` : "never"}.</>}>
        <span className={`badge ${e.alive ? "badge-ok" : "badge-warn"}`}>{e.alive ? `Running · ${e.jobs_done} task${e.jobs_done === 1 ? "" : "s"} done` : "Starting..."}</span>
      </Row>
      <p className="hint">Variables: WEB_WORKERS, MAX_REQUESTS, MAX_REQUESTS_JITTER, ENGINE_MAX_JOBS.</p>
    </Section>
  );
}

function PasscodeSection({ onSignOut }: { onSignOut: () => void }) {
  const toast = useToast();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [busy, setBusy] = useState(false);
  const change = async () => {
    setBusy(true);
    try {
      await api.post("/auth/change", { current, new: next });
      toast.ok("Passcode changed. Other devices were signed out.");
      setCurrent("");
      setNext("");
    } catch (e) {
      toast.error(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Section icon={ShieldCheck} title="Dashboard passcode" desc="Protects this dashboard. Anyone who can open its address still needs this passcode.">
      <div className="grid-2" style={{ marginTop: 10 }}>
        <div className="field"><label htmlFor="pc-cur">Current passcode</label><input id="pc-cur" className="input" type="password" value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" /></div>
        <div className="field"><label htmlFor="pc-new">New passcode</label><input id="pc-new" className="input" type="password" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" placeholder="At least 4 characters" /></div>
      </div>
      <div className="row" style={{ marginTop: 14 }}>
        <Button variant="primary" icon={Lock} disabled={!current || next.length < 4} loading={busy} onClick={change}>Change passcode</Button>
        <span className="spacer" />
        <Button icon={LogOut} onClick={onSignOut}>Lock dashboard now</Button>
      </div>
    </Section>
  );
}
