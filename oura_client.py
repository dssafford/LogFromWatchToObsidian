#!/usr/bin/env python3
"""Oura API v2 client: OAuth2 token management + daily Bio-Log extraction.

Stdlib-only (urllib) so it imports and unit-tests without the server's deps.

Sole source for the daily Bio-Log (replaces the flaky iPhone Health Auto
Export path). One-time authorization is done via oura_auth.py; after that
OuraAuth refreshes the access token automatically and rotates the refresh
token as Oura hands out new ones.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

# Known for this personal app so the user only needs to supply the secret.
DEFAULT_CLIENT_ID = "91fc5b15-9666-4126-8721-2c4d3a463115"
DEFAULT_REDIRECT_URI = "https://github.com/dssafford/LogFromWatchToObsidian"

# Oura migrated OAuth to moi.ouraring.com/oauth/v2/ext/* (discovered via the
# issuer's .well-known/openid-configuration). The old cloud/api endpoints are
# deprecated. The data API itself is still api.ouraring.com/v2/usercollection.
OURA_AUTHORIZE_URL = "https://moi.ouraring.com/oauth/v2/ext/oauth-authorize"
OURA_TOKEN_URL = "https://moi.ouraring.com/oauth/v2/ext/oauth-token"
OURA_API_BASE = "https://api.ouraring.com/v2/usercollection"
# New scopes are 'extapi:'-prefixed: daily summaries, heart rate, and
# meditation/breathing sessions (mindfulness).
OURA_SCOPES = "extapi:daily extapi:heartrate extapi:session extapi:personal"

SECRETS_FILE = Path(__file__).resolve().parent / "oura_secrets.json"
TOKENS_FILE = Path(__file__).resolve().parent / "oura_tokens.json"

# Oura session.type values that count as "mindful minutes".
MINDFUL_SESSION_TYPES = {"meditation", "breathing"}


class OuraError(Exception):
    """Any Oura auth/API/parse failure surfaced to the caller."""


# --- HTTP helpers (stdlib) ---

def _http_post_form(url: str, data: dict) -> dict:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise OuraError(f"token endpoint {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise OuraError(f"token endpoint unreachable: {e.reason}")


def _http_get_json(url: str, access_token: str) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise OuraError(f"GET {url} -> {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise OuraError(f"GET {url} unreachable: {e.reason}")


def _normalize_key(key: str) -> str:
    """Canonicalize an env key: upper-case, drop an optional OURA_ prefix."""
    key = key.strip().upper()
    return key[5:] if key.startswith("OURA_") else key


def _parse_env_file(path: Path) -> dict:
    """Tolerant .env reader: accepts `KEY=val` or `KEY: val`, with or without an
    OURA_ prefix, quotes optional. Returns canonical keys (CLIENT_SECRET, ...)."""
    data = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, val = line.split("=", 1)
        elif ":" in line:
            key, val = line.split(":", 1)
        else:
            continue
        data[_normalize_key(key)] = val.strip().strip('"').strip("'")
    return data


def load_secrets(path: Path = SECRETS_FILE) -> dict:
    """Assemble client_id/client_secret/redirect_uri.

    Source order: oura_secrets.json if present, otherwise a .env file next to
    this module merged over the process environment. Client ID and redirect URI
    default to this app's values, so a .env need only contain the secret, e.g.:
        CLIENT_SECRET=...   (or OURA_CLIENT_SECRET=..., or with a ':' separator)
    """
    if path.exists():
        secrets = json.loads(path.read_text())
    else:
        env = {_normalize_key(k): v for k, v in os.environ.items()}
        env_path = path.parent / ".env"
        if env_path.exists():
            env.update(_parse_env_file(env_path))
        secrets = {
            "client_id": env.get("CLIENT_ID") or DEFAULT_CLIENT_ID,
            "client_secret": env.get("CLIENT_SECRET", ""),
            "redirect_uri": env.get("REDIRECT_URI") or DEFAULT_REDIRECT_URI,
        }
    for key in ("client_id", "client_secret", "redirect_uri"):
        if not secrets.get(key):
            raise OuraError(
                f"missing '{key}' — set it in {path.name} or a .env file "
                "(the secret goes in CLIENT_SECRET)"
            )
    return secrets


# --- OAuth token lifecycle ---

class OuraAuth:
    def __init__(self, secrets: dict | None = None, tokens_file: Path = TOKENS_FILE):
        self.secrets = secrets if secrets is not None else load_secrets()
        self.tokens_file = tokens_file

    def _load_tokens(self) -> dict:
        if not self.tokens_file.exists():
            raise OuraError("not authorized yet — run 'python oura_auth.py' once")
        return json.loads(self.tokens_file.read_text())

    def _save_tokens(self, tokens: dict) -> None:
        self.tokens_file.write_text(json.dumps(tokens, indent=2))

    def _store_token_response(self, resp: dict, now: float | None = None) -> dict:
        if "access_token" not in resp:
            raise OuraError(f"token response had no access_token: {resp}")
        now = time.time() if now is None else now
        tokens = {
            "access_token": resp["access_token"],
            "refresh_token": resp.get("refresh_token"),
            # refresh 60s early to avoid edge-of-expiry failures
            "expires_at": now + int(resp.get("expires_in", 86400)) - 60,
        }
        self._save_tokens(tokens)
        return tokens

    def exchange_code(self, code: str) -> dict:
        """One-time: trade an authorization code for access+refresh tokens."""
        resp = _http_post_form(OURA_TOKEN_URL, {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.secrets["redirect_uri"],
            "client_id": self.secrets["client_id"],
            "client_secret": self.secrets["client_secret"],
        })
        return self._store_token_response(resp)

    def _refresh(self, tokens: dict, now: float | None = None) -> dict:
        if not tokens.get("refresh_token"):
            raise OuraError("no refresh_token stored — re-run oura_auth.py")
        resp = _http_post_form(OURA_TOKEN_URL, {
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "client_id": self.secrets["client_id"],
            "client_secret": self.secrets["client_secret"],
        })
        # Oura rotates refresh tokens; keep old one if a new one wasn't returned.
        if not resp.get("refresh_token"):
            resp["refresh_token"] = tokens["refresh_token"]
        return self._store_token_response(resp, now=now)

    def get_access_token(self, now: float | None = None) -> str:
        now = time.time() if now is None else now
        tokens = self._load_tokens()
        if now >= tokens.get("expires_at", 0):
            tokens = self._refresh(tokens, now=now)
        return tokens["access_token"]


# --- Data fetch ---

def _get_collection(endpoint: str, access_token: str, start_date: str, end_date: str) -> dict:
    qs = urllib.parse.urlencode({"start_date": start_date, "end_date": end_date})
    return _http_get_json(f"{OURA_API_BASE}/{endpoint}?{qs}", access_token)


def fetch_oura_day(access_token: str, day: str) -> dict:
    """Fetch the raw Oura responses needed to build a Bio-Log for `day`.

    Queries a [day-1, day+1] window so build_bio_log can select last night's
    sleep/readiness (Oura day == note day) and yesterday's steps/mindfulness.
    """
    d = datetime.strptime(day, "%Y-%m-%d").date()
    start = (d - timedelta(days=1)).isoformat()
    end = (d + timedelta(days=1)).isoformat()
    out = {
        "sleep": _get_collection("sleep", access_token, start, end),
        "daily_activity": _get_collection("daily_activity", access_token, start, end),
        "daily_readiness": _get_collection("daily_readiness", access_token, start, end),
    }
    # sleep/activity/readiness are essential; session (mindfulness) is optional —
    # if the app lacks the 'session' scope, degrade to no mindfulness rather than
    # failing the whole pull.
    try:
        out["session"] = _get_collection("session", access_token, start, end)
    except OuraError as e:
        out["session"] = {"data": []}
        out["_session_error"] = str(e)
    return out


# --- Pure transformation (unit-tested) ---

def _hours(seconds) -> float:
    return round((seconds or 0) / 3600.0, 2)


def _pick_for_day(records: list, target_day: str, day_key: str = "day"):
    """Record whose day == target; else latest on/before target; else latest."""
    if not records:
        return None
    exact = [r for r in records if r.get(day_key) == target_day]
    if exact:
        return exact[-1]
    earlier = [r for r in records if r.get(day_key, "") <= target_day]
    if earlier:
        return max(earlier, key=lambda r: r.get(day_key, ""))
    return max(records, key=lambda r: r.get(day_key, ""))


def _session_minutes(start: str, end: str) -> float:
    try:
        s = datetime.fromisoformat(start)
        e = datetime.fromisoformat(end)
        return max(0.0, (e - s).total_seconds() / 60.0)
    except (ValueError, TypeError):
        return 0.0


def build_bio_log(responses: dict, day: str) -> dict:
    """Turn raw Oura responses into Bio-Log metrics for the note dated `day`.

    Semantics mirror the old "Yesterday" export: sleep + readiness are from
    last night (Oura day == note day); steps + mindfulness are from the
    completed previous day.
    """
    yesterday = (datetime.strptime(day, "%Y-%m-%d").date() - timedelta(days=1)).isoformat()

    # Sleep — last night's main sleep only (ignore naps)
    sleep_records = responses.get("sleep", {}).get("data", []) or []
    long_sleep = [r for r in sleep_records if r.get("type") == "long_sleep"] or sleep_records
    s = _pick_for_day(long_sleep, day)
    if s:
        total_h = _hours(s.get("total_sleep_duration"))
        deep_h = _hours(s.get("deep_sleep_duration"))
        rem_h = _hours(s.get("rem_sleep_duration"))
        hrv = float(s.get("average_hrv") or 0)
        rhr = float(s.get("lowest_heart_rate") or 0)
        in_bed = s.get("time_in_bed") or 0
        efficiency = (s.get("total_sleep_duration", 0) / in_bed * 100) if in_bed else 0.0
    else:
        total_h = deep_h = rem_h = hrv = rhr = 0.0
        efficiency = 0.0

    # Steps — yesterday's completed day
    act = _pick_for_day(responses.get("daily_activity", {}).get("data", []) or [], yesterday)
    steps = int(act.get("steps", 0)) if act else 0

    # Readiness — today's score
    rd = _pick_for_day(responses.get("daily_readiness", {}).get("data", []) or [], day)
    readiness = int(rd["score"]) if rd and rd.get("score") is not None else None

    # Mindful — sum meditation/breathing session minutes that occurred yesterday
    mindful = 0.0
    for sess in responses.get("session", {}).get("data", []) or []:
        if sess.get("type") not in MINDFUL_SESSION_TYPES:
            continue
        start = sess.get("start_datetime", "") or ""
        if start[:10] != yesterday:
            continue
        mindful += _session_minutes(start, sess.get("end_datetime", ""))

    return {
        "steps": steps,
        "sleep": {"total": total_h, "deep": deep_h, "rem": rem_h, "efficiency": efficiency},
        "hrv": hrv,
        "rhr": rhr,
        "readiness": readiness,
        "mindful": round(mindful),
    }


def has_real_data(m: dict) -> bool:
    return (m["steps"] > 0 or m["sleep"]["total"] > 0 or m["hrv"] > 0
            or m["rhr"] > 0 or (m.get("readiness") or 0) > 0 or m["mindful"] > 0)


def render_bio_log_table(m: dict, timestamp: str) -> str:
    """Clean 3-column Bio-Log table (Metric | Value | Status) with a Readiness
    row and a 'synced HH:MM' caption line underneath."""
    def st(cond: bool) -> str:
        return "+" if cond else "-"

    rows = [
        f"| **Steps** | `{m['steps']}` | {st(m['steps'] > 8000)} |",
        f"| **Sleep** | `{m['sleep']['total']:.2f}h` | {st(m['sleep']['total'] > 7)} |",
        f"| **Deep Sleep** | `{m['sleep']['deep']:.2f}h` | {st(m['sleep']['deep'] > 1.0)} |",
        f"| **REM Sleep** | `{m['sleep']['rem']:.2f}h` | {st(m['sleep']['rem'] > 1.5)} |",
        f"| **Efficiency** | `{m['sleep']['efficiency']:.0f}%` | {st(m['sleep']['efficiency'] >= 85)} |",
        f"| **HRV** | `{m['hrv']:.0f} ms` | {st(m['hrv'] > 40)} |",
        f"| **RHR** | `{m['rhr']:.0f} bpm` |  |",
    ]
    if m.get("readiness") is not None:
        rows.append(f"| **Readiness** | `{m['readiness']}` | {st(m['readiness'] >= 70)} |")

    header = "| Metric | Value | Status |\n| :--- | :--- | :--- |"
    caption = f"\n\n*synced {timestamp} · Oura*"
    return header + "\n" + "\n".join(rows) + caption + "\n\n\n\n"
