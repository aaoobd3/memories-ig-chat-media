import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle, Archive, CheckCircle2, Eye, FileArchive, FolderOpen, FolderSync, History, Info, MessagesSquare, MoreHorizontal,
  RefreshCw, Search, Upload, Users, Zap,
} from "lucide-react";
import { api, uploadExport, type Chat, type MediaType } from "../api";
import { ago, avatarStyle, initials, plural, TYPE_COLOR, TYPE_LABEL_PLURAL } from "../lib/format";
import { go, useApp, useDialog, useJobStarter, useToast } from "../lib/app";
import { Button, ContextMenu, Empty, Modal, Progress, Seg, Spinner, Switch, TYPE_ICON, type MenuItem } from "../components/ui";
import { FolderPicker } from "../components/FolderTree";

type Filter = "all" | "on" | "off";

export default function ChatsPage() {
  const { status, settings, saveSettings, bump } = useApp();
  const toast = useToast();
  const dialog = useDialog();
  const start = useJobStarter();
  const [chats, setChats] = useState<Chat[] | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [q, setQ] = useState("");
  const [picker, setPicker] = useState<Chat | null>(null);
  const [menu, setMenu] = useState<{ x: number; y: number; items: MenuItem[] } | null>(null);
  const [exportOpen, setExportOpen] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await api.get<{ chats: Chat[] }>("/chats");
      setChats(r.chats);
    } catch (e) {
      toast.error(e);
    }
  }, [toast]);
  useEffect(() => {
    load();
  }, [load, bump, settings?.include_private]);

  const update = async (chat: Chat, body: Partial<Record<string, unknown>>, okText?: string) => {
    try {
      const fresh = await api.patch<Chat>(`/chats/${chat.id}`, body);
      setChats((all) => all?.map((c) => (c.id === chat.id ? fresh : c)) ?? null);
      if (okText) toast.ok(okText);
    } catch (e) {
      toast.error(e);
    }
  };

  const sync = (ids: number[], mode: "recent" | "all", dry = false) =>
    start(() => api.post("/sync", { chat_ids: ids, mode, dry_run: dry }), dry ? "Checking what's new (nothing will be downloaded)..." : "Started. You can keep using the app meanwhile.");

  const fullHistory = async (chat: Chat) => {
    const ok = await dialog.confirm({
      title: `Download the whole history of “${chat.title}”?`,
      desc: "This reads every message ever sent in the chat. For big chats it can take a long while, and very heavy use can make Instagram slow you down for a few hours. Normal syncs only read the latest messages.",
      ok: "Yes, download everything",
    });
    if (ok) sync([chat.id], "all");
  };

  const shown = useMemo(() => {
    const list = chats ?? [];
    return list.filter((c) => (filter === "on" ? c.selected : filter === "off" ? !c.selected : true) && (!q || c.title.toLowerCase().includes(q.toLowerCase())));
  }, [chats, filter, q]);
  const selectedIds = (chats ?? []).filter((c) => c.selected).map((c) => c.id);
  const igState = status?.instagram.state;
  const refreshing = status?.active_jobs.some((j) => j.kind === "refresh_chats");

  const chatMenu = (chat: Chat, e: React.MouseEvent) => {
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    setMenu({
      x: r.left - 180,
      y: r.bottom + 6,
      items: [
        { label: "Preview what's new", icon: Eye, onClick: () => sync([chat.id], "recent", true) },
        ...(chat.source === "live" ? [{ label: "Download full history...", icon: History, onClick: () => fullHistory(chat) }] : []),
        { label: "Open its folder", icon: FolderOpen, onClick: () => (chat.folder_id ? go(`library/${chat.folder_id}`) : toast.ok("The folder is created on the first sync.")) },
        { label: "Give it a fresh folder", icon: FolderSync, onClick: async () => {
          try {
            await api.post(`/chats/${chat.id}/folder/new`);
            toast.ok("Done. New downloads go to its own folder.");
            load();
          } catch (err) { toast.error(err); }
        } },
        { sep: true },
        { label: "Download everything again...", icon: RefreshCw, onClick: async () => {
          const ok = await dialog.confirm({ title: "Forget what was already downloaded?", desc: "The next sync will download every item again (existing files are kept; you may get duplicates).", ok: "Forget history" });
          if (!ok) return;
          await api.post(`/chats/${chat.id}/reset`).catch(toast.error);
          toast.ok("Download history cleared for this chat.");
        } },
        { label: "Remove from list", icon: Archive, danger: true, onClick: async () => {
          const ok = await dialog.confirm({ title: `Remove “${chat.title}” from the list?`, desc: "Files already saved stay in your library. You can load the chat again any time.", ok: "Remove", danger: true });
          if (!ok) return;
          await api.del(`/chats/${chat.id}`).catch(toast.error);
          load();
        } },
      ],
    });
  };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Chats</h1>
          <p>Switch on the chats you want to save photos, videos, posts and reels from. Each chat saves into its own folder, and you can change where.</p>
        </div>
        <div className="row-wrap">
          <Button icon={FileArchive} onClick={() => setExportOpen(true)}>Add Instagram export</Button>
          <Button variant="primary" icon={RefreshCw} loading={refreshing} onClick={() => start(() => api.post("/chats/refresh"), "Loading your chats from Instagram...")}>
            {chats?.length ? "Refresh chat list" : "Load my chats"}
          </Button>
        </div>
      </div>

      {igState !== "connected" && !chats?.some((c) => c.source === "live") && (
        <div className="callout callout-warn" style={{ marginBottom: 18 }}>
          <AlertTriangle size={18} />
          <div className="stack-sm" style={{ flex: 1 }}>
            <span><strong>Connect your Instagram account first</strong> so the app can see your chats. Or, if you'd rather not sign in, add an Instagram data export instead.</span>
            <div><Button size="sm" variant="primary" onClick={() => go("account")}>Connect Instagram</Button></div>
          </div>
        </div>
      )}

      {chats && chats.length > 0 && (
        <div className="row-wrap" style={{ marginBottom: 16 }}>
          <div className="input-wrap" style={{ width: 260 }}>
            <Search size={16} />
            <input className="input" placeholder="Find a chat" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <Seg<Filter> value={filter} onChange={setFilter} options={[
            { value: "all", label: `All (${chats.length})` },
            { value: "on", label: `Saving (${selectedIds.length})` },
            { value: "off", label: "Not saving" },
          ]} />
          <span className="spacer" />
          <label className="row small muted" style={{ cursor: "pointer" }}>
            <Switch on={!!settings?.include_private} onChange={(v) => saveSettings({ include_private: v })} label="Show one-to-one chats" />
            Show one-to-one chats
          </label>
        </div>
      )}

      {chats === null ? (
        <div className="empty"><Spinner size={28} /></div>
      ) : !chats.length ? (
        <div className="card">
          <Empty icon={MessagesSquare} title="No chats yet" action={<Button variant="primary" icon={RefreshCw} onClick={() => start(() => api.post("/chats/refresh"), "Loading your chats from Instagram...")}>Load my chats</Button>}>
            Press “Load my chats” to see your Instagram group chats here. Then switch on the ones you want to save.
          </Empty>
        </div>
      ) : !shown.length ? (
        <div className="card"><Empty icon={Search} title="No chats match">Try another name, or switch the filter to “All”.</Empty></div>
      ) : (
        <div className="stack">
          {shown.map((chat) => (
            <ChatCard
              key={chat.id}
              chat={chat}
              defaultLimit={settings?.message_limit ?? 200}
              autoGlobal={!!settings?.auto_sync}
              onUpdate={(body, msg) => update(chat, body, msg)}
              onSync={() => sync([chat.id], "recent")}
              onPickFolder={() => setPicker(chat)}
              onMenu={(e) => chatMenu(chat, e)}
            />
          ))}
        </div>
      )}

      {selectedIds.length > 0 && (
        <div className="sticky-bar">
          <CheckCircle2 size={20} style={{ color: "var(--ok)" }} />
          <div style={{ flex: 1, minWidth: 180 }}>
            <strong>{plural(selectedIds.length, "chat")} switched on</strong>
            <div className="small muted">
              {settings?.auto_sync ? `Checked automatically every ${settings.auto_interval_min} min (where “Check automatically” is on).` : "Automatic checking is off. Turn it on in Settings."}
            </div>
          </div>
          <Button icon={Eye} onClick={() => sync(selectedIds, "recent", true)}>Preview</Button>
          <Button variant="brand" icon={Zap} onClick={() => sync(selectedIds, "recent")}>Sync all now</Button>
        </div>
      )}

      {picker && (
        <FolderPicker
          title={`Where should “${picker.title}” save?`}
          desc="New downloads from this chat go into this folder (with Photos / Videos / Posts / Reels inside, if that option is on). Files already saved stay where they are."
          okLabel="Save here"
          initialId={picker.folder_id}
          onClose={() => setPicker(null)}
          onPick={async (id) => {
            await update(picker, { folder_id: id }, "Download folder changed.");
            setPicker(null);
          }}
        />
      )}
      {menu && <ContextMenu x={menu.x} y={menu.y} items={menu.items} onClose={() => setMenu(null)} />}
      {exportOpen && <ExportModal onClose={() => setExportOpen(false)} />}
    </div>
  );
}

function ChatCard({ chat, defaultLimit, autoGlobal, onUpdate, onSync, onPickFolder, onMenu }: {
  chat: Chat;
  defaultLimit: number;
  autoGlobal: boolean;
  onUpdate: (body: Record<string, unknown>, msg?: string) => void;
  onSync: () => void;
  onPickFolder: () => void;
  onMenu: (e: React.MouseEvent) => void;
}) {
  const on = !!chat.selected;
  const res = chat.last_result;
  const toggleType = (t: MediaType) => {
    const has = chat.types.includes(t);
    const next = has ? chat.types.filter((x) => x !== t) : [...chat.types, t];
    if (!next.length) return;
    onUpdate({ types: next });
  };
  return (
    <div className={`card chat-card ${on ? "on" : ""}`}>
      <div className="chat-top">
        <div className="avatar" style={avatarStyle(chat.title)}>{initials(chat.title)}</div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="chat-title ellipsis">{chat.title}</div>
          <div className="chat-meta">
            <span className="row" style={{ gap: 4 }}><Users size={13} /> {chat.people} people</span>
            {chat.source === "export" ? <span className="badge">From export</span> : chat.last_activity ? <span>Active {ago(chat.last_activity)}</span> : null}
            {chat.file_count > 0 && <span>{plural(chat.file_count, "file")} saved</span>}
          </div>
        </div>
        <span className="small muted only-desktop" style={{ fontWeight: 600 }}>{on ? "Saving" : "Off"}</span>
        <Switch on={on} onChange={(v) => onUpdate({ selected: v }, v ? `“${chat.title}” switched on.` : undefined)} label={`Save media from ${chat.title}`} />
      </div>

      {on && (
        <div className="chat-body">
          <div className="chat-opts">
            <div className="opt-line">
              <span className="opt-label">Saves to</span>
              <span className="folder-pill ellipsis"><FolderOpen size={15} /> {chat.folder_label ?? `Library / ${chat.title}`}</span>
              <Button size="sm" variant="ghost" onClick={onPickFolder}>Change</Button>
              {chat.folder_id && <Button size="sm" variant="ghost" onClick={() => go(`library/${chat.folder_id}`)}>Open</Button>}
            </div>
            <div className="opt-line">
              <span className="opt-label">What to save</span>
              {(["photo", "video", "post", "reel"] as MediaType[]).map((t) => {
                const Icon = TYPE_ICON[t];
                return (
                  <span key={t} className={`chip ${chat.types.includes(t) ? "on" : ""}`} onClick={() => toggleType(t)}>
                    <Icon size={14} style={{ color: TYPE_COLOR[t] }} /> {TYPE_LABEL_PLURAL[t]}
                  </span>
                );
              })}
            </div>
            <div className="opt-line">
              <span className="opt-label">Sort into</span>
              <Seg value={chat.split_by_type ? 1 : 0} onChange={(v) => onUpdate({ split_by_type: !!v })} options={[{ value: 1, label: "Photos / Videos / Posts / Reels folders" }, { value: 0, label: "One folder" }]} />
            </div>
            {chat.source === "live" && (
              <>
                <div className="opt-line">
                  <span className="opt-label">Messages to read</span>
                  <select className="select input-sm" style={{ width: 200 }} value={chat.message_limit ?? 0} onChange={(e) => onUpdate({ message_limit: Number(e.target.value) })}>
                    <option value={0}>Default ({defaultLimit})</option>
                    {[50, 100, 200, 500, 1000, 2000].map((n) => <option key={n} value={n}>Last {n}</option>)}
                  </select>
                </div>
                <div className="opt-line">
                  <span className="opt-label">Check automatically</span>
                  <Switch on={!!chat.auto_sync} onChange={(v) => onUpdate({ auto_sync: v }, v && !autoGlobal ? "Saved. Also turn on automatic checking in Settings." : undefined)} label="Check automatically" />
                  {!!chat.auto_sync && !autoGlobal && <span className="small" style={{ color: "var(--warn)" }}>Automatic checking is off in Settings</span>}
                </div>
              </>
            )}
            {res && (
              <div className="result-line">
                {res.ok ? <CheckCircle2 size={15} style={{ color: "var(--ok)" }} /> : <AlertTriangle size={15} style={{ color: "var(--bad)" }} />}
                <span>{res.preview ? "Preview: " : chat.last_synced_at ? `Last sync ${ago(chat.last_synced_at)}: ` : ""}{res.summary}</span>
              </div>
            )}
            {chat.listed_count > 0 && (
              <div className="result-line"><Info size={15} /> {plural(chat.listed_count, "shared post")} only listed (turn on “Download shared posts from exports” in Settings to fetch them).</div>
            )}
          </div>
          <div className="chat-actions">
            <Button variant="primary" icon={Zap} onClick={onSync}>Sync now</Button>
            <Button icon={MoreHorizontal} onClick={onMenu}>More</Button>
          </div>
        </div>
      )}
    </div>
  );
}

function ExportModal({ onClose }: { onClose: () => void }) {
  const [tab, setTab] = useState<"upload" | "path">("upload");
  const [path, setPath] = useState("");
  const [progress, setProgress] = useState<number | null>(null);
  const [drag, setDrag] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const toast = useToast();
  const start = useJobStarter();

  const send = async (file: File) => {
    if (!file.name.toLowerCase().endsWith(".zip")) {
      toast.error("Please choose the .zip file you downloaded from Instagram.");
      return;
    }
    setProgress(0);
    try {
      await uploadExport(file, setProgress);
      toast.ok("Uploaded. Reading the chats inside...");
      onClose();
    } catch (e) {
      toast.error(e);
      setProgress(null);
    }
  };

  return (
    <Modal wide title="Add an Instagram data export" desc="A safe way to get media without signing in: Instagram packs up your messages into a file you download." onClose={onClose}>
      <div className="stack">
        <details className="card card-pad">
          <summary className="strong" style={{ cursor: "pointer" }}>How do I get the export?</summary>
          <ol className="steps" style={{ marginTop: 14 }}>
            <li><span>In the Instagram app open <strong>Settings → Accounts Center → Your information and permissions → Download your information</strong>.</span></li>
            <li><span>Pick <strong>Some of your information → Messages</strong>, then <strong>Download to device</strong>.</span></li>
            <li><span>Choose <strong>Format: JSON</strong> and <strong>Media quality: High</strong>, then request it.</span></li>
            <li><span>When Instagram says it's ready (can take a few hours), download the .zip file and add it here.</span></li>
          </ol>
        </details>
        <Seg value={tab} onChange={setTab} options={[{ value: "upload", label: "Upload the .zip", icon: Upload }, { value: "path", label: "It's already on this computer", icon: FolderOpen }]} />
        {tab === "upload" ? (
          progress !== null ? (
            <div className="stack-sm"><strong>Uploading... {Math.round(progress * 100)}%</strong><Progress value={progress * 100} total={100} /></div>
          ) : (
            <div
              className="card"
              style={{ padding: 34, textAlign: "center", borderStyle: "dashed", borderWidth: 2, background: drag ? "var(--accent-soft)" : undefined, cursor: "pointer" }}
              onClick={() => input.current?.click()}
              onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
              onDragLeave={() => setDrag(false)}
              onDrop={(e) => { e.preventDefault(); setDrag(false); const f = e.dataTransfer.files[0]; if (f) send(f); }}
            >
              <FileArchive size={36} style={{ color: "var(--accent)" }} />
              <div className="strong" style={{ marginTop: 8 }}>Drop the export .zip here, or click to choose it</div>
              <div className="hint">It can be large. Keep this page open while it uploads.</div>
              <input ref={input} type="file" accept=".zip" hidden onChange={(e) => e.target.files?.[0] && send(e.target.files[0])} />
            </div>
          )
        ) : (
          <div className="stack-sm">
            <label className="label" htmlFor="exp-path">Folder (or .zip) on the computer running this app</label>
            <input id="exp-path" className="input mono" placeholder="C:\Users\me\Downloads\instagram-me-2026-11-19" value={path} onChange={(e) => setPath(e.target.value)} />
            <span className="hint">Tip: in Windows Explorer, hold Shift, right-click the folder and choose “Copy as path”.</span>
            <div><Button variant="primary" disabled={!path.trim()} onClick={async () => { if (await start(() => api.post("/exports/scan", { path }), "Reading the export...")) onClose(); }}>Read this export</Button></div>
          </div>
        )}
        <p className="hint">After it's read, the chats inside appear in the list; switch on the ones you want and press Sync now. An export is a one-time snapshot. It doesn't update by itself.</p>
      </div>
    </Modal>
  );
}
