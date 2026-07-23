"""Tests for health-payload hardening (CSV + multi-day). Run on the Studio:
    uv run --project /Users/dougs/PycharmProjects/LogFromWatch python test_health_parse.py
Importing server is side-effect-safe (the socket only binds under __main__).
"""
import server


def approx(a, b, tol=1e-6):
    assert abs(a - b) < tol, f"{a} != {b}"


def _sum(payload, name):
    return sum(p["qty"] for p in server.get_metric_data(payload, name))


def _avg(payload, name):
    pts = server.get_metric_data(payload, name)
    return sum(p["qty"] for p in pts) / len(pts) if pts else 0.0


def test_csv_basic_numeric():
    csv_text = (
        "Date/Time,Heart Rate Variability (ms),Mindful Minutes (min),"
        "Resting Heart Rate (count/min),Step Count (count)\n"
        "2026-07-22 08:00:00,20,10,60,3000\n"
        "2026-07-22 12:00:00,40,5,62,4000\n"
    )
    d = server.csv_to_health_payload(csv_text)
    approx(_sum(d, "step_count"), 7000)       # summed
    approx(_sum(d, "mindful_minutes"), 15)    # summed
    approx(_avg(d, "heart_rate_variability"), 30)  # averaged
    approx(_avg(d, "resting_heart_rate"), 61)      # averaged
    print("ok test_csv_basic_numeric")


def test_csv_multi_day_collapses_to_latest():
    csv_text = (
        "Date/Time,Step Count (count)\n"
        "2026-07-20 09:00:00,9999\n"   # older day — must be dropped
        "2026-07-22 09:00:00,1000\n"
        "2026-07-22 18:00:00,2000\n"
    )
    d = server.csv_to_health_payload(csv_text)
    approx(_sum(d, "step_count"), 3000)  # only 2026-07-22 counted
    print("ok test_csv_multi_day_collapses_to_latest")


def test_csv_sleep_fields():
    csv_text = (
        "Date/Time,Sleep Analysis [Asleep] (hr),Sleep Analysis [In Bed] (hr),"
        "Sleep Analysis [Deep] (hr),Sleep Analysis [REM] (hr)\n"
        "2026-07-22 23:00:00,4.0,4.5,0.8,0.5\n"
        "2026-07-23 05:00:00,4.0,4.5,0.7,0.5\n"  # latest day
    )
    d = server.csv_to_health_payload(csv_text)
    sleep = server.get_metric_data(d, "sleep_analysis")
    assert len(sleep) == 1, sleep
    pt = sleep[0]
    approx(pt["asleep"], 4.0)
    approx(pt["inBed"], 4.5)
    approx(pt["deep"], 0.7)
    approx(pt["totalSleep"], 4.0)  # derived from asleep
    print("ok test_csv_sleep_fields")


def test_parse_health_body_json_passthrough():
    payload = {"data": {"metrics": [{"name": "step_count",
                                     "data": [{"date": "2026-07-22 00:00:00", "qty": 5}]}]}}
    out = server.parse_health_body(server.json.dumps(payload), "application/json")
    approx(_sum(out, "step_count"), 5)
    print("ok test_parse_health_body_json_passthrough")


def test_parse_health_body_empty_raises():
    for body in ("", "   ", "\n\n"):
        try:
            server.parse_health_body(body)
            raise AssertionError("expected HealthParseError for empty body")
        except server.HealthParseError as e:
            assert "empty body" in str(e)
    print("ok test_parse_health_body_empty_raises")


def test_parse_health_body_csv_regression():
    # The exact failure shape: a CSV body that used to raise "char 0".
    csv_text = "Date/Time,Step Count (count)\n2026-07-22 00:00:00,4200\n"
    out = server.parse_health_body(csv_text, "text/csv")
    approx(_sum(out, "step_count"), 4200)
    # Also without a helpful content-type header (real shortcut sent app/json):
    out2 = server.parse_health_body(csv_text, "application/json")
    approx(_sum(out2, "step_count"), 4200)
    print("ok test_parse_health_body_csv_regression")


def test_json_multi_day_guard_still_active():
    payload = {"data": {"metrics": [{"name": "step_count", "data": [
        {"date": "2026-07-20 00:00:00", "qty": 8888},
        {"date": "2026-07-22 00:00:00", "qty": 1111},
    ]}]}}
    approx(_sum(payload, "step_count"), 1111)
    print("ok test_json_multi_day_guard_still_active")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"\nALL {len(fns)} TESTS PASSED")
