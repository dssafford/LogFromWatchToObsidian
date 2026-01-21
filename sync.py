#!/usr/bin/env python3
"""Core sync logic for reminders-to-obsidian."""
import subprocess
import os
import time
import logging
from datetime import datetime
from pathlib import Path

from config import (
    DAILY_NOTES_FOLDER, RETRY_ATTEMPTS, RETRY_DELAY_SECONDS,
    POST_WRITE_VERIFY_DELAY, FORMAT_PLAIN, FORMAT_BLOCKQUOTE,
    FORMAT_BULLET, FORMAT_NUMBERED, FORMAT_CHECKBOX,
)

log = logging.getLogger(__name__)


def run_applescript(script: str, description: str) -> tuple[bool, str]:
    """Run AppleScript by pre-compiling it first."""
    source_path = Path("/tmp/reminders_script.applescript")
    compiled_path = Path("/tmp/reminders_script.scpt")
    try:
        script_content = script.strip()
        source_path.write_text(script_content, encoding='utf-8')

        # Compile the script first
        compile_result = subprocess.run(
            ["/usr/bin/osacompile", "-o", str(compiled_path), str(source_path)],
            capture_output=True,
            text=True,
            timeout=30
        )
        if compile_result.returncode != 0:
            log.error(f"Compile failed: {compile_result.stderr.strip()}")
            return False, compile_result.stderr.strip()

        # Run the compiled script
        result = subprocess.run(
            ["/usr/bin/osascript", str(compiled_path)],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode != 0:
            log.error(f"{description} failed: {result.stderr.strip()}")
            return False, result.stderr.strip()
        return True, result.stdout.strip()
    except subprocess.TimeoutExpired:
        log.error(f"{description} timed out after 30 seconds")
        return False, "Timeout"
    except Exception as e:
        log.error(f"{description} exception: {e}")
        return False, str(e)


def get_reminders_applescript(list_name: str) -> str:
    """Returns AppleScript to fetch incomplete reminders from a list."""
    return f'''
tell application "Reminders"
set output to ""
set allLists to lists
repeat with aList in allLists
if name of aList is "{list_name}" then
set incompleteReminders to (reminders of aList whose completed is false)
repeat with r in incompleteReminders
set remId to id of r
set remName to name of r
set remCreated to creation date of r
set y to year of remCreated
set m to text -2 thru -1 of ("0" & (month of remCreated as integer))
set d to text -2 thru -1 of ("0" & day of remCreated)
set h to text -2 thru -1 of ("0" & hours of remCreated)
set mins to text -2 thru -1 of ("0" & minutes of remCreated)
set s to text -2 thru -1 of ("0" & seconds of remCreated)
set isoDate to y & "-" & m & "-" & d & "T" & h & ":" & mins & ":" & s & "Z"
set output to output & remId & "|" & isoDate & "|" & remName & linefeed
end repeat
end if
end repeat
end tell
return output
'''


def mark_reminders_complete_applescript(reminder_ids: list[str]) -> str:
    """Returns AppleScript to mark specific reminders complete by ID."""
    ids_list = ", ".join(f'"{rid}"' for rid in reminder_ids)
    return f'''
set targetIds to {{{ids_list}}}
set markedCount to 0
tell application "Reminders"
    repeat with targetId in targetIds
        try
            set r to reminder id targetId
            set completed of r to true
            set markedCount to markedCount + 1
        end try
    end repeat
end tell
return markedCount
'''


def get_reminders(list_name: str) -> list[tuple[str, datetime, str]]:
    """Fetch incomplete reminders from a list. Returns list of (id, timestamp, note)."""
    success, output = run_applescript(
        get_reminders_applescript(list_name),
        f"Get reminders from {list_name}"
    )
    if not success:
        return []

    entries = []
    for line in output.split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = line.split("|", 2)
        if len(parts) != 3:
            log.warning(f"Skipping malformed line: {line}")
            continue
        rid, ts_str, note = parts
        try:
            ts_str = ts_str.strip().replace('Z', '+00:00')
            ts = datetime.fromisoformat(ts_str)
            entries.append((rid.strip(), ts, note.strip()))
        except ValueError as e:
            log.warning(f"Skipping entry with bad timestamp: {ts_str} - {e}")
            continue

    return entries


def mark_reminders_complete(reminder_ids: list[str]) -> bool:
    """Mark specific reminders as complete by their IDs."""
    if not reminder_ids:
        return True

    log.info(f"Marking {len(reminder_ids)} reminder(s) as complete")
    script = mark_reminders_complete_applescript(reminder_ids)
    success, output = run_applescript(script, "Mark reminders complete")

    if success:
        try:
            marked_count = int(output)
            if marked_count != len(reminder_ids):
                log.warning(f"Expected to mark {len(reminder_ids)}, but marked {marked_count}")
            else:
                log.info(f"Successfully marked {marked_count} reminder(s) complete")
        except ValueError:
            log.warning(f"Could not parse marked count: {output}")

    return success


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
        return f"{index}. [ ] {text}"
    else:  # FORMAT_PLAIN
        return text


def insert_at_marker(content: str, marker: str, entries_text: str) -> str | None:
    """
    Insert entries after a marker in the content.
    Returns new content or None if marker not found.
    """
    if marker not in content:
        return None

    marker_pos = content.find(marker)
    # Find end of marker line
    line_end = content.find("\n", marker_pos)
    if line_end == -1:
        line_end = len(content)
    else:
        line_end += 1  # Include the newline

    # For section headers (##), find next section or divider
    if marker.startswith("##"):
        # Find the divider or next section after the header
        rest = content[line_end:]
        divider_pos = rest.find("\n---")
        next_section = rest.find("\n## ")

        if divider_pos != -1 and (next_section == -1 or divider_pos < next_section):
            insert_pos = line_end + divider_pos + 1  # +1 to be after the newline
        elif next_section != -1:
            insert_pos = line_end + next_section + 1
        else:
            insert_pos = line_end
    else:
        # For field markers, insert right after the marker line
        # Skip any existing > or empty line
        insert_pos = line_end

    return content[:insert_pos] + entries_text + content[insert_pos:]


def sync_list(list_config: dict) -> tuple[bool, int]:
    """
    Sync a single reminders list to the daily note.
    Returns (success, count_synced).
    """
    list_name = list_config["reminders_list"]
    marker = list_config["marker"]
    fmt = list_config["format"]

    log.info(f"Syncing list: {list_name}")

    # Fetch reminders
    entries = get_reminders(list_name)
    if not entries:
        log.info(f"No reminders in {list_name}")
        return True, 0

    log.info(f"Found {len(entries)} reminder(s) in {list_name}")
    for rid, ts, note in entries:
        log.info(f"  - {note[:60]}")

    # Get daily note
    daily_note = get_daily_note_path()
    if not daily_note.exists():
        log.error(f"Daily note does not exist: {daily_note}")
        return False, 0

    try:
        content = daily_note.read_text()
    except Exception as e:
        log.error(f"Failed to read daily note: {e}")
        return False, 0

    # Format entries
    sorted_entries = sorted(entries, key=lambda x: x[1])
    formatted_lines = []
    for i, (rid, ts, note) in enumerate(sorted_entries, 1):
        formatted_lines.append(format_entry(note, fmt, i))

    entries_text = "\n".join(formatted_lines) + "\n"

    # Insert at marker
    new_content = insert_at_marker(content, marker, entries_text)
    if new_content is None:
        log.error(f"Marker '{marker}' not found in daily note")
        return False, 0

    # Write with retry
    written_ids = [e[0] for e in sorted_entries]
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            daily_note.write_text(new_content)
            log.info(f"Wrote {len(written_ids)} entries (attempt {attempt})")

            time.sleep(POST_WRITE_VERIFY_DELAY)
            verify_content = daily_note.read_text()

            # Verify entries present
            missing = [note for rid, ts, note in sorted_entries if note not in verify_content]
            if missing:
                log.warning(f"Verification failed - missing: {missing[:2]}")
                if attempt < RETRY_ATTEMPTS:
                    time.sleep(RETRY_DELAY_SECONDS)
                    content = daily_note.read_text()
                    continue
                else:
                    return False, 0

            log.info("Write verified successfully")
            break
        except Exception as e:
            log.error(f"Write attempt {attempt} failed: {e}")
            if attempt < RETRY_ATTEMPTS:
                time.sleep(RETRY_DELAY_SECONDS)
            else:
                return False, 0

    # Mark complete
    time.sleep(1)
    if not mark_reminders_complete(written_ids):
        log.error("Failed to mark reminders complete")
        return False, len(written_ids)

    return True, len(written_ids)
