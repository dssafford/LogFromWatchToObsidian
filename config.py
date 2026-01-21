"""Configuration for reminders-to-obsidian sync."""
from pathlib import Path

# Obsidian daily notes folder
DAILY_NOTES_FOLDER = Path("/Users/dougs/Documents/obsidian/DougVault/Daily")

# Sync settings
RETRY_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 2
POST_WRITE_VERIFY_DELAY = 1
LOG_FILE = Path("/tmp/reminders-to-obsidian.log")
STATE_DIR = Path("/tmp/reminders-sync-state")

# Format types for insertion
FORMAT_PLAIN = "plain"           # Just the text
FORMAT_BLOCKQUOTE = "blockquote" # > text
FORMAT_BULLET = "bullet"         # - text
FORMAT_NUMBERED = "numbered"     # 1. 2. 3.
FORMAT_CHECKBOX = "checkbox"     # 1. [ ] 2. [ ] 3. [ ]

# List configurations
# Each entry maps a Reminders list to where/how it appears in the daily note
LISTS = {
    # === Morning Lists ===
    "intention": {
        "reminders_list": "Intention",
        "marker": "**Today's Intention:**",
        "format": FORMAT_BLOCKQUOTE,
        "schedule": "morning",
    },
    "priorities": {
        "reminders_list": "3Priorities",
        "marker": "**Three Priorities:**",
        "format": FORMAT_CHECKBOX,
        "schedule": "morning",
    },
    "concerns": {
        "reminders_list": "Concerns",
        "marker": "**Today's anxiety/concern:**",
        "format": FORMAT_BLOCKQUOTE,
        "schedule": "morning",
    },

    # === During Day ===
    "log": {
        "reminders_list": "Log",
        "marker": "## 📝 Daily Log",
        "format": FORMAT_PLAIN,
        "schedule": "always",
    },

    # === Evening Lists ===
    "gratitude": {
        "reminders_list": "Gratitude",
        "marker": "**3 things I'm grateful for:**",
        "format": FORMAT_NUMBERED,
        "schedule": "evening",
    },
    "wins": {
        "reminders_list": "Wins",
        "marker": "**One win from today:**",
        "format": FORMAT_BLOCKQUOTE,
        "schedule": "evening",
    },
    "whatgotdone": {
        "reminders_list": "WhatGotDone",
        "marker": "**What got done:**",
        "format": FORMAT_BULLET,
        "schedule": "evening",
    },
    "whatsstillopen": {
        "reminders_list": "WhatsStillOpen",
        "marker": "**What's still open (brain dump):**",
        "format": FORMAT_BULLET,
        "schedule": "evening",
    },
    "tomorrowfirstthing": {
        "reminders_list": "TomorrowFirstThing",
        "marker": "**Tomorrow's first thing:**",
        "format": FORMAT_BULLET,
        "schedule": "evening",
    },
}

# Schedule definitions (hour ranges, 24h format)
SCHEDULES = {
    "morning": (5, 12),   # 5am - noon
    "evening": (17, 24),  # 5pm - midnight
    "always": (0, 24),    # always run
}
