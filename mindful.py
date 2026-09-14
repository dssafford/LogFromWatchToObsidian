r"""Mindful moment capture: vocabulary, line composition, and dictation parsing.

Mindful moments are Daily Log lines. They live in the `## 📝 Daily Log`
section alongside the existing (mood::) entries, because the `Moments today`
dataviewjs block in the daily note template slices only that section:

    const sec = raw.match(/## 📝 Daily Log([\s\S]*?)(?:\n## |\n---|\Z)/);

Tag values must satisfy the template's regexes or Dataview renders nothing:

    mindful / mood  ->  [a-zA-Z][a-zA-Z \-]*   (letters, spaces, hyphens only)
    intensity       ->  a single digit
    time            ->  the leading HH:MM supplies the table's Time column

Canonical line:

    - HH:MM {prose} (mood::{mood}) (intensity::{n}) (mindful::{kind})

Nothing here touches disk, so it is testable without going near the vault.
"""
import re
from datetime import datetime

# --- Vocabulary ---

MINDFUL_WORDS = ("arrived", "stopped", "noticed", "present", "returned")

# Canon derived from Doug's own tag census across the 2026 daily notes.
MOOD_WORDS = (
    "calm", "content", "grateful", "proud", "excited", "energized",
    "great", "fine", "tired", "low", "sad", "anxious",
    "frustrated", "irritated", "angry",
)

MOOD_ALIASES = {
    "peaceful": "calm", "relaxed": "calm", "settled": "calm",
    "at ease": "calm", "unhurried": "calm",
    "happy": "content", "good": "content", "okay": "fine",
    "thankful": "grateful",
    "annoyed": "irritated", "mad": "angry", "furious": "angry",
    "exhausted": "tired", "drained": "tired", "wiped": "tired",
    "down": "low", "blue": "low", "flat": "low",
    "worried": "anxious", "nervous": "anxious", "stressed": "anxious",
    "amped": "energized", "wired": "energized",
    "stoked": "excited", "thrilled": "excited",
}

# Deliberately conservative: common words like "here", "with" and "back" are NOT
# aliased, because they appear constantly in ordinary prose and would shred it.
MINDFUL_ALIASES = {
    "landed": "arrived", "actually there": "arrived",
    "actually here": "arrived", "arrived anyway": "arrived",
    "paused": "stopped", "pause": "stopped", "stopped myself": "stopped",
    "caught myself": "noticed", "noticing": "noticed", "catching": "noticed",
    "fully present": "present", "really present": "present",
    "came back": "returned", "refocused": "returned", "returning": "returned",
}

NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}

MIN_INTENSITY = 1
MAX_INTENSITY = 5

# --- Normalization ---

def normalize_mood(value):
    """Return a canonical mood word, or None if unrecognized."""
    if not value:
        return None
    word = " ".join(str(value).strip().lower().split())
    word = MOOD_ALIASES.get(word, word)
    return word if word in MOOD_WORDS else None


def normalize_kind(value):
    """Return a canonical mindful word, or None if unrecognized."""
    if not value:
        return None
    word = " ".join(str(value).strip().lower().split())
    word = MINDFUL_ALIASES.get(word, word)
    return word if word in MINDFUL_WORDS else None


def normalize_intensity(value):
    """Return an int in 1-5, or None. The template regex matches one digit only."""
    if value is None or value is True or value is False:
        return None
    if isinstance(value, str):
        word = value.strip().lower()
        if word in NUMBER_WORDS:
            value = NUMBER_WORDS[word]
        elif word.isdigit():
            value = int(word)
        else:
            return None
    if not isinstance(value, int):
        return None
    return value if MIN_INTENSITY <= value <= MAX_INTENSITY else None


# --- Composition ---

TAG_RE = re.compile(r"\((?:mood|intensity|mindful)::[^)]*\)", re.I)


def strip_tags(text):
    """Remove any inline tags already present so we never emit a duplicate."""
    return _tidy(TAG_RE.sub(" ", text or ""))


def compose_line(prose="", mood=None, intensity=None, kind=None, now=None):
    """Build the canonical Daily Log line. Tags with no value are omitted."""
    time_str = (now or datetime.now()).strftime("%H:%M")
    parts = ["- " + time_str]
    prose = strip_tags(prose)
    if prose:
        parts.append(prose)
    if mood:
        parts.append("(mood::" + mood + ")")
    if intensity:
        parts.append("(intensity::" + str(intensity) + ")")
    if kind:
        parts.append("(mindful::" + kind + ")")
    return " ".join(parts)


# --- Dictation parsing ---

_TRIGGER_RE = re.compile(r"^\W*mindful(?:\s+moment)?\b\W*", re.I)


def _phrase_re(phrases):
    """Alternation over phrases, longest first so 'came back' beats 'back'."""
    ordered = sorted(phrases, key=len, reverse=True)
    return re.compile(r"\b(" + "|".join(re.escape(p) for p in ordered) + r")\b", re.I)


_MINDFUL_RE = _phrase_re(list(MINDFUL_WORDS) + list(MINDFUL_ALIASES))
_MOOD_RE = _phrase_re(list(MOOD_WORDS) + list(MOOD_ALIASES))
_LABELED_MOOD_RE = re.compile(r"\b(?:mood|feeling|feel|felt)\s+([a-z]+(?:\s+[a-z]+)?)\b", re.I)
_LABELED_INTENSITY_RE = re.compile(
    r"\bintensity\s+(\d+|" + "|".join(NUMBER_WORDS) + r")\b", re.I)
_BARE_INTENSITY_RE = re.compile(
    r"\b(["+ str(MIN_INTENSITY) + "-" + str(MAX_INTENSITY) + r"]|"
    + "|".join(NUMBER_WORDS) + r")\b", re.I)


def _cut(text, match):
    """Remove a match's span from text, leaving a space so words don't fuse."""
    return text[:match.start()] + " " + text[match.end():]


def _cut_all(text, pattern, normalize):
    """Take the first recognized value, and strip every recognized hit.

    Dictation often says the same thing twice ("actually there ... arrived"), so
    removing only the first match would leave a synonym stranded in the prose.
    """
    value = None
    while True:
        found = None
        for m in pattern.finditer(text):
            if normalize(m.group(1)):
                found = m
                break
        if not found:
            return value, text
        if value is None:
            value = normalize(found.group(1))
        text = _cut(text, found)


def _tidy(text):
    """Collapse whitespace and clean up punctuation orphaned by removals."""
    text = re.sub(r"\s+", " ", text or "")
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"([,;:]\s*){2,}", ", ", text)
    return text.strip(" ,;:-").strip()


def parse_dictation(utterance):
    """Parse a dictated moment into its parts.

    Returns {"prose", "mood", "intensity", "kind", "unknown"}. Keyword order does
    not matter -- dictation reorders things -- so the whole utterance is scanned.
    An unrecognized word is never fatal: it simply stays in the prose and, when it
    was explicitly labeled ("feeling squirrelly"), is reported in "unknown" so the
    caller can say what didn't stick.
    """
    text = _TRIGGER_RE.sub("", utterance or "", count=1)
    unknown = []
    mood = intensity = kind = None

    # Explicitly labeled fields win over a bare scan.
    m = _LABELED_MOOD_RE.search(text)
    if m:
        candidate = normalize_mood(m.group(1))
        if candidate is None:
            # Try just the first word: "feeling calm about it" -> "calm about"
            candidate = normalize_mood(m.group(1).split()[0])
        if candidate:
            mood = candidate
            text = _cut(text, m)
        else:
            unknown.append(m.group(1).strip().lower())

    m = _LABELED_INTENSITY_RE.search(text)
    if m:
        intensity = normalize_intensity(m.group(1))
        if intensity:
            text = _cut(text, m)

    # Unlabeled scan, longest phrases first.
    kind, text = _cut_all(text, _MINDFUL_RE, normalize_kind)

    if mood is None:
        mood, text = _cut_all(text, _MOOD_RE, normalize_mood)
    else:
        _, text = _cut_all(text, _MOOD_RE, normalize_mood)

    if intensity is None:
        m = _BARE_INTENSITY_RE.search(text)
        if m:
            intensity = normalize_intensity(m.group(1))
            if intensity:
                text = _cut(text, m)

    return {
        "prose": _tidy(strip_tags(text)),
        "mood": mood,
        "intensity": intensity,
        "kind": kind,
        "unknown": unknown,
    }
