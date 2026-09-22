"""Tests for the Shortcut heartbeat (no network).
    uv run --project /Users/dougs/PycharmProjects/LogFromWatch python test_heartbeat_oura.py
"""
import re

import heartbeat_oura as h

HOUR = 3600.0
NOW = 1790000000.0


def test_never_heard_from_phone():
    verdict, _ = h.decide(None, False, NOW)
    assert verdict == h.NO_PHONE, verdict
    print("ok test_never_heard_from_phone")


def test_unreachable_and_long_silent_flags_off_tailnet():
    """The real 2026-09-21 shape: phone left at 19:00, checked 15h later."""
    verdict, _ = h.decide(NOW - 15 * HOUR, False, NOW)
    assert verdict == h.OFF_TAILNET, verdict
    print("ok test_unreachable_and_long_silent_flags_off_tailnet")


def test_unreachable_but_recently_synced_is_silent():
    """A phone that synced at 08:30 and dozed off is not an outage."""
    verdict, _ = h.decide(NOW - 2 * HOUR, False, NOW)
    assert verdict == h.OK, verdict
    print("ok test_unreachable_but_recently_synced_is_silent")


def test_unreachable_right_at_threshold_is_silent():
    verdict, _ = h.decide(NOW - 11 * HOUR, False, NOW)
    assert verdict == h.OK, verdict
    print("ok test_unreachable_right_at_threshold_is_silent")


def test_reachable_with_recent_hit_is_silent():
    verdict, _ = h.decide(NOW - 5 * HOUR, True, NOW)
    assert verdict == h.OK, verdict
    print("ok test_reachable_with_recent_hit_is_silent")


def test_one_skipped_day_does_not_nag():
    """26h without opening the Oura app is normal; 36h is the threshold."""
    verdict, _ = h.decide(NOW - 26 * HOUR, True, NOW)
    assert verdict == h.OK, verdict
    print("ok test_one_skipped_day_does_not_nag")


def test_reachable_but_long_silent_flags_automation():
    verdict, detail = h.decide(NOW - 40 * HOUR, True, NOW)
    assert verdict == h.NO_SYNC, verdict
    assert "automation" in detail, detail
    print("ok test_reachable_but_long_silent_flags_automation")


def test_the_two_failures_are_distinguishable():
    """The whole point: the message must name which thing to go fix."""
    _, off = h.decide(NOW - 40 * HOUR, False, NOW)
    _, silent = h.decide(NOW - 40 * HOUR, True, NOW)
    assert "tailnet" in off and "automation" not in off, off
    assert "automation" in silent, silent
    print("ok test_the_two_failures_are_distinguishable")


def test_annotate_replaces_rather_than_stacks():
    section = ("\n| **Readiness** | `57` | - |\n\n"
               "> ⚠️ **Shortcut heartbeat** — old news\n")
    cleaned = re.sub(r"\n*" + h.HEARTBEAT_RE, "", section)
    assert "old news" not in cleaned, cleaned
    assert "**Readiness**" in cleaned, cleaned
    print("ok test_annotate_replaces_rather_than_stacks")


def test_heartbeat_does_not_collide_with_verify_verdicts():
    """The two annotators must not strip each other's lines."""
    import verify_oura as v
    beat = "> ⚠️ **Shortcut heartbeat** — iPhone off the tailnet"
    verdict = "*verified 08:05 · matches Oura*"
    assert not re.search(v.VERDICT_RE, beat), "verify would eat the heartbeat"
    assert not re.search(h.HEARTBEAT_RE, verdict), "heartbeat would eat the verdict"
    print("ok test_heartbeat_does_not_collide_with_verify_verdicts")


def test_reads_ip_from_checkin_record(tmp=None):
    """The phone's address comes from its own last check-in, so it follows
    the node churn (iphone182 -> iphone193 -> ...) with no hardcoded IP."""
    import json
    from pathlib import Path
    p = Path("/tmp/_hb_state_test.json")
    p.write_text(json.dumps({"ts": NOW, "ip": "100.64.0.1"}))
    assert h.read_last_seen(p) == (NOW, "100.64.0.1")
    p.unlink()
    print("ok test_reads_ip_from_checkin_record")


def test_missing_state_file_reads_as_never():
    from pathlib import Path
    assert h.read_last_seen(Path("/tmp/_hb_does_not_exist.json")) == (None, None)
    print("ok test_missing_state_file_reads_as_never")


if __name__ == "__main__":
    fns = [f for k, f in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"\nALL {len(fns)} TESTS PASSED")
