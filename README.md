# ig-chat-media

Scans an Instagram group chat and downloads every **photo**, **video**, **shared post** and **shared reel** in it, sorted into folders and logged in a spreadsheet-friendly `index.csv`. Running it again only downloads what's new.

## The dashboard (easiest)

A web app that does everything below without typing commands: connect Instagram, pick chats, sync, and manage your media like a file manager.

**Start it**

| Where | How |
|---|---|
| Windows | Double-click `start.bat`. Your browser opens at http://localhost:8000 |
| macOS / Linux | Run `./start.sh` |
| Any server (Docker) | `docker compose up -d`, then open http://your-server:8000 |

The first time, you choose a passcode that protects the dashboard. Then:

1. **Instagram**: enter your username and password, or paste your browser's `sessionid` cookie (step-by-step help is on the page). Press **Save and connect**. If Instagram asks for a code, a box pops up for it.
2. **Chats**: press **Load my chats**, switch on the group chats you want, and press **Sync now**. Each chat saves into its own folder; press **Change** to pick another one.
3. **Library**: browse, preview, and organize everything.

**What's in it**

- **Home**: connection status, a big Sync button, counts, recently saved media, "on this day" memories, charts of who shares the most.
- **Chats**: save from several chats at once. Each chat has its own download folder, types, message count, and automatic checking. **Preview** shows what's new without downloading. **Full history** downloads everything. You can also add an Instagram data export (upload the .zip or point at a folder).
- **Library**: a real file manager. Create folders and subfolders, copy, cut, paste (Ctrl+C / X / V), move to, copy to, rename (F2), delete to a trash with restore, drag and drop (hold Ctrl to copy), upload from your computer, download as zip. You can also change a file's type, which moves it between a chat's Photos/Videos/Posts/Reels folders. Plus favorites, duplicate finder, search, and a full-screen viewer (arrow keys).
- **Activity**: every task with a live log, progress and a Stop button.
- **Instagram**: update the username, password or cookie. **Fresh login** forces a new sign-in. You can also sign out.
- **Settings**: automatic checking (how often, how many messages), what to download, start date, speed/safety pace, trash retention, import files from any folder on the computer, rescan, theme, and passcode.

**Where things are kept**: everything lives in one data folder (`dashboard/data`, or the `/data` volume in Docker). The SQLite database `app.db` holds settings, chats, the folder tree, the file index and logs. The Instagram password and cookie are encrypted with `secret.key`. Your media is in `library/` as normal folders and files, so you can open it in Explorer/Finder too. Use **Settings → Rescan library** after changing things there by hand.

**Options** (environment variables): `PORT` (default 8000), `HOST` (`0.0.0.0` to open it from your phone on the same Wi-Fi), `DATA_DIR`, `LIBRARY_DIR`, `DASHBOARD_PASSCODE` (skip the first-run screen), `APP_SECRET_KEY`.

**Workers and automatic restarts.** `start.bat`, `start.sh` and Docker all run `python -m app.serve`, which starts:

| | What it does | Setting (default) |
|---|---|---|
| Web workers | Serve the dashboard in parallel. They keep no state of their own. | `WEB_WORKERS` (4) |
| Web worker recycling | Each worker is replaced with a fresh one after this many requests, plus a random extra amount so they never all restart at once. | `MAX_REQUESTS` (2000, `0` = never), `MAX_REQUESTS_JITTER` (200) |
| Background engine | One process does all the Instagram work, one task at a time, however many web workers there are. It's replaced after this many tasks (only between tasks), and restarted automatically if it crashes. | `ENGINE_MAX_JOBS` (50, `0` = never) |

Everything the processes share lives in SQLite: the task queue, cancel requests, the Instagram connection status, security codes, the "slow down" pause, and failed sign-in attempts. So it doesn't matter which worker answers a request. A lease in the database guarantees only one engine ever runs. If the launcher is killed, its workers and the engine shut themselves down instead of lingering. **Settings → Server** shows the current setup.

**Developing**: `cd dashboard/frontend && npm install && npm run dev` (UI on :5173, proxies the API), and `uvicorn app.main:app --app-dir dashboard/backend --reload` (single process with the engine built in; API on :8000, docs at `/api/docs`).

The command-line tool below still works on its own.

## Two ways to read the chat

| | `live` | `export` |
|---|---|---|
| How | Logs in with your account and reads the chat directly | Reads the official "Download your information" file from Instagram |
| Login needed | Yes | No (only for `--download-shared`) |
| Keeps running for new messages | Yes (`--watch`) | No, it's a snapshot |
| Account risk | Uses Instagram's **unofficial** API, which is against Instagram's terms. Heavy use can trigger security checks or temporary blocks. | None for photos/videos in the export |

If you only need the media once, `export` is the safe choice. For ongoing use, `live` works well at modest volume. Consider running it with a second account that's also in the group.

## Setup

You need Python 3.10 or newer on a computer (Windows, macOS, Linux). On Android, Termux works too.

```bash
cd ig-chat-media
python -m venv .venv
# Windows: .venv\Scripts\activate     macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

## Live mode

```bash
# 1. See your group chats (asks for username/password the first time)
python ig_chat_media.py live --list

# 2. Download from one chat (name or part of it), last 200 messages
python ig_chat_media.py live --chat "Family"

# Whole history
python ig_chat_media.py live --chat "Family" --all

# Keep running: check for new messages every ~5 minutes
python ig_chat_media.py live --chat "Family" --watch --interval 300
```

- The first login can ask for a 2FA code or a security code that Instagram emails or texts you. Type it in when prompted.
- After that, the login is saved in `ig_session.json` so you don't log in every run. **Treat that file like your password** and don't share it.
- You can set `IG_USERNAME` and `IG_PASSWORD` as environment variables instead of typing them. The password is never accepted as a command-line argument.
- If login fails with **"Your version of Instagram is out of date"**, the password login is being rejected (a known instagrapi problem, and sometimes it really means a wrong password). Log in with your browser session instead: sign in at instagram.com, open DevTools (F12) > Application > Cookies, copy the `sessionid` value, and paste it when the script asks. You can also set it as `IG_SESSIONID`. It's saved into `ig_session.json` like a normal login, so you only need to do this once.

## Export mode

1. In the Instagram app: **Settings → Accounts Center → Your information and permissions → Download your information**.
2. Pick **Some of your information → Messages**, then **Download to device**, **Format: JSON**, **Media quality: High**.
3. When Instagram says it's ready (can take hours), download and **unzip** it.

```bash
python ig_chat_media.py export --export-dir path/to/unzipped-export --list
python ig_chat_media.py export --export-dir path/to/unzipped-export --chat "Family"

# Also download the shared posts/reels (logs in like live mode)
python ig_chat_media.py export --export-dir path/to/unzipped-export --chat "Family" --download-shared
```

The export contains the photos and videos people sent in the chat, but shared posts and reels appear only as links. These are always written to `shared_links.csv`. Use `--download-shared` to fetch them too.

## Useful options (both modes)

| Option | What it does |
|---|---|
| `--types photo,reel` | Only some kinds (`photo`, `video`, `post`, `reel`) |
| `--since 2026-01-01` | Only messages from that date on |
| `--out D:\InstaMedia` | Where to save (default `downloads`) |
| `--dry-run` | Show what would be downloaded, save nothing |
| `--include-private` | Also show/search one-to-one chats, not just groups |

## What you get

```
downloads/
  Family/
    photos/   2026-11-18_213501_sara_k_3329...jpg
    videos/
    posts/    2026-11-18_220410_ahmad_DCx9Ab_01.jpg   (albums get _01, _02, ...)
    reels/    2026-11-19_090112_sara_k_DBr7Yz.mp4
    index.csv          who sent what, when, the link, and the saved file names
    .downloaded.json   remembers what's done so reruns skip it
```

## What it detects

- Photos and videos sent directly in the chat
- Posts shared into the chat, including multi-photo/video albums
- Reels shared into the chat
- Instagram post/reel links pasted as text, including `instagram.com/share/...` short links

It does **not** download view-once/disappearing photos and videos (the sender chose not to let those be kept), voice messages, stickers, GIFs, or stories.

## If something goes wrong

- **`FAILED ...` on one item:** it's skipped and retried on the next run.
- **"Instagram is rate-limiting this account":** the tool stops right away. Wait a few hours, and use a smaller `--limit` or a longer `--interval`.
- **"Challenge" or "checkpoint" errors:** open the Instagram app on your phone, confirm "it was me" if asked, then run again.
- **`instagrapi` stops working after an Instagram update:** run `pip install -U instagrapi`.

Only use this on chats you're a member of, and keep in mind the photos and posts belong to the people who made them.
