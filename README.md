# telegram-audio-dl

[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)

Interactive Python CLI to download audio/music files from Telegram channels you're subscribed to and play them locally. Downloads run in the background with checkpoint resume, the player supports media controls (next/prev/seek/pause), and playback can be minimized to "Now Playing" while you keep navigating the menu.

> **Notice**: this project uses Telegram's **personal MTProto API** (Telethon). It is not a bot. It only downloads audio from channels/chats you already have access to with your account. Respect Telegram's Terms of Service and the copyright of the material.

> **Language note**: the CLI menu and runtime messages are currently in Spanish. The codebase, docs, and this README are in English so they're usable internationally. PRs to internationalize the UI strings are welcome.

---

## Table of contents

1. [Features](#features)
2. [Requirements](#requirements)
3. [Step-by-step setup](#step-by-step-setup)
4. [Environment variables (`.env`)](#environment-variables-env)
5. [Project structure](#project-structure)
6. [How to run it](#how-to-run-it)
7. [Main menu flow](#main-menu-flow)
8. [Player: controls](#player-controls)
9. [Online streaming](#online-streaming)
10. [Daemon mode (background downloads)](#daemon-mode-background-downloads)
11. [Job states](#job-states)
12. [Range selection (multi-select)](#range-selection-multi-select)
13. [Logs and troubleshooting](#logs-and-troubleshooting)
14. [Persistent files](#persistent-files)
15. [Tests](#tests)
16. [FAQ](#faq)
17. [Known limitations](#known-limitations)
18. [Day-to-day operation](#day-to-day-operation)

---

## Features

- 🔍 **Channel listing** of subscribed chats with autocomplete (paginated if >100).
- ⬇️  **Background downloads** with an async `DownloadManager` — one job at a time to avoid rate limits.
- 📊 **Pre-inventory on enqueue**: the SQLite state is populated at the moment of enqueueing, so nothing is lost if the CLI is interrupted.
- ⏸️ **Pause on exit** + 🔁 **auto-resume** when reopened (with fallback to Telegram if the state was wiped).
- 🌐 **Telegram sync on resume** to detect new audios added to the channel.
- 🌐 **Online streaming** without touching disk (`iter_download` → `mpv` via stdin).
- 🎚️  **Online streaming queue** with auto-advance between tracks and prefetch of the next track in RAM (<1s start time between songs).
- 👀 **30-second preview** before downloading.
- 📚 **Browsable local library** with a table of channels, folders, sizes, and playback of one/multiple channels or specific track lookup.
- 🎲 **Global shuffle** of 50 random tracks across the entire library.
- 🔀 **Mixed queue** across multiple channels with shuffle/alphabetical/by-channel ordering.
- 🎵 **Media player** with ID3 metadata (mutagen), animated bar, mpv controls (pause/seek/etc).
- 📂 **Play arbitrary folder** from the filesystem (independent of the CLI).
- 🗄️  **SQLite with WAL** for state + jobs (reads don't block writes).
- 📝 **Rotating logs** configurable with `LOG_LEVEL=DEBUG`.

---

## Requirements

Works on **macOS** and **Linux** (Windows untested). The CLI auto-detects which player to use based on what's installed.

| Component | Version | macOS | Linux (Debian/Ubuntu) | Linux (Arch) |
|---|---|---|---|---|
| Python | ≥ 3.10 | `brew install python` | `apt install python3` | `pacman -S python` |
| `mpv` (recommended, full controls) | any recent | `brew install mpv` | `apt install mpv` | `pacman -S mpv` |
| Fallback player | — | `afplay` (built-in) | `apt install ffmpeg` (includes `ffplay`) | `pacman -S ffmpeg` |
| Telegram account | with phone number, optional 2FA | your own | your own | your own |
| App at my.telegram.org | api_id + api_hash | see step 1 below | see step 1 below | see step 1 below |

### Automatic player detection

The CLI looks for, in priority order:

1. **`mpv`** — full controls (pause/seek/queue navigation). Recommended on any platform.
2. **`afplay`** — macOS built-in, no controls.
3. **`ffplay`** — part of ffmpeg, cross-platform, no controls. Best Linux option if not using mpv.
4. **`mpg123`** — mp3-only, lightweight. Third Linux option.
5. **`paplay`** — PulseAudio, wav/raw only. Fourth Linux option.

Without any of the above: the **Local library** and **Preview** options are disabled with a clear message indicating what to install.

---

## Step-by-step setup

### 1. Telegram credentials

1. Go to <https://my.telegram.org> with your phone number.
2. You'll receive a code via Telegram (not SMS).
3. Click on **API development tools**.
4. Create an application:
   - **App title**: `telegram-audio-dl`
   - **Short name**: `tgaudiodl`
   - **Platform**: Desktop
5. Copy `api_id` (~8-digit integer) and `api_hash` (32-char hex string).

### 2. Player with controls (recommended)

| System | Command |
|---|---|
| macOS | `brew install mpv` |
| Debian/Ubuntu | `sudo apt install mpv` |
| Arch | `sudo pacman -S mpv` |
| Fedora | `sudo dnf install mpv` |

Without `mpv`, the player falls back to a simple binary without controls (only `Ctrl+C` to stop):
- On macOS, `afplay` (preinstalled).
- On Linux, `ffplay` if you have ffmpeg (`apt/pacman/dnf install ffmpeg`); otherwise `mpg123` or `paplay`.

### 3. Create venv and install

> Tip: create the venv **outside an external SSD** if you're using one, because macOS creates AppleDouble files (`._*`) on non-APFS filesystems and these break `pip`.

Using `uv` (recommended — fast, includes lock file):

```bash
uv sync                                    # uses uv.lock for reproducible install
uv sync --extra dev                        # also installs pytest
```

Using `pip`:

```bash
python3 -m venv ~/.virtualenvs/telegram-audio-dl
~/.virtualenvs/telegram-audio-dl/bin/pip install --upgrade pip
~/.virtualenvs/telegram-audio-dl/bin/pip install -e "$PWD"
```

### 4. Configure `.env`

```bash
cp .env.example .env
# Edit with your values
```

```dotenv
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=abcdef0123456789abcdef0123456789
TELEGRAM_PHONE=+1234567890
TELEGRAM_SESSION=telegram_audio_dl
DOWNLOAD_ROOT=
```

### 5. First login

```bash
~/.virtualenvs/telegram-audio-dl/bin/telegram-audio-dl
```

The first time:
1. It asks for the **code** sent via Telegram (in the official "Telegram" chat, not SMS).
2. If you have **2FA** enabled, it asks for the encryption password.
3. The session is stored in `telegram_audio_dl.session`. **You won't be asked again** in future runs.

---

## Environment variables (`.env`)

| Variable | Required | Default | Description |
|---|---|---|---|
| `TELEGRAM_API_ID` | yes | — | Integer. From my.telegram.org |
| `TELEGRAM_API_HASH` | yes | — | 32-char hex string. From my.telegram.org |
| `TELEGRAM_PHONE` | yes | — | International format with `+` |
| `TELEGRAM_SESSION` | no | `telegram_audio_dl` | Telethon session filename |
| `DOWNLOAD_ROOT` | no | `~/Downloads` | Root folder for downloads |
| `LOG_LEVEL` | no | `INFO` | `DEBUG` for detailed troubleshooting |

⚠ **Never** commit the `.env` or `*.session` — both are in `.gitignore`.

---

## Project structure

```
telegram-audio-dl/
├── .env.example                  # template — copy to .env
├── .gitignore
├── LICENSE                       # GPL v3
├── pyproject.toml                # dependencies and entry point
├── uv.lock                       # locked dependency versions (reproducible builds)
├── README.md                     # this file
├── CLAUDE.md                     # guide for Claude Code
├── docs/
│   ├── credenciales-telegram.md  # how to obtain credentials
│   └── escenarios.md             # scenario matrix + coverage
├── bin/
│   └── aut-03-run.sh             # Vaultwarden/Bitwarden wrapper (optional)
├── scripts/
│   ├── telegram-audio-dl.service # systemd unit file (Linux)
│   └── com.telegram-audio-dl.plist # launchd agent (macOS)
├── tools/
│   ├── kickoff_downloads.py      # script: download a fixed list of channels
│   └── migrate_json_to_sqlite.py # one-shot: legacy JSON state → SQLite
├── src/telegram_audio_dl/
│   ├── __main__.py               # python -m telegram_audio_dl
│   ├── entrypoint.py             # console script entry point
│   ├── cli.py                    # interactive menus and flows
│   ├── config.py                 # .env loading
│   ├── client.py                 # Telethon wrapper
│   ├── database.py               # SQLite wrapper (WAL, schema, migrations)
│   ├── downloader.py             # foreground download (Downloader)
│   ├── download_manager.py       # background jobs (DownloadManager)
│   ├── daemon.py                 # daemon mode + IPC
│   ├── ipc.py                    # IPC server/client (Unix socket)
│   ├── player.py                 # mpv player via IPC
│   ├── metadata.py               # mutagen for ID3/m4a/etc tags
│   ├── state.py                  # FileEntry + StateStore (over SQLite)
│   └── logging_setup.py          # logger configuration
└── tests/                        # 200+ pytest tests
```

Generated on first use (gitignored): `.env`, `*.session*`, `state/`.

---

## How to run it

```bash
# Installed entry point
~/.virtualenvs/telegram-audio-dl/bin/telegram-audio-dl

# Or without installing
python -m telegram_audio_dl

# With verbose logs
LOG_LEVEL=DEBUG ~/.virtualenvs/telegram-audio-dl/bin/telegram-audio-dl
```

### Development mode (without writing secrets to `.env`)

If your repo's `.env` is just a placeholder/documentation and you don't want to leave credentials on disk, export the variables in the shell before invoking the binary. `python-dotenv` **does not override** variables already present in the environment, so these take priority:

```bash
export TELEGRAM_API_ID=...
export TELEGRAM_API_HASH=...
export TELEGRAM_PHONE=+1234567890
~/.virtualenvs/telegram-audio-dl/bin/telegram-audio-dl
```

Useful for one-shot tests, shared machines, or repos where `.env` only carries metadata. Variables live only in the shell session — when you close the terminal they're gone.

> **Security tip**: if you keep credentials in a password manager (Vaultwarden, 1Password, Bitwarden CLI), you can wrap the `export` calls in a `scripts/dev-env.sh` (gitignored) that reads from there. Example with `bw` CLI:
>
> ```bash
> # scripts/dev-env.sh (do NOT commit)
> export BW_SESSION=$(bw unlock --raw)
> ITEM=$(bw get item "Telegram (AUT-03)" --session "$BW_SESSION")
> export TELEGRAM_API_ID=$(echo $ITEM | jq -r '.fields[]|select(.name=="api_id").value')
> export TELEGRAM_API_HASH=$(echo $ITEM | jq -r '.login.username')
> export TELEGRAM_PHONE=$(echo $ITEM | jq -r '.login.password')
> ```
>
> Then: `source scripts/dev-env.sh && ~/.virtualenvs/telegram-audio-dl/bin/telegram-audio-dl`

---

## Main menu flow

The CLI labels are in Spanish (see language note at the top); functional descriptions in English follow each entry:

```
Menú principal:
> Buscar canales en Telegram          ← search/download from a new channel
  Reanudar descargas pendientes        ← resume interrupted work
  Ver descargas en curso               ← monitor active jobs
  Biblioteca local                     ← downloaded channels (browse + play)
  Reproducir música de una carpeta     ← any arbitrary FS folder
  🌐 Reproducción online (streaming)   ← only if mpv is installed
  Salir
```

### Search channels in Telegram

1. Lists all subscribed channels/groups (paginated by 50 if >100).
2. You pick one → it shows the audios found (filtered by `InputMessagesFilterMusic`).
3. Action:
   - **Download all (with dedup)** — enqueues the full set to the manager.
   - **Select some** — checkbox if <100 audios; range selection if more.
   - **Preview (30s with afplay)** — short preview without downloading the full file.
   - **← Back to channels**
4. Confirm destination folder (default: `<project_root>/<channel>` or last used).
5. Job enqueued → back to menu.

> To play audios without saving, use the **🌐 Reproducción online (streaming)** option from the main menu — see [Online streaming](#online-streaming).

### Resume pending downloads

1. Lists channels with non-completed files.
2. Extended table showing:

   | Column | Meaning |
   |---|---|
   | Total | Audios known to the channel in local state |
   | Completed | Already 100% downloaded (verified on disk) |
   | Pending | Not finished (not started + partial) |
   | Partial | Subset of Pending with `downloaded_bytes > 0` |
   | % local | `Completed / Total` — how far along you are |
   | Missing | Bytes left to download |
   | Last folder | Recorded `destination_dir` |

3. Pick channel.
4. **Optional Telegram sync** (default: yes):
   - Queries Telegram for current channel state via `iter_messages`.
   - Compares each `message_id` against local state.
   - Adds new entries for audios the channel added since your last visit.
   - Reports: `✓ N audios nuevos agregados al state, M ya conocidos.`
5. Confirm destination folder → enqueue.

> **Why sync**: channels add music every day. Without syncing, "Resume" only processes what you already know. With syncing, the state updates first so new audios show up as pending.

> **Resume is instant**: it builds the `AudioItem` list from local state (`filename`, `size`) without re-resolving messages already known. Sync is optional — skipping it saves the query but won't detect new audios.

### View ongoing downloads

Table with all jobs (queued, running, done, failed, cancelled, interrupted).

```
Resumen: running: 1 · queued: 2 · done: 5

ID    Canal           Estado     Archivos                 Bytes               Velocidad   ETA       Actual
a3f1  House Techno    running    36/14898 (canal: 15102)  654.4 MiB / 219 GiB 1.4 MiB/s   43:57:12  Track Name
```

Actions:
- **Refresh (snapshot)** — re-renders with updated data.
- **Live view (Ctrl+C to return)** — Live mode with 2/s auto-refresh.
- **Cancel a job** — marks a job as cancelled; the worker stops at the next chunk.
- **← Main menu**

### Local library

Consolidated view of **all downloaded channels** based on local state. It tells you which channel ended up in which folder and lets you play from there.

Summary table:

```
                 📚 Biblioteca local
┌─────────────────────────┬────────┬──────────┬─────────────────────────────────────────┐
│ Canal                   │ Tracks │ Tamaño   │ Carpeta                                 │
├─────────────────────────┼────────┼──────────┼─────────────────────────────────────────┤
│ Channel A               │   320  │ 6.4 GiB  │ ~/Music/Channel A                       │
│ Channel B               │    98  │ 1.2 GiB  │ ~/Music/Channel B                       │
│ Channel C               │   410  │ 6.5 GiB  │ ~/Music/Channel C                       │
└─────────────────────────┴────────┴──────────┴─────────────────────────────────────────┘
```

Actions:

- **🎲 Global shuffle (50 random from the entire library)** — `random.sample` without replacement across **all channels**. Queue with media panel, previous/next 6, mpv controls.
- **▶️ Play a full channel** — full channel queue.
- **🔀 Play multiple channels (mixed queue)** — multi-select channels + ordering:
  - Shuffle.
  - Alphabetical by filename.
  - By channel (one after another).
- **🎵 Search and play a track** — autocomplete selector across ALL tracks of ALL channels (paginated if >100).
- **📂 View all folders** — detailed table showing each channel and its paths (useful if you downloaded the same channel into multiple folders).
- **↩️ Main menu**.

> **How it's built**: SQL query over `files` filtering `completed = 1 AND destination_dir IS NOT NULL`, then verifies each file still exists on disk. If you downloaded the same channel to several folders, all of them appear under `destination_dirs`.

### Play music from a folder

Point at any folder in the filesystem (autocomplete with Tab).
- Asks if recursive or not.
- Detects extensions: `.mp3 .m4a .mp4 .aac .ogg .oga .opus .flac .wav .aiff .aif`.
- Actions: "Play one (loop)" or "Play all in queue" (Ctrl+C cuts the queue).

---

## Player: controls

When the track is playing (with `mpv` installed):

| Key | Action (local playback) | Action (online streaming) |
|---|---|---|
| `Space` | Pause / Resume | Pause / Resume |
| `→` | +10 seconds | +10 seconds |
| `←` | −10 seconds | −10 seconds |
| `↑` | +30 seconds | +30 seconds |
| `↓` | −30 seconds | −30 seconds |
| `0` | Restart current track | Restart current track |
| `n` | — | **Next** (in queue) |
| `p` | — | **Previous** (in queue; on the first track, restarts it) |
| `q` or `Q` | Skip to next / close | **Exit the queue** |
| `Ctrl+C` | Close | Exit the queue |

> **Local vs streaming difference**: local playback only has `q`/`Ctrl+C` to close the current track and advance. Online streaming separates:
> - `n` skips to the next without waiting for it to finish.
> - `p` goes back to the previous (cancels the current prefetch and starts one for the previous track).
> - `q` and `Ctrl+C` exit the entire queue, not just the track.
>
> This mapping follows the mpv/cmus convention: `q` is always "quit", never "skip".

The panel shows:
- State: `▶ REPRODUCIENDO` (local file), `🌐 STREAMING` (online), or `⏸ PAUSADO`.
- Title · Artist · Album (from ID3 tags read with `mutagen`).
- Bitrate · sample rate · channels · file size.
- ASCII progress bar (40 chars) with real `MM:SS / MM:SS` (queried to mpv via IPC).
- For queues: extra panel with `↶ Previous`, `▶ Now`, `↷ Next 6`.
- Controls line.

Without `mpv`: similar panel but without active controls. Only `Ctrl+C` to stop.

---

## Online streaming

Plays audio **directly from Telegram without touching disk**. Bytes flow from `iter_download` to `mpv`'s stdin via an in-RAM `asyncio.Queue`. Requires `mpv` (`brew install mpv`).

### How to enter

It's a top-level option in the main menu: **🌐 Reproducción online (streaming)**. The option only appears if `mpv` is installed.

```
Main menu → 🌐 Reproducción online (streaming)
                     ↓
               Select channel (autocomplete)
                     ↓
               List channel audios
                     ↓
               Submenu: One only / Selection (queue) / All in queue / Cancel
```

Cancelling inside the submenu returns to channel selection; cancelling channel selection returns to the main menu.

### Submenu

| Option | What it does |
|---|---|
| 🎵 **One only** | Paginated selector, you pick one audio, it plays, ends. |
| 🔀 **Selection (queue)** | Multi-select (checkbox if <100 audios; range selection if more). Plays the selected ones in `message_id` order, auto-advancing. |
| 📃 **All in queue** | Queue with all the channel's audios, in order. |
| ↩️  Cancel | Returns to channel selection. |

### Queue and next-track prefetch

The "Selection" and "All in queue" options use `_stream_queue_play`, which:

1. **Builds a `PlaybackQueue`** for each track with `position`, `total`, `previous_name`, and `upcoming_names[:6]` — the player panel shows `↶ Previous · ▶ Now · ↷ Next`.
2. **Launches track N** with its own `mpv` instance (stdin pipe, new IPC socket).
3. **In parallel, pre-downloads track N+1** with `iter_download` into an in-memory `asyncio.Queue` (`Prefetch`).
4. **Auto-advances** when track N ends: N+1 already has the header (and usually the whole file) buffered → <1s start. N+2 enters as the new prefetch.
5. **Cleanup**: on `Ctrl+C`, `q` on the last track, or natural end of queue, all pending prefetch tasks are cancelled.

#### Prefetch: technical details

| Aspect | Decision | Why |
|---|---|---|
| Lookahead | Only the next (N+1) | Typical audios are 6-15 MB; caching 2 would be >30 MB in RAM for a use case (consumption) that doesn't justify it. |
| Queue size | `maxsize=128` chunks of 256 KB = ~32 MB cap | Natural backpressure if prefetch outpaces playback. |
| Cancellation | `task.cancel()` + `await` in `_cancel_prefetch` | Idempotent; tolerates `task=None` and tasks already `done()`. |
| End sentinel | `None` enqueued in the prefetch's `finally` | The mpv feed task detects `None` and closes stdin → mpv terminates. |
| Missing message | `prefetch.failed = True` + immediate sentinel | Feed ends without writing anything; mpv receives empty stdin and exits; queue advances to next. |

#### Expected latency

- **Track 1**: same as single-track stream (depends on the first chunk from Telegram, ~1-3s).
- **Tracks 2…N**: <1s between the end of the previous and the start of the next, assuming the prefetch finished (small audios) or has enough header for mpv to start.
- If the network is slow and prefetch didn't catch up, the feed task waits for bytes → mpv pauses until they arrive. No error, just latency.

### Differences vs. download-and-play

| | Download | Online stream |
|---|---|---|
| Touches disk | yes (final file) | no (everything in RAM) |
| Persistence | SQLite DB + filesystem | none; closes when you exit |
| Resume | yes (persistent offset) | no (restarts from 0) |
| Concurrency with other jobs | manager serializes 1 at a time | streaming is independent (doesn't use the manager) |
| Queue | from Local library | from the channel menu |
| Prefetch | n/a (linear download) | yes (next track in RAM) |

---

## Daemon mode (background downloads)

To keep downloads running even if you close the SSH session (homelab) or restart the CLI, run the binary as a **headless daemon**. The daemon:

- Owns the Telethon session (`*.session` SQLite — only one process at a time can open it).
- Runs the `DownloadManager` and auto-resumes `paused` jobs.
- Exposes a Unix socket (`state/daemon.sock`) with `0600` permissions for IPC commands.
- Accepts `SIGTERM`/`SIGINT` for clean shutdown: active jobs go to `paused` (no progress is lost).

### Subcommands

```bash
telegram-audio-dl                  # interactive mode (default)
telegram-audio-dl daemon           # daemon in foreground (for systemd / launchd / nohup)
telegram-audio-dl daemon --detach  # fork + setsid; parent returns 0
telegram-audio-dl status           # prints job table (requires daemon)
telegram-audio-dl status --watch   # refreshes every 2s (Ctrl+C to exit)
telegram-audio-dl status --json    # raw JSON output (for scripts)
telegram-audio-dl cancel <job_id>  # cancels an active job
telegram-audio-dl stop-daemon      # SIGTERM to daemon, waits for clean shutdown
telegram-audio-dl player           # local library only, no Telethon or daemon
```

### Daemon ↔ interactive CLI coexistence

**One Telethon session = one process.** While the daemon is running, the interactive CLI cannot open Telethon:

| Menu action | No daemon | Daemon running |
|---|---|---|
| 🔍 Buscar canales en Telegram | ✓ | ✗ ("stop the daemon first") |
| 🌐 Reproducción online (streaming) | ✓ | ✗ (Telegram busy with daemon) |
| ⏬ Reanudar descargas pendientes | ✓ | ✗ |
| 📊 Ver descargas en curso | ✓ (local manager) | ✓ (via IPC to daemon) |
| 📚 Biblioteca local | ✓ | ✓ |
| 🎵 Reproducir música de carpeta | ✓ | ✓ |

When the CLI detects the daemon (`state/daemon.pid` exists and the PID is alive), it warns at startup and shows only the compatible options. To use Telegram options, run `telegram-audio-dl stop-daemon` first.

### How to run the daemon in a homelab

#### Option A: systemd (Linux)

```bash
# 1. Copy and edit the template
cp scripts/telegram-audio-dl.service ~/.config/systemd/user/
$EDITOR ~/.config/systemd/user/telegram-audio-dl.service
#    → adjust WorkingDirectory, ExecStart and User=

# 2. Enable and start
systemctl --user daemon-reload
systemctl --user enable --now telegram-audio-dl
systemctl --user status telegram-audio-dl

# 3. Logs
journalctl --user -u telegram-audio-dl -f
# or the log file directly
tail -f state/logs/daemon.log
```

#### Option B: launchd (macOS)

```bash
cp scripts/com.telegram-audio-dl.plist ~/Library/LaunchAgents/
$EDITOR ~/Library/LaunchAgents/com.telegram-audio-dl.plist
#    → adjust absolute paths
launchctl load ~/Library/LaunchAgents/com.telegram-audio-dl.plist

# Status
launchctl list | grep telegram-audio-dl

# Stop
launchctl unload ~/Library/LaunchAgents/com.telegram-audio-dl.plist
```

#### Option C: quick, no service (for testing)

```bash
nohup telegram-audio-dl daemon > /dev/null 2>&1 &
disown
```

### Remote commands / from another terminal

While the daemon runs in the homelab, you can control it from another SSH terminal:

```bash
# Live status
telegram-audio-dl status --watch 5

# Cancel a job
telegram-audio-dl status                 # copy the job_id
telegram-audio-dl cancel a3f1b2c4

# JSON for scripts
telegram-audio-dl status --json | jq '.jobs[] | select(.state=="running")'

# Stop daemon cleanly
telegram-audio-dl stop-daemon
```

### IPC protocol

Line-delimited JSON over Unix socket. Permissions `0600` (owner only). Messages documented in `src/telegram_audio_dl/ipc.py`. Manual example with `socat`:

```bash
echo '{"cmd":"status"}' | socat - UNIX-CONNECT:state/daemon.sock
```

### Lifecycle / persistence

- `state/daemon.pid`: live daemon PID. If present but the process is gone, the next start cleans the stale file.
- `state/daemon.sock`: Unix socket. Removed on clean shutdown; zombie files from crashes are cleaned automatically on next `start`.
- `state/logs/daemon.log`: log separate from the interactive CLI (`telegram_audio_dl.log`). Rotation: 2 MiB × 5 backups.
- `state/telegram_audio_dl.db`: same SQLite database (with WAL) as the interactive CLI. Daemon writes; CLI can read in parallel (WAL doesn't block reads).

### Player on another machine

Use case: daemon running in homelab; you want to play music from YOUR machine (laptop) without disturbing the daemon:

```bash
# On the laptop, no Telethon or daemon, only local library:
telegram-audio-dl player
```

This requires the local library to be accessible (NFS / Syncthing / SSH FUSE / manual copy). The `player` subcommand doesn't open Telethon or IPC — it only reads `state/telegram_audio_dl.db` and plays with mpv/ffplay.

---

## Job states

| State | Color | Meaning |
|---|---|---|
| `queued` | gray | In queue, waiting for the worker |
| `running` | cyan | Downloading now |
| `done` | green | Completed |
| `failed` | red | Fatal error — see `error` and logs |
| `cancelled` | yellow | User cancelled it explicitly |
| `paused` | magenta | The CLI was closed while running — **auto-resumes** when reopened and connected |

**Concurrency**: 1 job at a time (avoids Telegram rate limits). Multiple enqueued jobs are processed FIFO.

**Auto-resume**: when you close the CLI with active downloads (`queued` or `running`), they go to `paused` and persist in the SQLite `jobs` table. The next time you open and the manager connects to Telegram, `_auto_resume_paused` picks them up and re-queues them with their pending items — you'll see `▶ Reanudando N descarga(s) pausada(s) automáticamente.`. **No manual action needed.** If a paused job has no local state (e.g. enqueued and closed before the worker started), it's rebuilt from Telegram with `list_audios`.

---

## Range selection (multi-select)

When a channel has >100 audios and you choose "Select some":

```
1500 audios disponibles. La tabla muestra los primeros 50; para ver más, escribe more.
Formato de rangos: 1-50,100-200,500  ·  all = todos  ·  vacío = cancelar

? Rangos (1-1500) o 'more' para siguiente página: ▌
```

| Input | Result |
|---|---|
| `1-100` | Items 1 to 100 |
| `1-50,100-200` | Two disjoint ranges |
| `5,7,12-20` | Mix of standalone numbers and ranges |
| `all` | All |
| `more` | Next table page |
| `prev` | Previous page |
| empty | Cancel |

---

## Logs and troubleshooting

### Where logs are

```
state/logs/telegram_audio_dl.log     # current log (max 2 MiB)
state/logs/telegram_audio_dl.log.1   # previous rotation
state/logs/telegram_audio_dl.log.2   # …
```

### Levels

```bash
# Default: INFO (key events)
~/.virtualenvs/telegram-audio-dl/bin/telegram-audio-dl

# Verbose: DEBUG (every player keystroke, every download chunk)
LOG_LEVEL=DEBUG ~/.virtualenvs/telegram-audio-dl/bin/telegram-audio-dl
```

### What gets logged

| Component | INFO | DEBUG |
|---|---|---|
| `cli` | Startup, menu options, errors | Every keystroke |
| `client` | Connect, list_channels, list_audios | — |
| `download_manager` | Job lifecycle (enqueued/running/done/failed) | — |
| `downloader` | Each file start, errors | Offset on resume |
| `player` | mpv start/stop, socket time, errors with stderr | Sent commands |
| `telethon` (external) | WARNING+ (FloodWait, expired session) | — |

### Quick diagnosis

```bash
# Last 50 lines
tail -50 state/logs/telegram_audio_dl.log

# Errors only
grep -E "ERROR|WARNING" state/logs/telegram_audio_dl.log

# Events of a specific job
grep "Job a3f1" state/logs/telegram_audio_dl.log

# Live stream
tail -f state/logs/telegram_audio_dl.log
```

### Common problems

| Symptom | Likely cause | Fix |
|---|---|---|
| `sqlite3.OperationalError: database is locked` (telethon) | Another CLI instance running | `pkill -f telegram_audio_dl` and retry |
| `mpv falló: mpv no creó el socket IPC` | Timeout or mpv config | Already fixed at 10s. Paste the stderr from the log if it returns |
| `RuntimeError: asyncio.run() cannot be called from a running event loop` | Mixing questionary `.ask()` with async | Use `.ask_async()` |
| `WARNING: Ignoring invalid distribution -X` | AppleDouble files in venv on external SSD | Create the venv in `~/.virtualenvs/` (APFS volume) |
| Streaming cuts mid-song | Unstable Telegram connection | Retry; mpv's 10s cache helps with short recovery |
| Download stops with no visible error | Job went to `paused` due to CLI close | Auto-resumes on reopen and connect |
| Library appears empty even with files present | DB not migrated from old JSON | `python tools/migrate_json_to_sqlite.py` |

---

## Persistent files

| File | Purpose | Typical size |
|---|---|---|
| `*.session` | Telethon session (auth) | ~50 KB |
| `state/telegram_audio_dl.db` | **SQLite**: channels, files, jobs | ~10-15 MB for 30K entries |
| `state/logs/*.log` | Rotating logs | up to 12 MB (2 MB × 6 versions) |

All are in `.gitignore`. Backup recommended before deleting `state/`.

### SQLite schema

```sql
channels(channel_id PK, channel_name, last_seen)
files(channel_id, message_id, filename, size, downloaded_bytes,
      completed, sha256, destination_dir, updated_at)
      PRIMARY KEY (channel_id, message_id)
jobs(job_id PK, channel_id, channel_name, destination, state, ...,
     started_at, finished_at, enqueued_at,
     completed_count, skipped_count, failed_count,
     bytes_done_session, bytes_done_total, total_bytes,
     total_files, channel_total_files, error)
```

**WAL** mode active: reads don't block the writer.

### Manual inspection

```bash
sqlite3 state/telegram_audio_dl.db

sqlite> SELECT channel_name, COUNT(*) AS total, SUM(completed) AS done
        FROM files JOIN channels USING(channel_id)
        GROUP BY channel_id;

sqlite> SELECT job_id, channel_name, state, completed_count || '/' || total_files
        FROM jobs ORDER BY enqueued_at DESC;
```

### Migration from JSON (if coming from older version)

```bash
~/.virtualenvs/telegram-audio-dl/bin/python tools/migrate_json_to_sqlite.py
```

Reads `state/*.json` + `state/_jobs_history.json`, populates the DB and renames the JSONs to `.bak` for safety.

---

## Tests

```bash
~/.virtualenvs/telegram-audio-dl/bin/pytest tests/
```

**220+ tests** cover:

| Suite | Tests | What it covers |
|---|---|---|
| `test_config.py` | 5 | `.env` loading, validation, defaults |
| `test_state.py` | 7 | StateStore/FileEntry, reconcile_with_disk |
| `test_downloader_pure.py` | 10 | filename sanitizer, ext by mime, sha256, dedup |
| `test_client_pure.py` | 7 | Parser of `DocumentAttributeAudio` and `DocumentAttributeFilename` |
| `test_cli_format.py` | 12 | `_fmt_size`, `_fmt_duration`, `_safe_dirname` |
| `test_metadata_and_format.py` | 10 | mutagen, `_fmt_mmss`, `_stringify_tag` |
| `test_pagination.py` | 13 | `_parse_ranges` (ranges, dedupe, edge cases) |
| `test_pending.py` | 9 | `_scan_pending` + `_sync_state_with_audios` |
| `test_library.py` | 17 | `_scan_audio_folder`, `_scan_library`, `_sample_shuffle` |
| `test_inventory_and_manager.py` | 16 | `Downloader.inventory`, `DownloadManager` (no network) |
| `test_history_and_player.py` | 23 | History persistence, paused/auto-resume, player panel, single-track streaming |
| `test_logging_setup.py` | 8 | Levels, no-duplicate handlers, telethon logger |
| `test_scenarios.py` | 10 | E2E with mock Telethon: download, cancel, resume, FloodWait, dedup |
| `test_streaming.py` | 12 | `Prefetch`, `_prefetch_audio`, queue with auto-advance + lookahead, cancellation, prev/next navigation, `PlaybackQueue` metadata |
| `test_paginated_select.py` | 9 | Guard against questionary bug: non-matching user filter string → None instead of propagating and breaking |
| `test_audio_player_detection.py` | 14 | Cross-platform detector (afplay/ffplay/mpg123/paplay), priority by OS, duration-to-flags mapping per binary |
| `test_ipc.py` | 16 | Daemon IPC protocol: server, client, errors, 0600 permissions, concurrency, daemon detection via PID file |
| `test_entrypoint.py` | 12 | argparse subcommands (`daemon`, `status`, `cancel`, `stop-daemon`, `player`), dispatch, graceful failures without daemon |
| `test_daemon_session_tuning.py` | 4 | WAL tuning of the Telethon `.session` SQLite at daemon startup |
| `tests/_db_helpers.py` | (helpers) | `seed_channel_files`, `seed_job` for fixtures |

**Total time: ~2 seconds**.

Not covered automatically (require live environment):
- `list_channels`, `list_audios`, `iter_download` against real Telegram network.
- Interactive questionary flow (needs TTY).
- mpv binary for player controls.
- End-to-end streaming (requires mpv + network). Tests use a mocked `_stream_one_track` to verify queue orchestration without spawning mpv.

For manual execution, see [`docs/escenarios.md`](docs/escenarios.md).

---

## FAQ

**Why does one job say `36/14898` and another `181/15102` for the same channel?**

Each job is built with the **pending items at the moment of enqueue**. If you enqueued 15102 and downloaded 181 before interrupting, on resume there are 14921 left. Previously completed items are not re-enqueued (dedup). The "Files" column shows `progress/total_in_this_job (channel: absolute_total)` to avoid confusion.

**How do I download new music added to the channel after my first download?**

In "Reanudar descargas pendientes" → pick channel → it asks `¿Sincronizar con Telegram?`. Say yes. It queries the channel, compares `message_id` by `message_id` against local state, and adds new entries. Those audios appear as pending in the job that gets enqueued. If you skip the sync, only what's already known is processed.

**Can I download from multiple channels in parallel?**

No. The worker processes one job at a time to avoid Telegram's `FloodWaitError`. If you enqueue two, the second waits for the first.

**How do I cancel a download without closing the CLI?**

Main menu → "Ver descargas en curso" → "Cancelar un trabajo" → pick the job. The cancel flag is honored on the next downloaded chunk.

**Does the session expire?**

Not automatically. It stays valid until you close it manually from another Telegram app (Settings → Active Sessions → Terminate).

**What happens if the file on disk is bigger than `size` from the checkpoint?**

`reconcile_with_disk` adjusts: if `actual > size`, it assumes the file is corrupt and restarts from 0. If `actual <= size`, it takes `actual` as the new starting point.

**Does it work on Linux?**

Yes. The `find_simple_audio_player()` detector resolves the difference with macOS:

| Task | macOS | Linux |
|---|---|---|
| Playback with controls | `mpv` | `mpv` (same) |
| Simple playback (fallback) | `afplay` (built-in) | `ffplay` → `mpg123` → `paplay` |
| 30s preview from "Search channels" menu | `afplay -t 30` | `ffplay -t 30` (or `mpg123 -n 1140`) |
| Keyboard listener | `termios` | `termios` (POSIX, identical) |
| mpv IPC (Unix socket in `/tmp`) | ✓ | ✓ |

The only thing that breaks on a minimal Linux box without `ffmpeg` or `mpg123`: the Local library and Preview options would be disabled. Online streaming and downloads work independently.

**Does it work on Windows?**

Untested. The keyboard listener uses `termios` (POSIX-only). It would need adapting with `msvcrt`. Linux and macOS are covered.

**How do I see which `mpv` process is running?**

```bash
ps aux | grep "mpv --input-ipc-server"
```

Each playback creates a socket at `/tmp/tg-mpv-XXXXXXXX.sock`.

**Why does track 2 start almost instantly in streaming, but track 1 takes a moment?**

The first track has no prior prefetch: it starts at the same time as `iter_download`, so it begins when the first chunk arrives from Telegram (~1-3s). While it plays, track 2 is being prefetched into an in-RAM `asyncio.Queue`. When track 1 ends, mpv for track 2 opens stdin and consumes the ready buffer → <1s start. Same goes for 3 when 2 begins.

**How much RAM does prefetch consume?**

Theoretical cap: `maxsize=128 chunks × 256 KB = 32 MB` per active prefetch. Since only the next track is prefetched (lookahead=1), the peak is ~32 MB. In practice, typical audios are 6-15 MB and the queue never fills.

**What happens if Telegram cuts off mid-prefetch?**

`prefetch.failed = True` and a sentinel `None` is enqueued. When the current track ends and the next track's feed task starts draining, it gets the sentinel without having written everything → mpv receives truncated stdin and ends prematurely. The queue advances to the next. There's no automatic retry in this version (see "E. Resilience" in the roadmap).

**Can I reorder the queue at runtime?**

Not in this version. The queue is fixed from the moment of selection. To skip to next use `n`; to go back use `p`.

**What happens if I press `p` on the first track of the queue?**

It restarts it from the beginning (no underflow, no jump to end). Equivalent to `0` (seek to start) but it relaunches mpv from scratch. If you only want to return to the start of the track without restarting the prefetch, use `0`.

**Why `p` and `n` instead of left/right arrows?**

Arrows are already assigned to seek (±10s), which is the convention in mpv and most players. Using `p`/`n` (previous/next) follows the cmus/vimusic/spotify-tui convention and avoids conflict.

---

## Known limitations

- **macOS and Linux**: both fully supported with auto-detection of the fallback player (`afplay` on macOS; `ffplay`/`mpg123`/`paplay` on Linux). Windows untested (`termios` is POSIX-only).
- **One concurrent download**: to avoid Telegram rate limits.
- **Streaming**: use **🌐 Reproducción online (streaming)** from the main menu (requires `mpv`). Bytes flow through RAM, never disk. Supports single track, selection, or full channel as a queue with auto-advance and next-track prefetch. Lookahead of 1 (no further prefetch to bound RAM). No cross-session resume — closing the CLI loses the queue.
- **No live refresh in `_jobs_view` by default**: you have to enter "Live view" because `Live` and `questionary` don't coexist in the same TTY.
- **`mpv` optional**: without it, no controls (afplay doesn't accept input).

---

## Day-to-day operation

### Start a new big download

```
1. telegram-audio-dl
2. → 🔍 Buscar canales en Telegram
3. → pick channel → "⬇️ Descargar todos"
4. → confirm destination folder
5. → ✓ Enqueued (state pre-inventoried in SQLite, you're back at the menu)
6. → 📊 Ver descargas en curso → "📺 Ver en vivo" to monitor
7. Close the terminal whenever → job goes to "paused"
```

### Resume the next day (auto-resume)

```
1. telegram-audio-dl
2. → Buscar canales en Telegram (or any option that connects the manager)
3. → "Conectando a Telegram…"
4. → "▶ Reanudando 1 descarga(s) pausada(s) automáticamente."
5. → you're back at the menu; downloads continue in the background
```

To also resume with **new audios from the channel**, use "Reanudar descargas pendientes" → pick channel → "¿Sincronizar con Telegram?" → Yes. That looks for new audios in the channel and adds them to the state before re-enqueueing.

If you skip the sync, only the already-known pending items are re-enqueued. Useful when you're offline or know the channel didn't change.

### Listen to what's downloaded while more is downloading

```
1. (you have a download running in the background)
2. → 📚 Biblioteca local
3. → 🎲 Shuffle global   (50 random across the entire library)
   or → ▶️ Reproducir un canal completo
4. Plays with media panel + mpv controls
5. The download keeps running in the background unaffected
```

### Test before downloading

```
1. → 🌐 Reproducción online (streaming)
2. → pick channel
3. → 🎵 Uno solo → pick audio → plays instantly without touching disk
4. Ctrl+C / q to end → nothing remains locally
5. If you like it: → 🔍 Buscar canales en Telegram → "Descargar todos" or "Seleccionar algunos"
```

### Sample several tracks of a new channel as a queue

```
1. → 🌐 Reproducción online (streaming)
2. → pick channel
3. → 🔀 Selección (cola)  →  mark 5-10 audios (space for checkbox, or ranges if >100)
4. The first plays with QUEUE panel showing ↶ Previous · ▶ Now · ↷ Next 6
5. While playing, the next is being prefetched into RAM
6. When the current ends: <1s start of next (thanks to prefetch)
7. n = next  ·  p = previous  ·  q or Ctrl+C = exit the entire queue
8. If you like the channel: → main menu → 🔍 Buscar canales → "Descargar todos"
```

### Wipe everything and start fresh

```bash
rm -rf state/ telegram_audio_dl.session
# ⚠ you lose the SQLite DB (channels, files, jobs) and the logs.
#   Files downloaded into their folders remain intact.
```
