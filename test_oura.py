"""Unit tests for oura_client (pure transform + token expiry). No network.
    python test_oura.py
"""
import json
import tempfile
from pathlib import Path

import oura_client


def approx(a, b, tol=1e-6):
    assert abs(a - b) < tol, f"{a} != {b}"


# Realistic Oura v2 response shapes for note day 2026-07-23 (yesterday = 07-22).
RESPONSES = {
    "sleep": {"data": [
        {"day": "2026-07-22", "type": "long_sleep",       # older night, ignore
         "total_sleep_duration": 3600, "deep_sleep_duration": 600,
         "rem_sleep_duration": 600, "average_hrv": 10, "lowest_heart_rate": 80,
         "time_in_bed": 4000},
        {"day": "2026-07-23", "type": "nap",              # a nap, ignore
         "total_sleep_duration": 1800, "deep_sleep_duration": 0,
         "rem_sleep_duration": 0, "average_hrv": 5, "lowest_heart_rate": 90,
         "time_in_bed": 1800},
        {"day": "2026-07-23", "type": "long_sleep",       # last night -> use this
         "total_sleep_duration": 25200, "deep_sleep_duration": 3600,
         "rem_sleep_duration": 5400, "average_hrv": 45, "lowest_heart_rate": 52,
         "time_in_bed": 28800},
    ]},
    "daily_activity": {"data": [
        {"day": "2026-07-22", "steps": 9000},   # yesterday -> use this
        {"day": "2026-07-23", "steps": 1200},   # today, partial -> ignore
    ]},
    "daily_readiness": {"data": [
        {"day": "2026-07-22", "score": 70},
        {"day": "2026-07-23", "score": 82},     # today -> use this
    ]},
    "session": {"data": [
        {"type": "meditation", "start_datetime": "2026-07-22T07:00:00-06:00",
         "end_datetime": "2026-07-22T07:15:00-06:00"},   # 15 min, yesterday
        {"type": "breathing", "start_datetime": "2026-07-22T21:00:00-06:00",
         "end_datetime": "2026-07-22T21:10:00-06:00"},    # 10 min, yesterday
        {"type": "rest", "start_datetime": "2026-07-22T12:00:00-06:00",
         "end_datetime": "2026-07-22T12:30:00-06:00"},    # rest -> excluded
        {"type": "meditation", "start_datetime": "2026-07-23T06:00:00-06:00",
         "end_datetime": "2026-07-23T06:20:00-06:00"},    # today -> excluded
    ]},
}


def test_build_bio_log_full():
    m = oura_client.build_bio_log(RESPONSES, "2026-07-23")
    assert m["steps"] == 9000, m
    approx(m["sleep"]["total"], 7.0)   # 25200s
    approx(m["sleep"]["deep"], 1.0)    # 3600s
    approx(m["sleep"]["rem"], 1.5)     # 5400s
    approx(m["hrv"], 45)
    approx(m["rhr"], 52)
    assert m["readiness"] == 82, m
    assert m["mindful"] == 25, m       # 15 + 10, rest & today excluded
    approx(m["sleep"]["efficiency"], 25200 / 28800 * 100)
    print("ok test_build_bio_log_full")


def test_build_bio_log_empty_is_zero():
    m = oura_client.build_bio_log({}, "2026-07-23")
    assert m["steps"] == 0 and m["mindful"] == 0 and m["readiness"] is None
    assert not oura_client.has_real_data(m)
    print("ok test_build_bio_log_empty_is_zero")


def test_render_rows():
    m = oura_client.build_bio_log(RESPONSES, "2026-07-23")
    table = oura_client.render_bio_log_table(m, "08:00")
    assert "**Readiness** | `82`" in table
    assert "**REM Sleep** | `1.50h`" in table
    assert "**Efficiency** | `88%`" in table   # 25200/28800 -> 87.5 -> 88
    assert "**Deep Sleep** | `1.00h`" in table
    assert "Mindful" not in table              # dropped
    assert "| Metric | Value | Status |\n" in table   # 3-column
    assert "*synced 08:00" in table                   # time is now a caption
    print("ok test_render_rows")


def test_render_omits_readiness_when_absent():
    m = oura_client.build_bio_log(RESPONSES, "2026-07-23")
    m["readiness"] = None
    table = oura_client.render_bio_log_table(m, "08:00")
    assert "Readiness" not in table
    print("ok test_render_omits_readiness_when_absent")


def test_token_refreshes_when_expired():
    calls = {"refresh": 0}

    def fake_post(url, data):
        assert data["grant_type"] == "refresh_token"
        calls["refresh"] += 1
        return {"access_token": "NEW", "refresh_token": "R2", "expires_in": 3600}

    orig = oura_client._http_post_form
    oura_client._http_post_form = fake_post
    try:
        with tempfile.TemporaryDirectory() as d:
            tf = Path(d) / "oura_tokens.json"
            tf.write_text(json.dumps({
                "access_token": "OLD", "refresh_token": "R1", "expires_at": 1000,
            }))
            auth = oura_client.OuraAuth(
                secrets={"client_id": "c", "client_secret": "s", "redirect_uri": "r"},
                tokens_file=tf,
            )
            # now well past expires_at -> must refresh
            tok = auth.get_access_token(now=2000)
            assert tok == "NEW", tok
            assert calls["refresh"] == 1
            saved = json.loads(tf.read_text())
            assert saved["refresh_token"] == "R2"      # rotation persisted
            assert saved["expires_at"] == 2000 + 3600 - 60
    finally:
        oura_client._http_post_form = orig
    print("ok test_token_refreshes_when_expired")


def test_token_not_refreshed_when_valid():
    def fake_post(url, data):
        raise AssertionError("should not refresh a valid token")

    orig = oura_client._http_post_form
    oura_client._http_post_form = fake_post
    try:
        with tempfile.TemporaryDirectory() as d:
            tf = Path(d) / "oura_tokens.json"
            tf.write_text(json.dumps({
                "access_token": "GOOD", "refresh_token": "R1", "expires_at": 9999,
            }))
            auth = oura_client.OuraAuth(
                secrets={"client_id": "c", "client_secret": "s", "redirect_uri": "r"},
                tokens_file=tf,
            )
            assert auth.get_access_token(now=1000) == "GOOD"
    finally:
        oura_client._http_post_form = orig
    print("ok test_token_not_refreshed_when_valid")


# The 2026-08-30 miss: at 08:00 the ring had not uploaded yet, so Oura had no
# sleep/readiness row for the note day — only the previous night's. Steps (which
# target the completed previous day) were present as normal.
STALE_RESPONSES = {
    "sleep": {"data": [
        {"day": "2026-08-29", "type": "long_sleep",
         "total_sleep_duration": 26424, "deep_sleep_duration": 252,
         "rem_sleep_duration": 5436, "average_hrv": 15, "lowest_heart_rate": 51,
         "time_in_bed": 31087},
    ]},
    "daily_activity": {"data": [
        {"day": "2026-08-29", "steps": 2347},
    ]},
    "daily_readiness": {"data": [
        {"day": "2026-08-29", "score": 82},
    ]},
    "session": {"data": []},
}


def test_missing_night_does_not_reuse_yesterday():
    """Last night's sleep/readiness must never fall back to an earlier day."""
    m = oura_client.build_bio_log(STALE_RESPONSES, "2026-08-30")
    assert m["readiness"] is None, m               # not 82
    approx(m["sleep"]["total"], 0.0)               # not 7.34h
    approx(m["hrv"], 0)                            # not 15
    approx(m["rhr"], 0)                            # not 51
    assert m["steps"] == 2347, m                   # steps still resolve
    print("ok test_missing_night_does_not_reuse_yesterday")


def test_missing_night_fails_the_sync_guard():
    """Steps alone must not make a stale table look like a successful sync."""
    m = oura_client.build_bio_log(STALE_RESPONSES, "2026-08-30")
    assert not oura_client.has_real_data(m), m
    print("ok test_missing_night_fails_the_sync_guard")


def test_steps_keep_their_backward_fallback():
    """Steps target a completed day, so an older activity row is still valid."""
    responses = dict(STALE_RESPONSES)
    responses["daily_activity"] = {"data": [{"day": "2026-08-27", "steps": 6402}]}
    m = oura_client.build_bio_log(responses, "2026-08-30")
    assert m["steps"] == 6402, m
    print("ok test_steps_keep_their_backward_fallback")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"\nALL {len(fns)} TESTS PASSED")
