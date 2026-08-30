"""Tests for the Bio-Log verifier (no network).
    uv run --project /Users/dougs/PycharmProjects/LogFromWatch python test_verify_oura.py
"""
import verify_oura as v

# A real note section, with the column padding Obsidian applies when edited.
PADDED_NOTE = """

| Metric         | Value    | Status |
| :------------- | :------- | :----- |
| **Steps**      | `2478`   | -      |
| **Sleep**      | `7.85h`  | +      |
| **HRV**        | `9 ms`   | -      |
| **Readiness**  | `57`     | -      |

*synced 08:00 · Oura*



"""

FRESH = """
| Metric | Value | Status |
| :--- | :--- | :--- |
| **Steps** | `2478` | - |
| **Sleep** | `7.85h` | + |
| **HRV** | `9 ms` | - |
| **Readiness** | `57` | - |

*synced 08:05 · Oura*
"""


def test_parse_tolerates_obsidian_padding():
    got = v.parse_biolog_table(PADDED_NOTE)
    assert got["Readiness"] == "57", got
    assert got["Sleep"] == "7.85h", got
    assert got["HRV"] == "9 ms", got
    print("ok test_parse_tolerates_obsidian_padding")


def test_padded_and_fresh_compare_equal():
    """Formatting differences must not read as a mismatch."""
    assert v.compare(v.parse_biolog_table(PADDED_NOTE),
                     v.parse_biolog_table(FRESH)) == []
    print("ok test_padded_and_fresh_compare_equal")


def test_extract_section_stops_at_divider():
    content = f"# Day\n{v.BIOLOG_MARKER}\n| **Readiness** | `57` | - |\n\n---\n\n## Next\n| **Readiness** | `99` | - |\n"
    got = v.parse_biolog_table(v.extract_biolog_section(content))
    assert got == {"Readiness": "57"}, got
    print("ok test_extract_section_stops_at_divider")


def test_the_2026_08_30_regression_is_caught():
    """The exact miss this was built for: stale 82 in the note, 57 at Oura."""
    noted = {"Readiness": "82", "Sleep": "7.34h", "HRV": "15 ms"}
    expected = {"Readiness": "57", "Sleep": "7.85h", "HRV": "9 ms"}
    verdict, detail = v.classify("section", noted, expected)
    assert verdict == v.FAIL, (verdict, detail)
    assert "note 82 != Oura 57" in detail, detail
    print("ok test_the_2026_08_30_regression_is_caught")


def test_matching_table_passes():
    vals = {"Readiness": "57", "Sleep": "7.85h"}
    verdict, _ = v.classify("section", dict(vals), dict(vals))
    assert verdict == v.PASS, verdict
    print("ok test_matching_table_passes")


def test_no_table_and_no_oura_data_is_a_correct_failure():
    """Ring never uploaded: the marker is right, not a defect."""
    verdict, detail = v.classify("section", {}, None)
    assert verdict == v.OK_NODATA, (verdict, detail)
    print("ok test_no_table_and_no_oura_data_is_a_correct_failure")


def test_no_table_but_oura_has_data_is_late():
    """08:00 was too early; the 10:00 retry will pick it up."""
    verdict, detail = v.classify("section", {}, {"Readiness": "57"})
    assert verdict == v.LATE, (verdict, detail)
    print("ok test_no_table_but_oura_has_data_is_late")


def test_table_without_oura_data_is_a_failure():
    """A table that Oura cannot account for is exactly the old bug's shape."""
    verdict, _ = v.classify("section", {"Readiness": "82"}, None)
    assert verdict == v.FAIL, verdict
    print("ok test_table_without_oura_data_is_a_failure")


def test_missing_section_is_an_error():
    verdict, _ = v.classify(None, {}, None)
    assert verdict == v.ERROR, verdict
    print("ok test_missing_section_is_an_error")


def test_missing_metric_is_reported():
    diffs = v.compare({"Readiness": "57"}, {"Readiness": "57", "HRV": "9 ms"})
    assert len(diffs) == 1 and "missing from note" in diffs[0], diffs
    print("ok test_missing_metric_is_reported")


if __name__ == "__main__":
    fns = [f for k, f in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"\nALL {len(fns)} TESTS PASSED")
