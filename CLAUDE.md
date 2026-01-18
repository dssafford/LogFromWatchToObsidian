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
