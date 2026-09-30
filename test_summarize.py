"""Tests for GET /summarize.

Reads today's `## 📝 Daily Log` section and asks the local Ollama model to
summarize it for TTS playback on the Watch. Both the Ollama call and the
daily note path are monkeypatched — no network, no real vault.

    uv run --project . python test_summarize.py
"""
import tempfile
from pathlib import Path

import server


def _note_with_log(log_body: str) -> Path:
    """A daily note with a Daily Log section containing `log_body`."""
    tmp = Path(tempfile.mkdtemp()) / "2026-09-30.md"
    tmp.write_text(
        "tags:: #calendar/journal\n\n"
        "## 🌅 Morning Set\n"
        "**Three Priorities:**\n"
        "1. [ ] a\n\n"
        "## 📝 Daily Log\n"
        f"{log_body}\n"
        "## 🌇 Evening\n"
        "\n"
        "---\n"
    )
    return tmp


def _with_note_and_model(note, model_reply, fn):
    """Point get_daily_note_path at `note` and stub Ollama to return `model_reply`.

    `model_reply` is either a string (successful summary) or a tuple
    (False, "error message") to simulate an Ollama failure.
    """
    orig_note = server.get_daily_note_path
    orig_call = server._call_ollama_chat
    server.get_daily_note_path = lambda *a, **k: note
    if isinstance(model_reply, tuple):
        server._call_ollama_chat = lambda *a, **k: model_reply
    else:
        server._call_ollama_chat = lambda *a, **k: (True, model_reply)
    try:
        return fn()
    finally:
        server.get_daily_note_path = orig_note
        server._call_ollama_chat = orig_call


def test_summarize_happy_path_returns_model_text():
    note = _note_with_log(
        "- 09:12 first coffee, feeling focused\n"
        "- 10:45 debugged the launchd path issue\n"
        "- 13:20 walk after lunch, calm (mindful::present)\n"
    )
    reply = "A focused morning centered on a launchd fix, ending with a calming lunchtime walk."
    ok, result = _with_note_and_model(note, reply, server.summarize_today_log)
    assert ok, result
    assert result["status"] == "ok", result
    assert result["summary"] == reply, result
    assert result["speech"] == reply, result
    print("ok test_summarize_happy_path_returns_model_text")


def test_summarize_empty_log_short_circuits_without_calling_model():
    note = _note_with_log("")
    calls = []
    def spy(*a, **k):
        calls.append(a)
        return (True, "should not be called")
    orig_note = server.get_daily_note_path
    orig_call = server._call_ollama_chat
    server.get_daily_note_path = lambda *a, **k: note
    server._call_ollama_chat = spy
    try:
        ok, result = server.summarize_today_log()
    finally:
        server.get_daily_note_path = orig_note
        server._call_ollama_chat = orig_call
    assert ok, result
    assert result["status"] == "empty-log", result
    assert result["speech"] == "Nothing logged yet today.", result
    assert calls == [], f"model was called for empty log: {calls}"
    print("ok test_summarize_empty_log_short_circuits_without_calling_model")


def test_summarize_model_unavailable_speaks_graceful_fallback():
    note = _note_with_log("- 09:00 something happened\n")
    ok, result = _with_note_and_model(
        note, (False, "connection refused"), server.summarize_today_log
    )
    assert not ok, result
    assert result["status"] == "model-unavailable", result
    assert result["speech"] == "Summary unavailable.", result
    # The underlying error is preserved for debugging.
    assert "connection refused" in result["message"], result
    print("ok test_summarize_model_unavailable_speaks_graceful_fallback")


def test_summarize_reports_note_missing():
    tmp = Path(tempfile.mkdtemp()) / "2026-09-30.md"
    # Do not create the file.
    ok, result = _with_note_and_model(tmp, "", server.summarize_today_log)
    assert not ok, result
    assert result["status"] == "note-missing", result
    print("ok test_summarize_reports_note_missing")


def test_summarize_reports_marker_missing():
    tmp = Path(tempfile.mkdtemp()) / "2026-09-30.md"
    tmp.write_text("no daily log marker here\n")
    ok, result = _with_note_and_model(tmp, "", server.summarize_today_log)
    assert not ok, result
    assert result["status"] == "marker-missing", result
    print("ok test_summarize_reports_marker_missing")


def test_extract_section_text_stops_at_next_h2():
    body = (
        "## 📝 Daily Log\n"
        "line one\n"
        "line two\n"
        "## 🌇 Evening\n"
        "should not appear\n"
    )
    got = server._extract_section_text(body, "## 📝 Daily Log")
    assert got == "line one\nline two", repr(got)
    print("ok test_extract_section_text_stops_at_next_h2")


def test_extract_section_text_stops_at_divider():
    body = (
        "## 📝 Daily Log\n"
        "the only line\n"
        "\n"
        "---\n"
        "footer\n"
    )
    got = server._extract_section_text(body, "## 📝 Daily Log")
    assert got == "the only line", repr(got)
    print("ok test_extract_section_text_stops_at_divider")


def test_extract_section_text_returns_none_when_missing():
    got = server._extract_section_text("no marker at all\n", "## 📝 Daily Log")
    assert got is None, repr(got)
    print("ok test_extract_section_text_returns_none_when_missing")


def test_extract_section_text_returns_empty_when_section_empty():
    body = "## 📝 Daily Log\n\n## Next\n"
    got = server._extract_section_text(body, "## 📝 Daily Log")
    assert got == "", repr(got)
    print("ok test_extract_section_text_returns_empty_when_section_empty")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"\nALL {len(fns)} TESTS PASSED")
