"""Tests for GET /today/priorities.

Reads the Three Priorities section of today's daily note and returns
structured items plus a speech-friendly string so a Watch Shortcut can read
them aloud.

    uv run --project . python test_priorities.py
"""
import tempfile
from pathlib import Path

import server


def _note_with_priorities(body: str) -> Path:
    """A daily note seeded with a Three Priorities block containing `body`."""
    tmp = Path(tempfile.mkdtemp()) / "2026-09-28.md"
    tmp.write_text(
        "tags:: #calendar/journal\n\n"
        "## 🌅 Morning Set\n\n"
        "**Three Priorities:**\n"
        f"{body}\n"
        "**Today's Intention:**\n"
        ">\n\n"
        "---\n"
    )
    return tmp


def _with_note(note, fn):
    orig = server.get_daily_note_path
    server.get_daily_note_path = lambda *a, **k: note
    try:
        return fn()
    finally:
        server.get_daily_note_path = orig


def test_priorities_returns_three_populated_items():
    note = _note_with_priorities(
        "1. [ ] ship the priorities endpoint\n"
        "2. [ ] finish the sync status refactor\n"
        "3. [ ] walk after lunch\n"
    )
    ok, result = _with_note(note, server.get_today_priorities)
    assert ok, result
    assert [p["text"] for p in result["priorities"]] == [
        "ship the priorities endpoint",
        "finish the sync status refactor",
        "walk after lunch",
    ], result
    assert [p["done"] for p in result["priorities"]] == [False, False, False], result
    print("ok test_priorities_returns_three_populated_items")


def test_priorities_marks_completed_checkboxes():
    note = _note_with_priorities(
        "1. [x] shipped early\n"
        "2. [ ] still open\n"
        "3. [X] also done\n"
    )
    ok, result = _with_note(note, server.get_today_priorities)
    assert ok, result
    assert [p["done"] for p in result["priorities"]] == [True, False, True], result
    print("ok test_priorities_marks_completed_checkboxes")


def test_priorities_skips_empty_placeholder_lines():
    # Server-format checkbox output can leave trailing `2.` / `3.` placeholders
    # behind after inserting a batch — those aren't real priorities.
    note = _note_with_priorities(
        "1. [ ] real one\n"
        "2.\n"
        "3.\n"
    )
    ok, result = _with_note(note, server.get_today_priorities)
    assert ok, result
    assert [p["text"] for p in result["priorities"]] == ["real one"], result
    print("ok test_priorities_skips_empty_placeholder_lines")


def test_priorities_returns_empty_list_when_nothing_captured():
    note = _note_with_priorities("1.\n2.\n3.\n")
    ok, result = _with_note(note, server.get_today_priorities)
    assert ok, result
    assert result["priorities"] == [], result
    assert result["speech"] == "No priorities set today.", result
    print("ok test_priorities_returns_empty_list_when_nothing_captured")


def test_priorities_speech_string_is_watch_friendly():
    note = _note_with_priorities(
        "1. [ ] ship the endpoint\n"
        "2. [x] finish the refactor\n"
        "3. [ ] walk after lunch\n"
    )
    ok, result = _with_note(note, server.get_today_priorities)
    assert ok, result
    assert result["speech"] == (
        "One, ship the endpoint. "
        "Two, finish the refactor (done). "
        "Three, walk after lunch."
    ), result["speech"]
    print("ok test_priorities_speech_string_is_watch_friendly")


def test_priorities_reports_marker_missing():
    tmp = Path(tempfile.mkdtemp()) / "2026-09-28.md"
    tmp.write_text("no marker here\n")
    ok, result = _with_note(tmp, server.get_today_priorities)
    assert not ok, result
    assert result["status"] == "marker-missing", result
    print("ok test_priorities_reports_marker_missing")


def test_priorities_reports_note_missing():
    tmp = Path(tempfile.mkdtemp()) / "2026-09-28.md"
    # Do not create the file.
    ok, result = _with_note(tmp, server.get_today_priorities)
    assert not ok, result
    assert result["status"] == "note-missing", result
    print("ok test_priorities_reports_note_missing")


def test_priorities_stops_at_next_bold_marker():
    # Making sure we don't accidentally slurp lines from the next section.
    note = _note_with_priorities(
        "1. [ ] real one\n"
        "2. [ ] real two\n"
        "3. [ ] real three\n"
    )
    # The seeded note has **Today's Intention:** right after — we shouldn't
    # walk into its blockquote line.
    ok, result = _with_note(note, server.get_today_priorities)
    assert ok, result
    assert len(result["priorities"]) == 3, result
    for p in result["priorities"]:
        assert ">" not in p["text"], p
    print("ok test_priorities_stops_at_next_bold_marker")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"\nALL {len(fns)} TESTS PASSED")
