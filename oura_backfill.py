#!/usr/bin/env python3
"""Backfill the Bio-Log for past daily notes from Oura.

    uv run python oura_backfill.py            # dry run (last 7 days)
    uv run python oura_backfill.py --write    # actually write missing days
    uv run python oura_backfill.py --write --days 10 --force

Only fills notes whose Bio-Log section is empty or shows a failure marker,
unless --force (then it overwrites existing Bio-Log tables too). Skips days
where Oura itself has no data.
"""
import sys
from datetime import datetime, timedelta

import oura_client
import server

MARKER = "## 🩺 Bio-Log"


def biolog_section(content: str):
    """Return the Bio-Log section text, or None if the marker is absent."""
    if MARKER not in content:
        return None
    start = content.index(MARKER)
    rest = content[start + len(MARKER):]
    end = len(rest)
    for m in ("\n---", "\n## "):
        i = rest.find(m)
        if i != -1:
            end = min(end, i)
    return rest[:end]


def main() -> int:
    write = "--write" in sys.argv
    force = "--force" in sys.argv
    days = 7
    if "--days" in sys.argv:
        days = int(sys.argv[sys.argv.index("--days") + 1])

    token = oura_client.OuraAuth().get_access_token()
    today = datetime.now().date()
    now_hhmm = datetime.now().strftime("%H:%M")

    wrote = skipped = 0
    print(f"{'DAY':12} {'NOTE':11} {'OURA':10} DETAIL")
    for i in range(days):
        d = today - timedelta(days=i)
        ds = d.isoformat()
        note = server.get_daily_note_path(datetime(d.year, d.month, d.day))
        if not note.exists():
            print(f"{ds:12} {'no-note':11} {'-':10} skip"); continue

        content = note.read_text()
        section = biolog_section(content)
        has_table = section is not None and "| **Steps**" in section
        note_state = "no-section" if section is None else ("has-data" if has_table else "MISSING")

        metrics = oura_client.build_bio_log(oura_client.fetch_oura_day(token, ds), ds)
        real = oura_client.has_real_data(metrics)
        detail = (f"steps={metrics['steps']} sleep={metrics['sleep']['total']}h "
                  f"hrv={metrics['hrv']:.0f} rhr={metrics['rhr']:.0f} "
                  f"readiness={metrics['readiness']} mindful={metrics['mindful']}")
        print(f"{ds:12} {note_state:11} {('data' if real else 'no-data'):10} {detail}")

        # decide
        if section is None:
            print(f"{'':35}-> skip: no Bio-Log section"); skipped += 1; continue
        if has_table and not force:
            print(f"{'':35}-> skip: already has data (use --force to overwrite)"); skipped += 1; continue
        if not real:
            print(f"{'':35}-> skip: Oura has no data for this day"); skipped += 1; continue
        if not write:
            print(f"{'':35}-> WOULD WRITE (dry run)"); continue

        table = oura_client.render_bio_log_table(metrics, now_hhmm)
        orig = server.get_daily_note_path
        server.get_daily_note_path = lambda *a, _n=note, **k: _n
        try:
            ok, msg = server.process_entry({"section": "biolog", "text": table})
        finally:
            server.get_daily_note_path = orig
        if ok:
            print(f"{'':35}-> WROTE {note.name}"); wrote += 1
        else:
            print(f"{'':35}-> FAILED: {msg}"); skipped += 1

    print(f"\n{'WROTE' if write else 'WOULD WRITE'}: {wrote}   skipped: {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
