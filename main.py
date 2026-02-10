#!/usr/bin/env python3
"""
iCloud-to-Obsidian sync.

Watches an iCloud folder for JSON files from Shortcuts,
writes content to the appropriate section in Obsidian daily notes,
then deletes processed files.

JSON format: {"section": "concerns", "text": "My text...", "ts": "2026-01-23T09:15:00"}
"""
import socket
import subprocess
import sys
import json
import logging
import time
from datetime import datetime
from pathlib import Path

# Prevent slow reverse DNS lookups in logging module
socket.getfqdn = socket.gethostname

import things

from config import (
    DAILY_NOTES_FOLDER, ICLOUD_INPUT_FOLDERS, LOG_FILE, SECTIONS, TEMPLATE_PATH,
    FORMAT_PLAIN, FORMAT_BLOCKQUOTE, FORMAT_BULLET, FORMAT_NUMBERED, FORMAT_CHECKBOX,
    FORMAT_BULLET_CHECKBOX,
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
    except subprocess.CalledProcessError:
        log.debug(f"brctl download failed for {path} (may need Full Disk Access)")
    except subprocess.TimeoutExpired:
        log.debug("brctl download timed out")
    except Exception as e:
        log.debug(f"Could not trigger brctl download: {e}")

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


def load_json_file(file_path: Path, retries: int = 5, delay: float = 5) -> dict | None:
    """Load a JSON file, waiting for iCloud if needed."""
    for i in range(retries):
        try:
            # Use cat to force iCloud download - shell commands trigger downloads
            # more reliably than Python's open()
            result = subprocess.run(
                ['/bin/cat', str(file_path)],
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode != 0:
                log.debug(f"cat failed (iCloud syncing?), waiting... ({i + 1}/{retries})")
                time.sleep(delay)
                continue

            content = result.stdout
            if not content.strip():
                log.debug(f"File is empty, waiting... ({i + 1}/{retries})")
                time.sleep(delay)
                continue

            return json.loads(content)

        except subprocess.TimeoutExpired:
            log.debug(f"cat timed out (iCloud syncing?), waiting... ({i + 1}/{retries})")
            time.sleep(delay)
        except json.JSONDecodeError as e:
            log.warning(f"Invalid JSON in {file_path}: {e}")
            return None
        except Exception as e:
            log.debug(f"Error reading {file_path}: {e}, waiting... ({i + 1}/{retries})")
            time.sleep(delay)

    log.error(f"Could not read {file_path} after {retries} attempts")
    return None


def get_daily_note_path(for_date: datetime = None) -> Path:
    """Get the path to the daily note for a given date."""
    if for_date is None:
        for_date = datetime.now()
    date_str = for_date.strftime("%Y-%m-%d")
    return DAILY_NOTES_FOLDER / f"{date_str}.md"


def ensure_daily_note_exists(file_path: Path) -> bool:
    """Create daily note from template if it doesn't exist. Returns True if created."""
    if file_path.exists():
        return False

    if not TEMPLATE_PATH.exists():
        log.error(f"Template not found: {TEMPLATE_PATH}")
        return False

    template = TEMPLATE_PATH.read_text()
    today = datetime.now()

    # Replace template placeholders
    template = template.replace("{{date}}", today.strftime("%Y-%m-%d"))
    template = template.replace("{{title}}", today.strftime("%Y-%m-%d"))
    template = template.replace("{{date:YYYY-MM-DD}}", today.strftime("%Y-%m-%d"))

    file_path.write_text(template)
    log.info(f"Created daily note: {file_path.name}")
    return True


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


def get_things3_today_tasks() -> list[dict]:
    """Fetch Today tasks from Things 3, sorted by today_index."""
    try:
        tasks = things.today()
        return sorted(tasks, key=lambda t: t.get('today_index', 0))
    except Exception as e:
        log.error(f"Failed to fetch Things3 tasks: {e}")
        return []


def format_things3_task(task: dict) -> str:
    """Format a Things3 task. Project name appended in parentheses if present."""
    title = task.get('title', 'Untitled')
    project = task.get('project_title')
    if project:
        return f"{title} ({project})"
    return title


def sync_things3_tasks(daily_note: Path) -> tuple[int, int]:
    """
    Sync Things3 Today tasks to the morningset section.
    Returns (success_count, fail_count).
    """
    config = SECTIONS.get("morningset")
    if not config:
        log.error("morningset section not configured")
        return 0, 1

    marker = config["marker"]
    position = config.get("position", "end")

    tasks = get_things3_today_tasks()
    if not tasks:
        log.info("Things3: No Today tasks to sync")
        return 0, 0

    log.info(f"Things3: Found {len(tasks)} Today tasks")

    # Read current content
    try:
        content = daily_note.read_text()
    except Exception as e:
        log.error(f"Failed to read daily note for Things3 sync: {e}")
        return 0, 1

    # Check if marker exists
    if marker not in content:
        log.error(f"Marker '{marker}' not found in daily note")
        return 0, 1

    # Check if tasks already synced today (look for checkboxes after marker)
    marker_pos = content.find(marker)
    section_start = content.find("\n", marker_pos) + 1
    rest_of_section = content[section_start:]
    # Find where section ends (next ## or ---)
    next_section = rest_of_section.find("\n## ")
    next_divider = rest_of_section.find("\n---")
    if next_section == -1:
        next_section = len(rest_of_section)
    if next_divider == -1:
        next_divider = len(rest_of_section)
    section_end = min(next_section, next_divider)
    section_content = rest_of_section[:section_end]

    if "- [ ]" in section_content or "- [x]" in section_content:
        log.info("Things3: Tasks already synced today, skipping")
        return 0, 0

    # Format tasks as bullet checkboxes
    formatted_lines = [f"- [ ] {format_things3_task(t)}" for t in tasks]
    for line in formatted_lines:
        log.info(f"  {line}")

    formatted = "\n".join(formatted_lines)

    # Insert at marker (use server.py style insertion for position support)
    marker_pos = content.find(marker)
    line_end = content.find("\n", marker_pos)
    if line_end == -1:
        line_end = len(content)
    else:
        line_end += 1

    if position == "start":
        # Insert right after the marker line
        insert_pos = line_end
    else:
        # Insert at end of section (before next --- or ##)
        rest = content[line_end:]
        divider_pos = rest.find("\n---")
        next_section = rest.find("\n## ")

        if divider_pos != -1 and (next_section == -1 or divider_pos < next_section):
            insert_pos = line_end + divider_pos + 1
        elif next_section != -1:
            insert_pos = line_end + next_section + 1
        else:
            insert_pos = line_end

    new_content = content[:insert_pos] + formatted + "\n" + content[insert_pos:]

    # Write
    try:
        daily_note.write_text(new_content)
        log.info(f"Things3: Wrote {len(tasks)} tasks to morningset")
        return len(tasks), 0
    except Exception as e:
        log.error(f"Failed to write Things3 tasks: {e}")
        return 0, 1


def check_health_sync(daily_note: Path) -> None:
    """
    Check if health data was synced by 6:05 AM.
    If not, write a warning to the biolog section.
    Only runs once between 6:05 and 6:15 AM.
    """
    now = datetime.now()

    # Only check between 6:05 and 6:15 AM
    if now.hour != 6 or not (5 <= now.minute <= 15):
        return

    config = SECTIONS.get("biolog")
    if not config:
        return

    marker = config["marker"]

    try:
        content = daily_note.read_text()
    except Exception:
        return

    if marker not in content:
        return

    # Check biolog section content
    marker_pos = content.find(marker)
    section_start = content.find("\n", marker_pos) + 1
    rest_of_section = content[section_start:]
    next_section = rest_of_section.find("\n## ")
    next_divider = rest_of_section.find("\n---")
    if next_section == -1:
        next_section = len(rest_of_section)
    if next_divider == -1:
        next_divider = len(rest_of_section)
    section_end = min(next_section, next_divider)
    section_content = rest_of_section[:section_end]

    # Already has health data - all good
    if "| Metric |" in section_content:
        return

    # Already warned - don't repeat
    if "Health sync failed" in section_content:
        return

    # No health data by 6:05 AM - write warning
    log.warning("Health sync: No data received by 6:05 AM - check iOS Shortcut")
    warning_msg = "> ⚠️ Health sync failed - check iOS Shortcut automation"
    new_content = insert_at_marker(content, marker, warning_msg)
    if new_content:
        daily_note.write_text(new_content)


def main() -> int:
    """Main entry point."""
    log.info("=" * 50)
    log.info("iCloud-to-Obsidian sync")

    # Ensure daily note exists (create from template if needed)
    daily_note = get_daily_note_path()
    if not daily_note.exists():
        if not ensure_daily_note_exists(daily_note):
            log.error(f"Daily note does not exist and could not be created: {daily_note}")
            return 1

    # Collect files from all input folders
    json_files = []
    for folder in ICLOUD_INPUT_FOLDERS:
        if not folder.exists():
            log.debug(f"Input folder does not exist: {folder}")
            continue
        trigger_icloud_download(folder)
        json_files.extend(folder.glob("*.json"))
        json_files.extend(folder.glob("*.txt"))

    if not json_files:
        log.info("No iCloud files to process")

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

    log.info(f"iCloud files: {success_count} succeeded, {fail_count} failed")

    # Sync Things3 tasks
    things_success, things_fail = sync_things3_tasks(daily_note)
    success_count += things_success
    fail_count += things_fail

    # Health data comes via API (POST /obsidian/health)
    # Check if it arrived by 6:05 AM, warn if not
    check_health_sync(daily_note)

    log.info(f"Complete: {success_count} succeeded, {fail_count} failed")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
