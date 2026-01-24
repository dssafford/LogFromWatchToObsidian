#!/usr/bin/env python3
"""
iCloud-to-Obsidian sync.

Watches an iCloud folder for JSON files from Shortcuts,
writes content to the appropriate section in Obsidian daily notes,
then deletes processed files.

JSON format: {"section": "concerns", "text": "My text...", "ts": "2026-01-23T09:15:00"}
"""
import subprocess
import sys
import json
import logging
import time
from datetime import datetime
from pathlib import Path

from config import (
    DAILY_NOTES_FOLDER, ICLOUD_INPUT_FOLDER, LOG_FILE, SECTIONS,
    FORMAT_PLAIN, FORMAT_BLOCKQUOTE, FORMAT_BULLET, FORMAT_NUMBERED, FORMAT_CHECKBOX,
)

# Logging setup
log = logging.getLogger(__name__)
log.setLevel(logging.INFO)

console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
))
log.addHandler(console_handler)

file_handler = logging.FileHandler(LOG_FILE)
file_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
))
log.addHandler(file_handler)


def trigger_icloud_download(path: Path, retries: int = 10, delay: float = 2) -> bool:
    """
    Force iCloud to download files in the given path.
    Returns True if path exists and is accessible.
    """
    if not path.exists():
        log.warning(f"Path does not exist: {path}")
        return False

    try:
        log.debug(f"Triggering iCloud download for: {path}")
        subprocess.run(['/usr/bin/brctl', 'download', str(path)], check=True, timeout=30)
    except subprocess.CalledProcessError as e:
        log.warning(f"brctl download failed: {e}")
    except subprocess.TimeoutExpired:
        log.warning("brctl download timed out")
    except Exception as e:
        log.warning(f"Could not trigger brctl download: {e}")

    # Wait for files to become available
    for i in range(retries):
        try:
            # Try to list the directory to verify it's accessible
            list(path.iterdir())
            return True
        except OSError as e:
            if e.errno == 11:  # Resource deadlock (iCloud syncing)
                log.debug(f"Waiting for iCloud sync... ({i + 1}/{retries})")
                time.sleep(delay)
            else:
                raise
    return True


def load_json_file(file_path: Path, retries: int = 5, delay: float = 1) -> dict | None:
    """Load a JSON file, waiting for iCloud if needed."""
    for i in range(retries):
        try:
            size = file_path.stat().st_size
            if size == 0:
                log.debug(f"File is 0 bytes, waiting... ({i + 1}/{retries})")
                time.sleep(delay)
                continue

            with open(file_path, 'r') as f:
                return json.load(f)

        except OSError as e:
            if e.errno == 11:  # Resource deadlock
                log.debug(f"File locked by iCloud... ({i + 1}/{retries})")
                time.sleep(delay)
            else:
                log.error(f"Error reading {file_path}: {e}")
                return None
        except json.JSONDecodeError as e:
            log.warning(f"Invalid JSON in {file_path}: {e}")
            return None

    log.error(f"Could not read {file_path} after {retries} attempts")
    return None


def get_daily_note_path(for_date: datetime = None) -> Path:
    """Get the path to the daily note for a given date."""
    if for_date is None:
        for_date = datetime.now()
    date_str = for_date.strftime("%Y-%m-%d")
    return DAILY_NOTES_FOLDER / f"{date_str}.md"


def format_entry(text: str, fmt: str, index: int = 1) -> str:
    """Format a single entry according to the format type."""
    if fmt == FORMAT_BLOCKQUOTE:
        return f"> {text}"
    elif fmt == FORMAT_BULLET:
        return f"- {text}"
    elif fmt == FORMAT_NUMBERED:
        return f"{index}. {text}"
    elif fmt == FORMAT_CHECKBOX:
        return f"- [ ] {text}"
    else:  # FORMAT_PLAIN
        return text


def insert_at_marker(content: str, marker: str, entry_text: str) -> str | None:
    """
    Insert entry after a marker in the content.
    Returns new content or None if marker not found.
    """
    if marker not in content:
        return None

    marker_pos = content.find(marker)
    line_end = content.find("\n", marker_pos)
    if line_end == -1:
        line_end = len(content)
    else:
        line_end += 1  # Include the newline

    # For section headers (##), find next section or divider
    if marker.startswith("##"):
        rest = content[line_end:]
        divider_pos = rest.find("\n---")
        next_section = rest.find("\n## ")

        if divider_pos != -1 and (next_section == -1 or divider_pos < next_section):
            insert_pos = line_end + divider_pos + 1
        elif next_section != -1:
            insert_pos = line_end + next_section + 1
        else:
            insert_pos = line_end
    else:
        # For field markers, replace empty placeholder line if present
        rest = content[line_end:]
        first_line_end = rest.find("\n")
        if first_line_end == -1:
            first_line_end = len(rest)
        first_line = rest[:first_line_end].strip()

        # Check if first line is an empty placeholder (>, -, 1., etc.)
        if first_line in (">", "-", "1.", "2.", "3.", ""):
            # Replace the placeholder line
            return content[:line_end] + entry_text + rest[first_line_end:]
        else:
            # Insert after marker
            insert_pos = line_end
            return content[:insert_pos] + entry_text + "\n" + content[insert_pos:]

    return content[:insert_pos] + entry_text + "\n" + content[insert_pos:]


def process_entry(entry: dict, daily_note: Path) -> bool:
    """
    Process a single entry and write to daily note.
    Returns True on success.
    """
    section = entry.get("section", "").lower()
    text = entry.get("text", "")

    # Normalize text to list (handle string, list, or string that looks like a list)
    if isinstance(text, str):
        text = text.strip()
        # Handle string that looks like JSON array: "[item1, item2]" or "[item]"
        if text.startswith("[") and text.endswith("]"):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    texts = [t.strip() for t in parsed if isinstance(t, str) and t.strip()]
                else:
                    texts = [text]
            except json.JSONDecodeError:
                # Not valid JSON, just strip the brackets
                texts = [text[1:-1].strip()] if len(text) > 2 else []
        else:
            texts = [text] if text else []
    elif isinstance(text, list):
        texts = [t.strip() for t in text if isinstance(t, str) and t.strip()]
    else:
        texts = []

    if not section or not texts:
        log.warning(f"Invalid entry - missing section or text: {entry}")
        return False

    if section not in SECTIONS:
        log.error(f"Unknown section: {section}")
        return False

    config = SECTIONS[section]
    marker = config["marker"]
    fmt = config["format"]

    # Read current content
    try:
        content = daily_note.read_text()
    except Exception as e:
        log.error(f"Failed to read daily note: {e}")
        return False

    # Format and insert all items
    formatted_lines = [format_entry(t, fmt, index=i+1) for i, t in enumerate(texts)]
    formatted = "\n".join(formatted_lines)
    new_content = insert_at_marker(content, marker, formatted)

    if new_content is None:
        log.error(f"Marker '{marker}' not found in daily note")
        return False

    # Write
    try:
        daily_note.write_text(new_content)
        log.info(f"Wrote to {section}: {text[:50]}...")
        return True
    except Exception as e:
        log.error(f"Failed to write daily note: {e}")
        return False


def main() -> int:
    """Main entry point."""
    log.info("=" * 50)
    log.info("iCloud-to-Obsidian sync")

    # Check daily note exists
    daily_note = get_daily_note_path()
    if not daily_note.exists():
        log.error(f"Daily note does not exist: {daily_note}")
        return 1

    # Trigger iCloud download
    if not ICLOUD_INPUT_FOLDER.exists():
        log.info(f"Input folder does not exist: {ICLOUD_INPUT_FOLDER}")
        return 0

    trigger_icloud_download(ICLOUD_INPUT_FOLDER)

    # Find input files (JSON or TXT from Shortcuts)
    json_files = list(ICLOUD_INPUT_FOLDER.glob("*.json")) + list(ICLOUD_INPUT_FOLDER.glob("*.txt"))
    if not json_files:
        log.info("No files to process")
        return 0

    log.info(f"Found {len(json_files)} file(s) to process")

    success_count = 0
    fail_count = 0

    for json_file in json_files:
        log.info(f"Processing: {json_file.name}")

        # Load JSON
        entry = load_json_file(json_file)
        if entry is None:
            log.error(f"Failed to load {json_file.name}")
            fail_count += 1
            continue

        # Process entry
        if process_entry(entry, daily_note):
            # Delete file on success
            try:
                json_file.unlink()
                log.info(f"Deleted: {json_file.name}")
                success_count += 1
            except Exception as e:
                log.error(f"Failed to delete {json_file.name}: {e}")
                fail_count += 1
        else:
            fail_count += 1

    log.info(f"Complete: {success_count} succeeded, {fail_count} failed")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
