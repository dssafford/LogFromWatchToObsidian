"""Configuration for iCloud-to-Obsidian sync."""
from pathlib import Path

# Paths
DAILY_NOTES_FOLDER = Path("/Users/dougs/Documents/obsidian/DougVault/Daily")
TEMPLATE_PATH = Path.home() / "Documents/obsidian/DougVault/templates/Daily Note Template.md"
ICLOUD_INPUT_FOLDERS = [
    Path.home() / "Library/Mobile Documents/iCloud~is~workflow~my~workflows/Documents/log_to_obsidian",
    Path.home() / "Library/Mobile Documents/iCloud~dougs~SimpleWatch/Documents/log_to_obsidian",
]
LOG_FILE = Path("/tmp/log-to-obsidian.log")

# Format types
FORMAT_PLAIN = "plain"           # Just the text
FORMAT_BLOCKQUOTE = "blockquote" # > text
FORMAT_BULLET = "bullet"         # - text
FORMAT_NUMBERED = "numbered"     # 1. 2. 3.
FORMAT_CHECKBOX = "checkbox"     # 1. [ ] 2. [ ] 3. [ ]
FORMAT_BULLET_CHECKBOX = "bullet_checkbox"  # - [ ] text

# Section configurations
# Maps section name (from JSON) → marker in daily note + format
# Optional "position": "start" inserts right after marker (default is "end" - before next section)
SECTIONS = {
    # === Morning ===
    "morningset": {
        "marker": "## 🌅 Morning Set",
        "format": FORMAT_BULLET_CHECKBOX,
        "position": "start",  # Insert right after marker, not at end of section
    },
    "intention": {
        "marker": "**Today's Intention:**",
        "format": FORMAT_BLOCKQUOTE,
    },
    "priorities": {
        "marker": "**Three Priorities:**",
        "format": FORMAT_CHECKBOX,
    },
    "concerns": {
        "marker": "**Today's anxiety/concern:**",
        "format": FORMAT_BLOCKQUOTE,
    },

    # === Bio/Health ===
    "biolog": {
        "marker": "## 🩺 Bio-Log",
        "format": FORMAT_PLAIN,  # Health sync sends pre-formatted markdown table
        "can_create_note": True,  # This section can create the daily note
    },

    # === During Day ===
    "log": {
        "marker": "## 📝 Daily Log",
        "format": FORMAT_PLAIN,
    },
    # Mindful moments are Daily Log lines: the "Moments today" dataviewjs block
    # in the note template only scrapes (mindful::) tags from inside that
    # section, so this must share the marker with "log".
    "mindful": {
        "marker": "## 📝 Daily Log",
        "format": FORMAT_PLAIN,
    },

    # === Evening ===
    "gratitude": {
        "marker": "**3 things I'm grateful for:**",
        "format": FORMAT_NUMBERED,
    },
    "wins": {
        "marker": "**One win from today:**",
        "format": FORMAT_BLOCKQUOTE,
    },
    "whatgotdone": {
        "marker": "**What got done:**",
        "format": FORMAT_BULLET,
    },
    "whatsstillopen": {
        "marker": "**What's still open (brain dump):**",
        "format": FORMAT_BULLET,
    },
    "tomorrowfirstthing": {
        "marker": "**Tomorrow's first thing:**",
        "format": FORMAT_BULLET,
    },
}
