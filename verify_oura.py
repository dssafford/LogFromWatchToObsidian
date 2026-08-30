#!/usr/bin/env python3
"""Verify the Bio-Log the 08:00 sync wrote actually matches Oura.

Runs a few minutes after com.dougs.oura-sync. Re-fetches the day from Oura,
re-renders the table the sync would produce now, and compares it to what is
in the note. Catches a stale or wrong write even when the sync reported "ok"
(see the 2026-08-30 readiness 82-vs-57 miss).

Appends a verdict to VERIFY_LOG and a line to the note's Bio-Log section.
    python verify_oura.py [YYYY-MM-DD]
"""
import re
import sys
from datetime import datetime
from pathlib import Path

import oura_client
from config import DAILY_NOTES_FOLDER

BIOLOG_MARKER = "## 🩺 Bio-Log"
VERIFY_LOG = Path.home() / "Library/Logs/oura-verify.log"

# Verdicts
PASS = "PASS"            # table present and matches Oura
FAIL = "FAIL"            # table present but disagrees with Oura
OK_NODATA = "OK-NODATA"  # no table, and Oura still has nothing — correct failure
LATE = "LATE"            # no table, but Oura has data now — the retry will fix it
ERROR = "ERROR"          # note or section missing


def extract_biolog_section(content: str) -> str | None:
    """The Bio-Log section body: marker line to the next --- divider."""
    if BIOLOG_MARKER not in content:
        return None
    start = content.find(BIOLOG_MARKER) + len(BIOLOG_MARKER)
    rest = content[start:]
    end = rest.find("\n---")
    return rest if end == -1 else rest[:end]


def parse_biolog_table(text: str) -> dict:
    """Metric name -> value string, tolerant of Obsidian's column padding."""
    out = {}
    for row in re.finditer(r"\|\s*\*\*(.+?)\*\*\s*\|\s*`([^`]*)`\s*\|", text):
        out[row.group(1).strip()] = row.group(2).strip()
    return out


def compare(noted: dict, expected: dict) -> list[str]:
    """Human-readable mismatches between the note and a fresh render."""
    diffs = []
    for metric, want in expected.items():
        got = noted.get(metric)
        if got is None:
            diffs.append(f"{metric}: missing from note (Oura says {want})")
        elif got != want:
            diffs.append(f"{metric}: note {got} != Oura {want}")
    for metric in noted:
        if metric not in expected:
            diffs.append(f"{metric}: in note ({noted[metric]}) but not in Oura render")
    return diffs


def classify(section: str | None, noted: dict, expected: dict | None) -> tuple[str, str]:
    """(verdict, detail) for the day's Bio-Log."""
    if section is None:
        return ERROR, "no Bio-Log section in the note"
    if not noted:
        if expected is None:
            return OK_NODATA, "no table written; Oura still has no night — correct failure"
        return LATE, "no table written, but Oura has data now — 10:00 retry should fix it"
    if expected is None:
        return FAIL, "note has a table but Oura has no night for this day"
    diffs = compare(noted, expected)
    if diffs:
        return FAIL, "; ".join(diffs)
    return PASS, ", ".join(f"{k} {v}" for k, v in expected.items())


def annotate_note(path: Path, content: str, verdict: str, detail: str, stamp: str) -> None:
    """Put the verdict inside the Bio-Log section, just before the divider."""
    if verdict == PASS:
        line = f"*verified {stamp} · matches Oura*"
    else:
        line = f"> ⚠️ **Bio-Log check {verdict}** — {detail}"

    section = extract_biolog_section(content)
    if section is None:
        return
    # Drop a previous run's verdict so re-runs don't stack up.
    cleaned = re.sub(r"\n*(?:\*verified [^\n]*\*|> ⚠️ \*\*Bio-Log check [^\n]*)", "", section)
    start = content.find(BIOLOG_MARKER) + len(BIOLOG_MARKER)
    updated = cleaned.rstrip("\n") + "\n\n" + line + "\n\n\n\n"
    path.write_text(content[:start] + updated + content[start + len(section):])


def main() -> int:
    day = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")
    stamp = datetime.now().strftime("%H:%M")

    note = DAILY_NOTES_FOLDER / f"{day}.md"
    if not note.exists():
        _log(day, ERROR, f"no daily note at {note}")
        return 1
    content = note.read_text()
    section = extract_biolog_section(content)
    noted = parse_biolog_table(section or "")

    try:
        token = oura_client.OuraAuth().get_access_token()
        metrics = oura_client.build_bio_log(oura_client.fetch_oura_day(token, day), day)
    except Exception as e:
        _log(day, ERROR, f"could not reach Oura: {e}")
        return 1

    # Render through the same path the sync uses so rounding can't drift.
    expected = (parse_biolog_table(oura_client.render_bio_log_table(metrics, stamp))
                if oura_client.has_real_data(metrics) else None)

    verdict, detail = classify(section, noted, expected)
    _log(day, verdict, detail)
    try:
        annotate_note(note, content, verdict, detail, stamp)
    except OSError as e:
        _log(day, ERROR, f"could not annotate note: {e}")
    print(f"{verdict}: {detail}")
    return 0 if verdict in (PASS, OK_NODATA) else 1


def _log(day: str, verdict: str, detail: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} [{verdict}] {day} {detail}\n"
    try:
        VERIFY_LOG.parent.mkdir(parents=True, exist_ok=True)
        with VERIFY_LOG.open("a") as f:
            f.write(line)
    except OSError:
        pass
    sys.stderr.write(line)


if __name__ == "__main__":
    sys.exit(main())
