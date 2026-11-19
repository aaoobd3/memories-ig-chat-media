import { useEffect, useState } from "react";
import { Calendar, ChevronLeft, ChevronRight, Copy, Download, ExternalLink, FileText, FolderOpen, HardDrive, Heart, MessagesSquare, RotateCcw, Trash2, User, X } from "lucide-react";
import { api, contentUrl, type AnyType, type FileItem } from "../api";
import { bytes, dateTime, isImage, isVideo, TYPE_LABEL } from "../lib/format";
import { go, useToast } from "../lib/app";
import { Button, Seg, TYPE_ICON } from "./ui";

export function Lightbox({ items, index, onIndex, onClose, onChanged, onRemoved }: {
  items: FileItem[];
  index: number;
  onIndex: (i: number) => void;
  onClose: () => void;
  onChanged: (f: FileItem) => void;
  onRemoved: (id: number) => void;
}) {
  const file = items[index];
  const [info, setInfo] = useState<FileItem | null>(null);
  const toast = useToast();

  useEffect(() => {
    setInfo(null);
    if (file) api.get<FileItem>(`/files/${file.id}`).then(setInfo).catch(() => undefined);
  }, [file?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.tagName === "INPUT") return;
      if (e.key === "Escape") onClose();
      else if (e.key === "ArrowLeft" && index > 0) onIndex(index - 1);
      else if (e.key === "ArrowRight" && index < items.length - 1) onIndex(index + 1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [index, items.length, onClose, onIndex]);

  if (!file) return null;
  const meta = info ?? file;
  const trashed = !!file.trashed_at;

  const patch = async (body: Record<string, unknown>, okText: string) => {
    try {
      await api.patch(`/files/${file.id}`, body);
      const fresh = await api.get<FileItem>(`/files/${file.id}`);
      setInfo(fresh);
      onChanged(fresh);
      toast.ok(okText);
    } catch (e) {
      toast.error(e);
    }
  };
  const remove = async () => {
    try {
      if (trashed) await api.post("/files/restore", { ids: [file.id] });
      else await api.post("/entries/delete", { file_ids: [file.id] });
      toast.ok(trashed ? "Restored." : "Moved to the trash.");
      onRemoved(file.id);
    } catch (e) {
      toast.error(e);
    }
  };

  const src = contentUrl(file.id);
  return (
    <div className="lightbox" role="dialog" aria-modal="true">
      <div className="lb-stage" onClick={(e) => e.target === e.currentTarget && onClose()}>
        <div className="lb-top ellipsis">
          {index + 1} of {items.length} · {file.name}
        </div>
        {isVideo(file.mime) ? (
          <video key={file.id} src={src} controls autoPlay playsInline />
        ) : isImage(file.mime) ? (
          <img key={file.id} src={src} alt={file.name} />
        ) : (
          <div className="empty" style={{ color: "#d6d2e6" }}>
            <FileText size={56} />
            <p>No preview for this kind of file.</p>
            <a className="btn" href={contentUrl(file.id, true)}>Download it</a>
          </div>
        )}
        <button className="lb-btn lb-prev" disabled={index === 0} onClick={() => onIndex(index - 1)} aria-label="Previous"><ChevronLeft size={26} /></button>
        <button className="lb-btn lb-next" disabled={index >= items.length - 1} onClick={() => onIndex(index + 1)} aria-label="Next"><ChevronRight size={26} /></button>
        <button className="lb-btn lb-close" onClick={onClose} aria-label="Close"><X size={22} /></button>
      </div>

      <aside className="lb-info">
        <div>
          <div className="tiny" style={{ color: "#8b87a3", fontWeight: 700, textTransform: "uppercase", letterSpacing: ".08em" }}>{TYPE_LABEL[meta.media_type]}</div>
          <h3 style={{ marginTop: 4, wordBreak: "break-word", color: "#fff" }}>{meta.name}</h3>
        </div>

        <div className="meta">
          {meta.sender && (<><User size={16} /><span>Sent by <strong>{meta.sender}</strong></span></>)}
          {meta.chat_title && (<><MessagesSquare size={16} /><span>In <strong>{meta.chat_title}</strong></span></>)}
          {meta.sent_at && (<><Calendar size={16} /><span>{dateTime(meta.sent_at)}</span></>)}
          <HardDrive size={16} /><span>{bytes(meta.size)} · saved {dateTime(meta.created_at)}</span>
          {info?.breadcrumbs && (<><FolderOpen size={16} /><span>{info.breadcrumbs.map((b) => b.name).join(" / ")}</span></>)}
          {trashed && (<><Trash2 size={16} /><span>In the trash (was in {meta.trash_origin || "Library"})</span></>)}
          {!!info?.duplicates && (<><Copy size={16} /><span>{info.duplicates} identical {info.duplicates === 1 ? "copy" : "copies"} elsewhere in the library</span></>)}
        </div>

        {!trashed && (
          <div className="stack-sm">
            <div className="small" style={{ color: "#b4afc8", fontWeight: 600 }}>Type</div>
            <Seg<AnyType>
              value={meta.media_type}
              onChange={(t) => patch({ media_type: t }, `Changed to ${TYPE_LABEL[t].toLowerCase()}.`)}
              options={(["photo", "video", "post", "reel"] as AnyType[]).map((t) => ({ value: t, label: TYPE_LABEL[t], icon: TYPE_ICON[t] }))}
            />
          </div>
        )}

        <div className="stack-sm">
          {!trashed && (
            <Button icon={Heart} onClick={() => patch({ favorite: !meta.favorite }, meta.favorite ? "Removed from favorites." : "Added to favorites.")}
              style={meta.favorite ? { color: "#f472b6", borderColor: "#f472b6" } : undefined}>
              {meta.favorite ? "Favorite" : "Add to favorites"}
            </Button>
          )}
          <a className="btn" href={contentUrl(file.id, true)}><Download size={17} /> Download</a>
          {meta.permalink && meta.permalink.startsWith("http") && (
            <a className="btn" href={meta.permalink} target="_blank" rel="noreferrer noopener"><ExternalLink size={17} /> Open on Instagram</a>
          )}
          {!trashed && meta.folder_id != null && (
            <Button icon={FolderOpen} onClick={() => { onClose(); go(`library/${meta.folder_id}`); }}>Show in folder</Button>
          )}
          <Button icon={trashed ? RotateCcw : Trash2} variant={trashed ? "default" : "danger"} onClick={remove}>
            {trashed ? "Restore" : "Move to trash"}
          </Button>
        </div>
        <p className="tiny" style={{ color: "#7f7a96" }}>Tip: use the ← → arrow keys to flip through, Esc to close.</p>
      </aside>
    </div>
  );
}
