"""Tests for mindful moment capture.

Covers the vocabulary, line composition, dictation parsing, and the write path.
The write test redirects the daily note to a temp file, so the real vault is
never touched.
    uv run python test_mindful.py
"""
import re
import tempfile
from datetime import datetime
from pathlib import Path

import server
from mindful import (
    MINDFUL_WORDS,
    compose_line,
    normalize_intensity,
    normalize_kind,
    normalize_mood,
    parse_dictation,
    strip_tags,
)

NOW = datetime(2026, 9, 13, 16, 30)

# Copied verbatim from the daily note template's dataviewjs blocks. If a change
# to composition stops satisfying these, Dataview silently renders nothing --
# which is exactly the failure these tests exist to catch.
DV_MINDFUL = re.compile(r"\(mindful::\s*([a-zA-Z][a-zA-Z \-]*?)\)", re.I)
DV_MOOD = re.compile(r"\(mood::\s*([a-zA-Z][a-zA-Z \-]*?)\)", re.I)
DV_INTENSITY = re.compile(r"\(intensity::\s*(\d)\s*\)", re.I)
DV_TIME = re.compile(r"(\d{1,2}:\d{2})")
DV_SECTION = re.compile(r"## \N{MEMO} Daily Log([\s\S]*?)(?:\n## |\n---|\Z)")

EXAMPLE = ("- 16:30 walked out of the workshop into the parking lot, actually there "
           "(mood::calm) (intensity::3) (mindful::arrived)")


def test_compose_reproduces_the_example_line():
    line = compose_line(
        "walked out of the workshop into the parking lot, actually there",
        "calm", 3, "arrived", now=NOW)
    assert line == EXAMPLE, line
    print("ok test_compose_reproduces_the_example_line")


def test_compose_omits_absent_tags():
    assert compose_line("just here", now=NOW) == "- 16:30 just here"
    assert compose_line("", kind="noticed", now=NOW) == "- 16:30 (mindful::noticed)"
    assert compose_line("x", mood="calm", now=NOW) == "- 16:30 x (mood::calm)"
    print("ok test_compose_omits_absent_tags")


def test_compose_never_duplicates_an_existing_tag():
    line = compose_line("already (mood::calm) tagged", mood="calm", now=NOW)
    assert line.count("(mood::") == 1, line
    assert line == "- 16:30 already tagged (mood::calm)", line
    print("ok test_compose_never_duplicates_an_existing_tag")


def test_composed_line_matches_the_template_regexes():
    line = compose_line("walked out", "calm", 3, "arrived", now=NOW)
    assert DV_TIME.search(line).group(1) == "16:30"
    assert DV_MOOD.search(line).group(1) == "calm"
    assert DV_INTENSITY.search(line).group(1) == "3"
    assert DV_MINDFUL.search(line).group(1) == "arrived"
    # Every vocabulary word must survive the letters/spaces/hyphens constraint.
    for word in MINDFUL_WORDS:
        probe = compose_line("x", kind=word, now=NOW)
        assert DV_MINDFUL.search(probe), word
    print("ok test_composed_line_matches_the_template_regexes")


def test_dictation_parses_the_example_utterance():
    p = parse_dictation("mindful walked out of the workshop into the parking lot "
                        "actually there calm three arrived")
    assert p["kind"] == "arrived", p
    assert p["mood"] == "calm", p
    assert p["intensity"] == 3, p
    assert p["prose"] == "walked out of the workshop into the parking lot", p
    print("ok test_dictation_parses_the_example_utterance")


def test_dictation_is_order_independent():
    a = parse_dictation("mindful walked the dog actually there calm three arrived")
    b = parse_dictation("mindful arrived calm 3 walked the dog")
    for key in ("mood", "intensity", "kind"):
        assert a[key] == b[key], (key, a, b)
    assert a["prose"] == b["prose"] == "walked the dog", (a, b)
    print("ok test_dictation_is_order_independent")


def test_dictation_strips_every_synonym_not_just_the_first():
    # "actually there" and "arrived" both mean arrived; neither may survive.
    p = parse_dictation("mindful actually there at the gate arrived")
    assert p["kind"] == "arrived", p
    assert "arrived" not in p["prose"], p
    assert "actually there" not in p["prose"], p
    print("ok test_dictation_strips_every_synonym_not_just_the_first")


def test_aliases_map_to_canon():
    assert normalize_mood("peaceful") == "calm"
    assert normalize_mood("ANNOYED") == "irritated"
    assert normalize_mood("at ease") == "calm"
    assert normalize_kind("landed") == "arrived"
    assert normalize_kind("came back") == "returned"
    assert normalize_kind("Paused") == "stopped"
    print("ok test_aliases_map_to_canon")


def test_multiword_alias_beats_its_substring():
    p = parse_dictation("mindful came back to the page")
    assert p["kind"] == "returned", p
    print("ok test_multiword_alias_beats_its_substring")


def test_unknown_mood_leaves_prose_intact_and_is_reported():
    p = parse_dictation("mindful noticed the light feeling squirrelly")
    assert p["kind"] == "noticed", p
    assert p["mood"] is None, p
    assert "squirrelly" in p["prose"], p
    assert "squirrelly" in p["unknown"], p
    print("ok test_unknown_mood_leaves_prose_intact_and_is_reported")


def test_unknown_words_are_never_dropped_from_the_line():
    p = parse_dictation("mindful stopped mid-sentence about the quarterly forecast")
    line = compose_line(p["prose"], p["mood"], p["intensity"], p["kind"], now=NOW)
    assert "quarterly forecast" in line, line
    print("ok test_unknown_words_are_never_dropped_from_the_line")


def test_intensity_accepts_digits_and_words_and_rejects_the_rest():
    assert normalize_intensity(3) == 3
    assert normalize_intensity("3") == 3
    assert normalize_intensity("three") == 3
    assert normalize_intensity(5) == 5
    assert normalize_intensity(0) is None
    assert normalize_intensity(7) is None
    assert normalize_intensity(10) is None, "two digits break the template regex"
    assert normalize_intensity("high") is None
    assert normalize_intensity(None) is None
    assert normalize_intensity(True) is None
    print("ok test_intensity_accepts_digits_and_words_and_rejects_the_rest")


def test_strip_tags_removes_all_three_kinds():
    assert strip_tags("a (mood::calm) b (intensity::3) c (mindful::arrived) d") == "a b c d"
    print("ok test_strip_tags_removes_all_three_kinds")


def _temp_note():
    """A daily note seeded with the real template's Daily Log section."""
    tmp = Path(tempfile.mkdtemp()) / "2026-09-13.md"
    tmp.write_text(
        "tags:: #calendar/journal\n\n"
        "## \N{STETHOSCOPE} Bio-Log\n\n---\n\n"
        "## \N{MEMO} Daily Log\n\n"
        "- 09:55 (mood::calm) (intensity::3)\n\n"
        "---\n\n"
        "## \N{CITYSCAPE AT DUSK} Downshift\n"
    )
    return tmp


def test_process_entry_writes_a_mindful_line_into_the_daily_log():
    note = _temp_note()
    orig = server.get_daily_note_path
    server.get_daily_note_path = lambda *a, **k: note
    try:
        ok, msg = server.process_entry({
            "section": "mindful",
            "text": "mindful walked out of the workshop into the parking lot calm three arrived",
        })
    finally:
        server.get_daily_note_path = orig
    assert ok, msg
    body = DV_SECTION.search(note.read_text()).group(1)
    hits = [ln for ln in body.split("\n") if DV_MINDFUL.search(ln)]
    assert len(hits) == 1, body
    assert DV_MINDFUL.search(hits[0]).group(1) == "arrived", hits
    assert DV_MOOD.search(hits[0]).group(1) == "calm", hits
    assert DV_TIME.search(hits[0]), hits
    assert "walked out of the workshop" in hits[0], hits
    print("ok test_process_entry_writes_a_mindful_line_into_the_daily_log")


def test_handle_mindful_writes_structured_fields():
    note = _temp_note()
    orig = server.get_daily_note_path
    server.get_daily_note_path = lambda *a, **k: note
    try:
        ok, result = server.handle_mindful({
            "text": "walked out of the workshop into the parking lot, actually there",
            "mood": "peaceful", "intensity": "3", "kind": "landed",
        })
    finally:
        server.get_daily_note_path = orig
    assert ok, result
    assert result["kind"] == "arrived", result
    assert result["mood"] == "calm", result
    assert result["warnings"] == [], result
    body = DV_SECTION.search(note.read_text()).group(1)
    assert result["line"] in body, (result["line"], body)
    print("ok test_handle_mindful_writes_structured_fields")


def test_handle_mindful_rejects_an_unknown_kind():
    ok, result = server.handle_mindful({"text": "x", "kind": "vibing"})
    assert not ok
    assert "valid_kinds" in result, result
    assert set(result["valid_kinds"]) == set(MINDFUL_WORDS), result
    print("ok test_handle_mindful_rejects_an_unknown_kind")


def test_handle_mindful_degrades_rather_than_rejecting_a_bad_mood():
    note = _temp_note()
    orig = server.get_daily_note_path
    server.get_daily_note_path = lambda *a, **k: note
    try:
        ok, result = server.handle_mindful({
            "text": "paused at the gate", "mood": "squirrelly",
            "intensity": 9, "kind": "stopped",
        })
    finally:
        server.get_daily_note_path = orig
    assert ok, result
    assert result["mood"] is None and result["intensity"] is None, result
    assert len(result["warnings"]) == 2, result
    assert "paused at the gate" in result["line"], result
    assert "(mood::" not in result["line"], result
    print("ok test_handle_mindful_degrades_rather_than_rejecting_a_bad_mood")


def test_query_params_are_flattened():
    got = server.mindful_params_from_query(
        "/mindful?kind=arrived&mood=calm&intensity=3&text=paused+before+the+call")
    assert got == {"kind": "arrived", "mood": "calm", "intensity": "3",
                   "text": "paused before the call"}, got
    print("ok test_query_params_are_flattened")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"\nALL {len(fns)} TESTS PASSED")
