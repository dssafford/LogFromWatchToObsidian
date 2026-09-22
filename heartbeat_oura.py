#!/usr/bin/env python3
"""Flag a dead iPhone -> Studio Shortcut channel.

The "Oura update" Shortcut POSTs /sync/oura over Tailscale 30s after every
Oura app open. When the phone drops off the tailnet the request never lands,
so nothing is logged and no failure marker is written -- and the 08:00 cron
(127.0.0.1) keeps the Bio-Log looking complete, hiding the outage. That is
exactly how 2026-09-21 went unnoticed for 13 hours.

Two signals, because they fail differently: last-contact is historical and
cannot tell "phone died" from "never opened the app", while reachability is
live but blind to a disabled automation. Together they name the cause.

Reachability is plain ICMP to the address the phone last checked in from, not
`tailscale status`: the bundled Tailscale.app CLI needs a GUI session and
fails under launchd ("The Tailscale GUI failed to start"), and the Homebrew
CLI talks to a different, logged-out daemon. Using the check-in address also
means no hardcoded IP -- it follows the phone's node churn (iphone182 ->
iphone193 -> ...) on its own.

Runs at 10:15, after the last retry (10:00) and verify-late (10:05).
    python heartbeat_oura.py [YYYY-MM-DD]
"""
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from config import DAILY_NOTES_FOLDER
from verify_oura import BIOLOG_MARKER, extract_biolog_section

STATE_FILE = Path(__file__).resolve().parent / ".shortcut_last_seen.json"
HEARTBEAT_LOG = Path.home() / "Library/Logs/oura-heartbeat.log"

# Hours of silence tolerated while the phone is demonstrably reachable. Above
# 24 so a single day of not opening the Oura app does not nag.
STALE_HOURS = 36.0

# A locked or dozing iPhone can miss ICMP while still being perfectly online,
# so silence has to corroborate unreachability before we cry outage. The real
# incident left a 15h gap, so 12h still catches it.
UNREACHABLE_QUIET_HOURS = 12.0

# Verdicts
OK = "OK"                    # channel healthy, or quiet for a normal reason
OFF_TAILNET = "OFF-TAILNET"  # phone unreachable and silent -- the known failure
NO_SYNC = "NO-SYNC"          # phone reachable but silent -- automation suspect
NO_PHONE = "NO-PHONE"        # the phone has never reached us at all

# Must not overlap verify_oura.VERDICT_RE, or the two annotators eat each other.
HEARTBEAT_RE = r"> ⚠️ \*\*Shortcut heartbeat\*\*[^\n]*"


def read_last_seen(path: Path = STATE_FILE) -> tuple[float | None, str | None]:
    """(epoch seconds, address) of the last non-loopback Oura request."""
    try:
        state = json.loads(path.read_text())
        return float(state["ts"]), state.get("ip")
    except (FileNotFoundError, json.JSONDecodeError, OSError, KeyError,
            TypeError, ValueError):
        return None, None


def phone_reachable(ip: str, count: int = 3, wait_ms: int = 2000) -> bool:
    """True if the phone answers ICMP on the tailnet."""
    try:
        out = subprocess.run(["/sbin/ping", "-c", str(count), "-W", str(wait_ms), ip],
                             capture_output=True, text=True, timeout=30)
        return out.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def decide(last_ts: float | None, reachable: bool, now: float,
           stale_hours: float = STALE_HOURS,
           quiet_hours: float = UNREACHABLE_QUIET_HOURS) -> tuple[str, str]:
    """(verdict, detail) from last phone contact and live reachability."""
    if last_ts is None:
        return NO_PHONE, ("the Shortcut has never reached the Studio "
                          "— check the Oura Shortcut automation.")

    quiet = (now - last_ts) / 3600.0
    since = datetime.fromtimestamp(last_ts).strftime("%Y-%m-%d %H:%M")

    if not reachable:
        if quiet >= quiet_hours:
            return OFF_TAILNET, (
                f"iPhone unreachable on the tailnet and silent since {since} "
                f"({quiet:.0f}h) — the Oura Shortcut cannot reach the "
                f"Studio. Reconnect Tailscale on the phone."
            )
        # Unreachable but talking to us recently: asleep, not gone.
        return OK, f"last Shortcut sync {quiet:.1f}h ago (phone not answering pings)."

    if quiet > stale_hours:
        return NO_SYNC, (
            f"no Shortcut sync since {since} ({quiet:.0f}h) though the phone "
            f"is reachable — check the Oura Shortcut automation."
        )
    return OK, f"last Shortcut sync {quiet:.1f}h ago."


def annotate_note(path: Path, content: str, verdict: str, detail: str) -> bool:
    """Put (or clear) the heartbeat line inside the Bio-Log section.

    Mirrors verify_oura.annotate_note so the two coexist: each strips only its
    own shape, so a verdict and a heartbeat can sit in the section together.
    """
    section = extract_biolog_section(content)
    if section is None:
        return False
    cleaned = re.sub(r"\n*" + HEARTBEAT_RE, "", section)
    if verdict == OK:
        updated = cleaned.rstrip("\n") + "\n\n\n\n"
    else:
        line = f"> ⚠️ **Shortcut heartbeat** — {detail}"
        updated = cleaned.rstrip("\n") + "\n\n" + line + "\n\n\n\n"
    if updated == section:
        return False
    start = content.find(BIOLOG_MARKER) + len(BIOLOG_MARKER)
    path.write_text(content[:start] + updated + content[start + len(section):])
    return True


def _log(line: str) -> None:
    try:
        HEARTBEAT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with HEARTBEAT_LOG.open("a") as fh:
            fh.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {line}\n")
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    day = argv[0] if argv else datetime.now().strftime("%Y-%m-%d")

    last_ts, ip = read_last_seen()
    reachable = bool(ip) and phone_reachable(ip)
    verdict, detail = decide(last_ts, reachable, time.time())

    _log(f"{verdict} {detail}")
    print(f"{verdict}: {detail}")

    note = DAILY_NOTES_FOLDER / f"{day}.md"
    if not note.exists():
        _log(f"note missing: {note}")
        return 0
    if annotate_note(note, note.read_text(), verdict, detail):
        _log(f"annotated {note.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
