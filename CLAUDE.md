# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

LogFromWatch captures quick notes from an Apple Watch and appends them to
Obsidian daily notes. Capture happens two ways: iOS Shortcuts drop JSON files
into iCloud folders, and Shortcuts POST directly to the local HTTP server.

The Apple Reminders path (an `osascript` poll of a list named "Log") was
**removed** — there is no `REMINDERS_LIST` constant and no AppleScript in this
repo. Ignore any older description that says otherwise.

## How It Works

1. `main.py` runs every 180s under launchd and drains `ICLOUD_INPUT_FOLDERS`
   of `*.json` / `*.txt` drops, routing each through `process_entry()`
2. `server.py` serves the same `process_entry()` over HTTP on port 9847
3. Both append to the current day's daily note, under the section named by the
   payload's `section` key
4. A failed entry's file is left in place, so the next run retries it

## Running

```bash
uv run --project . python main.py     # one drain pass
uv run --project . python server.py   # the HTTP server
```

Stdlib only apart from the `things` package (Things 3 sync).

## Configuration

All in `config.py`, not `main.py`:
- `DAILY_NOTES_FOLDER` — Obsidian daily notes directory
- `TEMPLATE_PATH` — daily note template used to create a missing note
- `ICLOUD_INPUT_FOLDERS` — where Shortcuts drop capture files
- `SECTIONS` — the marker/format registry mapping a payload's `section` name to
  a literal marker string in the note. **This is the extension point**: adding a
  capture target is usually one entry here plus a branch in `process_entry()`.

## Tests

No pytest and no CI. Each `test_*.py` is a standalone script of bare-assert
functions with a discovery footer; run them individually:

```bash
uv run --project . python test_mindful.py
```

Tests that exercise a write path monkeypatch `server.get_daily_note_path` to a
tempfile. Never point a test at the real vault.

## Requirements

- macOS
- Python 3.11.9+
- Obsidian vault with daily notes folder

## HTTP Server Endpoints

`server.py` runs on port `9847` via `com.dougs.logserver` launchd agent. Reachable at:
- Local: `http://192.168.4.145:9847`
- Tailscale: `http://100.104.66.106:9847`

Restart after changes: `launchctl kickstart -k gui/$UID/com.dougs.logserver`

### Installing the launch agents

Seven agents drive this repo: `logserver` (the HTTP server, KeepAlive),
`logfromwatch` (main.py every 180s), and five Oura agents on calendar triggers
(sync 08:00, verify 08:05, retry 10:00, verify-late 10:05, heartbeat 10:15). To install:

```bash
cp com.dougs.*.plist ~/Library/LaunchAgents/
for f in ~/Library/LaunchAgents/com.dougs.*.plist; do
  launchctl bootstrap gui/$UID "$f"
done
```

`launchctl list | grep dougs` shows what is registered; `launchctl bootout
gui/$UID/<label>` removes one.

**Production host is the Mac Studio** (`mac-studio`, Tailscale `100.104.66.106`).
It runs all seven agents and is the single writer to the Obsidian vault. Don't
install these agents on a second machine — the vault syncs between them, so two
hosts would double-write the daily note.

**The Tailscale CLI does not work under launchd.**
`/Applications/Tailscale.app/Contents/MacOS/Tailscale status --json` prints
"The Tailscale GUI failed to start" and exits 0 with no JSON when run from a
launchd agent -- it needs a GUI session, which is why it works over SSH but not
on a timer. Homebrew's `tailscale` is no help either: it talks to a separate,
logged-out `tailscaled`. `heartbeat_oura.py` therefore tests the phone with
plain ICMP instead of asking Tailscale anything.

**Things 3 and Full Disk Access.** A launchd-spawned process only reads the
Things 3 database if that machine has granted Full Disk Access; without it,
`things.today()` raises `unable to open database file` under launchd while
working fine from a terminal. The Mac Studio **has** the grant — verified, both
`logfromwatch` and `logserver` read Today tasks under launchd. A machine without
it will fail silently.

**Silently is the operative word.** `/sync/things3` returns the same body for a
hard failure and a successful no-op:

```
{"status":"ok","success":0,"failed":0}   # DB read failed
{"status":"ok","success":0,"failed":0}   # worked, already synced today
```

The response cannot distinguish them — only `/tmp/logserver.stderr.log` can.
That ambiguity is worth fixing if this bites again; until then, check the log
rather than trusting a `success: 0`.

### Mindful moments (GET or POST)

Mindful moments are **Daily Log lines**, not their own section — the
`Moments today` dataviewjs block in the note template scrapes `(mindful::)`
tags only from inside `## 📝 Daily Log`. A line written anywhere else is
invisible to it.

```
- 16:30 walked out of the workshop into the parking lot (mood::calm) (intensity::3) (mindful::arrived)
```

Two capture paths, both landing on the same composed line:

| Path | Payload |
| :--- | :--- |
| `POST /mindful` | `{"text":"…","mood":"calm","intensity":3,"kind":"arrived"}` |
| `GET /mindful?…` | `kind=arrived&mood=calm&intensity=3&text=…` |
| Watch dictation | `POST /obsidian/daily` with `{"section":"mindful","text":"<utterance>"}`, or the same JSON dropped in an iCloud folder |

The dictated path scans the whole utterance for vocabulary — word order does not
matter. `mindful walked out of the workshop actually there calm three arrived`
and `mindful arrived calm 3 walked out of the workshop` produce the same line.

Vocabulary lives in `mindful.py`:
- `mindful::` — `arrived`, `stopped`, `noticed`, `present`, `returned`
- `mood::` — 15 canonical words drawn from the vault's own tag history
- `intensity::` — 1–5 only; the template regex matches a **single digit**

Aliases are mapped first (`peaceful`→`calm`, `landed`→`arrived`). An unrecognized
word is never fatal: it stays in the prose and the line is written untagged, with
a warning in the response. Only an unknown `kind` on the structured endpoint is
rejected, with the valid list in `valid_kinds`.

### Retired endpoints

`/wait5` and `/breathwork/<box|478|sigh|wimhof|coherent>` still exist in
`server.py` but are **dead**. Their `**Wait-5 Tally:**` / `- Box Breathing…`
markers were removed from the daily note template on 2026-08-31, so they now
return "marker not found". The last note containing them is `2026-07-01.md`.

### Other endpoints
- `GET /health` — liveness check
- `POST /obsidian/daily` — append entry; body `{"section":"...","text":"..."}`
  (valid sections are the keys of `SECTIONS` in `config.py`)
- `GET|POST /mindful` — log a mindful moment (see above)
- `POST /obsidian/health` — ingest Auto Health Export JSON, writes to biolog
- `POST /sync/things3` — pull Today tasks into morningset
- `POST /sync/icloud` — process pending iCloud JSON files
- `POST /sync/morning` — runs Things3 + iCloud together

## WindowServer Memory Monitoring

Monitor script: `~/bin/monitor-windowserver.sh`
Logs: `~/Library/Logs/windowserver-memory.log` and `~/Library/Logs/windowserver-growth.log`

### Baseline (2026-02-01 reboot)
- **Reboot baseline**: 81-83 MB
- **After GUI activity starts**: ~257 MB (176 MB jump)
- **Growth rate during active use**: ~34 MB/hour

### How to check
```bash
# Current memory
ps aux | grep WindowServer | grep -v grep | awk '{printf "%.0f MB\n", $6/1024}'

# Recent log entries
tail -20 ~/Library/Logs/windowserver-memory.log

# Growth alerts
cat ~/Library/Logs/windowserver-growth.log
```

## gstack

Use the `/browse` skill from gstack for all web browsing. Never use `mcp__claude-in-chrome__*` tools.

Available gstack skills: `/office-hours`, `/plan-ceo-review`, `/plan-eng-review`, `/plan-design-review`, `/design-consultation`, `/design-shotgun`, `/design-html`, `/review`, `/ship`, `/land-and-deploy`, `/canary`, `/benchmark`, `/browse`, `/connect-chrome`, `/qa`, `/qa-only`, `/design-review`, `/setup-browser-cookies`, `/setup-deploy`, `/retro`, `/investigate`, `/document-release`, `/codex`, `/cso`, `/autoplan`, `/plan-devex-review`, `/devex-review`, `/careful`, `/freeze`, `/guard`, `/unfreeze`, `/gstack-upgrade`, `/learn`.
