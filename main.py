#!/usr/bin/env python3
import subprocess
import sys
import tempfile
import time
import os
import logging
from datetime import datetime
from pathlib import Path

# Configuration
DAILY_NOTES_FOLDER = Path("/Users/dougs/Documents/obsidian/DougVault/Daily")
REMINDERS_LIST = "Log"
RETRY_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 2
POST_WRITE_VERIFY_DELAY = 1
LOG_FILE = Path("/tmp/reminders-to-obsidian.log")

# Logging setup - both console and file
log = logging.getLogger(__name__)
log.setLevel(logging.INFO)

# Console handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
))
log.addHandler(console_handler)

# File handler
file_handler = logging.FileHandler(LOG_FILE)
file_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
))
log.addHandler(file_handler)


def get_reminders_applescript():
    """Returns AppleScript to fetch reminders."""
    return f'''
tell application "Reminders"
set output to ""
set allLists to lists
repeat with aList in allLists
if name of aList is "{REMINDERS_LIST}" then
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


def mark_reminders_complete_applescript(reminder_ids: list[str]):
    """Returns AppleScript to mark specific reminders complete by ID."""
    # Build AppleScript list of IDs
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


def run_applescript(script: str, description: str) -> tuple[bool, str]:
    """Run AppleScript by pre-compiling it first."""
    log.debug(f"Running AppleScript: {description}")
    source_path = Path("/tmp/reminders_script.applescript")
    compiled_path = Path("/tmp/reminders_script.scpt")
    try:
        # Write source script
        script_content = script.strip()
        source_path.write_text(script_content, encoding='utf-8')
        log.info(f"Script written to {source_path} ({len(script_content)} chars)")

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


def get_reminders() -> list[tuple[str, datetime, str]]:
    """Fetch incomplete reminders. Returns list of (id, timestamp, note)."""
    success, output = run_applescript(get_reminders_applescript(), "Get reminders")
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
            # Handle ISO format with Z suffix
            ts_str = ts_str.strip().replace('Z', '+00:00')
            ts = datetime.fromisoformat(ts_str)
            entries.append((rid.strip(), ts, note.strip()))
            log.debug(f"Found reminder: {rid} - {note[:50]}")
        except ValueError as e:
            log.warning(f"Skipping entry with bad timestamp: {ts_str} - {e}")
            continue

    log.info(f"Found {len(entries)} incomplete reminder(s)")
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


def get_daily_note_path(for_date: datetime) -> Path:
    """Get the path to the daily note for a given date."""
    date_str = for_date.strftime("%Y-%m-%d")
    return DAILY_NOTES_FOLDER / f"{date_str}.md"


def append_to_daily_note(entries: list[tuple[str, datetime, str]]) -> tuple[bool, list[str]]:
    """
    Append entries to the daily note.
    Returns (success, list of reminder IDs that were written).
    """
    if not entries:
        log.info("No entries to add")
        return True, []

    today = datetime.now()
    daily_note = get_daily_note_path(today)

    log.info(f"Target daily note: {daily_note}")

    if not DAILY_NOTES_FOLDER.exists():
        log.error(f"Daily notes folder does not exist: {DAILY_NOTES_FOLDER}")
        return False, []

    if not daily_note.exists():
        log.error(f"Daily note does not exist: {daily_note}")
        return False, []

    # Read current content
    try:
        content = daily_note.read_text()
    except Exception as e:
        log.error(f"Failed to read daily note: {e}")
        return False, []

    # Build new entries text
    sorted_entries = sorted(entries, key=lambda x: x[1])  # Sort by timestamp
    new_entries_text = ""
    written_ids = []
    for rid, ts, note in sorted_entries:
        new_entries_text += f"{note}\n"
        written_ids.append(rid)

    # Find insertion point
    section_header = "## 📝 Daily Log"
    if section_header not in content:
        log.error(f"Section '{section_header}' not found in daily note")
        return False, []

    header_pos = content.find(section_header)
    header_end = content.find("\n", header_pos)
    if header_end == -1:
        header_end = len(content)
    else:
        header_end += 1

    # Find the divider or next section
    divider_pos = content.find("\n---", header_end)
    next_section = content.find("\n## ", header_end)

    if divider_pos != -1 and (next_section == -1 or divider_pos < next_section):
        insert_pos = divider_pos + 1
    elif next_section != -1:
        insert_pos = next_section + 1
    else:
        insert_pos = header_end

    # Insert entries
    new_content = content[:insert_pos] + new_entries_text + content[insert_pos:]

    # Write with retry
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            daily_note.write_text(new_content)
            log.info(f"Wrote {len(written_ids)} entries to daily note (attempt {attempt})")

            # Verify write
            time.sleep(POST_WRITE_VERIFY_DELAY)
            verify_content = daily_note.read_text()

            # Check if our entries are present
            missing = []
            for rid, ts, note in sorted_entries:
                if note not in verify_content:
                    missing.append(note[:50])

            if missing:
                log.warning(f"Verification failed - missing entries: {missing}")
                if attempt < RETRY_ATTEMPTS:
                    log.info(f"Retrying write in {RETRY_DELAY_SECONDS}s...")
                    time.sleep(RETRY_DELAY_SECONDS)
                    content = daily_note.read_text()
                    continue
                else:
                    log.error("All retry attempts failed")
                    return False, []

            log.info("Write verified successfully")
            return True, written_ids

        except Exception as e:
            log.error(f"Write attempt {attempt} failed: {e}")
            if attempt < RETRY_ATTEMPTS:
                time.sleep(RETRY_DELAY_SECONDS)
            else:
                return False, []

    return False, []


def main() -> int:
    """Main entry point. Returns exit code."""
    log.info("=" * 50)
    log.info("Starting reminders-to-obsidian sync (AppleScript)")
    log.info(f"Running as user: {os.getenv('USER')} (uid={os.getuid()}), HOME={os.getenv('HOME')}")

    # Fetch reminders
    entries = get_reminders()
    if not entries:
        log.info("No reminders to process")
        return 0

    log.info(f"Processing {len(entries)} reminder(s)")
    for rid, ts, note in entries:
        log.info(f"  - [{ts.strftime('%H:%M')}] {note[:60]}")

    # Small delay to let any pending Reminders sync complete
    time.sleep(1)

    # Write to daily note
    success, written_ids = append_to_daily_note(entries)

    if not success:
        log.error("Failed to append to daily note - reminders NOT marked complete")
        return 1

    if not written_ids:
        log.info("No entries were written")
        return 0

    # Mark only the successfully written reminders as complete
    time.sleep(1)

    if mark_reminders_complete(written_ids):
        log.info(f"Successfully synced {len(written_ids)} reminder(s)")
        return 0
    else:
        log.error("Failed to mark reminders complete - entries may duplicate on next run")
        return 1


if __name__ == "__main__":
    sys.exit(main())
