import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent, type MouseEvent } from "react";
import {
  ArrowDownUp, CheckSquare, ChevronRight, ClipboardPaste, Clock, Copy, CopyPlus, Download, Folder as FolderIcon, FolderInput,
  FolderOpen, FolderPlus, FolderTree as FolderTreeIcon, Heart, Images, LayoutGrid, List, Palette, Pencil, RotateCcw, Scissors,
  Search, Shapes, Trash2, Upload, X,
} from "lucide-react";
import { api, qs, thumbUrl, uploadFiles, zipUrl, type AnyType, type BulkResult, type FileItem, type FileList, type Folder, type MediaType } from "../api";
import { bytes, num, plural, shortDate, TYPE_COLOR, TYPE_LABEL, TYPE_LABEL_PLURAL } from "../lib/format";
import { go, useApp, useDialog, useToast } from "../lib/app";
import { Button, ContextMenu, Empty, Modal, Progress, Seg, Spinner, TYPE_ICON, TypeTag, type MenuItem } from "../components/ui";
import { FOLDER_COLORS, FolderGlyph, FolderPicker, FolderTree, useFolders } from "../components/FolderTree";
import { Lightbox } from "../components/Lightbox";

type View = "folder" | "all" | "favorites" | "recent" | "duplicates" | "trash";
type Key = string; // "f:12" file, "d:7" folder
interface Origins { files: Record<number, number>; folders: Record<number, number> }
interface Clip { mode: "copy" | "cut"; file_ids: number[]; folder_ids: number[]; origins: Origins }

const VIEWS: { key: Exclude<View, "folder">; label: string; icon: typeof Images }[] = [
  { key: "all", label: "All media", icon: Images },
  { key: "favorites", label: "Favorites", icon: Heart },
  { key: "recent", label: "Recently added", icon: Clock },
  { key: "duplicates", label: "Duplicates", icon: Copy },
  { key: "trash", label: "Trash", icon: Trash2 },
];
const PAGE = 120;
let clipboard: Clip | null = null; // survives page switches inside the app

function readPref<T extends string>(key: string, fallback: T): T {
  try {
    return (localStorage.getItem(key) as T) || fallback;
  } catch {
    return fallback;
  }
}
function writePref(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* ignore */
  }
}
const fk = (id: number): Key => `f:${id}`;
const dk = (id: number): Key => `d:${id}`;
const DRAG_MIME = "application/x-igcm-entries";

export default function LibraryPage({ route }: { route: string[] }) {
  const { settings, bump, refreshStatus, status } = useApp();
  const toast = useToast();
  const dialog = useDialog();

  // ----- route -> what to show -------------------------------------------
  const view: View = route[0] === "view" && route[1] ? (route[1] as View) : "folder";
  const folderParam = view === "folder" && route[0] ? Number(route[0]) : null;

  const [reloadKey, setReloadKey] = useState(0);
  const reloadAll = useCallback(() => setReloadKey((k) => k + 1), []);
  const { folders, rootId, reload: reloadTree } = useFolders(`${bump}-${reloadKey}`);
  const folderId = view === "folder" ? folderParam ?? rootId : null;

  const [type, setType] = useState<AnyType | "">("");
  const [q, setQ] = useState("");
  const [qDebounced, setQDebounced] = useState("");
  const [sort, setSort] = useState<string>(() => readPref("igcm-sort", "sent"));
  const [order, setOrder] = useState<"asc" | "desc">(() => readPref("igcm-order", "desc"));
  const [layout, setLayout] = useState<"grid" | "list">(() => readPref("igcm-layout", "grid"));
  const [recursive, setRecursive] = useState(false);
  const size = settings?.grid_size ?? "medium";

  const [data, setData] = useState<FileList | null>(null);
  const [items, setItems] = useState<FileItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);

  const [sel, setSel] = useState<Set<Key>>(new Set());
  const anchor = useRef<Key | null>(null);
  const [clip, setClip] = useState<Clip | null>(clipboard);
  const [lightbox, setLightbox] = useState<number | null>(null);
  const [menu, setMenu] = useState<{ x: number; y: number; items: MenuItem[] } | null>(null);
  const [picker, setPicker] = useState<null | { mode: "move" | "copy"; file_ids: number[]; folder_ids: number[] }>(null);
  const [typeDialog, setTypeDialog] = useState<number[] | null>(null);
  const [colorDialog, setColorDialog] = useState<Folder | null>(null);
  const [upload, setUpload] = useState<{ count: number; progress: number } | null>(null);
  const [dropKey, setDropKey] = useState<string | null>(null);
  const [osDrag, setOsDrag] = useState(false);
  const [sideOpen, setSideOpen] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const sentinel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const t = setTimeout(() => setQDebounced(q), 280);
    return () => clearTimeout(t);
  }, [q]);
  useEffect(() => writePref("igcm-sort", sort), [sort]);
  useEffect(() => writePref("igcm-order", order), [order]);
  useEffect(() => writePref("igcm-layout", layout), [layout]);

  // reset selection when the location changes
  useEffect(() => {
    setSel(new Set());
    setSideOpen(false);
    setRecursive(false);
  }, [view, folderId]);

  // ----- loading ------------------------------------------------------------
  const params = useMemo(
    () => ({ view, folder_id: folderId ?? undefined, type: type || undefined, q: qDebounced || undefined, sort, order, recursive: recursive || undefined }),
    [view, folderId, type, qDebounced, sort, order, recursive],
  );
  useEffect(() => {
    if (view === "folder" && folderId == null) return;
    let cancelled = false;
    setLoading(true);
    api
      .get<FileList>(`/files${qs({ ...params, offset: 0, limit: PAGE })}`)
      .then((r) => {
        if (cancelled) return;
        setData(r);
        setItems(r.items);
      })
      .catch((e) => {
        if (cancelled) return;
        toast.error(e);
        if (view === "folder" && folderParam != null) go("library");
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [params, bump, reloadKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const loadMore = useCallback(async () => {
    if (!data || loadingMore || items.length >= data.total) return;
    setLoadingMore(true);
    try {
      const r = await api.get<FileList>(`/files${qs({ ...params, offset: items.length, limit: PAGE })}`);
      setItems((prev) => [...prev, ...r.items.filter((x) => !prev.some((p) => p.id === x.id))]);
    } catch (e) {
      toast.error(e);
    } finally {
      setLoadingMore(false);
    }
  }, [data, items.length, loadingMore, params, toast]);

  useEffect(() => {
    const el = sentinel.current;
    if (!el) return;
    const io = new IntersectionObserver((entries) => entries[0].isIntersecting && loadMore(), { rootMargin: "600px" });
    io.observe(el);
    return () => io.disconnect();
  }, [loadMore]);

  const subfolders = view === "folder" ? data?.folders ?? [] : [];
  const orderKeys: Key[] = useMemo(() => [...subfolders.map((f) => dk(f.id)), ...items.map((f) => fk(f.id))], [subfolders, items]);

  // ----- selection ------------------------------------------------------------
  const selectedFiles = useMemo(() => items.filter((f) => sel.has(fk(f.id))).map((f) => f.id), [items, sel]);
  const selectedFolders = useMemo(() => subfolders.filter((f) => sel.has(dk(f.id))).map((f) => f.id), [subfolders, sel]);
  const selecting = sel.size > 0;

  const clickSelect = (key: Key, e: MouseEvent, open: () => void) => {
    if (e.shiftKey && anchor.current) {
      const a = orderKeys.indexOf(anchor.current);
      const b = orderKeys.indexOf(key);
      if (a >= 0 && b >= 0) {
        const [lo, hi] = a < b ? [a, b] : [b, a];
        setSel(new Set([...sel, ...orderKeys.slice(lo, hi + 1)]));
        return;
      }
    }
    if (e.ctrlKey || e.metaKey || selecting) {
      toggle(key);
      return;
    }
    open();
  };
  const toggle = (key: Key) => {
    anchor.current = key;
    setSel((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };
  const clearSel = () => setSel(new Set());
  const selectAll = () => setSel(new Set(orderKeys));

  // ----- actions ------------------------------------------------------------
  const refresh = useCallback(async () => {
    reloadAll();
    await reloadTree();
    refreshStatus();
  }, [reloadAll, reloadTree, refreshStatus]);

  const report = (r: BulkResult, what: string) => {
    if (r.errors.length) toast.error(`${r.done ? `${what} ${r.done}, but ` : ""}${r.errors[0]}${r.errors.length > 1 ? ` (+${r.errors.length - 1} more)` : ""}`);
  };

  const originsOf = (file_ids: number[], folder_ids: number[]): Origins => {
    const o: Origins = { files: {}, folders: {} };
    for (const id of file_ids) {
      const f = items.find((x) => x.id === id);
      if (f?.folder_id != null) o.files[id] = f.folder_id;
    }
    for (const id of folder_ids) {
      const f = folders.find((x) => x.id === id);
      if (f?.parent_id != null) o.folders[id] = f.parent_id;
    }
    return o;
  };

  const moveEntries = async (file_ids: number[], folder_ids: number[], target: number, opts: { copy?: boolean; undoable?: boolean; origins?: Origins } = {}) => {
    if (!file_ids.length && !folder_ids.length) return;
    // remember where things were, for Undo
    const before = new Map<number, number[]>();
    const beforeFolders = new Map<number, number[]>();
    if (!opts.copy && opts.undoable !== false) {
      const o = opts.origins ?? originsOf(file_ids, folder_ids);
      for (const id of file_ids) {
        const from = o.files[id];
        if (from != null && from !== target) before.set(from, [...(before.get(from) ?? []), id]);
      }
      for (const id of folder_ids) {
        const from = o.folders[id];
        if (from != null && from !== target) beforeFolders.set(from, [...(beforeFolders.get(from) ?? []), id]);
      }
    }
    try {
      const r = await api.post<BulkResult>(opts.copy ? "/entries/copy" : "/entries/move", { file_ids, folder_ids, target_id: target });
      report(r, opts.copy ? "Copied" : "Moved");
      const name = folders.find((f) => f.id === target);
      const where = name?.role === "root" ? "Library" : name?.name ?? "folder";
      if (r.done) {
        const undo = !opts.copy && opts.undoable !== false && (before.size || beforeFolders.size)
          ? {
              label: "Undo",
              run: async () => {
                for (const [dest, ids] of before) await api.post("/entries/move", { file_ids: ids, folder_ids: [], target_id: dest });
                for (const [dest, ids] of beforeFolders) await api.post("/entries/move", { file_ids: [], folder_ids: ids, target_id: dest });
                toast.ok("Undone.");
                refresh();
              },
            }
          : undefined;
        toast.ok(`${opts.copy ? "Copied" : "Moved"} ${plural(r.done, "item")} to “${where}”.`, undo);
      }
      clearSel();
      refresh();
    } catch (e) {
      toast.error(e);
    }
  };

  const doCopy = (mode: "copy" | "cut", file_ids = selectedFiles, folder_ids = selectedFolders) => {
    if (!file_ids.length && !folder_ids.length) return;
    clipboard = { mode, file_ids, folder_ids, origins: originsOf(file_ids, folder_ids) };
    setClip(clipboard);
    toast.ok(`${mode === "copy" ? "Copied" : "Cut"} ${plural(file_ids.length + folder_ids.length, "item")}. Open a folder and press Paste.`);
  };

  const doPaste = async (target = folderId) => {
    if (!clip || target == null) return;
    await moveEntries(clip.file_ids, clip.folder_ids, target, { copy: clip.mode === "copy", origins: clip.origins });
    if (clip.mode === "cut") {
      clipboard = null;
      setClip(null);
    }
  };

  const doDelete = async (file_ids = selectedFiles, folder_ids = selectedFolders) => {
    const n = file_ids.length + folder_ids.length;
    if (!n) return;
    if (view === "trash") {
      const ok = await dialog.confirm({ title: `Delete ${plural(n, "file")} forever?`, desc: "They'll be gone for good. This can't be undone.", ok: "Delete forever", danger: true });
      if (!ok) return;
      try {
        report(await api.post<BulkResult>("/files/purge", { ids: file_ids }), "Deleted");
      } catch (e) {
        toast.error(e);
      }
      clearSel();
      refresh();
      return;
    }
    const folderNote = folder_ids.length ? " Folders are removed, and everything inside them goes to the trash." : "";
    const ok = await dialog.confirm({ title: `Move ${plural(n, "item")} to the trash?`, desc: `You can restore them from the Trash for ${settings?.trash_retention_days ?? 30} days.${folderNote}`, ok: "Move to trash", danger: true });
    if (!ok) return;
    try {
      const r = await api.post<BulkResult>("/entries/delete", { file_ids, folder_ids });
      report(r, "Deleted");
      if (r.done) toast.ok(`Moved ${plural(r.done, "item")} to the trash.`, file_ids.length && !folder_ids.length ? {
        label: "Undo",
        run: async () => {
          await api.post("/files/restore", { ids: file_ids });
          toast.ok("Restored.");
          refresh();
        },
      } : undefined);
    } catch (e) {
      toast.error(e);
    }
    clearSel();
    refresh();
  };

  const doRestore = async (ids = selectedFiles) => {
    try {
      const r = await api.post<BulkResult>("/files/restore", { ids });
      report(r, "Restored");
      if (r.done) toast.ok(`Restored ${plural(r.done, "file")} to where they were.`);
    } catch (e) {
      toast.error(e);
    }
    clearSel();
    refresh();
  };

  const emptyTrash = async () => {
    const ok = await dialog.confirm({ title: "Empty the trash?", desc: `${plural(data?.total ?? 0, "file")} will be deleted forever.`, ok: "Empty trash", danger: true });
    if (!ok) return;
    try {
      await api.post("/trash/empty");
      toast.ok("Trash emptied.");
    } catch (e) {
      toast.error(e);
    }
    refresh();
  };

  const newFolder = async (parent = folderId) => {
    if (parent == null) return;
    const name = await dialog.prompt({ title: "New folder", ok: "Create", placeholder: "e.g. Best of 2026" });
    if (!name) return;
    try {
      await api.post("/folders", { parent_id: parent, name });
      toast.ok(`Created “${name}”.`);
      refresh();
    } catch (e) {
      toast.error(e);
    }
  };

  const rename = async (key: Key) => {
    const [kind, idStr] = key.split(":");
    const id = Number(idStr);
    const current = kind === "f" ? items.find((f) => f.id === id)?.name : (subfolders.find((f) => f.id === id) ?? folders.find((f) => f.id === id))?.name;
    if (!current) return;
    const name = await dialog.prompt({ title: kind === "f" ? "Rename file" : "Rename folder", value: current, ok: "Rename", selectStem: kind === "f" });
    if (!name || name === current) return;
    try {
      await api.patch(kind === "f" ? `/files/${id}` : `/folders/${id}`, { name });
      toast.ok("Renamed.");
      refresh();
    } catch (e) {
      toast.error(e);
    }
  };

  const setFavorite = async (ids: number[], value: boolean) => {
    try {
      await api.post("/files/favorite", { ids, value });
      setItems((prev) => prev.map((f) => (ids.includes(f.id) ? { ...f, favorite: value ? 1 : 0 } : f)));
      toast.ok(value ? "Added to favorites." : "Removed from favorites.");
    } catch (e) {
      toast.error(e);
    }
  };

  const setTypeFor = async (ids: number[], t: AnyType) => {
    try {
      const r = await api.post<BulkResult>("/files/set-type", { ids, media_type: t });
      report(r, "Changed");
      if (r.done) toast.ok(`Changed ${plural(r.done, "file")} to ${TYPE_LABEL_PLURAL[t].toLowerCase()}.`);
    } catch (e) {
      toast.error(e);
    }
    setTypeDialog(null);
    clearSel();
    refresh();
  };

  const download = (file_ids = selectedFiles, folder_ids = selectedFolders) => {
    if (file_ids.length === 1 && !folder_ids.length) {
      window.location.href = `/api/files/${file_ids[0]}/content?download=1`;
      return;
    }
    const name = folder_ids.length === 1 && !file_ids.length ? folders.find((f) => f.id === folder_ids[0])?.name : "instagram-media";
    window.location.href = zipUrl(file_ids, folder_ids, name || "Library");
    toast.ok("Preparing your download...");
  };

  const doUpload = async (files: File[], target = folderId) => {
    if (!files.length || target == null) return;
    setUpload({ count: files.length, progress: 0 });
    try {
      const r = await uploadFiles(target, files, (p) => setUpload({ count: files.length, progress: p }));
      report(r, "Uploaded");
      if (r.done) toast.ok(`Added ${plural(r.done, "file")}.`);
    } catch (e) {
      toast.error(e);
    } finally {
      setUpload(null);
      refresh();
    }
  };

  // ----- keyboard shortcuts ---------------------------------------------------
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (lightbox !== null || picker || typeDialog || colorDialog || document.querySelector(".overlay")) return;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key.toLowerCase() === "a") { e.preventDefault(); selectAll(); }
      else if (mod && e.key.toLowerCase() === "c" && view !== "trash") doCopy("copy");
      else if (mod && e.key.toLowerCase() === "x" && view !== "trash") doCopy("cut");
      else if (mod && e.key.toLowerCase() === "v" && view === "folder") doPaste();
      else if (e.key === "Delete" || (e.key === "Backspace" && mod)) doDelete();
      else if (e.key === "F2" && sel.size === 1) rename([...sel][0]);
      else if (e.key === "Escape") clearSel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  // ----- drag & drop ------------------------------------------------------------
  const dragStart = (key: Key, e: DragEvent) => {
    let keys = sel;
    if (!sel.has(key)) {
      keys = new Set([key]);
      setSel(keys);
    }
    const file_ids = [...keys].filter((k) => k.startsWith("f:")).map((k) => Number(k.slice(2)));
    const folder_ids = [...keys].filter((k) => k.startsWith("d:")).map((k) => Number(k.slice(2)));
    e.dataTransfer.setData(DRAG_MIME, JSON.stringify({ file_ids, folder_ids }));
    e.dataTransfer.effectAllowed = "copyMove";
  };
  const dropOn = (target: number, e: DragEvent) => {
    setDropKey(null);
    setOsDrag(false);
    const raw = e.dataTransfer.getData(DRAG_MIME);
    if (raw) {
      const { file_ids, folder_ids } = JSON.parse(raw) as { file_ids: number[]; folder_ids: number[] };
      const fids = folder_ids.filter((id) => id !== target);
      moveEntries(file_ids, fids, target, { copy: e.ctrlKey || e.altKey });
    } else if (e.dataTransfer.files.length) {
      doUpload(Array.from(e.dataTransfer.files), target);
    }
  };
  const tileDropProps = (folder: Folder) => ({
    onDragOver: (e: DragEvent) => {
      if (sel.has(dk(folder.id))) return;
      e.preventDefault();
      e.stopPropagation();
      setDropKey(dk(folder.id));
    },
    onDragLeave: () => setDropKey((k) => (k === dk(folder.id) ? null : k)),
    onDrop: (e: DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      dropOn(folder.id, e);
    },
  });

  // ----- context menus -----------------------------------------------------------
  const fileMenu = (f: FileItem, e: MouseEvent) => {
    e.preventDefault();
    const key = fk(f.id);
    let keys = sel;
    if (!sel.has(key)) {
      keys = new Set([key]);
      setSel(keys);
    }
    const files = [...keys].filter((k) => k.startsWith("f:")).map((k) => Number(k.slice(2)));
    const dirs = [...keys].filter((k) => k.startsWith("d:")).map((k) => Number(k.slice(2)));
    const n = files.length + dirs.length;
    const idx = items.findIndex((x) => x.id === f.id);
    const menuItems: MenuItem[] = view === "trash"
      ? [
          { label: "Open", icon: FolderOpen, onClick: () => setLightbox(idx) },
          { label: n > 1 ? `Restore ${n} files` : "Restore", icon: RotateCcw, onClick: () => doRestore(files) },
          { sep: true },
          { label: "Delete forever", icon: Trash2, danger: true, onClick: () => doDelete(files, []) },
        ]
      : [
          { label: "Open", icon: FolderOpen, onClick: () => setLightbox(idx), disabled: n > 1 },
          ...(view !== "folder" && f.folder_id != null ? [{ label: "Show in folder", icon: FolderTreeIcon, onClick: () => go(`library/${f.folder_id}`) }] : []),
          { sep: true },
          { label: "Copy", icon: Copy, shortcut: "Ctrl+C", onClick: () => doCopy("copy", files, dirs) },
          { label: "Cut", icon: Scissors, shortcut: "Ctrl+X", onClick: () => doCopy("cut", files, dirs) },
          { label: "Move to...", icon: FolderInput, onClick: () => setPicker({ mode: "move", file_ids: files, folder_ids: dirs }) },
          { label: "Copy to...", icon: CopyPlus, onClick: () => setPicker({ mode: "copy", file_ids: files, folder_ids: dirs }) },
          { sep: true },
          { label: "Change type...", icon: Shapes, onClick: () => setTypeDialog(files), disabled: !files.length },
          { label: f.favorite && n === 1 ? "Remove from favorites" : "Add to favorites", icon: Heart, onClick: () => setFavorite(files, !(f.favorite && n === 1)) },
          { label: "Rename", icon: Pencil, shortcut: "F2", onClick: () => rename(key), disabled: n > 1 },
          { label: n > 1 ? `Download ${n} items` : "Download", icon: Download, onClick: () => download(files, dirs) },
          { sep: true },
          { label: "Move to trash", icon: Trash2, shortcut: "Del", danger: true, onClick: () => doDelete(files, dirs) },
        ];
    setMenu({ x: e.clientX, y: e.clientY, items: menuItems });
  };

  const folderMenu = (folder: Folder, e: MouseEvent, fromTree = false) => {
    e.preventDefault();
    e.stopPropagation();
    let files: number[] = [];
    let dirs = [folder.id];
    if (!fromTree) {
      const key = dk(folder.id);
      let keys = sel;
      if (!sel.has(key)) {
        keys = new Set([key]);
        setSel(keys);
      }
      files = [...keys].filter((k) => k.startsWith("f:")).map((k) => Number(k.slice(2)));
      dirs = [...keys].filter((k) => k.startsWith("d:")).map((k) => Number(k.slice(2)));
    }
    const isRoot = folder.role === "root";
    const n = files.length + dirs.length;
    setMenu({
      x: e.clientX,
      y: e.clientY,
      items: [
        { label: "Open", icon: FolderOpen, onClick: () => go(`library/${folder.id}`) },
        { label: "New folder inside", icon: FolderPlus, onClick: () => newFolder(folder.id) },
        { label: clip ? `Paste ${plural(clip.file_ids.length + clip.folder_ids.length, "item")} here` : "Paste here", icon: ClipboardPaste, onClick: () => doPaste(folder.id), disabled: !clip },
        { sep: true },
        { label: "Copy", icon: Copy, onClick: () => doCopy("copy", files, dirs), disabled: isRoot },
        { label: "Cut", icon: Scissors, onClick: () => doCopy("cut", files, dirs), disabled: isRoot },
        { label: "Move to...", icon: FolderInput, onClick: () => setPicker({ mode: "move", file_ids: files, folder_ids: dirs }), disabled: isRoot },
        { label: "Copy to...", icon: CopyPlus, onClick: () => setPicker({ mode: "copy", file_ids: files, folder_ids: dirs }), disabled: isRoot },
        { label: "Rename", icon: Pencil, onClick: () => rename(dk(folder.id)), disabled: isRoot || n > 1 },
        { label: "Color...", icon: Palette, onClick: () => setColorDialog(folder), disabled: isRoot || folder.role !== "user" },
        { label: "Download as zip", icon: Download, onClick: () => download(files, dirs) },
        { sep: true },
        { label: n > 1 ? `Delete ${n} items` : "Delete folder", icon: Trash2, danger: true, onClick: () => doDelete(files, dirs), disabled: isRoot },
      ],
    });
  };

  const backgroundMenu = (e: MouseEvent) => {
    if (view !== "folder") return;
    e.preventDefault();
    setMenu({
      x: e.clientX,
      y: e.clientY,
      items: [
        { label: "New folder", icon: FolderPlus, onClick: () => newFolder() },
        { label: "Upload files...", icon: Upload, onClick: () => fileInput.current?.click() },
        { label: clip ? `Paste ${plural(clip.file_ids.length + clip.folder_ids.length, "item")}` : "Paste", icon: ClipboardPaste, shortcut: "Ctrl+V", onClick: () => doPaste(), disabled: !clip },
        { sep: true },
        { label: "Select all", icon: CheckSquare, shortcut: "Ctrl+A", onClick: selectAll },
      ],
    });
  };

  // ----- derived bits -------------------------------------------------------------
  const crumbs = data?.breadcrumbs ?? [];
  const currentFolder = data?.folder;
  const viewInfo = VIEWS.find((v) => v.key === view);
  const cutIds = clip?.mode === "cut" ? new Set([...clip.file_ids.map(fk), ...clip.folder_ids.map(dk)]) : null;
  const total = data?.total ?? 0;
  const pickerDisabled = useMemo(() => {
    if (!picker || picker.mode !== "move") return new Set<number>();
    // can't move a folder into itself or its children
    const kids = new Map<number, number[]>();
    folders.forEach((f) => f.parent_id != null && kids.set(f.parent_id, [...(kids.get(f.parent_id) ?? []), f.id]));
    const out = new Set<number>();
    const walk = (id: number) => {
      out.add(id);
      (kids.get(id) ?? []).forEach(walk);
    };
    picker.folder_ids.forEach(walk);
    return out;
  }, [picker, folders]);

  const emptyState = () => {
    if (q) return <Empty icon={Search} title="Nothing found">Nothing matches “{q}”{type ? ` in ${TYPE_LABEL_PLURAL[type].toLowerCase()}` : ""}. Try another word.</Empty>;
    switch (view) {
      case "trash": return <Empty icon={Trash2} title="The trash is empty">Deleted files wait here for {settings?.trash_retention_days ?? 30} days before they disappear.</Empty>;
      case "favorites": return <Empty icon={Heart} title="No favorites yet">Open a photo or video and tap the heart to keep it here.</Empty>;
      case "duplicates": return <Empty icon={Copy} title="No duplicates">Every file in your library is unique. Nice and tidy!</Empty>;
      case "recent":
      case "all": return <Empty icon={Images} title="No media yet" action={<Button variant="primary" onClick={() => go("chats")}>Choose chats to save</Button>}>Once you sync a chat, its photos, videos, posts and reels show up here.</Empty>;
      default:
        return (
          <Empty icon={FolderOpen} title="This folder is empty" action={<div className="row"><Button icon={Upload} onClick={() => fileInput.current?.click()}>Upload files</Button><Button icon={FolderPlus} onClick={() => newFolder()}>New folder</Button></div>}>
            Drag photos here from your computer, paste files you copied, or create subfolders to organise things.
          </Empty>
        );
    }
  };

  // ----- render ---------------------------------------------------------------------
  return (
    <div className="lib">
      <aside className={`lib-side ${sideOpen ? "open" : ""}`}>
        <h4>Views</h4>
        {VIEWS.map((v) => (
          <button key={v.key} className={`side-item ${view === v.key ? "active" : ""}`} onClick={() => go(`library/view/${v.key}`)}>
            <v.icon size={17} />
            {v.label}
            {v.key === "all" && <span className="count">{num(status?.library.files)}</span>}
            {v.key === "trash" && !!status?.trash && <span className="count">{num(status.trash)}</span>}
          </button>
        ))}
        <h4 className="row" style={{ justifyContent: "space-between" }}>
          Folders
          <Button size="sm" variant="ghost" iconOnly icon={FolderPlus} title="New folder" onClick={() => newFolder(folderId ?? rootId)} style={{ height: 24, width: 24 }} />
        </h4>
        {rootId != null && (
          <FolderTree folders={folders} rootId={rootId} activeId={folderId} onOpen={(id) => go(`library/${id}`)} onDrop={dropOn} onMenu={(f, e) => folderMenu(f, e, true)} />
        )}
        <p className="hint" style={{ padding: "16px 10px 0" }}>
          Tip: drag files onto a folder to move them. Hold <span className="kbd">Ctrl</span> while dropping to copy instead.
        </p>
      </aside>

      <section
        className="lib-main"
        onDragOver={(e) => {
          if (view === "folder" && e.dataTransfer.types.includes("Files")) {
            e.preventDefault();
            setOsDrag(true);
          }
        }}
        onDragLeave={(e) => {
          if (e.currentTarget === e.target) setOsDrag(false);
        }}
        onDrop={(e) => {
          if (view === "folder" && folderId != null && e.dataTransfer.files.length) {
            e.preventDefault();
            setOsDrag(false);
            doUpload(Array.from(e.dataTransfer.files));
          }
        }}
      >
        <div className="lib-head">
          <div className="row" style={{ minWidth: 0 }}>
            <Button className="only-mobile" size="sm" icon={FolderTreeIcon} onClick={() => setSideOpen(!sideOpen)}>Folders</Button>
            {view === "folder" ? (
              <nav className="crumbs" aria-label="Folder path">
                {crumbs.map((c, i) => (
                  <span key={c.id} className="row" style={{ gap: 2, minWidth: 0 }}>
                    {i > 0 && <ChevronRight size={18} className="sep" />}
                    <button
                      className={dropKey === `c:${c.id}` ? "drop" : ""}
                      onClick={() => go(`library/${c.id}`)}
                      onDragOver={(e) => { e.preventDefault(); setDropKey(`c:${c.id}`); }}
                      onDragLeave={() => setDropKey(null)}
                      onDrop={(e) => { e.preventDefault(); dropOn(c.id, e); }}
                    >
                      {c.name}
                    </button>
                  </span>
                ))}
              </nav>
            ) : (
              <div className="crumbs">
                {viewInfo && <viewInfo.icon size={22} style={{ marginRight: 8 }} />}
                {viewInfo?.label}
              </div>
            )}
            <span className="spacer" />
            <span className="faint small" style={{ whiteSpace: "nowrap" }}>
              {loading ? "" : `${view === "folder" ? `${plural(subfolders.length, "folder")} · ` : ""}${plural(total, "file")}`}
            </span>
          </div>

          {currentFolder && (currentFolder.role === "chat" || currentFolder.role === "type") && view === "folder" && (
            <div className="callout callout-accent small" style={{ padding: "8px 12px" }}>
              <FolderGlyph folder={currentFolder} size={16} />
              <span>
                {currentFolder.role === "chat" ? "New media from this chat is saved in here." : `New ${TYPE_LABEL_PLURAL[currentFolder.media_type ?? "photo"].toLowerCase()} from the chat land in this folder. Files you drop here become ${TYPE_LABEL_PLURAL[currentFolder.media_type ?? "photo"].toLowerCase()}.`}
              </span>
            </div>
          )}

          <div className="toolbar">
            {view === "folder" && (
              <>
                <Button size="sm" icon={FolderPlus} onClick={() => newFolder()}>New folder</Button>
                <Button size="sm" icon={Upload} onClick={() => fileInput.current?.click()}>Upload</Button>
                {clip && (
                  <Button size="sm" variant="primary" icon={ClipboardPaste} onClick={() => doPaste()}>
                    Paste {clip.file_ids.length + clip.folder_ids.length}
                  </Button>
                )}
              </>
            )}
            {view === "trash" && total > 0 && <Button size="sm" variant="danger" icon={Trash2} onClick={emptyTrash}>Empty trash</Button>}
            <div className="row-wrap" style={{ gap: 6 }}>
              <span className={`chip ${type === "" ? "on" : ""}`} onClick={() => setType("")}>All</span>
              {(["photo", "video", "post", "reel"] as MediaType[]).map((t) => (
                <span key={t} className={`chip ${type === t ? "on" : ""}`} onClick={() => setType(type === t ? "" : t)}>
                  <span className="dot" style={{ background: TYPE_COLOR[t] }} />
                  {TYPE_LABEL_PLURAL[t]}
                </span>
              ))}
            </div>
            <span className="spacer" />
            <div className="input-wrap" style={{ width: 220 }}>
              <Search size={16} />
              <input className="input input-sm" placeholder="Search names, people, chats" value={q} onChange={(e) => setQ(e.target.value)} />
              {q && <Button size="sm" variant="ghost" iconOnly icon={X} className="input-action" style={{ height: 26, width: 26 }} onClick={() => setQ("")} aria-label="Clear search" />}
            </div>
            {view === "folder" && (
              <Button size="sm" variant={recursive ? "primary" : "default"} icon={FolderTreeIcon} onClick={() => setRecursive(!recursive)} title="Also show files inside subfolders">
                {recursive ? "Incl. subfolders" : "This folder only"}
              </Button>
            )}
            {view !== "recent" && view !== "duplicates" && (
              <div className="row" style={{ gap: 4 }}>
                <select className="select input-sm" style={{ width: 150 }} value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Sort by">
                  <option value="sent">Date sent</option>
                  <option value="date">Date saved</option>
                  <option value="name">Name</option>
                  <option value="size">Size</option>
                  <option value="type">Type</option>
                </select>
                <Button size="sm" iconOnly icon={ArrowDownUp} onClick={() => setOrder(order === "asc" ? "desc" : "asc")} title={order === "asc" ? "Oldest / A-Z first" : "Newest / Z-A first"} />
              </div>
            )}
            <Seg value={layout} onChange={setLayout} options={[{ value: "grid", label: "", icon: LayoutGrid }, { value: "list", label: "", icon: List }]} />
          </div>
        </div>

        <div className="lib-scroll" onClick={(e) => e.target === e.currentTarget && clearSel()} onContextMenu={(e) => e.target === e.currentTarget && backgroundMenu(e)}>
          {loading && !data ? (
            <div className="empty"><Spinner size={28} /></div>
          ) : !subfolders.length && !items.length ? (
            <div onContextMenu={backgroundMenu}>{emptyState()}</div>
          ) : layout === "grid" ? (
            <div className={selecting ? "selecting" : ""} onContextMenu={(e) => e.target === e.currentTarget && backgroundMenu(e)}>
              {subfolders.length > 0 && (
                <>
                  <div className="section-label">Folders</div>
                  <div className={`grid size-${size}`} style={{ marginBottom: 26 }}>
                    {subfolders.map((f) => {
                      const key = dk(f.id);
                      return (
                        <div
                          key={f.id}
                          className={`folder-tile ${sel.has(key) ? "selected" : ""} ${dropKey === key ? "drop" : ""} ${cutIds?.has(key) ? "cut" : ""}`}
                          draggable
                          onDragStart={(e) => dragStart(key, e)}
                          {...tileDropProps(f)}
                          onClick={(e) => clickSelect(key, e, () => go(`library/${f.id}`))}
                          onDoubleClick={() => go(`library/${f.id}`)}
                          onContextMenu={(e) => folderMenu(f, e)}
                          title={f.name}
                        >
                          <button className="select-dot" onClick={(e) => { e.stopPropagation(); toggle(key); }} aria-label="Select">
                            {sel.has(key) && <CheckSquare size={14} />}
                          </button>
                          <div className="folder-cover">
                            {f.cover_id ? <img src={thumbUrl(f.cover_id)} alt="" loading="lazy" onError={(e) => (e.currentTarget.style.display = "none")} /> : <FolderIcon size={34} />}
                            <span className="folder-icon-big"><FolderGlyph folder={f} size={16} /></span>
                          </div>
                          <div>
                            <div className="folder-name ellipsis">{f.name}</div>
                            <div className="folder-sub">
                              {plural(f.file_count, "file")}
                              {f.folder_count ? ` · ${plural(f.folder_count, "folder")}` : ""}
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </>
              )}
              {items.length > 0 && (
                <>
                  {subfolders.length > 0 && <div className="section-label">Files</div>}
                  <div className={`grid size-${size}`}>
                    {items.map((f, i) => {
                      const key = fk(f.id);
                      return (
                        <div
                          key={f.id}
                          className={`thumb-tile ${sel.has(key) ? "selected" : ""} ${cutIds?.has(key) ? "cut" : ""}`}
                          draggable={view !== "trash"}
                          onDragStart={(e) => dragStart(key, e)}
                          onClick={(e) => clickSelect(key, e, () => setLightbox(i))}
                          onDoubleClick={() => setLightbox(i)}
                          onContextMenu={(e) => fileMenu(f, e)}
                          title={`${f.name}${f.sender ? ` · from ${f.sender}` : ""}`}
                        >
                          <Thumb file={f} />
                          <div className="tile-shade" />
                          <button className="select-dot" onClick={(e) => { e.stopPropagation(); toggle(key); }} aria-label="Select">
                            {sel.has(key) && <CheckSquare size={14} />}
                          </button>
                          <TypeTag type={f.media_type} />
                          {!!f.favorite && <Heart size={18} className="fav-tag" fill="currentColor" />}
                          <div className="tile-name ellipsis">{f.sender ? `${f.sender} · ${shortDate(f.sent_at)}` : f.name}</div>
                        </div>
                      );
                    })}
                  </div>
                </>
              )}
            </div>
          ) : (
            <table className="list">
              <thead>
                <tr>
                  <th style={{ width: 36 }} />
                  <th>Name</th>
                  <th>Type</th>
                  <th>From</th>
                  <th>{view === "trash" ? "Was in" : "Chat"}</th>
                  <th>Date</th>
                  <th style={{ textAlign: "right" }}>Size</th>
                </tr>
              </thead>
              <tbody>
                {subfolders.map((f) => {
                  const key = dk(f.id);
                  return (
                    <tr
                      key={key}
                      className={`${sel.has(key) ? "selected" : ""} ${dropKey === key ? "drop" : ""} ${cutIds?.has(key) ? "cut" : ""}`}
                      draggable
                      onDragStart={(e) => dragStart(key, e)}
                      {...tileDropProps(f)}
                      onClick={(e) => clickSelect(key, e, () => go(`library/${f.id}`))}
                      onContextMenu={(e) => folderMenu(f, e)}
                    >
                      <td><input type="checkbox" checked={sel.has(key)} onChange={() => toggle(key)} onClick={(e) => e.stopPropagation()} aria-label="Select" /></td>
                      <td><div className="row"><span className="mini"><FolderGlyph folder={f} size={18} /></span><span className="strong ellipsis">{f.name}</span></div></td>
                      <td className="muted">Folder</td>
                      <td />
                      <td />
                      <td className="muted">{plural(f.file_count, "file")}</td>
                      <td />
                    </tr>
                  );
                })}
                {items.map((f, i) => {
                  const key = fk(f.id);
                  const Icon = TYPE_ICON[f.media_type];
                  return (
                    <tr
                      key={key}
                      className={`${sel.has(key) ? "selected" : ""} ${cutIds?.has(key) ? "cut" : ""}`}
                      draggable={view !== "trash"}
                      onDragStart={(e) => dragStart(key, e)}
                      onClick={(e) => clickSelect(key, e, () => setLightbox(i))}
                      onDoubleClick={() => setLightbox(i)}
                      onContextMenu={(e) => fileMenu(f, e)}
                    >
                      <td><input type="checkbox" checked={sel.has(key)} onChange={() => toggle(key)} onClick={(e) => e.stopPropagation()} aria-label="Select" /></td>
                      <td>
                        <div className="row" style={{ minWidth: 0 }}>
                          <span className="mini"><Thumb file={f} small /></span>
                          <span className="ellipsis" style={{ maxWidth: 360 }}>{f.name}</span>
                          {!!f.favorite && <Heart size={14} fill="var(--accent-2)" color="var(--accent-2)" />}
                        </div>
                      </td>
                      <td><span className="row" style={{ gap: 6, color: TYPE_COLOR[f.media_type] }}><Icon size={15} /> {TYPE_LABEL[f.media_type]}</span></td>
                      <td className="muted">{f.sender ?? ""}</td>
                      <td className="muted ellipsis" style={{ maxWidth: 200 }}>{view === "trash" ? f.trash_origin || "Library" : f.chat_title ?? ""}</td>
                      <td className="muted" style={{ whiteSpace: "nowrap" }}>{shortDate(f.sent_at ?? f.created_at)}</td>
                      <td className="muted" style={{ textAlign: "right", whiteSpace: "nowrap" }}>{bytes(f.size)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
          <div ref={sentinel} style={{ height: 1 }} />
          {loadingMore && <div className="empty" style={{ padding: 20 }}><Spinner /></div>}
        </div>

        {osDrag && <div className="dropzone-overlay"><div className="stack-sm" style={{ alignItems: "center" }}><Upload size={40} />Drop to add to this folder</div></div>}

        {selecting && (
          <div className="selbar" role="toolbar" aria-label="Selection actions">
            <span className="count">{sel.size} selected</span>
            <div className="vsep" />
            {view === "trash" ? (
              <>
                <Button size="sm" icon={RotateCcw} onClick={() => doRestore()}>Restore</Button>
                <Button size="sm" variant="danger" icon={Trash2} onClick={() => doDelete()}>Delete forever</Button>
              </>
            ) : (
              <>
                <Button size="sm" icon={Copy} onClick={() => doCopy("copy")} title="Copy (Ctrl+C)">Copy</Button>
                <Button size="sm" icon={Scissors} onClick={() => doCopy("cut")} title="Cut (Ctrl+X)">Cut</Button>
                <Button size="sm" icon={FolderInput} onClick={() => setPicker({ mode: "move", file_ids: selectedFiles, folder_ids: selectedFolders })}>Move to</Button>
                <Button size="sm" icon={CopyPlus} onClick={() => setPicker({ mode: "copy", file_ids: selectedFiles, folder_ids: selectedFolders })}>Copy to</Button>
                {selectedFiles.length > 0 && <Button size="sm" icon={Shapes} onClick={() => setTypeDialog(selectedFiles)}>Type</Button>}
                {selectedFiles.length > 0 && <Button size="sm" icon={Heart} onClick={() => setFavorite(selectedFiles, true)}>Favorite</Button>}
                <Button size="sm" icon={Download} onClick={() => download()}>Download</Button>
                <Button size="sm" variant="danger" icon={Trash2} onClick={() => doDelete()} title="Delete (Del)">Delete</Button>
              </>
            )}
            <div className="vsep" />
            <Button size="sm" iconOnly icon={X} onClick={clearSel} aria-label="Clear selection" />
          </div>
        )}
      </section>

      <input ref={fileInput} type="file" multiple hidden accept="image/*,video/*" onChange={(e) => { doUpload(Array.from(e.target.files ?? [])); e.target.value = ""; }} />

      {upload && (
        <div className="card upload-panel stack-sm">
          <div className="row"><Upload size={17} /><strong>Uploading {plural(upload.count, "file")}...</strong></div>
          <Progress value={upload.progress * 100} total={100} />
        </div>
      )}

      {menu && <ContextMenu x={menu.x} y={menu.y} items={menu.items} onClose={() => setMenu(null)} />}

      {picker && (
        <FolderPicker
          title={picker.mode === "move" ? `Move ${plural(picker.file_ids.length + picker.folder_ids.length, "item")} to...` : `Copy ${plural(picker.file_ids.length + picker.folder_ids.length, "item")} to...`}
          desc="Pick a folder, or create a new one."
          okLabel={picker.mode === "move" ? "Move here" : "Copy here"}
          initialId={folderId}
          disabledIds={pickerDisabled}
          onClose={() => setPicker(null)}
          onPick={async (target) => {
            await moveEntries(picker.file_ids, picker.folder_ids, target, { copy: picker.mode === "copy" });
            setPicker(null);
          }}
        />
      )}

      {typeDialog && (
        <Modal title={`Change type of ${plural(typeDialog.length, "file")}`} desc="Files inside a chat's Photos/Videos/Posts/Reels folders also move to the matching folder." onClose={() => setTypeDialog(null)}>
          <div className="grid-2">
            {(["photo", "video", "post", "reel"] as MediaType[]).map((t) => {
              const Icon = TYPE_ICON[t];
              return (
                <button key={t} className="method-card" onClick={() => setTypeFor(typeDialog, t)}>
                  <span className="t"><Icon size={18} style={{ color: TYPE_COLOR[t] }} /> {TYPE_LABEL[t]}</span>
                </button>
              );
            })}
          </div>
        </Modal>
      )}

      {colorDialog && (
        <Modal title={`Color for “${colorDialog.name}”`} onClose={() => setColorDialog(null)}>
          <div className="row-wrap">
            {["", ...Object.keys(FOLDER_COLORS)].map((c) => (
              <button
                key={c || "none"}
                className="btn btn-icon"
                style={{ background: c ? FOLDER_COLORS[c] : undefined, borderColor: colorDialog.color === c ? "var(--text)" : undefined, borderWidth: 2 }}
                title={c || "No color"}
                onClick={async () => {
                  await api.patch(`/folders/${colorDialog.id}`, { color: c }).catch(toast.error);
                  setColorDialog(null);
                  refresh();
                }}
              >
                {!c && <X size={16} />}
              </button>
            ))}
          </div>
        </Modal>
      )}

      {lightbox !== null && items[lightbox] && (
        <Lightbox
          items={items}
          index={lightbox}
          onIndex={setLightbox}
          onClose={() => setLightbox(null)}
          onChanged={(f) => setItems((prev) => prev.map((x) => (x.id === f.id ? { ...x, ...f } : x)))}
          onRemoved={(id) => {
            const rest = items.filter((x) => x.id !== id);
            setItems(rest);
            if (!rest.length) setLightbox(null);
            else setLightbox(Math.min(lightbox, rest.length - 1));
            reloadTree();
            refreshStatus();
          }}
        />
      )}
    </div>
  );
}

function Thumb({ file, small }: { file: FileItem; small?: boolean }) {
  const [failed, setFailed] = useState(false);
  const Icon = TYPE_ICON[file.media_type];
  if (failed || file.media_type === "other" && !file.mime?.startsWith("image/")) {
    return (
      <div className="thumb-fallback">
        <div className="stack-sm" style={{ alignItems: "center" }}>
          <Icon size={small ? 18 : 30} />
          {!small && <span>{file.name}</span>}
        </div>
      </div>
    );
  }
  return <img src={thumbUrl(file.id)} alt={file.name} loading="lazy" decoding="async" draggable={false} onError={() => setFailed(true)} />;
}
