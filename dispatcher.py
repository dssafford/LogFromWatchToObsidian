#!/usr/bin/env python3
"""
Dispatcher for reminders-to-obsidian sync.
Runs appropriate lists based on time of day or explicit arguments.

Usage:
    python dispatcher.py              # Run lists appropriate for current time
    python dispatcher.py morning      # Run only morning lists
    python dispatcher.py evening      # Run only evening lists
    python dispatcher.py log          # Run only the log list
    python dispatcher.py all          # Run all lists
    python dispatcher.py intention priorities  # Run specific lists
"""
import sys
import os
import json
import logging
from datetime import datetime
from pathlib import Path

from config import LISTS, SCHEDULES, LOG_FILE, STATE_DIR
from sync import sync_list


def get_state_file() -> Path:
    """Get today's state file path."""
    STATE_DIR.mkdir(exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    return STATE_DIR / f"{today}.json"


def load_state() -> dict:
    """Load today's processed lists state."""
    state_file = get_state_file()
    if state_file.exists():
        try:
            return json.loads(state_file.read_text())
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def save_state(state: dict):
    """Save processed lists state."""
    state_file = get_state_file()
    state_file.write_text(json.dumps(state, indent=2))


def mark_list_processed(list_key: str, count: int):
    """Mark a list as processed for today."""
    state = load_state()
    state[list_key] = {
        "processed_at": datetime.now().isoformat(),
        "count": count
    }
    save_state(state)


def is_list_processed(list_key: str) -> bool:
    """Check if a list has already been processed today."""
    state = load_state()
    return list_key in state


def cleanup_old_state_files():
    """Remove state files older than today."""
    if not STATE_DIR.exists():
        return
    today = datetime.now().strftime("%Y-%m-%d")
    for f in STATE_DIR.glob("*.json"):
        if f.stem != today:
            f.unlink()

# Logging setup
log = logging.getLogger()
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


def get_current_schedule() -> str:
    """Determine which schedule applies based on current hour."""
    hour = datetime.now().hour
    for schedule_name, (start, end) in SCHEDULES.items():
        if schedule_name == "always":
            continue
        if start <= hour < end:
            return schedule_name
    return "always"


def get_lists_for_schedule(schedule: str) -> list[str]:
    """Get list names that should run for a given schedule."""
    result = []
    for list_key, config in LISTS.items():
        list_schedule = config.get("schedule", "always")
        if list_schedule == schedule or list_schedule == "always":
            result.append(list_key)
    return result


def run_lists(list_keys: list[str], force: bool = False) -> tuple[int, int]:
    """Run sync for specified lists. Returns (success_count, fail_count)."""
    success_count = 0
    fail_count = 0
    skipped_count = 0

    for list_key in list_keys:
        if list_key not in LISTS:
            log.warning(f"Unknown list: {list_key}")
            fail_count += 1
            continue

        config = LISTS[list_key]

        # Skip if already processed today (except for "always" schedule lists like log)
        if not force and config.get("schedule") != "always" and is_list_processed(list_key):
            log.info(f"Skipping {list_key} (already processed today)")
            skipped_count += 1
            success_count += 1
            continue

        try:
            success, count = sync_list(config)
            if success:
                if count > 0:
                    log.info(f"Synced {count} item(s) from {list_key}")
                    # Mark as processed only if we actually synced something
                    if config.get("schedule") != "always":
                        mark_list_processed(list_key, count)
                success_count += 1
            else:
                log.error(f"Failed to sync {list_key}")
                fail_count += 1
        except Exception as e:
            log.error(f"Exception syncing {list_key}: {e}")
            fail_count += 1

    if skipped_count > 0:
        log.info(f"Skipped {skipped_count} already-processed list(s)")

    return success_count, fail_count


def main() -> int:
    log.info("=" * 50)
    log.info("Reminders-to-Obsidian dispatcher")
    log.info(f"Running as user: {os.getenv('USER')} (uid={os.getuid()})")

    # Cleanup old state files
    cleanup_old_state_files()

    args = sys.argv[1:]
    force = "--force" in args
    if force:
        args.remove("--force")
        log.info("Force mode: ignoring processed state")

    if not args:
        # Auto-detect based on time of day
        current_schedule = get_current_schedule()
        log.info(f"Current schedule: {current_schedule}")
        list_keys = get_lists_for_schedule(current_schedule)
    elif args[0] == "all":
        list_keys = list(LISTS.keys())
    elif args[0] == "morning":
        list_keys = [k for k, v in LISTS.items() if v.get("schedule") == "morning"]
        list_keys.append("log")  # Always include log
    elif args[0] == "evening":
        list_keys = [k for k, v in LISTS.items() if v.get("schedule") == "evening"]
        list_keys.append("log")  # Always include log
    else:
        # Specific list names provided
        list_keys = args

    if not list_keys:
        log.info("No lists to process")
        return 0

    log.info(f"Processing lists: {', '.join(list_keys)}")

    success_count, fail_count = run_lists(list_keys, force=force)

    log.info(f"Complete: {success_count} succeeded, {fail_count} failed")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
