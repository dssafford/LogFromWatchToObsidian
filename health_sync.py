#!/usr/bin/env python3
"""
Health data sync via LogFromWatch server.
Reads health metrics from iCloud JSON (from Health Export app) and POSTs to server.

This is typically triggered by an iOS automation when opening the Weather app.
"""
import json
import subprocess
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import Any

# iCloud input file from Health Export app
INPUT_FILE = Path(
    "/Users/dougs/Library/Mobile Documents/iCloud~is~workflow~my~workflows/Documents/VitalityBridge/DayPythonPayload.json"
)

# Server config
SERVER_URL = "http://localhost:9847/obsidian/daily"


def load_metrics(file_path: Path, retries: int = 30, delay: int = 2) -> dict[str, Any]:
    """Load JSON file, triggering iCloud download if needed."""
    print(f"Looking for payload at: {file_path}")

    # Wait for file to appear
    for i in range(10):
        if file_path.exists():
            break
        print(f"  Waiting for file to appear ({i + 1}/10)")
        time.sleep(1)

    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    # Trigger iCloud download
    try:
        print(f"Triggering iCloud download for: {file_path.name}")
        subprocess.run(['/usr/bin/brctl', 'download', str(file_path)], check=True)
    except Exception as e:
        print(f"Note: Could not trigger brctl download: {e}")

    # Wait for file to have content
    for i in range(retries):
        try:
            size = file_path.stat().st_size
            if size == 0:
                print(f"File is 0 bytes (downloading)... ({i + 1}/{retries})")
                time.sleep(delay)
                continue

            with open(file_path, 'r') as f:
                data = json.load(f)
                print(f"Payload loaded ({size} bytes)")
                return data

        except OSError as e:
            if e.errno == 11:  # Resource deadlock
                print(f"File locked by iCloud... ({i + 1}/{retries})")
                time.sleep(delay)
            else:
                raise
        except json.JSONDecodeError:
            print(f"File still writing... ({i + 1}/{retries})")
            time.sleep(delay)

    raise Exception(f"Could not load {file_path} after {retries} attempts")


def get_metric_data(data: dict[str, Any], metric_name: str) -> list:
    """Get data points for a specific metric."""
    metrics_list = data.get('data', {}).get('metrics', [])
    payload = next((item for item in metrics_list if item["name"] == metric_name), None)
    return payload['data'] if payload else []


def process_steps(data: dict[str, Any]) -> int:
    step_data = get_metric_data(data, 'step_count')
    return int(sum(item.get('qty', 0) for item in step_data))


def process_heart_rate(data: dict[str, Any], metric_name: str) -> float:
    hr_data = get_metric_data(data, metric_name)
    values = [item.get('qty', 0) for item in hr_data]
    return sum(values) / len(values) if values else 0.0


def process_sleep(data: dict[str, Any]) -> dict[str, float]:
    sleep_list = get_metric_data(data, 'sleep_analysis')
    if not sleep_list:
        return {"total": 0.0, "deep": 0.0, "rem": 0.0, "efficiency": 0.0}
    daily = sleep_list[0]
    in_bed = daily.get('inBed', 0)
    asleep = daily.get('asleep', 0)
    efficiency = (asleep / in_bed * 100) if in_bed > 0 else 0.0
    return {
        "total": daily.get('totalSleep', 0.0),
        "deep": daily.get('deep', 0.0),
        "rem": daily.get('rem', 0.0),
        "efficiency": efficiency
    }


def process_mindful_minutes(data: dict[str, Any]) -> float:
    mindful_data = get_metric_data(data, 'mindful_minutes')
    return sum(item.get('qty', 0) for item in mindful_data)


def generate_markdown(steps: int, hrv: float, rhr: float, sleep: dict, mindful: float) -> str:
    """Generate markdown table for bio metrics."""
    timestamp = datetime.now().strftime("%H:%M")
    step_status = "+" if steps > 8000 else "-"
    sleep_status = "+" if sleep['total'] > 7 else "-"
    deep_status = "+" if sleep['deep'] > 1.0 else "-"
    hrv_status = "+" if hrv > 40 else "-"
    mindful_status = "+" if mindful >= 10 else "-"

    return f"""| Metric | Value | Status | ({timestamp}) |
| :--- | :--- | :--- | :--- |
| **Steps** | `{steps}` | {step_status} | |
| **Sleep** | `{sleep['total']:.2f}h` | {sleep_status} | |
| **Deep Sleep** | `{sleep['deep']:.2f}h` | {deep_status} | |
| **HRV** | `{hrv:.0f} ms` | {hrv_status} | |
| **RHR** | `{rhr:.0f} bpm` | | |
| **Mindful** | `{mindful:.0f} min` | {mindful_status} | |"""


def post_to_server(content: str) -> bool:
    """POST health data to the LogFromWatch server."""
    payload = {
        "section": "biolog",
        "text": content
    }

    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        SERVER_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            result = json.loads(response.read().decode())
            print(f"Server response: {result}")
            return result.get("status") == "ok"
    except urllib.error.URLError as e:
        print(f"Failed to connect to server: {e}")
        return False
    except Exception as e:
        print(f"Error: {e}")
        return False


def main():
    print("Health Sync Started...")

    try:
        raw_data = load_metrics(INPUT_FILE)

        steps = process_steps(raw_data)
        hrv = process_heart_rate(raw_data, 'heart_rate_variability')
        rhr = process_heart_rate(raw_data, 'resting_heart_rate')
        sleep = process_sleep(raw_data)
        mindful = process_mindful_minutes(raw_data)

        print(f"  Steps: {steps}")
        print(f"  Sleep: {sleep['total']:.2f}h (deep: {sleep['deep']:.2f}h)")
        print(f"  HRV: {hrv:.0f} ms, RHR: {rhr:.0f} bpm")
        print(f"  Mindful: {mindful:.0f} min")

        md_output = generate_markdown(steps, hrv, rhr, sleep, mindful)

        if post_to_server(md_output):
            print("SUCCESS")
        else:
            print("FAILED")

    except Exception as e:
        print(f"Error: {e}")
        raise


if __name__ == "__main__":
    main()
