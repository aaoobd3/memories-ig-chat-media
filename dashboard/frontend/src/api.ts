// Typed client for the FastAPI backend.

export type MediaType = "photo" | "video" | "post" | "reel";
export type AnyType = MediaType | "other";

export interface Folder {
  id: number;
  parent_id: number | null;
  name: string;
  role: "root" | "chat" | "type" | "user";
  chat_id: number | null;
  media_type: MediaType | null;
  color: string | null;
  file_count: number;
  bytes?: number;
  folder_count?: number;
  cover_id?: number | null;
}

export interface FileItem {
  id: number;
  folder_id: number | null;
  name: string;
  media_type: AnyType;
  mime: string | null;
  size: number;
  sha256: string | null;
  chat_id: number | null;
  chat_title: string | null;
  sender: string | null;
  sent_at: string | null;
  permalink: string | null;
  favorite: number;
  trashed_at: string | null;
  trash_origin: string | null;
  created_at: string;
  updated_at: string;
  breadcrumbs?: Crumb[];
  duplicates?: number;
}

export interface Crumb { id: number; name: string; role: string }

export interface FileList {
  items: FileItem[];
  total: number;
  offset: number;
  limit: number;
  folder?: Folder;
  breadcrumbs?: Crumb[];
  folders?: Folder[];
}

export interface Chat {
  id: number;
  source: "live" | "export";
  external_id: string;
  title: string;
  is_group: number;
  people: number;
  selected: number;
  auto_sync: number;
  folder_id: number | null;
  folder_label: string | null;
  split_by_type: number;
  types: MediaType[];
  message_limit: number | null;
  export_dir: string | null;
  last_activity: string | null;
  last_synced_at: string | null;
  last_result: { ok: boolean; summary: string; preview?: boolean } | null;
  file_count: number;
  failed_count: number;
  listed_count: number;
}

export interface Job {
  id: number;
  kind: string;
  title: string;
  status: "queued" | "running" | "done" | "failed" | "cancelled" | "interrupted";
  params: Record<string, unknown>;
  stats: Record<string, any>;
  progress: number;
  total: number;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface LogLine { id: number; ts: string; level: "info" | "warn" | "error" | "success"; message: string }

export interface IgStatus {
  state: "disconnected" | "connecting" | "connected" | "needs_code" | "error";
  username: string | null;
  message: string;
  last_ok: string | null;
  session_saved: boolean;
  pending_code: { kind: "two_factor" | "challenge"; message: string; since: string } | null;
  paused_until: string | null;
}

export interface Status {
  instagram: IgStatus;
  has_credentials: boolean;
  session_saved: boolean;
  auto_sync: boolean;
  auto_interval_min: number;
  next_auto_run: string | null;
  active_jobs: Job[];
  last_sync: Job | null;
  library: { files: number; bytes: number; photos: number; videos: number; posts: number; reels: number };
  chats: { total: number; selected: number; auto: number };
  trash: number;
  disk: { free: number; total: number };
  engine: { alive: boolean; pid: number | null; since: string | null; jobs_done: number; max_jobs: number };
  server: { pid: number; mode: string; web_workers: number; max_requests: number; max_requests_jitter: number; engine_max_jobs: number };
}

export interface Settings {
  ig_username: string;
  login_method: "auto" | "password" | "sessionid";
  message_limit: number;
  watch_batch: number;
  auto_sync: boolean;
  auto_interval_min: number;
  default_types: MediaType[];
  since: string;
  include_private: boolean;
  search_threads: number;
  delay_min: number;
  delay_max: number;
  download_shared_from_export: boolean;
  split_by_type: boolean;
  trash_retention_days: number;
  theme: "system" | "light" | "dark";
  grid_size: "small" | "medium" | "large";
}

export interface Account {
  username: string;
  login_method: Settings["login_method"];
  has_password: boolean;
  password_updated: string | null;
  has_sessionid: boolean;
  sessionid_updated: string | null;
  status: IgStatus;
  job_id?: number;
}

export interface BulkResult { done: number; errors: string[] }

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  const init: RequestInit = { method, credentials: "same-origin", headers };
  if (method !== "GET") headers["x-igcm"] = "1";
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    headers["content-type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  let res: Response;
  try {
    res = await fetch(`/api${path}`, init);
  } catch {
    throw new ApiError("Can't reach the app. Is it still running?", 0);
  }
  if (res.status === 401 && !path.startsWith("/auth")) window.dispatchEvent(new Event("igcm:signed-out"));
  if (!res.ok) {
    let message = res.statusText || "Something went wrong.";
    try {
      const data = await res.json();
      if (typeof data.detail === "string") message = data.detail;
      else if (Array.isArray(data.detail)) message = data.detail.map((d: { msg: string }) => d.msg).join(", ");
    } catch {
      /* not JSON */
    }
    throw new ApiError(message, res.status);
  }
  return res.json() as Promise<T>;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {}),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body ?? {}),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body ?? {}),
  del: <T>(path: string) => request<T>("DELETE", path),
};

export function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
}

/** Upload with progress (fetch can't report upload progress). */
export function uploadFiles(folderId: number, files: File[], onProgress: (fraction: number) => void): Promise<BulkResult> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("folder_id", String(folderId));
    files.forEach((f) => form.append("files", f, f.name));
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/files/upload");
    xhr.setRequestHeader("x-igcm", "1");
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => {
      try {
        const data = JSON.parse(xhr.responseText);
        if (xhr.status >= 400) reject(new ApiError(data.detail || "Upload failed.", xhr.status));
        else resolve(data);
      } catch {
        reject(new ApiError("Upload failed.", xhr.status));
      }
    };
    xhr.onerror = () => reject(new ApiError("Upload failed. Check the connection.", 0));
    xhr.send(form);
  });
}

export function uploadExport(file: File, onProgress: (fraction: number) => void): Promise<{ job_id: number }> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("file", file, file.name);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/exports/upload");
    xhr.setRequestHeader("x-igcm", "1");
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => {
      try {
        const data = JSON.parse(xhr.responseText);
        if (xhr.status >= 400) reject(new ApiError(data.detail || "Upload failed.", xhr.status));
        else resolve(data);
      } catch {
        reject(new ApiError("Upload failed.", xhr.status));
      }
    };
    xhr.onerror = () => reject(new ApiError("Upload failed. Check the connection.", 0));
    xhr.send(form);
  });
}

export const thumbUrl = (id: number) => `/api/files/${id}/thumb`;
export const contentUrl = (id: number, download = false) => `/api/files/${id}/content${download ? "?download=1" : ""}`;
export const zipUrl = (fileIds: number[], folderIds: number[], name = "instagram-media") =>
  `/api/zip${qs({ files: fileIds.join(","), folders: folderIds.join(","), name })}`;
