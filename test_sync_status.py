"""Tests for sync outcome distinguishability.

CLAUDE.md flags that the old `/sync/things3` returned the same JSON body for
a DB read failure and a legit no-op (both `{"status":"ok","success":0,"failed":0}`).
These tests pin the new statuses that make the two cases distinguishable, plus
matching coverage for iCloud.

    uv run --project . python test_sync_status.py
"""
import json
import tempfile
from pathlib import Path

import server


TEMPLATE_NOTE = (
    "tags:: #calendar/journal\n\n"
    "## 🌅 Morning Set\n\n"
    "**Today's Intention:**\n"
    ">\n\n"
    "---\n"
    "## 📝 Daily Log\n\n---\n"
)


def _seed_note() -> Path:
    tmp = Path(tempfile.mkdtemp()) / "2026-09-28.md"
    tmp.write_text(TEMPLATE_NOTE)
    return tmp


def _with_note(note, fn):
    orig = server.get_daily_note_path
    server.get_daily_note_path = lambda *a, **k: note
    try:
        return fn()
    finally:
        server.get_daily_note_path = orig


def _with_things_today(tasks_or_exc, fn):
    """Monkeypatch things.today() and ensure the DB error path is exercised."""
    orig = server.things.today
    if isinstance(tasks_or_exc, Exception):
        server.things.today = lambda: (_ for _ in ()).throw(tasks_or_exc)
    else:
        server.things.today = lambda: tasks_or_exc
    try:
        return fn()
    finally:
        server.things.today = orig


# --- Things3 ---

def test_things3_db_read_failure_is_distinct_from_no_tasks():
    note = _seed_note()
    fail = _with_things_today(
        RuntimeError("unable to open database file"),
        lambda: _with_note(note, server.sync_things3_tasks),
    )
    empty = _with_things_today(
        [],
        lambda: _with_note(note, server.sync_things3_tasks),
    )
    assert fail["status"] == "db-read-failed", fail
    assert fail["failed"] == 1 and fail["success"] == 0, fail
    assert empty["status"] == "no-tasks", empty
    assert empty["failed"] == 0 and empty["success"] == 0, empty
    assert fail["status"] != empty["status"], "must be distinguishable"
    print("ok test_things3_db_read_failure_is_distinct_from_no_tasks")


def test_things3_success_reports_ok_and_count():
    note = _seed_note()
    tasks = [{"title": "a", "today_index": 1},
             {"title": "b", "today_index": 2, "project_title": "Proj"}]
    result = _with_things_today(
        tasks, lambda: _with_note(note, server.sync_things3_tasks))
    assert result["status"] == "ok", result
    assert result["success"] == 2, result
    assert result["failed"] == 0, result
    assert "- [ ] a" in note.read_text()
    assert "- [ ] b (Proj)" in note.read_text()
    print("ok test_things3_success_reports_ok_and_count")


def test_things3_second_run_reports_already_synced():
    note = _seed_note()
    tasks = [{"title": "a", "today_index": 1}]
    first = _with_things_today(
        tasks, lambda: _with_note(note, server.sync_things3_tasks))
    assert first["status"] == "ok", first
    second = _with_things_today(
        tasks, lambda: _with_note(note, server.sync_things3_tasks))
    assert second["status"] == "already-synced", second
    assert second["success"] == 0 and second["failed"] == 0, second
    print("ok test_things3_second_run_reports_already_synced")


def test_things3_marker_missing_is_error_not_success():
    tmp = Path(tempfile.mkdtemp()) / "2026-09-28.md"
    tmp.write_text("no morning marker here\n")
    result = _with_things_today(
        [{"title": "a"}],
        lambda: _with_note(tmp, server.sync_things3_tasks),
    )
    assert result["status"] == "marker-missing", result
    assert result["failed"] == 1, result
    print("ok test_things3_marker_missing_is_error_not_success")


# --- iCloud ---

def test_icloud_no_files_is_distinct_from_partial_failure():
    tmp_note = _seed_note()
    empty_folder = Path(tempfile.mkdtemp())
    orig_folders = server.ICLOUD_INPUT_FOLDERS
    server.ICLOUD_INPUT_FOLDERS = [empty_folder]
    try:
        result = _with_note(tmp_note, server.sync_icloud_files)
    finally:
        server.ICLOUD_INPUT_FOLDERS = orig_folders
    assert result["status"] == "no-files", result
    assert result["files_found"] == 0, result
    print("ok test_icloud_no_files_is_distinct_from_partial_failure")


def test_icloud_all_success():
    tmp_note = _seed_note()
    folder = Path(tempfile.mkdtemp())
    (folder / "a.json").write_text(json.dumps({"section": "log", "text": "hello"}))
    (folder / "b.json").write_text(json.dumps({"section": "log", "text": "world"}))
    orig_folders = server.ICLOUD_INPUT_FOLDERS
    server.ICLOUD_INPUT_FOLDERS = [folder]
    try:
        result = _with_note(tmp_note, server.sync_icloud_files)
    finally:
        server.ICLOUD_INPUT_FOLDERS = orig_folders
    assert result["status"] == "ok", result
    assert result["success"] == 2 and result["failed"] == 0, result
    assert list(folder.glob("*.json")) == [], "processed files should be deleted"
    print("ok test_icloud_all_success")


def test_icloud_partial_success_is_distinct_status():
    tmp_note = _seed_note()
    folder = Path(tempfile.mkdtemp())
    (folder / "good.json").write_text(json.dumps({"section": "log", "text": "hi"}))
    # Unknown section -> process_entry returns False -> counted as failure.
    (folder / "bad.json").write_text(json.dumps({"section": "nope", "text": "x"}))
    orig_folders = server.ICLOUD_INPUT_FOLDERS
    server.ICLOUD_INPUT_FOLDERS = [folder]
    try:
        result = _with_note(tmp_note, server.sync_icloud_files)
    finally:
        server.ICLOUD_INPUT_FOLDERS = orig_folders
    assert result["status"] == "partial", result
    assert result["success"] == 1 and result["failed"] == 1, result
    # Good file was consumed, bad file was left for a retry.
    assert (folder / "bad.json").exists()
    assert not (folder / "good.json").exists()
    print("ok test_icloud_partial_success_is_distinct_status")


def test_icloud_all_failed_is_distinct_from_no_files():
    tmp_note = _seed_note()
    folder = Path(tempfile.mkdtemp())
    (folder / "bad.json").write_text(json.dumps({"section": "nope", "text": "x"}))
    orig_folders = server.ICLOUD_INPUT_FOLDERS
    server.ICLOUD_INPUT_FOLDERS = [folder]
    try:
        result = _with_note(tmp_note, server.sync_icloud_files)
    finally:
        server.ICLOUD_INPUT_FOLDERS = orig_folders
    assert result["status"] == "all-failed", result
    assert result["files_found"] == 1, result
    assert result["failed"] == 1, result
    print("ok test_icloud_all_failed_is_distinct_from_no_files")


# --- sync_morning aggregation ---

def test_sync_morning_passes_through_sub_statuses():
    note = _seed_note()
    folder = Path(tempfile.mkdtemp())
    (folder / "a.json").write_text(json.dumps({"section": "log", "text": "hi"}))
    orig_folders = server.ICLOUD_INPUT_FOLDERS
    server.ICLOUD_INPUT_FOLDERS = [folder]
    try:
        result = _with_things_today(
            [], lambda: _with_note(note, server.sync_morning))
    finally:
        server.ICLOUD_INPUT_FOLDERS = orig_folders
    assert result["things3"]["status"] == "no-tasks", result
    assert result["icloud"]["status"] == "ok", result
    assert result["total"]["success"] == 1, result
    assert result["total"]["failed"] == 0, result
    print("ok test_sync_morning_passes_through_sub_statuses")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"\nALL {len(fns)} TESTS PASSED")
