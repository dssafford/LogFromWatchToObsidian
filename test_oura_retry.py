"""Tests for the 10:00 Oura retry guard (no network).
    uv run --project /Users/dougs/PycharmProjects/LogFromWatch python test_oura_retry.py
Importing server is side-effect-safe (the socket only binds under __main__).
"""
import json
import tempfile
from datetime import datetime
from pathlib import Path

import server


def _with_state(contents):
    """Point server at a throwaway state file, optionally pre-seeded."""
    d = tempfile.mkdtemp()
    f = Path(d) / ".oura_last_run.json"
    if contents is not None:
        f.write_text(json.dumps(contents))
    server.OURA_STATE_FILE = f
    return f


def test_no_state_file_means_not_synced():
    _with_state(None)
    assert not server._oura_synced_today()
    print("ok test_no_state_file_means_not_synced")


def test_stale_state_means_not_synced():
    _with_state({"date": "2020-01-01"})
    assert not server._oura_synced_today()
    print("ok test_stale_state_means_not_synced")


def test_corrupt_state_means_not_synced():
    f = _with_state(None)
    f.write_text("{not json")
    assert not server._oura_synced_today()
    print("ok test_corrupt_state_means_not_synced")


def test_mark_then_synced_today():
    _with_state(None)
    server._mark_oura_synced()
    assert server._oura_synced_today()
    assert json.loads(server.OURA_STATE_FILE.read_text())["date"] == \
        datetime.now().strftime("%Y-%m-%d")
    print("ok test_mark_then_synced_today")


def test_retry_noops_after_a_good_morning_run():
    """A successful 08:00 must not be rewritten at 10:00."""
    _with_state(None)
    server._mark_oura_synced()
    calls = {"n": 0}
    orig = server.sync_oura
    server.sync_oura = lambda: calls.__setitem__("n", calls["n"] + 1) or (True, "ran")
    try:
        ok, msg = server.sync_oura_retry()
    finally:
        server.sync_oura = orig
    assert ok and calls["n"] == 0, (ok, msg, calls)
    assert "skipped" in msg, msg
    print("ok test_retry_noops_after_a_good_morning_run")


def test_retry_runs_when_morning_failed():
    """A late ring upload gets picked up on the second pass."""
    _with_state(None)
    calls = {"n": 0}
    orig = server.sync_oura
    server.sync_oura = lambda: calls.__setitem__("n", calls["n"] + 1) or (True, "OK: wrote to biolog")
    try:
        ok, msg = server.sync_oura_retry()
    finally:
        server.sync_oura = orig
    assert ok and calls["n"] == 1, (ok, msg, calls)
    print("ok test_retry_runs_when_morning_failed")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"\nALL {len(fns)} TESTS PASSED")
