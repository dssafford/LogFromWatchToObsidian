#!/usr/bin/env python3
"""
Unified server for Obsidian daily note sync.

Handles:
- Log entries from iOS Shortcuts (POST /obsidian/daily)
- Health data from Apple Health Export (POST /obsidian/health)
- Things3 task sync (POST /sync/things3)
- iCloud file processing (POST /sync/icloud)
- Morning sync - all of the above (POST /sync/morning)

Run with: uv run python server.py
"""
import csv
import io
import json
import logging
import os
import re
import subprocess
import time
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from typing import Any

import things

from config import DAILY_NOTES_FOLDER, TEMPLATE_PATH, SECTIONS, LOG_FILE, ICLOUD_INPUT_FOLDERS
from config import FORMAT_PLAIN, FORMAT_BLOCKQUOTE, FORMAT_BULLET, FORMAT_NUMBERED, FORMAT_CHECKBOX, FORMAT_BULLET_CHECKBOX
from mindful import (
    MINDFUL_WORDS, MOOD_WORDS,
    compose_line, parse_dictation, normalize_time, extract_leading_time,
    normalize_mood, normalize_kind, normalize_intensity,
)

# Server config
HOST = "0.0.0.0"  # Listen on all interfaces (needed for Tailscale)
PORT = 9847

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
        return f"{index}. [ ] {text}"
    elif fmt == FORMAT_BULLET_CHECKBOX:
        return f"- [ ] {text}"
    else:  # FORMAT_PLAIN
        return text


def replace_section_content(content: str, marker: str, new_content: str) -> str | None:
    """Replace content between a ## marker and the next --- divider."""
    if marker not in content:
        return None

    marker_pos = content.find(marker)
    line_end = content.find("\n", marker_pos)
    if line_end == -1:
        line_end = len(content)
    else:
        line_end += 1

    # Find the --- divider
    rest = content[line_end:]
    divider_pos = -1
    for i, line in enumerate(rest.split("\n")):
        if line.strip() == "---":
            divider_pos = sum(len(rest.split("\n")[j]) + 1 for j in range(i))
            break

    if divider_pos == -1:
        return None

    # Replace: marker line + blank + new content + 3 blank lines + ---
    return content[:line_end] + "\n" + new_content + "\n\n\n\n" + content[line_end + divider_pos:]


def insert_at_marker(content: str, marker: str, entry_text: str, position: str = "end") -> str | None:
    """Insert entry after a marker in the content.

    position: "start" inserts right after marker, "end" inserts before next section.
    """
    if marker not in content:
        return None

    marker_pos = content.find(marker)
    line_end = content.find("\n", marker_pos)
    if line_end == -1:
        line_end = len(content)
    else:
        line_end += 1

    inserting_before_divider = False
    if marker.startswith("##"):
        if position == "start":
            # Insert right after the marker line
            insert_pos = line_end
        else:
            # Insert at end of section (before next --- or ##)
            rest = content[line_end:]
            next_section = rest.find("\n## ")

            # Find --- divider (may have blank lines before it)
            divider_pos = -1
            for i, line in enumerate(rest.split("\n")):
                if line.strip() == "---":
                    divider_pos = sum(len(rest.split("\n")[j]) + 1 for j in range(i))
                    break
                if line.startswith("## "):
                    break

            if divider_pos != -1 and (next_section == -1 or divider_pos < next_section):
                insert_pos = line_end + divider_pos
                inserting_before_divider = True
            elif next_section != -1:
                insert_pos = line_end + next_section + 1
            else:
                insert_pos = line_end
    else:
        rest = content[line_end:]
        first_line_end = rest.find("\n")
        if first_line_end == -1:
            first_line_end = len(rest)
        first_line = rest[:first_line_end].strip()

        if first_line in (">", "-", "1.", "2.", "3.", ""):
            # Replace empty placeholder
            return content[:line_end] + entry_text + rest[first_line_end:]
        else:
            # Find end of first contiguous block of matching format
            lines = rest.split("\n")
            format_prefix = entry_text[0] if entry_text else ""  # e.g., ">" or "-"
            end_of_block = 0

            for i, line in enumerate(lines):
                stripped = line.strip()
                # Stop at section boundaries
                if stripped.startswith("**") or stripped.startswith("## ") or stripped == "---":
                    break
                # Track contiguous lines matching our format
                if stripped.startswith(format_prefix):
                    end_of_block = i + 1
                elif end_of_block > 0:
                    # We found content, then hit a non-matching line - stop here
                    break

            # Calculate insert position after the contiguous block
            insert_offset = sum(len(lines[j]) + 1 for j in range(end_of_block))
            insert_pos = line_end + insert_offset
            return content[:insert_pos] + entry_text + "\n" + content[insert_pos:]

    # Add blank line before --- dividers (needed for markdown tables)
    trailing = "\n\n" if inserting_before_divider else "\n"
    return content[:insert_pos] + entry_text + trailing + content[insert_pos:]


def process_entry(entry: dict) -> tuple[bool, str]:
    """Process an entry and write to daily note. Returns (success, message)."""
    section = entry.get("section", "").lower()
    text = entry.get("text", "")
    # Accept boolean true or string "true" for timestamp
    ts_value = entry.get("timestamp", False)
    add_timestamp = ts_value is True or ts_value == "true"

    # Normalize text to list
    if isinstance(text, str):
        text = text.strip()
        if text.startswith("[") and text.endswith("]"):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    texts = [t.strip() for t in parsed if isinstance(t, str) and t.strip()]
                else:
                    texts = [text]
            except json.JSONDecodeError:
                texts = [text[1:-1].strip()] if len(text) > 2 else []
        else:
            texts = [text] if text else []
    elif isinstance(text, list):
        texts = [t.strip() for t in text if isinstance(t, str) and t.strip()]
    else:
        texts = []

    if not section or not texts:
        return False, f"Invalid entry - missing section or text"

    if section not in SECTIONS:
        return False, f"Unknown section: {section}"

    config = SECTIONS[section]
    marker = config["marker"]
    fmt = config["format"]
    position = config.get("position", "end")
    can_create = config.get("can_create_note", False)

    daily_note = get_daily_note_path()

    # Only create daily note if section allows it (to avoid sync conflicts)
    if not daily_note.exists():
        if can_create:
            ensure_daily_note_exists(daily_note)
        else:
            return False, f"Daily note doesn't exist yet - skipping to avoid sync conflict"

    if not daily_note.exists():
        return False, f"Daily note does not exist and could not be created: {daily_note}"

    try:
        content = daily_note.read_text()
    except Exception as e:
        return False, f"Failed to read daily note: {e}"

    unknown_words: list[str] = []
    if section == "mindful":
        # Mindful lines always carry HH:MM regardless of the timestamp flag --
        # the time is what populates the Time column in the Moments dataviewjs.
        formatted_lines = []
        for t in texts:
            parsed = parse_dictation(t)
            unknown_words.extend(parsed["unknown"])
            formatted_lines.append(compose_line(
                parsed["prose"], parsed["mood"], parsed["intensity"], parsed["kind"],
                time=parsed["time"]))
    elif add_timestamp and section == "log":
        time_str = datetime.now().strftime("%H:%M")
        formatted_lines = [f"- {time_str} {t}" for t in texts]
    else:
        formatted_lines = [format_entry(t, fmt, index=i+1) for i, t in enumerate(texts)]
    formatted = "\n".join(formatted_lines)

    # Biolog replaces section content entirely (for proper table formatting)
    if section == "biolog":
        new_content = replace_section_content(content, marker, formatted)
    else:
        new_content = insert_at_marker(content, marker, formatted, position)

    if new_content is None:
        return False, f"Marker '{marker}' not found in daily note"

    try:
        daily_note.write_text(new_content)
        log.info(f"Wrote to {section}: {texts[0][:50]}...")
        if unknown_words:
            log.info(f"Unrecognized words left in prose: {unknown_words}")
            return True, f"OK: wrote to {section} (untagged: {', '.join(unknown_words)})"
        return True, f"OK: wrote to {section}"
    except Exception as e:
        return False, f"Failed to write daily note: {e}"


# --- iCloud file processing ---

def trigger_icloud_download(path: Path, retries: int = 10, delay: float = 2) -> bool:
    """Force iCloud to download files in the given path."""
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


def sync_icloud_files() -> tuple[int, int]:
    """Process JSON files from iCloud folders. Returns (success_count, fail_count)."""
    daily_note = get_daily_note_path()
    if not daily_note.exists():
        ensure_daily_note_exists(daily_note)
    if not daily_note.exists():
        log.error("Daily note does not exist and could not be created")
        return 0, 1

    json_files = []
    for folder in ICLOUD_INPUT_FOLDERS:
        if not folder.exists():
            log.debug(f"Input folder does not exist: {folder}")
            continue
        trigger_icloud_download(folder)
        json_files.extend(folder.glob("*.json"))
        json_files.extend(folder.glob("*.txt"))

    if not json_files:
        log.info("iCloud: No files to process")
        return 0, 0

    log.info(f"iCloud: Found {len(json_files)} file(s)")

    success_count = 0
    fail_count = 0

    for json_file in json_files:
        log.info(f"Processing: {json_file.name}")
        entry = load_json_file(json_file)
        if entry is None:
            log.error(f"Failed to load {json_file.name}")
            fail_count += 1
            continue

        success, message = process_entry(entry)
        if success:
            try:
                json_file.unlink()
                log.info(f"Deleted: {json_file.name}")
                success_count += 1
            except Exception as e:
                log.error(f"Failed to delete {json_file.name}: {e}")
                fail_count += 1
        else:
            log.error(f"Failed to process {json_file.name}: {message}")
            fail_count += 1

    return success_count, fail_count


# --- Things3 sync ---

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


def sync_things3_tasks() -> tuple[int, int]:
    """Sync Things3 Today tasks to the morningset section. Returns (success_count, fail_count)."""
    config = SECTIONS.get("morningset")
    if not config:
        log.error("morningset section not configured")
        return 0, 1

    marker = config["marker"]
    position = config.get("position", "end")

    daily_note = get_daily_note_path()
    if not daily_note.exists():
        ensure_daily_note_exists(daily_note)
    if not daily_note.exists():
        log.error("Daily note does not exist and could not be created")
        return 0, 1

    tasks = get_things3_today_tasks()
    if not tasks:
        log.info("Things3: No Today tasks to sync")
        return 0, 0

    log.info(f"Things3: Found {len(tasks)} Today tasks")

    try:
        content = daily_note.read_text()
    except Exception as e:
        log.error(f"Failed to read daily note: {e}")
        return 0, 1

    if marker not in content:
        log.error(f"Marker '{marker}' not found in daily note")
        return 0, 1

    # Check if already synced
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

    if "- [ ]" in section_content or "- [x]" in section_content:
        log.info("Things3: Tasks already synced today, skipping")
        return 0, 0

    # Format tasks
    formatted_lines = [f"- [ ] {format_things3_task(t)}" for t in tasks]
    for line in formatted_lines:
        log.info(f"  {line}")
    formatted = "\n".join(formatted_lines)

    # Insert
    line_end = content.find("\n", marker_pos)
    if line_end == -1:
        line_end = len(content)
    else:
        line_end += 1

    if position == "start":
        insert_pos = line_end
    else:
        rest = content[line_end:]
        divider_pos = rest.find("\n---")
        next_sec = rest.find("\n## ")
        if divider_pos != -1 and (next_sec == -1 or divider_pos < next_sec):
            insert_pos = line_end + divider_pos + 1
        elif next_sec != -1:
            insert_pos = line_end + next_sec + 1
        else:
            insert_pos = line_end

    new_content = content[:insert_pos] + formatted + "\n" + content[insert_pos:]

    try:
        daily_note.write_text(new_content)
        log.info(f"Things3: Wrote {len(tasks)} tasks to morningset")
        return len(tasks), 0
    except Exception as e:
        log.error(f"Failed to write Things3 tasks: {e}")
        return 0, 1


WAIT5_PATTERN = re.compile(r"(\*\*Wait-5 Tally:\*\*\s*`)(\d+)(`)")


def increment_wait5_tally() -> tuple[bool, int, str]:
    """Increment the Wait-5 Tally in today's daily note. Returns (success, new_count, message)."""
    daily_note = get_daily_note_path()
    if not daily_note.exists():
        ensure_daily_note_exists(daily_note)

    try:
        content = daily_note.read_text()
    except Exception as e:
        return False, 0, f"Failed to read daily note: {e}"

    match = WAIT5_PATTERN.search(content)
    if not match:
        return False, 0, "Wait-5 Tally marker not found in daily note"

    new_count = int(match.group(2)) + 1
    new_content = content[:match.start()] + f"{match.group(1)}{new_count}{match.group(3)}" + content[match.end():]

    try:
        daily_note.write_text(new_content)
        log.info(f"Wait-5 Tally incremented to {new_count}")
        return True, new_count, "ok"
    except Exception as e:
        return False, 0, f"Failed to write daily note: {e}"


# Breathwork tally: slug → label as it appears in the daily note bullet
BREATHWORK_LABELS: dict[str, str] = {
    "box": "Box Breathing (4-4-4-4)",
    "478": "4-7-8 Breathing",
    "sigh": "Physiological Sigh",
    "wimhof": "Wim Hof / Power Breath",
    "coherent": "Coherent Breathing (5-5)",
}


def increment_breathwork_tally(kind: str) -> tuple[bool, int, str]:
    """Increment a breathwork tally line in today's daily note. Returns (success, new_count, message)."""
    label = BREATHWORK_LABELS.get(kind)
    if not label:
        return False, 0, f"Unknown breathwork kind: {kind}. Valid: {', '.join(BREATHWORK_LABELS)}"

    daily_note = get_daily_note_path()
    if not daily_note.exists():
        ensure_daily_note_exists(daily_note)

    try:
        content = daily_note.read_text()
    except Exception as e:
        return False, 0, f"Failed to read daily note: {e}"

    pattern = re.compile(r"(- " + re.escape(label) + r":\s*`)(\d+)(`)")
    match = pattern.search(content)
    if not match:
        return False, 0, f"Breathwork tally marker '{label}' not found in daily note"

    new_count = int(match.group(2)) + 1
    new_content = content[:match.start()] + f"{match.group(1)}{new_count}{match.group(3)}" + content[match.end():]

    try:
        daily_note.write_text(new_content)
        log.info(f"Breathwork '{label}' incremented to {new_count}")
        return True, new_count, "ok"
    except Exception as e:
        return False, 0, f"Failed to write daily note: {e}"


# --- Mindful moments ---

def handle_mindful(params: dict) -> tuple[bool, dict]:
    """Write one mindful moment from structured fields.

    Unlike the dictated path, the caller here named its fields, so an unknown
    `kind` is worth rejecting outright -- a Shortcut can surface the valid list
    immediately. An unknown `mood` or `intensity` only degrades to an untagged
    line, because a captured moment should never be lost over a stray word.
    """
    warnings: list[str] = []

    raw_kind = params.get("kind") or params.get("mindful")
    kind = normalize_kind(raw_kind)
    if not kind:
        return False, {
            "message": f"Unknown mindful kind: {raw_kind!r}",
            "valid_kinds": list(MINDFUL_WORDS),
        }

    raw_mood = params.get("mood")
    mood = normalize_mood(raw_mood)
    if raw_mood and not mood:
        warnings.append(f"unknown mood {raw_mood!r} - left untagged")

    raw_intensity = params.get("intensity")
    intensity = normalize_intensity(raw_intensity)
    if raw_intensity not in (None, "") and not intensity:
        warnings.append(f"intensity {raw_intensity!r} out of range 1-5 - left untagged")

    prose = (params.get("text") or params.get("prose") or "").strip()

    # An explicit time wins; otherwise honour one the client stamped onto the text.
    raw_time = params.get("time")
    moment_time = normalize_time(raw_time)
    if raw_time and not moment_time:
        warnings.append(f"unrecognized time {raw_time!r} - used the clock instead")
    if not moment_time:
        moment_time, prose = extract_leading_time(prose)

    line = compose_line(prose, mood, intensity, kind, time=moment_time)

    daily_note = get_daily_note_path()
    if not daily_note.exists():
        # Match the "log" section: never create the note, to avoid racing
        # Obsidian sync. The watch path retries until the note exists.
        return False, {"message": "Daily note doesn't exist yet - skipping to avoid sync conflict"}

    marker = SECTIONS["mindful"]["marker"]
    try:
        content = daily_note.read_text()
    except Exception as e:
        return False, {"message": f"Failed to read daily note: {e}"}

    new_content = insert_at_marker(content, marker, line, "end")
    if new_content is None:
        return False, {"message": f"Marker '{marker}' not found in daily note"}

    try:
        daily_note.write_text(new_content)
    except Exception as e:
        return False, {"message": f"Failed to write daily note: {e}"}

    log.info(f"Mindful moment: {line}")
    return True, {
        "message": "ok",
        "line": line,
        "kind": kind,
        "mood": mood,
        "intensity": intensity,
        "warnings": warnings,
    }


def mindful_params_from_query(path: str) -> dict:
    """Flatten a GET query string into the dict handle_mindful expects."""
    query = parse_qs(urlparse(path).query)
    return {k: v[0] for k, v in query.items() if v}


def sync_morning() -> dict:
    """Run all morning sync tasks. Returns summary dict."""
    results = {}

    # Things3 first (tasks for the day)
    things_success, things_fail = sync_things3_tasks()
    results["things3"] = {"success": things_success, "failed": things_fail}

    # iCloud files
    icloud_success, icloud_fail = sync_icloud_files()
    results["icloud"] = {"success": icloud_success, "failed": icloud_fail}

    total_success = things_success + icloud_success
    total_fail = things_fail + icloud_fail
    results["total"] = {"success": total_success, "failed": total_fail}

    log.info(f"Morning sync complete: {total_success} succeeded, {total_fail} failed")
    return results


# --- Health data processing ---

class HealthParseError(Exception):
    """Raised when a health POST body can't be turned into a metrics payload."""


# CSV header keyword (lowercased substring) -> internal metric name.
# The iPhone "Health Auto Export" action periodically reverts from JSON to CSV
# (and the shortcut's export action resets to "Choose"); rather than failing the
# whole day with an opaque "Invalid JSON: char 0", we parse the CSV into the same
# {"data": {"metrics": [...]}} shape the JSON export produces and reuse one path.
_CSV_METRIC_KEYWORDS = [
    ("heart rate variability", "heart_rate_variability"),
    ("hrv", "heart_rate_variability"),
    ("resting heart rate", "resting_heart_rate"),
    ("step count", "step_count"),
    ("steps", "step_count"),
    ("mindful", "mindful_minutes"),
]

# Sleep columns look like "Sleep Analysis [Asleep] (hr)"; map bracket/stage words
# to the sub-field names process_health_payload reads off a sleep_analysis point.
_CSV_SLEEP_KEYWORDS = [
    ("in bed", "inBed"),
    ("inbed", "inBed"),
    ("total sleep", "totalSleep"),
    ("totalsleep", "totalSleep"),
    ("asleep", "asleep"),
    ("deep", "deep"),
    ("rem", "rem"),
    ("core", "core"),
    ("awake", "awake"),
]


def _to_float(s: str):
    s = (s or "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _map_csv_header(header: str):
    """Return ("metric", name) or ("sleep", subfield) for a CSV column, else None."""
    hl = header.lower()
    if "sleep" in hl:
        for kw, key in _CSV_SLEEP_KEYWORDS:
            if kw in hl:
                return ("sleep", key)
        return None
    for kw, name in _CSV_METRIC_KEYWORDS:
        if kw in hl:
            return ("metric", name)
    return None


def csv_to_health_payload(text: str) -> dict[str, Any]:
    """Convert a Health Auto Export CSV body into the JSON export's dict shape.

    Recognized numeric columns (steps, HRV, RHR, mindful) become one point per
    row so the existing sum/average + latest-day logic applies unchanged. Sleep
    stage columns are summed per calendar day into a single sleep_analysis point.
    Unrecognized columns are ignored (graceful degradation). Raises
    HealthParseError if nothing usable is found.
    """
    rows = [r for r in csv.reader(io.StringIO(text)) if any((c or "").strip() for c in r)]
    if not rows:
        raise HealthParseError("CSV had no rows")

    header = rows[0]
    date_idx = 0
    for i, h in enumerate(header):
        hl = h.lower()
        if "date" in hl or "time" in hl:
            date_idx = i
            break

    col_map = {}
    for i, h in enumerate(header):
        if i == date_idx:
            continue
        mapped = _map_csv_header(h)
        if mapped:
            col_map[i] = mapped
    if not col_map:
        raise HealthParseError(
            "CSV recognized no health columns; header=" + ",".join(header)[:200]
        )

    metric_points: dict[str, list] = {}
    sleep_by_day: dict[str, dict] = {}
    sleep_date_of_day: dict[str, str] = {}

    for row in rows[1:]:
        if len(row) <= date_idx:
            continue
        date = row[date_idx].strip()
        day = date[:10]
        for i, (kind, key) in col_map.items():
            if i >= len(row):
                continue
            val = _to_float(row[i])
            if val is None:
                continue
            if kind == "metric":
                metric_points.setdefault(key, []).append({"date": date, "qty": val})
            else:  # sleep stage
                fields = sleep_by_day.setdefault(day, {})
                fields[key] = fields.get(key, 0.0) + val
                sleep_date_of_day.setdefault(day, date)

    metrics = [{"name": name, "data": pts} for name, pts in metric_points.items()]

    if sleep_by_day:
        sleep_points = []
        for day, fields in sleep_by_day.items():
            point = {"date": sleep_date_of_day[day]}
            point.update(fields)
            if "totalSleep" not in point:
                if "asleep" in point:
                    point["totalSleep"] = point["asleep"]
                else:
                    point["totalSleep"] = sum(point.get(k, 0.0) for k in ("deep", "rem", "core"))
            if "asleep" not in point:
                point["asleep"] = point.get("totalSleep", 0.0)
            sleep_points.append(point)
        metrics.append({"name": "sleep_analysis", "data": sleep_points})

    log.warning(
        "Health payload was CSV, not JSON — auto-parsed %d column(s). "
        "Fix the iPhone Health Auto Export format back to JSON.", len(col_map)
    )
    return {"data": {"metrics": metrics}}


def parse_health_body(body: str, content_type: str = "") -> dict[str, Any]:
    """Turn a raw /obsidian/health POST body into a metrics dict.

    Accepts JSON (normal) or CSV (degraded fallback). Raises HealthParseError
    with an actionable message for empty/unparseable bodies instead of leaking
    the opaque 'Invalid JSON: Expecting value: line 1 column 1 (char 0)'.
    """
    stripped = body.strip()
    if not stripped:
        raise HealthParseError(
            "empty body — Health Export produced no data "
            "(check the shortcut actually ran and the date range isn't empty)"
        )
    looks_json = stripped[0] in "{["
    is_csv_content_type = "csv" in content_type.lower()
    if looks_json and not is_csv_content_type:
        try:
            return json.loads(stripped)
        except json.JSONDecodeError as e:
            raise HealthParseError(f"body starts like JSON but failed to parse: {e}")
    return csv_to_health_payload(stripped)


def _latest_day_points(points: list) -> list:
    """Keep only the points from the most recent calendar day present.

    Defense-in-depth: a Health Export payload is expected to cover a single
    day (shortcut range = "Yesterday"). If a shortcut default flips to a
    multi-day range, summing every point would inflate the Bio-Log. Here we
    collapse to just the latest day so totals stay per-day.
    """
    def day_of(pt):
        d = pt.get('date') if isinstance(pt, dict) else None
        return d[:10] if isinstance(d, str) and len(d) >= 10 else None
    days = [day_of(p) for p in points]
    days = [d for d in days if d]
    if not days:
        return points  # no usable date info -> leave untouched (old behavior)
    target = max(days)  # YYYY-MM-DD sorts lexicographically == chronologically
    kept = [p for p in points if day_of(p) == target]
    if len(kept) < len(points):
        log.info(
            "Multi-day payload: collapsed %d point(s) to %d for latest day %s",
            len(points), len(kept), target,
        )
    return kept


def get_metric_data(data: dict[str, Any], metric_name: str) -> list:
    """Get data points for a specific metric from Health Export payload."""
    metrics_list = data.get('data', {}).get('metrics', [])
    payload = next((item for item in metrics_list if item["name"] == metric_name), None)
    points = payload['data'] if payload else []
    return _latest_day_points(points)


HEALTH_STATE_FILE = Path(__file__).resolve().parent / ".health_last_run.json"


def _health_recorded_today() -> bool:
    """True if a real health payload was already recorded today."""
    try:
        state = json.loads(HEALTH_STATE_FILE.read_text())
        return state.get("date") == datetime.now().strftime("%Y-%m-%d")
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False


def _mark_health_recorded() -> None:
    """Record today as done so later reruns no-op."""
    try:
        HEALTH_STATE_FILE.write_text(
            json.dumps({"date": datetime.now().strftime("%Y-%m-%d")})
        )
    except OSError as e:
        log.warning(f"Could not write health state file: {e}")


def process_health_payload(data: dict[str, Any]) -> tuple[bool, str]:
    """Process Health Export JSON and write to biolog section."""
    # Idempotency: once a real health payload has been recorded today, skip
    # reruns (multiple automations + manual fallback all POST; first win).
    if _health_recorded_today():
        log.info("Health already recorded today; skipping rerun.")
        return True, "Health already recorded today; skipping rerun."
    # Extract metrics
    step_data = get_metric_data(data, 'step_count')
    steps = int(sum(item.get('qty', 0) for item in step_data))

    hrv_data = get_metric_data(data, 'heart_rate_variability')
    hrv_values = [item.get('qty', 0) for item in hrv_data]
    hrv = sum(hrv_values) / len(hrv_values) if hrv_values else 0.0

    rhr_data = get_metric_data(data, 'resting_heart_rate')
    rhr_values = [item.get('qty', 0) for item in rhr_data]
    rhr = sum(rhr_values) / len(rhr_values) if rhr_values else 0.0

    sleep_list = get_metric_data(data, 'sleep_analysis')
    if sleep_list:
        daily = sleep_list[0]
        in_bed = daily.get('inBed', 0)
        asleep = daily.get('asleep', 0)
        efficiency = (asleep / in_bed * 100) if in_bed > 0 else 0.0
        sleep = {
            "total": daily.get('totalSleep', 0.0),
            "deep": daily.get('deep', 0.0),
            "rem": daily.get('rem', 0.0),
            "efficiency": efficiency
        }
    else:
        sleep = {"total": 0.0, "deep": 0.0, "rem": 0.0, "efficiency": 0.0}

    mindful_data = get_metric_data(data, 'mindful_minutes')
    mindful = sum(item.get('qty', 0) for item in mindful_data)

    # Generate markdown table
    timestamp = datetime.now().strftime("%H:%M")
    step_status = "+" if steps > 8000 else "-"
    sleep_status = "+" if sleep['total'] > 7 else "-"
    deep_status = "+" if sleep['deep'] > 1.0 else "-"
    hrv_status = "+" if hrv > 40 else "-"
    mindful_status = "+" if mindful >= 10 else "-"

    md_table = f"""| Metric | Value | Status | ({timestamp}) |
| :--- | :--- | :--- | :--- |
| **Steps** | `{steps}` | {step_status} | |
| **Sleep** | `{sleep['total']:.2f}h` | {sleep_status} | |
| **Deep Sleep** | `{sleep['deep']:.2f}h` | {deep_status} | |
| **HRV** | `{hrv:.0f} ms` | {hrv_status} | |
| **RHR** | `{rhr:.0f} bpm` | | |
| **Mindful** | `{mindful:.0f} min` | {mindful_status} | |



"""

    log.info(f"Health: steps={steps}, sleep={sleep['total']:.2f}h, hrv={hrv:.0f}, rhr={rhr:.0f}")

    # Write to biolog section using existing process_entry
    success, message = process_entry({"section": "biolog", "text": md_table})

    # Only lock the day when the write succeeded AND the payload carried real
    # data, so an early empty/partial export can't block a later real one.
    has_data = steps > 0 or sleep['total'] > 0 or hrv > 0 or rhr > 0 or mindful > 0
    if success and has_data:
        _mark_health_recorded()

    return success, message


# --- Oura sync (primary Bio-Log source, replaces iPhone Health Auto Export) ---

def _write_oura_failure(reason: str) -> None:
    """Replace the Bio-Log section with a visible failure marker."""
    try:
        process_entry({"section": "biolog", "text": f"> ⚠️ Oura sync failed — {reason}"})
    except Exception as e:
        log.error(f"Could not write Oura failure note: {e}")


OURA_STATE_FILE = Path(__file__).resolve().parent / ".oura_last_run.json"


def _oura_synced_today() -> bool:
    """True if a Bio-Log with the note day's own night was already written."""
    try:
        state = json.loads(OURA_STATE_FILE.read_text())
        return state.get("date") == datetime.now().strftime("%Y-%m-%d")
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False


def _mark_oura_synced() -> None:
    """Record today as done so the 10:00 retry no-ops after a good 08:00."""
    try:
        OURA_STATE_FILE.write_text(
            json.dumps({"date": datetime.now().strftime("%Y-%m-%d")})
        )
    except OSError as e:
        log.warning(f"Could not write Oura state file: {e}")


SHORTCUT_STATE_FILE = Path(__file__).resolve().parent / ".shortcut_last_seen.json"


def _record_shortcut_checkin(client_ip: str, path: str) -> None:
    """Remember when the iPhone Shortcut last reached us.

    Only non-loopback hits on the Oura endpoints count: the 08:00 cron calls
    the same paths over 127.0.0.1, and a laptop poking /health is not the
    phone. heartbeat_oura.py reads this to tell a dead Shortcut channel from a
    merely quiet one -- when the phone leaves the tailnet the request never
    arrives, so silence here is the only trace.
    """
    if not path.startswith("/sync/oura"):
        return
    if client_ip.startswith("127.") or client_ip == "::1":
        return
    try:
        tmp = SHORTCUT_STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"ts": time.time(), "ip": client_ip}))
        os.replace(tmp, SHORTCUT_STATE_FILE)  # atomic; the server is threaded
    except OSError as e:
        log.warning(f"Could not record Shortcut check-in: {e}")


def sync_oura() -> tuple[bool, str]:
    """Pull today's Bio-Log from the Oura API and write it to the daily note."""
    try:
        import oura_client
    except Exception as e:
        return False, f"oura_client import failed: {e}"

    day = datetime.now().strftime("%Y-%m-%d")
    try:
        token = oura_client.OuraAuth().get_access_token()
        responses = oura_client.fetch_oura_day(token, day)
        if responses.get("_session_error"):
            log.warning(f"Oura session/mindfulness skipped: {responses['_session_error']}")
        metrics = oura_client.build_bio_log(responses, day)
    except oura_client.OuraError as e:
        log.error(f"Oura sync failed: {e}")
        _write_oura_failure(str(e))
        return False, f"Oura sync failed: {e}"
    except Exception as e:
        log.error(f"Oura sync error: {e}")
        _write_oura_failure(str(e))
        return False, f"Oura sync error: {e}"

    if not oura_client.has_real_data(metrics):
        log.warning("Oura returned no usable data (ring not synced yet?)")
        _write_oura_failure("Oura returned no data yet (ring not synced?)")
        return False, "Oura returned no data"

    table = oura_client.render_bio_log_table(metrics, datetime.now().strftime("%H:%M"))
    success, message = process_entry({"section": "biolog", "text": table})
    if success:
        _mark_oura_synced()
        log.info(
            f"Oura: steps={metrics['steps']} sleep={metrics['sleep']['total']}h "
            f"hrv={metrics['hrv']:.0f} rhr={metrics['rhr']:.0f} "
            f"readiness={metrics['readiness']} mindful={metrics['mindful']}"
        )
    return success, message


def sync_oura_retry() -> tuple[bool, str]:
    """Second pass for a ring that had not uploaded by 08:00.

    The 08:00 job only succeeds once Oura has a row for the note day; when the
    ring uploads later the note is left showing the failure marker. This runs
    again mid-morning and no-ops if 08:00 already got a real night, so a good
    table (and its 'synced 08:00' caption) is never rewritten.
    """
    if _oura_synced_today():
        log.info("Oura already synced today; retry skipped.")
        return True, "Oura already synced today; retry skipped."
    log.info("Oura retry: 08:00 had no night data, trying again")
    return sync_oura()


class LogHandler(BaseHTTPRequestHandler):
    """HTTP request handler for log entries."""

    timeout = 30

    def _send_response(self, status: int, message: str):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        response = json.dumps({"status": "ok" if status == 200 else "error", "message": message})
        self.wfile.write(response.encode())

    def _send_json(self, status: int, data: dict):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_GET(self):
        """Health check endpoint."""
        if self.path == "/health":
            self._send_response(200, "Server is running")
        elif self.path == "/wait5":
            success, count, message = increment_wait5_tally()
            self._send_json(200 if success else 500, {"status": "ok" if success else "error", "count": count, "message": message})
        elif self.path.startswith("/breathwork/"):
            kind = self.path[len("/breathwork/"):]
            success, count, message = increment_breathwork_tally(kind)
            self._send_json(200 if success else 500, {"status": "ok" if success else "error", "kind": kind, "count": count, "message": message})
        elif self.path.split("?")[0] == "/mindful":
            success, result = handle_mindful(mindful_params_from_query(self.path))
            self._send_json(200 if success else 400,
                            {"status": "ok" if success else "error", **result})
        elif self.path == "/sync/oura":
            success, message = sync_oura()
            self._send_json(200 if success else 500, {"status": "ok" if success else "error", "message": message})
        elif self.path == "/sync/oura/retry":
            success, message = sync_oura_retry()
            self._send_json(200 if success else 500, {"status": "ok" if success else "error", "message": message})
        else:
            self._send_response(404, "Endpoints: GET /health, /mindful?kind=&mood=&intensity=&text=, /sync/oura, /sync/oura/retry, POST /mindful, /obsidian/daily, /obsidian/health, /sync/things3, /sync/icloud, /sync/morning")

    def do_POST(self):
        """Handle all POST endpoints."""
        _record_shortcut_checkin(self.client_address[0], self.path)
        # Sync endpoints (no body required)
        if self.path == "/sync/things3":
            log.info("Sync: Things3")
            success, fail = sync_things3_tasks()
            self._send_json(200, {"status": "ok", "success": success, "failed": fail})
            return

        if self.path == "/sync/icloud":
            log.info("Sync: iCloud files")
            success, fail = sync_icloud_files()
            self._send_json(200, {"status": "ok", "success": success, "failed": fail})
            return

        if self.path == "/sync/morning":
            log.info("Sync: Morning (Things3 + iCloud)")
            results = sync_morning()
            self._send_json(200, {"status": "ok", **results})
            return

        if self.path == "/sync/oura":
            log.info("Sync: Oura")
            success, message = sync_oura()
            self._send_json(200 if success else 500,
                            {"status": "ok" if success else "error", "message": message})
            return

        if self.path == "/sync/oura/retry":
            success, message = sync_oura_retry()
            self._send_json(200 if success else 500,
                            {"status": "ok" if success else "error", "message": message})
            return

        if self.path == "/wait5":
            success, count, message = increment_wait5_tally()
            self._send_json(200 if success else 500, {"status": "ok" if success else "error", "count": count, "message": message})
            return

        if self.path.startswith("/breathwork/"):
            kind = self.path[len("/breathwork/"):]
            success, count, message = increment_breathwork_tally(kind)
            self._send_json(200 if success else 500, {"status": "ok" if success else "error", "kind": kind, "count": count, "message": message})
            return

        # Endpoints requiring a body
        if self.path not in ("/obsidian/daily", "/obsidian/health", "/mindful"):
            self._send_response(404, "Endpoints: GET /health, /mindful?kind=&mood=&intensity=&text=, /sync/oura, /sync/oura/retry, POST /mindful, /obsidian/daily, /obsidian/health, /sync/things3, /sync/icloud, /sync/morning")
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode()

        if self.path == "/mindful":
            try:
                params = json.loads(body) if body.strip() else {}
            except json.JSONDecodeError as e:
                self._send_json(400, {"status": "error", "message": f"Invalid JSON: {e}"})
                return
            if not isinstance(params, dict):
                self._send_json(400, {"status": "error", "message": "Body must be a JSON object"})
                return
            success, result = handle_mindful(params)
            self._send_json(200 if success else 400,
                            {"status": "ok" if success else "error", **result})
            return

        # Health endpoint tolerates CSV (shortcut format reverts) and gives an
        # actionable error on empty bodies instead of the opaque char-0 message.
        if self.path == "/obsidian/health":
            try:
                data = parse_health_body(body, self.headers.get("Content-Type", ""))
            except HealthParseError as e:
                log.error(f"Health payload rejected: {e}")
                self._send_response(400, f"Health payload rejected: {e}")
                return
            except Exception as e:
                log.error(f"Error parsing health body: {e}")
                self._send_response(500, f"Server error: {e}")
                return
            log.info("Received health data")
            try:
                success, message = process_health_payload(data)
            except Exception as e:
                log.error(f"Error processing health payload: {e}")
                self._send_response(500, f"Server error: {e}")
                return
            self._send_response(200 if success else 400, message)
            return

        # /obsidian/daily — JSON only
        try:
            data = json.loads(body)
            log.info(f"Received: {data}")
            success, message = process_entry(data)
            self._send_response(200 if success else 400, message)
        except json.JSONDecodeError as e:
            log.error(f"Invalid JSON: {e}")
            self._send_response(400, f"Invalid JSON: {e}")
        except Exception as e:
            log.error(f"Error processing request: {e}")
            self._send_response(500, f"Server error: {e}")

    def log_message(self, format, *args):
        """Suppress default logging (we use our own)."""
        pass

    def address_string(self):
        """Return client IP without reverse DNS lookup."""
        return self.client_address[0]


class LogServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    log.info(f"Starting server on {HOST}:{PORT}")
    server = LogServer((HOST, PORT), LogHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
