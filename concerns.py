#!/usr/bin/env python3
"""Sync concerns from Apple Reminders to Obsidian daily notes."""
import subprocess
import sys
import time
import logging
from datetime import datetime
from pathlib import Path

# Configuration
DAILY_NOTES_FOLDER = Path("/Users/dougs/Documents/obsidian/DougVault/Daily")
REMINDERS_LIST = "Concerns"
RETRY_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 2
POST_WRITE_VERIFY_DELAY = 1
LOG_FILE = Path("/tmp/concerns-to-obsidian.log")

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


def wake_reminders() -> bool:
    """Wake the Reminders app to avoid timeout on first query."""
    log.info("Waking Reminders app...")
    script = '''
do shell script "open -a Reminders -g"
delay 2
'''
    success, _ = run_applescript(script, "Wake Reminders")
    if not success:
        log.warning("Wake via AppleScript failed, trying direct open command")
        try:
            subprocess.run(["/usr/bin/open", "-a", "Reminders", "-g"], timeout=10)
            time.sleep(2)
        except Exception as e:
            log.warning(f"Direct open also failed: {e}")
    return True


def get_reminders_script():
    """Returns AppleScript that fetches reminders with their IDs."""
    return f'''
tell application "Reminders"
    set captureList to list "{REMINDERS_LIST}"
    set output to ""
    repeat with r in (reminders in captureList whose completed is false)
        set ts to creation date of r
        set rid to id of r
        set output to output & rid & "|" & (ts as «class isot» as string) & "|" & name of r & linefeed
    end repeat
    return output
end tell
'''


def mark_specific_reminders_complete_script(reminder_ids: list[str]):
    """Returns AppleScript that marks specific reminders complete by ID."""
    ids_list = ", ".join(f'"{rid}"' for rid in reminder_ids)
    return f'''
tell application "Reminders"
    set targetIds to {{{ids_list}}}
    set markedCount to 0
    repeat with targetId in targetIds
        try
            set r to first reminder whose id is targetId
            set completed of r to true
            set markedCount to markedCount + 1
        end try
    end repeat
    return markedCount
end tell
'''


def run_applescript(script: str, description: str) -> tuple[bool, str]:
    """Run an AppleScript and return (success, output/error)."""
    log.debug(f"Running AppleScript: {description}")
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
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
    success, output = run_applescript(get_reminders_script(), "Get reminders")
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
            ts = datetime.fromisoformat(ts_str.strip())
            entries.append((rid.strip(), ts, note.strip()))
            log.debug(f"Found reminder: {rid} - {note[:50]}")
        except ValueError as e:
            log.warning(f"Skipping entry with bad timestamp: {ts_str} - {e}")
            continue

    log.info(f"Found {len(entries)} incomplete concern(s)")
    return entries


def mark_reminders_complete(reminder_ids: list[str]) -> bool:
    """Mark specific reminders as complete by their IDs."""
    if not reminder_ids:
        return True

    log.info(f"Marking {len(reminder_ids)} concern(s) as complete")
    script = mark_specific_reminders_complete_script(reminder_ids)
    success, output = run_applescript(script, "Mark reminders complete")

    if success:
        try:
            marked_count = int(output)
            if marked_count != len(reminder_ids):
                log.warning(f"Expected to mark {len(reminder_ids)}, but marked {marked_count}")
            else:
                log.info(f"Successfully marked {marked_count} concern(s) complete")
        except ValueError:
            log.warning(f"Could not parse marked count: {output}")

    return success


def get_daily_note_path(for_date: datetime) -> Path:
    """Get the path to the daily note for a given date."""
    date_str = for_date.strftime("%Y-%m-%d")
    return DAILY_NOTES_FOLDER / f"{date_str}.md"


def update_concern_in_daily_note(entries: list[tuple[str, datetime, str]]) -> tuple[bool, list[str]]:
    """
    Update the Today's anxiety/concern section in the daily note.
    Returns (success, list of reminder IDs that were written).
    """
    if not entries:
        log.info("No concerns to add")
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

    # Combine all concerns (in case there are multiple)
    sorted_entries = sorted(entries, key=lambda x: x[1])
    concerns_text = " ".join(note for _, _, note in sorted_entries)
    written_ids = [rid for rid, _, _ in sorted_entries]

    # Find the Today's anxiety/concern section
    section_marker = "**Today's anxiety/concern:**"
    if section_marker not in content:
        log.error(f"Section '{section_marker}' not found in daily note")
        return False, []

    marker_pos = content.find(section_marker)
    marker_end = marker_pos + len(section_marker)

    # Find where the current concern content ends (next line that doesn't start with >)
    rest_of_content = content[marker_end:]
    lines = rest_of_content.split("\n")

    # Skip the first line (could be empty or have existing content on same line)
    first_line = lines[0] if lines else ""
    remaining_lines = lines[1:] if len(lines) > 1 else []

    # Find where the blockquote section ends
    end_idx = 0
    for i, line in enumerate(remaining_lines):
        stripped = line.strip()
        if stripped.startswith(">") or stripped == "":
            end_idx = i + 1
        else:
            break

    # Build the new content
    after_concern = "\n".join(remaining_lines[end_idx:]) if end_idx < len(remaining_lines) else ""

    new_content = (
        content[:marker_end] +
        "\n> " + concerns_text + "\n" +
        ("\n" if after_concern and not after_concern.startswith("\n") else "") +
        after_concern
    )

    # Write with retry
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            daily_note.write_text(new_content)
            log.info(f"Wrote concern to daily note (attempt {attempt})")

            # Verify write
            time.sleep(POST_WRITE_VERIFY_DELAY)
            verify_content = daily_note.read_text()

            if concerns_text not in verify_content:
                log.warning("Verification failed - concern not found")
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
    log.info("Starting concerns-to-obsidian sync")

    # Wake Reminders app first to avoid timeout
    if not wake_reminders():
        log.error("Failed to wake Reminders app")
        return 1

    # Fetch reminders
    entries = get_reminders()
    if not entries:
        log.info("No concerns to process")
        return 0

    log.info(f"Processing {len(entries)} concern(s)")
    for rid, ts, note in entries:
        log.info(f"  - [{ts.strftime('%H:%M')}] {note[:60]}")

    # Small delay to let any pending Reminders sync complete
    time.sleep(1)

    # Write to daily note
    success, written_ids = update_concern_in_daily_note(entries)

    if not success:
        log.error("Failed to update daily note - concerns NOT marked complete")
        return 1

    if not written_ids:
        log.info("No concerns were written")
        return 0

    # Mark only the successfully written reminders as complete
    time.sleep(1)

    if mark_reminders_complete(written_ids):
        log.info(f"Successfully synced {len(written_ids)} concern(s)")
        return 0
    else:
        log.error("Failed to mark concerns complete - entries may duplicate on next run")
        return 1


if __name__ == "__main__":
    sys.exit(main())
