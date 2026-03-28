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
