#!/usr/bin/env python3
"""
ig_chat_media.py - find and download the photos, posts and reels shared in an
Instagram group chat.

Two ways to read the chat:

  live    Log in with your own account and read the chat directly
          (uses the unofficial `instagrapi` library). Supports --watch to keep
          checking for new messages.

  export  Read the official "Download your information" export from Instagram
          (JSON format). No login and no risk to your account for photos and
          videos sent in the chat; shared posts/reels are listed, and can be
          downloaded with --download-shared (which logs in).

Examples:
  python ig_chat_media.py live --list
  python ig_chat_media.py live --chat "Family" --limit 500
  python ig_chat_media.py live --chat "Family" --watch --interval 300
  python ig_chat_media.py export --export-dir ~/Downloads/instagram-me-2026 --list
  python ig_chat_media.py export --export-dir ~/Downloads/instagram-me-2026 --chat "Family"
"""
from __future__ import annotations

import argparse
import csv
import getpass
import json
import mimetypes
import os
import random
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Optional
from urllib.parse import parse_qs, urlparse
import dotenv
dotenv.load_dotenv()
KINDS = ("photo", "video", "post", "reel")
FOLDERS = {"photo": "photos", "video": "videos", "post": "posts", "reel": "reels"}
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)

# instagram.com/p/CODE, /reel/CODE, /reels/CODE, /tv/CODE, also /username/p/CODE
POST_URL_RE = re.compile(
    r"https?://(?:www\.|m\.)?instagram\.com/(?!share/)(?:[A-Za-z0-9_.]+/)?(p|reel|reels|tv)/([A-Za-z0-9_-]+)",
    re.I,
)
# instagram.com/share/p/xxxx or /share/reel/xxxx - short links that redirect
SHARE_URL_RE = re.compile(
    r"https?://(?:www\.)?instagram\.com/share/(?:p|reel|reels|tv)/[A-Za-z0-9_-]+/?", re.I
)


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
@dataclass
class Found:
    """One downloadable thing detected in a chat message."""

    key: str                      # unique per thing, used to skip it next time
    when: datetime
    sender: str
    kind: str                     # photo | video | post | reel
    url: Optional[str] = None     # direct file URL (photo/video sent in the chat)
    permalink: Optional[str] = None  # instagram.com/p/... or /reel/...
    media: object = None          # instagrapi Media object, when the message carries it
    local_path: Optional[Path] = None  # export mode: file inside the export
    note: str = ""
    saved: list = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def safe_name(text: str, limit: int = 60) -> str:
    text = re.sub(r"[^\w\-. ]+", "_", text or "", flags=re.UNICODE).strip(" ._")
    text = re.sub(r"\s+", "_", text)
    return (text[:limit] or "unnamed")


def kind_from_path(kind: str) -> str:
    return "reel" if kind.lower() in ("reel", "reels") else "post"


def permalink_for(code: str, kind: str) -> str:
    return f"https://www.instagram.com/{'reel' if kind == 'reel' else 'p'}/{code}/"


def find_post_links(text: str) -> list[tuple[str, str]]:
    """Return [(kind, url)] for every Instagram post/reel link in some text."""
    out, seen = [], set()
    if not text:
        return out
    for m in POST_URL_RE.finditer(text):
        kind, code = kind_from_path(m.group(1)), m.group(2)
        if code not in seen:
            seen.add(code)
            out.append((kind, permalink_for(code, kind)))
    for m in SHARE_URL_RE.finditer(text):
        url = m.group(0)
        if url not in seen:
            seen.add(url)
            out.append(("reel" if "/reel" in url else "post", url))
    return out


def resolve_share_url(url: str) -> str:
    """instagram.com/share/... links redirect to the real /p/ or /reel/ URL."""
    if "/share/" not in url:
        return url
    import requests

    try:
        r = requests.get(url, headers={"User-Agent": UA}, allow_redirects=False, timeout=20)
        loc = r.headers.get("Location") or r.headers.get("location")
        if loc and POST_URL_RE.search(loc):
            return loc
    except Exception:
        pass
    return url


def ext_for(url: str, content_type: str = "") -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in (".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif", ".mp4", ".mov", ".m4v"):
        return suffix
    guessed = mimetypes.guess_extension((content_type or "").split(";")[0].strip())
    return guessed or ".bin"


def http_download(url: str, dest_no_ext: Path) -> Path:
    """Download a file, picking the extension from the URL or Content-Type."""
    import requests

    with requests.get(str(url), headers={"User-Agent": UA}, stream=True, timeout=60) as r:
        r.raise_for_status()
        dest = dest_no_ext.with_name(dest_no_ext.name + ext_for(str(url), r.headers.get("Content-Type", "")))
        tmp = dest.with_name(dest.name + ".part")
        with open(tmp, "wb") as fh:
            for chunk in r.iter_content(1 << 16):
                fh.write(chunk)
        if dest.suffix == ".bin":  # no hint from URL/headers: look at the file itself
            dest = dest.with_suffix(sniff_ext(tmp))
        tmp.replace(dest)
        return dest


def sniff_ext(path: Path) -> str:
    head = path.read_bytes()[:12]
    if head[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    if head[4:8] == b"ftyp":
        return ".mp4"
    return ".bin"


def as_aware(dt: datetime) -> datetime:
    """instagrapi returns naive local-time datetimes; make them timezone-aware."""
    return dt if dt.tzinfo else dt.astimezone()


def polite_pause(lo: float = 1.5, hi: float = 4.0) -> None:
    time.sleep(random.uniform(lo, hi))


# --------------------------------------------------------------------------- #
# Output folder, state (what was already downloaded) and index.csv
# --------------------------------------------------------------------------- #
class Store:
    def __init__(self, root: Path, chat_title: str, dry_run: bool = False):
        self.dir = root / safe_name(chat_title)
        self.dry_run = dry_run
        self.state_file = self.dir / ".downloaded.json"
        self.index_file = self.dir / "index.csv"
        self.done: set[str] = set()
        if self.state_file.exists():
            try:
                self.done = set(json.loads(self.state_file.read_text("utf-8")).get("done", []))
            except Exception:
                self.done = set()

    def folder(self, kind: str) -> Path:
        path = self.dir / FOLDERS[kind]
        if not self.dry_run:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def base_name(self, f: Found, ident: str) -> str:
        return f"{f.when.astimezone():%Y-%m-%d_%H%M%S}_{safe_name(f.sender, 30)}_{safe_name(ident, 40)}"

    def mark_done(self, f: Found) -> None:
        self.done.add(f.key)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps({"done": sorted(self.done)}, indent=0), "utf-8")
        new = not self.index_file.exists()
        with open(self.index_file, "a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(["sent_at", "sender", "type", "link", "saved_files", "note"])
            w.writerow([
                f.when.astimezone().isoformat(timespec="seconds"),
                f.sender,
                f.kind,
                f.permalink or "",
                " | ".join(str(p.relative_to(self.dir)) for p in f.saved),
                f.note,
            ])


# --------------------------------------------------------------------------- #
# LIVE mode: log in and read the chat through instagrapi
# --------------------------------------------------------------------------- #
def _login_with_sessionid(cl, sessionid):
    """Log in with the 'sessionid' cookie copied from a logged-in browser."""
    sessionid = sessionid.strip().strip('"')
    if "%3A" in sessionid:  # browsers often show the cookie URL-encoded
        from urllib.parse import unquote
        sessionid = unquote(sessionid)
    try:
        cl.login_by_sessionid(sessionid)
    except AssertionError:
        sys.exit("That doesn't look like a sessionid (it should start with digits, like 1234567890:AbC...).")
    print(f"Logged in with browser session as @{cl.username}")


def make_client(args):
    try:
        from instagrapi import Client
        from instagrapi.exceptions import ClientNeedsUpgradeError, LoginRequired, TwoFactorRequired
    except ImportError:
        sys.exit("instagrapi is not installed. Run:  pip install -r requirements.txt")

    session = Path(args.session).expanduser()
    username = args.username or os.environ.get("IG_USERNAME") or input("Instagram username: ").strip()

    cl = Client()
    cl.delay_range = [1, 3]  # small random pause between API calls
    cl.challenge_code_handler = lambda user, choice: input(
        f"Instagram sent a security code to your {getattr(choice, 'name', choice)}. Enter it: "
    ).strip()

    # 1) Re-use a saved session if we have one (avoids logging in every run).
    if session.exists():
        try:
            cl.load_settings(session)
            cl.get_timeline_feed()  # cheap check that the session still works
            if not cl.username:
                cl.username = username
            print(f"Using saved session for @{cl.username or username}")
            return cl
        except LoginRequired:
            print("Saved session expired, logging in again...")
            uuids = cl.get_settings().get("uuids")
            cl.set_settings({})
            if uuids:
                cl.set_uuids(uuids)  # keep the same "device" so Instagram isn't alarmed
        except Exception as e:
            print(f"Could not reuse session ({e}); logging in again...")

    # 2) Fresh login. Prefer a browser sessionid if one was given: it skips
    #    instagrapi's password login, which Instagram sometimes rejects with
    #    "Your version of Instagram is out of date" (error_type=needs_upgrade).
    sessionid = os.environ.get("IG_SESSIONID", "").strip()
    if sessionid:
        _login_with_sessionid(cl, sessionid)
    else:
        password = os.environ.get("IG_PASSWORD") or getpass.getpass(f"Password for @{username}: ")
        try:
            try:
                cl.login(username, password)
            except TwoFactorRequired:
                code = input("Two-factor code (from your authenticator app or SMS): ").strip()
                cl.login(username, password, verification_code=code)
        except ClientNeedsUpgradeError:
            print(
                "\nInstagram rejected the password login with 'Your version of Instagram is out of date'.\n"
                "This is a known instagrapi problem (it can also mean the password is wrong).\n"
                "Workaround: log in with your browser's session cookie instead.\n"
                "  1. Log in to instagram.com in your browser.\n"
                "  2. Open DevTools (F12) > Application > Cookies > https://www.instagram.com\n"
                "  3. Copy the value of the 'sessionid' cookie.\n"
                "(Next time you can set it as the IG_SESSIONID environment variable.)\n"
            )
            sessionid = getpass.getpass("Paste sessionid (input is hidden, leave empty to quit): ").strip()
            if not sessionid:
                sys.exit("No sessionid given; stopping.")
            _login_with_sessionid(cl, sessionid)
    username = cl.username or username

    session.parent.mkdir(parents=True, exist_ok=True)
    cl.dump_settings(session)
    try:
        os.chmod(session, 0o600)  # the session file is as good as your password
    except OSError:
        pass
    print(f"Logged in as @{username}; session saved to {session}")
    return cl


def live_list_chats(cl, include_private: bool, amount: int) -> list:
    threads = cl.direct_threads(amount=amount, thread_message_limit=1)
    return [t for t in threads if include_private or t.is_group]


def thread_label(t) -> str:
    if t.thread_title:
        return t.thread_title
    return ", ".join(u.username for u in t.users[:4]) or str(t.id)


def live_pick_thread(cl, args):
    if args.thread_id:
        return str(args.thread_id)
    if not args.chat:
        sys.exit("Tell me which chat: --chat \"name\" or --thread-id ID (see --list).")
    wanted = args.chat.casefold()
    threads = live_list_chats(cl, include_private=True, amount=args.search_threads)
    exact = [t for t in threads if thread_label(t).casefold() == wanted]
    partial = [t for t in threads if wanted in thread_label(t).casefold()]
    matches = exact or partial
    if not matches:
        sys.exit(f"No chat named like '{args.chat}' in your latest {args.search_threads} chats. "
                 "Try --list, or raise --search-threads.")
    if len(matches) > 1:
        print("Several chats match; pick one with --thread-id:")
        for t in matches:
            print(f"  {t.id}  {thread_label(t)}")
        sys.exit(1)
    return str(matches[0].id)


def media_kind(media) -> str:
    return "reel" if (getattr(media, "product_type", "") or "") == "clips" else "post"


def detect_live(msg, usernames: dict) -> Iterator[Found]:
    """Look at one instagrapi DirectMessage and yield what can be downloaded."""
    when = as_aware(msg.timestamp)
    sender = usernames.get(str(msg.user_id), str(msg.user_id))
    item_type = (msg.item_type or "").lower()
    seen_links: set[str] = set()
    n = 0

    def make(kind, **kw) -> Found:
        nonlocal n
        n += 1
        return Found(key=f"{msg.id}:{n}", when=when, sender=sender, kind=kind, **kw)

    # View-once / disappearing photos and videos are skipped on purpose:
    # the sender chose not to let them be kept.
    if item_type in ("raven_media", "visual_media", "expired_placeholder") or msg.visual_media:
        return

    # Photo or video sent straight into the chat (not voice notes).
    if msg.media and item_type != "voice_media":
        m = msg.media
        if m.media_type == 1 and m.thumbnail_url:
            yield make("photo", url=str(m.thumbnail_url))
        elif m.media_type == 2 and m.video_url:
            yield make("video", url=str(m.video_url))

    # A post or reel shared into the chat (classic format).
    for media in (msg.media_share, msg.clip):
        if media is None:
            continue
        kind = "reel" if media is msg.clip else media_kind(media)
        link = permalink_for(media.code, kind) if media.code else None
        if link:
            seen_links.add(link)
        yield make(kind, media=media, permalink=link)

    # Newer "XMA" share format: we only get a link, so resolve it later.
    xmas = ([msg.xma_share] if msg.xma_share else []) + list(msg.generic_xma or [])
    for x in xmas:
        target = str(x.video_url or "")
        if target.startswith("instagram://"):
            media_id = (parse_qs(urlparse(target).query).get("id") or [""])[0]
            if media_id:
                kind = "reel" if "clip" in target or "reel" in target else "post"
                yield make(kind, permalink=f"instagram-media-id:{media_id}")
            continue
        for kind, link in find_post_links(target):
            if link not in seen_links:
                seen_links.add(link)
                yield make(kind, permalink=link)

    # Links pasted as text.
    texts = [msg.text or ""]
    if msg.link:
        texts += [msg.link.text or "", msg.link.link_context.link_url or ""]
    for kind, link in find_post_links("\n".join(texts)):
        if link not in seen_links:
            seen_links.add(link)
            yield make(kind, permalink=link)


def download_media_object(cl, media, store: Store, f: Found) -> list[Path]:
    """Save every photo/video in an instagrapi Media (single, video or carousel)."""
    if media.media_type == 8 and not media.resources and media.pk:
        polite_pause()
        media = cl.media_info(media.pk)
    if media.media_type == 2 and not media.video_url and media.pk:
        polite_pause()
        media = cl.media_info(media.pk)

    f.kind = media_kind(media) if f.kind in ("post", "reel") else f.kind
    if media.code:
        f.permalink = permalink_for(media.code, f.kind)  # canonical link for index.csv
    owner = getattr(getattr(media, "user", None), "username", "") or ""
    if owner:
        f.note = f"by @{owner}"
    folder = store.folder(f.kind)
    base = store.base_name(f, media.code or str(media.pk))

    files: list[tuple[str, str]] = []  # (url, suffix)
    if media.media_type == 1 and media.thumbnail_url:
        files.append((str(media.thumbnail_url), ""))
    elif media.media_type == 2 and media.video_url:
        files.append((str(media.video_url), ""))
    elif media.media_type == 8:
        for i, res in enumerate(media.resources, 1):
            url = res.video_url if res.media_type == 2 else res.thumbnail_url
            if url:
                files.append((str(url), f"_{i:02d}"))
    if not files:
        raise RuntimeError("no downloadable file found in this post")

    saved = []
    for url, suffix in files:
        saved.append(http_download(url, folder / (base + suffix)))
    return saved


def fetch_by_permalink(cl, link: str):
    if link.startswith("instagram-media-id:"):
        pk = cl.media_pk(link.split(":", 1)[1])
    else:
        link = resolve_share_url(link)
        pk = cl.media_pk_from_url(link)
    polite_pause()
    return cl.media_info(pk)


def process(found: Iterable[Found], store: Store, cl=None, types: set = set(KINDS),
            fetch_links: bool = True) -> tuple[int, int, int]:
    """Download everything in `found` that hasn't been downloaded before."""
    ok = skipped = failed = 0
    for f in found:
        if f.kind not in types:
            continue
        link_only = not (f.local_path or f.url or f.media is not None)
        if link_only and not fetch_links:
            continue  # export mode without --download-shared: these are only listed
        if f.key in store.done:
            skipped += 1
            continue
        label = f"{f.when.astimezone():%Y-%m-%d %H:%M} {f.sender}: {f.kind}"
        if store.dry_run:
            print(f"  [would download] {label} {f.permalink or ''}")
            ok += 1
            continue
        try:
            if f.local_path:                                    # export: file is already on disk
                dest = store.folder(f.kind) / (store.base_name(f, f.local_path.stem) + f.local_path.suffix)
                shutil.copy2(f.local_path, dest)
                f.saved = [dest]
            elif f.url:                                         # photo/video sent in the chat
                f.saved = [http_download(f.url, store.folder(f.kind) / store.base_name(f, f.key.split(":")[0][-12:]))]
            elif f.media is not None:                           # shared post/reel with details
                f.saved = download_media_object(cl, f.media, store, f)
            else:                                               # shared post/reel, link only
                f.saved = download_media_object(cl, fetch_by_permalink(cl, f.permalink), store, f)
            store.mark_done(f)
            ok += 1
            print(f"  saved  {label} -> {', '.join(p.name for p in f.saved)}")
        except Exception as e:  # keep going; it will be retried on the next run
            failed += 1
            print(f"  FAILED {label} {f.permalink or ''}: {type(e).__name__}: {e}")
            if type(e).__name__ in ("PleaseWaitFewMinutes", "RateLimitError", "FeedbackRequired"):
                print("  Instagram is rate-limiting this account. Stopping now; try again later.")
                break
    return ok, skipped, failed


def live_scan(cl, thread_id: str, args, types: set) -> None:
    amount = 0 if args.all else args.limit
    print(f"Reading {'all' if amount == 0 else f'the last {amount}'} messages...")
    thread = cl.direct_thread(thread_id, amount=amount)
    title = thread_label(thread)
    usernames = {str(u.pk): u.username for u in thread.users}
    usernames[str(cl.user_id)] = cl.username or "me"
    store = Store(Path(args.out), title, dry_run=args.dry_run)
    since = parse_since(args.since)

    found = []
    # API returns newest first; reverse then sort so ties keep oldest-first order
    for msg in sorted(reversed(thread.messages), key=lambda m: as_aware(m.timestamp)):
        if since and as_aware(msg.timestamp) < since:
            continue
        found.extend(detect_live(msg, usernames))
    print(f"Chat '{title}': {len(thread.messages)} messages, {len(found)} photos/videos/posts/reels found")
    ok, skipped, failed = process(found, store, cl, types)
    print(f"Done: {ok} new, {skipped} already downloaded, {failed} failed -> {store.dir}")


def run_live(args) -> None:
    types = parse_types(args.types)
    cl = make_client(args)

    if args.list:
        threads = live_list_chats(cl, include_private=args.include_private, amount=args.search_threads)
        if not threads:
            print("No group chats found in your latest chats (try --include-private or --search-threads 100).")
        for t in threads:
            print(f"{t.id}  {thread_label(t)}  ({len(t.users) + 1} people)")
        return

    thread_id = live_pick_thread(cl, args)
    live_scan(cl, thread_id, args, types)

    if args.watch:
        interval = max(60, args.interval)
        print(f"Watching for new messages every ~{interval}s. Press Ctrl+C to stop.")
        args.all, args.limit = False, args.watch_batch
        try:
            while True:
                time.sleep(interval + random.uniform(0, interval * 0.3))
                try:
                    live_scan(cl, thread_id, args, types)
                except KeyboardInterrupt:
                    raise
                except Exception as e:
                    print(f"Check failed ({type(e).__name__}: {e}); will retry next round.")
        except KeyboardInterrupt:
            print("\nStopped.")


# --------------------------------------------------------------------------- #
# EXPORT mode: read Instagram's "Download your information" JSON export
# --------------------------------------------------------------------------- #
def fix_text(s: str) -> str:
    """Instagram exports store UTF-8 text as if it were Latin-1; undo that."""
    if not isinstance(s, str):
        return s
    try:
        return s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


@dataclass
class ExportChat:
    folder: Path
    title: str
    participants: list
    files: list


def export_chats(root: Path) -> list[ExportChat]:
    chats: dict[Path, ExportChat] = {}
    for f in sorted(root.rglob("message_*.json")):
        if "messages" not in f.parts:
            continue
        chat = chats.get(f.parent)
        if chat is None:
            try:
                data = json.loads(f.read_text("utf-8"))
            except Exception:
                continue
            people = [fix_text(p.get("name", "")) for p in data.get("participants", [])]
            title = fix_text(data.get("title", "")) or ", ".join(people[:4]) or f.parent.name
            chat = chats[f.parent] = ExportChat(f.parent, title, people, [])
        chat.files.append(f)
    return list(chats.values())


def resolve_export_uri(root: Path, chat_dir: Path, uri: str) -> Optional[Path]:
    if not uri or uri.startswith("http"):
        return None
    rel = Path(uri)
    root_r = root.resolve()
    bases = [root] + [p for p in chat_dir.parents if p == root or root in p.parents]
    for base in bases:
        p = (base / rel).resolve()
        if p.is_file() and (p == root_r or root_r in p.parents):  # never read outside the export
            return p
    hits = [h for h in chat_dir.rglob(rel.name) if h.is_file()]
    return hits[0] if hits else None


def detect_export(chat: ExportChat, root: Path) -> Iterator[Found]:
    messages = []
    for f in chat.files:
        messages += json.loads(f.read_text("utf-8")).get("messages", [])
    messages.sort(key=lambda m: m.get("timestamp_ms", 0))

    for m in messages:
        when = datetime.fromtimestamp(m.get("timestamp_ms", 0) / 1000, tz=timezone.utc)
        sender = fix_text(m.get("sender_name", "unknown"))
        base_key = f"{m.get('timestamp_ms', 0)}:{sender}"
        n = 0

        def make(kind, **kw):
            nonlocal n
            n += 1
            return Found(key=f"{base_key}:{n}", when=when, sender=sender, kind=kind, **kw)

        for group, kind in (("photos", "photo"), ("videos", "video")):
            for item in m.get(group, []) or []:
                path = resolve_export_uri(root, chat.folder, item.get("uri", ""))
                if path:
                    yield make(kind, local_path=path)
                else:
                    print(f"  (missing in export: {item.get('uri')})")

        share = m.get("share") or {}
        text = "\n".join([share.get("link", "") or "", fix_text(m.get("content", "") or "")])
        for kind, link in find_post_links(text):
            owner = fix_text(share.get("original_content_owner", "") or "")
            yield make(kind, permalink=link, note=f"by @{owner}" if owner else "")


def run_export(args) -> None:
    types = parse_types(args.types)
    root = Path(args.export_dir).expanduser()
    if root.is_file() and root.suffix.lower() == ".zip":
        sys.exit("Unzip the export first, then point --export-dir at the unzipped folder.")
    if not root.is_dir():
        sys.exit(f"Folder not found: {root}")
    chats = export_chats(root)
    if not chats:
        sys.exit("No messages found. Make sure you requested the export in JSON format and "
                 "included 'Messages', then unzip it and point --export-dir at that folder.")
    if not args.include_private:
        groups = [c for c in chats if len(c.participants) > 2]
        chats = groups or chats

    if args.list:
        for c in sorted(chats, key=lambda c: c.title.casefold()):
            print(f"{c.title}  ({len(c.participants)} people)  [{c.folder.name}]")
        return
    if not args.chat:
        sys.exit("Tell me which chat: --chat \"name\" (see --list).")

    wanted = args.chat.casefold()
    matches = [c for c in chats if c.title.casefold() == wanted or c.folder.name.casefold() == wanted] \
        or [c for c in chats if wanted in c.title.casefold()]
    if not matches:
        sys.exit(f"No chat named like '{args.chat}'. Try --list.")
    if len(matches) > 1:
        print("Several chats match; use the exact name or the folder name in [brackets]:")
        for c in matches:
            print(f"  {c.title}  [{c.folder.name}]")
        sys.exit(1)

    chat = matches[0]
    store = Store(Path(args.out), chat.title, dry_run=args.dry_run)
    since = parse_since(args.since)
    found = [f for f in detect_export(chat, root) if not since or f.when >= since]
    found = [f for f in found if f.kind in types]
    shared = [f for f in found if f.permalink and not f.local_path]
    print(f"Chat '{chat.title}': {len(found)} items found "
          f"({len(found) - len(shared)} photos/videos in the export, {len(shared)} shared posts/reels)")

    cl = make_client(args) if (args.download_shared and shared and not args.dry_run) else None
    ok, skipped, failed = process(found, store, cl, types, fetch_links=args.download_shared)

    # Always write the list of shared post/reel links, downloaded or not.
    if shared and not args.dry_run:
        store.dir.mkdir(parents=True, exist_ok=True)
        links_file = store.dir / "shared_links.csv"
        with open(links_file, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["sent_at", "sender", "type", "link", "note"])
            for f in shared:
                w.writerow([f.when.astimezone().isoformat(timespec="seconds"), f.sender, f.kind, f.permalink, f.note])
        print(f"Listed {len(shared)} shared posts/reels in {links_file.name}"
              + ("" if cl else " (add --download-shared to download them too)"))
    print(f"Done: {ok} new, {skipped} already downloaded, {failed} failed -> {store.dir}")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_types(value: str) -> set:
    chosen = {t.strip().lower().rstrip("s") for t in (value or "").split(",") if t.strip()}
    chosen = {("photo" if t in ("image", "img", "pic") else t) for t in chosen}
    bad = chosen - set(KINDS)
    if bad:
        sys.exit(f"Unknown --types {', '.join(sorted(bad))}. Use any of: {', '.join(KINDS)}")
    return chosen or set(KINDS)


def parse_since(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").astimezone()
    except ValueError:
        sys.exit("--since must look like 2026-01-31")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Download the photos, posts and reels shared in an Instagram group chat.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:" + __doc__.split("Examples:", 1)[1],
    )
    sub = p.add_subparsers(dest="mode", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--list", action="store_true", help="list your group chats and exit")
    common.add_argument("--chat", help="chat name (or part of it)")
    common.add_argument("--include-private", action="store_true", help="also include one-to-one chats")
    common.add_argument("--types", default="photo,video,post,reel",
                        help="what to download, comma separated (default: photo,video,post,reel)")
    common.add_argument("--since", help="only messages on or after this date, e.g. 2026-01-31")
    common.add_argument("--out", default="downloads", help="output folder (default: downloads)")
    common.add_argument("--dry-run", action="store_true", help="show what would be downloaded, save nothing")
    common.add_argument("--username", help="Instagram username (or set IG_USERNAME)")
    common.add_argument("--session", default="ig_session.json",
                        help="where to keep the login session (default: ig_session.json)")
    common.add_argument("--search-threads", type=int, default=60,
                        help="how many recent chats to search for --chat/--list (default: 60)")

    live = sub.add_parser("live", parents=[common], help="log in and read the chat directly")
    live.add_argument("--thread-id", help="chat ID from --list (instead of --chat)")
    live.add_argument("--limit", type=int, default=200, help="how many recent messages to read (default: 200)")
    live.add_argument("--all", action="store_true", help="read the whole chat history (slow)")
    live.add_argument("--watch", action="store_true", help="keep running and download new items as they arrive")
    live.add_argument("--interval", type=int, default=300, help="seconds between checks in --watch (min 60)")
    live.add_argument("--watch-batch", type=int, default=30, help="messages to read on each --watch check")

    exp = sub.add_parser("export", parents=[common], help="read Instagram's data export (no login)")
    exp.add_argument("--export-dir", required=True, help="the unzipped Instagram export folder")
    exp.add_argument("--download-shared", action="store_true",
                     help="also download shared posts/reels (logs in like live mode)")
    return p


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    try:
        if args.mode == "live":
            run_live(args)
        else:
            run_export(args)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
