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
import json
import logging
import re
import subprocess
import time
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

import things

from config import DAILY_NOTES_FOLDER, TEMPLATE_PATH, SECTIONS, LOG_FILE, ICLOUD_INPUT_FOLDERS
from config import FORMAT_PLAIN, FORMAT_BLOCKQUOTE, FORMAT_BULLET, FORMAT_NUMBERED, FORMAT_CHECKBOX, FORMAT_BULLET_CHECKBOX

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

    if add_timestamp and section == "log":
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

def get_metric_data(data: dict[str, Any], metric_name: str) -> list:
    """Get data points for a specific metric from Health Export payload."""
    metrics_list = data.get('data', {}).get('metrics', [])
    payload = next((item for item in metrics_list if item["name"] == metric_name), None)
    return payload['data'] if payload else []


def process_health_payload(data: dict[str, Any]) -> tuple[bool, str]:
    """Process Health Export JSON and write to biolog section."""
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
    return process_entry({"section": "biolog", "text": md_table})


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
        else:
            self._send_response(404, "Endpoints: GET /wait5, POST /obsidian/daily, /obsidian/health, /sync/things3, /sync/icloud, /sync/morning")

    def do_POST(self):
        """Handle all POST endpoints."""
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

        if self.path == "/wait5":
            success, count, message = increment_wait5_tally()
            self._send_json(200 if success else 500, {"status": "ok" if success else "error", "count": count, "message": message})
            return

        # Endpoints requiring JSON body
        if self.path not in ("/obsidian/daily", "/obsidian/health"):
            self._send_response(404, "Endpoints: GET /wait5, POST /obsidian/daily, /obsidian/health, /sync/things3, /sync/icloud, /sync/morning, /wait5")
            return

        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode()
            data = json.loads(body)

            if self.path == "/obsidian/health":
                log.info("Received health data")
                success, message = process_health_payload(data)
            else:
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
