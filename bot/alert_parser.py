"""Parse New Relic alert messages posted in Teams channels.

NR alerts arrive as cards/messages with a pattern like:
  🟢 [Culture] [Culture tab] LCP (Fast-burn rate)
  Started at   2026-04-12 03:41:00 UTC
  Activated at 2026-04-12 04:49:41 UTC
  Closed at    2026-04-12 04:55:40 UTC
  Duration     5m 58.874s

This module extracts the entity name, timestamps, and infers entity type.
"""

import re
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Matches the entity title line, e.g.:
# 🟢 [Culture] [Culture tab] LCP (Fast-burn rate)
# 🔴 Culture MMI Page is Down
_TITLE_PATTERN = re.compile(
    r"^[^\w\[]*"           # leading emoji / whitespace
    r"(.+?)"               # entity name (greedy-ish)
    r"(?:\s*\([\w\s-]+\))?"  # optional parenthetical like (Fast-burn rate)
    r"\s*$",
    re.MULTILINE,
)

# Matches timestamp lines like:  Started at   2026-04-12 03:41:00 UTC
_TS_PATTERN = re.compile(
    r"(Started at|Activated at|Closed at|Opened at)\s+"
    r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s*(?:UTC)?",
    re.IGNORECASE,
)

# Entity type hints embedded in the title or its trailing parenthetical
_TYPE_HINTS = {
    "is down": "SYNTHETIC",
    "is failing": "SYNTHETIC",
    "sm": "SYNTHETIC",
    "synthetic": "SYNTHETIC",
    "fast-burn rate": "SERVICE_LEVEL",
    "slow-burn rate": "SERVICE_LEVEL",
    "burn rate": "SERVICE_LEVEL",
    "compliance": "SERVICE_LEVEL",
    "sl": "SERVICE_LEVEL",
    "slo": "SERVICE_LEVEL",
    "error rate": "APM",
    "response time": "APM",
    "throughput": "APM",
    "apdex": "APM",
    "apm": "APM",
}

# Trailing parenthetical at end of a name, e.g. " (Fast-burn rate)"
_TRAILING_PAREN_PATTERN = re.compile(r"\s*\([^)]*\)\s*$")


def strip_trailing_parenthetical(name: str) -> tuple[str, str]:
    """Strip a trailing parenthetical from an alert/entity name.

    NR alert card titles look like "[Foo] Availability (Fast-burn rate)" —
    the parenthetical describes the alert *condition*, not the entity. The
    NR entity is named without it, so we strip it before any search.

    Returns (cleaned_name, parenthetical_content_lowercased).
    The parenthetical content is returned without the surrounding `()` and
    lowercased so callers can match it against `_TYPE_HINTS`.
    """
    if not name:
        return name, ""
    match = _TRAILING_PAREN_PATTERN.search(name)
    if not match:
        return name.strip(), ""
    paren = match.group(0).strip()           # e.g. "(Fast-burn rate)"
    inner = paren[1:-1].strip().lower()      # e.g. "fast-burn rate"
    cleaned = _TRAILING_PAREN_PATTERN.sub("", name).strip()
    return cleaned, inner


def infer_type_hint(text: str) -> str | None:
    """Return the first matching entity type hint found in `text`, or None.

    Match is substring, case-insensitive. `text` may be a full title or just
    the parenthetical content.
    """
    if not text:
        return None
    lower = text.lower()
    for phrase, hint_type in _TYPE_HINTS.items():
        if phrase in lower:
            return hint_type
    return None


def parse_alert_message(text: str) -> dict | None:
    """Parse a NR alert message and extract structured fields.

    Returns a dict with:
      - entity_name: str
      - entity_type_hint: str | None  (APM, SYNTHETIC, SERVICE_LEVEL)
      - time_start: str (ISO 8601)
      - time_end: str (ISO 8601)
      - raw_timestamps: dict of label → datetime
    Or None if the message doesn't look like an alert.
    """
    if not text:
        return None

    # Clean up any HTML / adaptive card artifacts
    clean = re.sub(r"<[^>]+>", "", text).strip()

    if not clean:
        return None

    # --- Extract timestamps ---
    timestamps = {}
    for match in _TS_PATTERN.finditer(clean):
        label = match.group(1).lower().replace(" ", "_")
        dt_str = match.group(2)
        try:
            dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            timestamps[label] = dt
        except ValueError:
            continue

    # --- Extract title (first non-empty line) ---
    lines = [l.strip() for l in clean.split("\n") if l.strip()]
    if not lines:
        return None

    title_line = lines[0]

    # Remove leading emoji characters (status indicators)
    # Covers 🟢🔴🟡⚠️ and other unicode symbols
    entity_name = re.sub(r"^[\U0001F000-\U0001FFFF\u2600-\u27BF\u2700-\u27BF\s]+", "", title_line).strip()

    # Strip trailing parenthetical alert-condition signal, e.g. "(Fast-burn rate)"
    entity_name, burn_info = strip_trailing_parenthetical(entity_name)

    if not entity_name:
        return None

    # Prefer the parenthetical signal (most specific), then fall back to the
    # entity name itself for embedded hints like "is down".
    entity_type_hint = infer_type_hint(burn_info) or infer_type_hint(entity_name)

    # --- Build investigation time window ---
    # Use the earliest and latest timestamps, padded by 1 hour each side
    if timestamps:
        earliest = min(timestamps.values())
        latest = max(timestamps.values())
        time_start = (earliest - timedelta(hours=1)).isoformat()
        time_end = (latest + timedelta(hours=1)).isoformat()
    else:
        # No timestamps found — can't determine window
        time_start = None
        time_end = None

    result = {
        "entity_name": entity_name,
        "entity_type_hint": entity_type_hint,
        "time_start": time_start,
        "time_end": time_end,
        "raw_timestamps": {k: v.isoformat() for k, v in timestamps.items()},
    }

    logger.info("Parsed alert: %s", result)
    return result
