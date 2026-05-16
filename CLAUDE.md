# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

LogFromWatch is a macOS utility that captures reminders from the Apple Reminders app (specifically a list named "Log") and appends them to Obsidian daily notes. It's designed for logging quick notes captured on Apple Watch to a markdown-based note system.

## How It Works

1. Uses AppleScript via `osascript` to fetch incomplete reminders from the "Log" list in Apple Reminders
2. Marks fetched reminders as complete
3. Appends entries to the current day's Obsidian daily note with timestamps

## Running the Script

```bash
python main.py
```

The script requires no external dependencies beyond Python 3.11.9+ standard library.

## Configuration

Hardcoded paths in `main.py`:
- `DAILY_NOTES_FOLDER`: Path to Obsidian daily notes directory
- `REMINDERS_LIST`: Name of the Reminders list to pull from ("Log")

## Requirements

- macOS (uses AppleScript for Reminders integration)
- Python 3.11.9+
- Access to Apple Reminders app
- Obsidian vault with daily notes folder

## HTTP Server Endpoints

`server.py` runs on port `9847` via `com.dougs.logserver` launchd agent. Reachable at:
- Local: `http://192.168.4.145:9847`
- Tailscale: `http://100.104.66.106:9847`

Restart after changes: `launchctl kickstart -k gui/$UID/com.dougs.logserver`

### Tally endpoints (GET or POST, no body)
Increment a counter on a `**Label:**` `` `N` `` line in today's daily note.

| Endpoint | Daily note line |
| :--- | :--- |
| `/wait5` | `**Wait-5 Tally:**` |
| `/breathwork/box` | `- Box Breathing (4-4-4-4):` |
| `/breathwork/478` | `- 4-7-8 Breathing:` |
| `/breathwork/sigh` | `- Physiological Sigh:` |
| `/breathwork/wimhof` | `- Wim Hof / Power Breath:` |
| `/breathwork/coherent` | `- Coherent Breathing (5-5):` |

Response: `{"status":"ok","count":N,"message":"ok"}` (breathwork also includes `"kind"`).

### Other endpoints
- `GET /health` — liveness check
- `POST /obsidian/daily` — append entry; body `{"section":"...","text":"..."}`
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
