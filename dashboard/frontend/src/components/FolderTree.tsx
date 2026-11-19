import { useCallback, useEffect, useMemo, useState, type DragEvent, type MouseEvent } from "react";
import { ChevronDown, ChevronRight, Folder as FolderIcon, FolderPlus, Library, MessagesSquare } from "lucide-react";
import { api, type Folder } from "../api";
import { useDialog, useToast } from "../lib/app";
import { Button, Modal, Spinner, TYPE_ICON } from "./ui";

export const FOLDER_COLORS: Record<string, string> = {
  red: "#ef4444", orange: "#f97316", yellow: "#eab308", green: "#10b981", blue: "#3b82f6", purple: "#8b5cf6", pink: "#ec4899",
};

export function FolderGlyph({ folder, size = 17 }: { folder: Pick<Folder, "role" | "media_type" | "color">; size?: number }) {
  if (folder.role === "root") return <Library size={size} />;
  if (folder.role === "chat") return <MessagesSquare size={size} style={{ color: "var(--accent)" }} />;
  if (folder.role === "type" && folder.media_type) {
    const Icon = TYPE_ICON[folder.media_type];
    return <Icon size={size} style={{ color: `var(--c-${folder.media_type})` }} />;
  }
  return <FolderIcon size={size} style={{ color: folder.color ? FOLDER_COLORS[folder.color] : undefined }} fill={folder.color ? FOLDER_COLORS[folder.color] : "none"} fillOpacity={0.25} />;
}

export function useFolders(dep?: unknown) {
  const [folders, setFolders] = useState<Folder[]>([]);
  const [rootId, setRootId] = useState<number | null>(null);
  const reload = useCallback(async () => {
    const r = await api.get<{ root_id: number; folders: Folder[] }>("/folders");
    setFolders(r.folders);
    setRootId(r.root_id);
  }, []);
  useEffect(() => {
    reload().catch(() => undefined);
  }, [reload, dep]);
  return { folders, rootId, reload };
}

export function childrenMap(folders: Folder[]): Map<number | null, Folder[]> {
  const m = new Map<number | null, Folder[]>();
  for (const f of folders) {
    const list = m.get(f.parent_id) ?? [];
    list.push(f);
    m.set(f.parent_id, list);
  }
  for (const list of m.values()) list.sort((a, b) => Number(b.role === "type") - Number(a.role === "type") || a.name.localeCompare(b.name, undefined, { sensitivity: "base" }));
  return m;
}

export function ancestorsOf(folders: Folder[], id: number | null): Set<number> {
  const byId = new Map(folders.map((f) => [f.id, f]));
  const out = new Set<number>();
  let cur = id != null ? byId.get(id) : undefined;
  while (cur) {
    out.add(cur.id);
    cur = cur.parent_id != null ? byId.get(cur.parent_id) : undefined;
  }
  return out;
}

interface TreeProps {
  folders: Folder[];
  rootId: number;
  activeId: number | null;
  onOpen: (id: number) => void;
  onDrop?: (targetId: number, e: DragEvent) => void;
  onMenu?: (folder: Folder, e: MouseEvent) => void;
  disabledIds?: Set<number>;
  showCounts?: boolean;
}

export function FolderTree({ folders, rootId, activeId, onOpen, onDrop, onMenu, disabledIds, showCounts = true }: TreeProps) {
  const kids = useMemo(() => childrenMap(folders), [folders]);
  const [open, setOpen] = useState<Set<number>>(() => new Set([rootId]));
  const [dropId, setDropId] = useState<number | null>(null);

  // Always reveal the active folder.
  useEffect(() => {
    if (activeId == null) return;
    const anc = ancestorsOf(folders, activeId);
    setOpen((prev) => {
      const next = new Set(prev);
      anc.forEach((id) => id !== activeId && next.add(id));
      next.add(rootId);
      return next;
    });
  }, [activeId, folders, rootId]);

  const root = folders.find((f) => f.id === rootId);
  if (!root) return null;

  const render = (f: Folder, depth: number) => {
    const children = kids.get(f.id) ?? [];
    const isOpen = open.has(f.id);
    const disabled = disabledIds?.has(f.id);
    return (
      <div key={f.id}>
        <div
          className={`tree-node ${activeId === f.id ? "active" : ""} ${dropId === f.id ? "drop" : ""}`}
          style={{ paddingLeft: 4 + depth * 14, opacity: disabled ? 0.4 : 1, cursor: disabled ? "not-allowed" : "pointer" }}
          onClick={() => !disabled && onOpen(f.id)}
          onContextMenu={(e) => {
            if (onMenu) {
              e.preventDefault();
              onMenu(f, e);
            }
          }}
          onDragOver={(e) => {
            if (!onDrop || disabled) return;
            e.preventDefault();
            setDropId(f.id);
          }}
          onDragLeave={() => setDropId((d) => (d === f.id ? null : d))}
          onDrop={(e) => {
            setDropId(null);
            if (onDrop && !disabled) {
              e.preventDefault();
              onDrop(f.id, e);
            }
          }}
          title={f.role === "root" ? "Library" : f.name}
        >
          <button
            className="caret"
            style={{ visibility: children.length ? "visible" : "hidden" }}
            onClick={(e) => {
              e.stopPropagation();
              setOpen((prev) => {
                const next = new Set(prev);
                if (next.has(f.id)) next.delete(f.id);
                else next.add(f.id);
                return next;
              });
            }}
            aria-label={isOpen ? "Collapse" : "Expand"}
          >
            {isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          </button>
          <FolderGlyph folder={f} />
          <span className="ellipsis" style={{ marginLeft: 6 }}>{f.role === "root" ? "Library" : f.name}</span>
          {showCounts && f.file_count > 0 && <span className="count">{f.file_count}</span>}
        </div>
        {isOpen && children.map((c) => render(c, depth + 1))}
      </div>
    );
  };
  return <div>{render(root, 0)}</div>;
}

/** Modal to choose a destination folder (with "new folder here"). */
export function FolderPicker({ title, desc, okLabel = "Choose", initialId, disabledIds, onPick, onClose }: {
  title: string;
  desc?: string;
  okLabel?: string;
  initialId?: number | null;
  disabledIds?: Set<number>;
  onPick: (id: number) => void | Promise<void>;
  onClose: () => void;
}) {
  const { folders, rootId, reload } = useFolders();
  const [sel, setSel] = useState<number | null>(initialId ?? null);
  const [busy, setBusy] = useState(false);
  const dialog = useDialog();
  const toast = useToast();
  const target = sel ?? rootId;
  const label = folders.find((f) => f.id === target);

  const newFolder = async () => {
    if (target == null) return;
    const name = await dialog.prompt({ title: "New folder", desc: `Inside “${label?.role === "root" ? "Library" : label?.name}”`, ok: "Create", placeholder: "Folder name" });
    if (!name) return;
    try {
      const r = await api.post<{ id: number }>("/folders", { parent_id: target, name });
      await reload();
      setSel(r.id);
    } catch (e) {
      toast.error(e);
    }
  };

  return (
    <Modal
      title={title}
      desc={desc}
      onClose={onClose}
      footer={
        <>
          <Button icon={FolderPlus} onClick={newFolder} style={{ marginRight: "auto" }}>New folder</Button>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            loading={busy}
            disabled={target == null || disabledIds?.has(target)}
            onClick={async () => {
              if (target == null) return;
              setBusy(true);
              try {
                await onPick(target);
              } finally {
                setBusy(false);
              }
            }}
          >
            {okLabel}
          </Button>
        </>
      }
    >
      <div className="folder-picker">
        {rootId == null ? <div className="empty"><Spinner /></div> : <FolderTree folders={folders} rootId={rootId} activeId={target} onOpen={setSel} disabledIds={disabledIds} showCounts={false} />}
      </div>
      <p className="hint" style={{ marginTop: 10 }}>
        Selected: <strong>{label ? (label.role === "root" ? "Library" : label.name) : "..."}</strong>
      </p>
    </Modal>
  );
}
