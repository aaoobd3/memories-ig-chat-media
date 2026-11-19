import type { AnyType } from "../api";

export function bytes(n: number | null | undefined): string {
  if (!n) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v >= 100 || i === 0 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
}

export function num(n: number | null | undefined): string {
  return (n ?? 0).toLocaleString();
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${num(n)} ${n === 1 ? one : many}`;
}

export function parseDate(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function ago(iso: string | null | undefined): string {
  const d = parseDate(iso);
  if (!d) return "never";
  const s = Math.round((Date.now() - d.getTime()) / 1000);
  if (s < 0) return inTime(iso);
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  if (s < 86400 * 7) return `${Math.round(s / 86400)} days ago`;
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

export function inTime(iso: string | null | undefined): string {
  const d = parseDate(iso);
  if (!d) return "";
  const s = Math.round((d.getTime() - Date.now()) / 1000);
  if (s <= 30) return "any moment now";
  if (s < 3600) return `in ${Math.max(1, Math.round(s / 60))} min`;
  return `at ${d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}`;
}

export function dateTime(iso: string | null | undefined): string {
  const d = parseDate(iso);
  if (!d) return "";
  return d.toLocaleString(undefined, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function shortDate(iso: string | null | undefined): string {
  const d = parseDate(iso);
  if (!d) return "";
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

export function timeOnly(iso: string): string {
  const d = parseDate(iso);
  return d ? d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "";
}

export const TYPE_LABEL: Record<AnyType, string> = {
  photo: "Photo",
  video: "Video",
  post: "Post",
  reel: "Reel",
  other: "File",
};
export const TYPE_LABEL_PLURAL: Record<AnyType, string> = {
  photo: "Photos",
  video: "Videos",
  post: "Posts",
  reel: "Reels",
  other: "Other files",
};
export const TYPE_COLOR: Record<AnyType, string> = {
  photo: "var(--c-photo)",
  video: "var(--c-video)",
  post: "var(--c-post)",
  reel: "var(--c-reel)",
  other: "var(--text-3)",
};

const AVATAR_GRADIENTS = [
  "linear-gradient(135deg,#f59e0b,#ec4899)",
  "linear-gradient(135deg,#8b5cf6,#ec4899)",
  "linear-gradient(135deg,#06b6d4,#8b5cf6)",
  "linear-gradient(135deg,#10b981,#06b6d4)",
  "linear-gradient(135deg,#f97316,#ef4444)",
  "linear-gradient(135deg,#6366f1,#0ea5e9)",
];

export function avatarStyle(seed: string): { background: string } {
  let h = 0;
  for (const ch of seed) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return { background: AVATAR_GRADIENTS[h % AVATAR_GRADIENTS.length] };
}

export function initials(text: string): string {
  const words = text.replace(/[^\p{L}\p{N} ]/gu, " ").trim().split(/\s+/).filter(Boolean);
  if (!words.length) return "#";
  return (words[0][0] + (words[1]?.[0] ?? "")).toUpperCase();
}

export function isVideo(mime: string | null | undefined): boolean {
  return !!mime && mime.startsWith("video/");
}
export function isImage(mime: string | null | undefined): boolean {
  return !!mime && mime.startsWith("image/");
}
