#!/usr/bin/env python3
"""
HTTP server for receiving log entries from iOS Shortcuts.
Writes directly to Obsidian daily notes - no iCloud needed.

Run with: uv run python server.py
"""
import json
import logging
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

from config import DAILY_NOTES_FOLDER, SECTIONS, LOG_FILE
from config import FORMAT_PLAIN, FORMAT_BLOCKQUOTE, FORMAT_BULLET, FORMAT_NUMBERED, FORMAT_CHECKBOX

# Server config
HOST = "0.0.0.0"  # Listen on all interfaces (needed for Tailscale)
PORT = 8080

# Logging setup
log = logging.getLogger(__name__)
log.setLevel(logging.INFO)

console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
))
log.addHandler(console_handler)

file_handler = logging.FileHandler(LOG_FILE)
file_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
))
log.addHandler(file_handler)


def get_daily_note_path(for_date: datetime = None) -> Path:
    """Get the path to the daily note for a given date."""
    if for_date is None:
        for_date = datetime.now()
    date_str = for_date.strftime("%Y-%m-%d")
    return DAILY_NOTES_FOLDER / f"{date_str}.md"


def format_entry(text: str, fmt: str, index: int = 1) -> str:
    """Format a single entry according to the format type."""
    if fmt == FORMAT_BLOCKQUOTE:
        return f"> {text}"
    elif fmt == FORMAT_BULLET:
        return f"- {text}"
    elif fmt == FORMAT_NUMBERED:
        return f"{index}. {text}"
    elif fmt == FORMAT_CHECKBOX:
        return f"{index}. [ ] {text}"
    else:  # FORMAT_PLAIN
        return text


def insert_at_marker(content: str, marker: str, entry_text: str) -> str | None:
    """Insert entry after a marker in the content."""
    if marker not in content:
        return None

    marker_pos = content.find(marker)
    line_end = content.find("\n", marker_pos)
    if line_end == -1:
        line_end = len(content)
    else:
        line_end += 1

    if marker.startswith("##"):
        rest = content[line_end:]
        divider_pos = rest.find("\n---")
        next_section = rest.find("\n## ")

        if divider_pos != -1 and (next_section == -1 or divider_pos < next_section):
            insert_pos = line_end + divider_pos + 1
        elif next_section != -1:
            insert_pos = line_end + next_section + 1
        else:
            insert_pos = line_end
    else:
        rest = content[line_end:]
        first_line_end = rest.find("\n")
        if first_line_end == -1:
            first_line_end = len(rest)
        first_line = rest[:first_line_end].strip()

        if first_line in (">", "-", "1.", "2.", "3.", ""):
            return content[:line_end] + entry_text + rest[first_line_end:]
        else:
            insert_pos = line_end
            return content[:insert_pos] + entry_text + "\n" + content[insert_pos:]

    return content[:insert_pos] + entry_text + "\n" + content[insert_pos:]


def process_entry(entry: dict) -> tuple[bool, str]:
    """Process an entry and write to daily note. Returns (success, message)."""
    section = entry.get("section", "").lower()
    text = entry.get("text", "")
    # Accept boolean true or string "true" for timestamp
    ts_value = entry.get("timestamp", False)
    add_timestamp = ts_value is True or ts_value == "true"

    # Normalize text to list
    if isinstance(text, str):
        text = text.strip()
        if text.startswith("[") and text.endswith("]"):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    texts = [t.strip() for t in parsed if isinstance(t, str) and t.strip()]
                else:
                    texts = [text]
            except json.JSONDecodeError:
                texts = [text[1:-1].strip()] if len(text) > 2 else []
        else:
            texts = [text] if text else []
    elif isinstance(text, list):
        texts = [t.strip() for t in text if isinstance(t, str) and t.strip()]
    else:
        texts = []

    if not section or not texts:
        return False, f"Invalid entry - missing section or text"

    if section not in SECTIONS:
        return False, f"Unknown section: {section}"

    config = SECTIONS[section]
    marker = config["marker"]
    fmt = config["format"]

    daily_note = get_daily_note_path()
    if not daily_note.exists():
        return False, f"Daily note does not exist: {daily_note}"

    try:
        content = daily_note.read_text()
    except Exception as e:
        return False, f"Failed to read daily note: {e}"

    if add_timestamp:
        time_str = datetime.now().strftime("%H:%M")
        formatted_lines = [f"- {time_str} {t}" for t in texts]
    else:
        formatted_lines = [format_entry(t, fmt, index=i+1) for i, t in enumerate(texts)]
    formatted = "\n".join(formatted_lines)
    new_content = insert_at_marker(content, marker, formatted)

    if new_content is None:
        return False, f"Marker '{marker}' not found in daily note"

    try:
        daily_note.write_text(new_content)
        log.info(f"Wrote to {section}: {texts[0][:50]}...")
        return True, f"OK: wrote to {section}"
    except Exception as e:
        return False, f"Failed to write daily note: {e}"


class LogHandler(BaseHTTPRequestHandler):
    """HTTP request handler for log entries."""

    def _send_response(self, status: int, message: str):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        response = json.dumps({"status": "ok" if status == 200 else "error", "message": message})
        self.wfile.write(response.encode())

    def do_GET(self):
        """Health check endpoint."""
        if self.path == "/health":
            self._send_response(200, "Server is running")
        else:
            self._send_response(404, "Not found. Use POST /obsidian/daily")

    def do_POST(self):
        """Receive log entry."""
        if self.path != "/obsidian/daily":
            self._send_response(404, "Not found. Use POST /obsidian/daily")
            return

        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode()
            entry = json.loads(body)
            log.info(f"Received: {entry}")

            success, message = process_entry(entry)
            self._send_response(200 if success else 400, message)

        except json.JSONDecodeError as e:
            log.error(f"Invalid JSON: {e}")
            self._send_response(400, f"Invalid JSON: {e}")
        except Exception as e:
            log.error(f"Error processing request: {e}")
            self._send_response(500, f"Server error: {e}")

    def log_message(self, format, *args):
        """Suppress default logging (we use our own)."""
        pass

    def address_string(self):
        """Return client IP without reverse DNS lookup."""
        return self.client_address[0]


def main():
    log.info(f"Starting server on {HOST}:{PORT}")
    server = HTTPServer((HOST, PORT), LogHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
