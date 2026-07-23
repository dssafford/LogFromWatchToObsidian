"""Integration test: server.sync_oura() writes a Bio-Log to a note.

Mocks the Oura API (no network/token) and redirects the daily note to a temp
file, so it exercises the real write path without touching the vault.
    uv run python test_oura_endpoint.py
"""
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import oura_client
import server

TODAY = datetime.now().strftime("%Y-%m-%d")
YESTERDAY = (datetime.now().date() - timedelta(days=1)).isoformat()

FIXTURE = {
    "sleep": {"data": [{
        "day": TODAY, "type": "long_sleep",
        "total_sleep_duration": 25200, "deep_sleep_duration": 3600,
        "rem_sleep_duration": 5400, "average_hrv": 45,
        "lowest_heart_rate": 52, "time_in_bed": 28800,
    }]},
    "daily_activity": {"data": [{"day": YESTERDAY, "steps": 9000}]},
    "daily_readiness": {"data": [{"day": TODAY, "score": 82}]},
    "session": {"data": [{
        "type": "meditation",
        "start_datetime": YESTERDAY + "T07:00:00-06:00",
        "end_datetime": YESTERDAY + "T07:15:00-06:00",
    }]},
}


class _FakeAuth:
    def __init__(self, *a, **k):
        pass

    def get_access_token(self, *a, **k):
        return "FAKE_TOKEN"


def run():
    orig_path = server.get_daily_note_path
    orig_auth = oura_client.OuraAuth
    orig_fetch = oura_client.fetch_oura_day
    with tempfile.TemporaryDirectory() as d:
        note = Path(d) / f"{TODAY}.md"
        note.write_text(f"# {TODAY}\n\n## 🩺 Bio-Log\n\n---\n\n## Log\n")
        server.get_daily_note_path = lambda *a, **k: note
        oura_client.OuraAuth = _FakeAuth
        oura_client.fetch_oura_day = lambda token, day: FIXTURE
        try:
            ok, msg = server.sync_oura()
            content = note.read_text()
        finally:
            server.get_daily_note_path = orig_path
            oura_client.OuraAuth = orig_auth
            oura_client.fetch_oura_day = orig_fetch

    assert ok, f"sync_oura failed: {msg}"
    assert "**Steps** | `9000`" in content, content
    assert "**Sleep** | `7.00h`" in content, content
    assert "**HRV** | `45 ms`" in content, content
    assert "**RHR** | `52 bpm`" in content, content
    assert "**Readiness** | `82`" in content, content
    assert "**Mindful** | `15 min`" in content, content
    print("ok test_sync_oura_writes_table")
    print("\n--- rendered Bio-Log section ---")
    start = content.index("## 🩺 Bio-Log")
    print(content[start:content.index("## Log", start)].rstrip())


if __name__ == "__main__":
    run()
    print("\nENDPOINT INTEGRATION TEST PASSED")
