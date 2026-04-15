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

# Entity type hints embedded in the title
_TYPE_HINTS = {
    "is down": "SYNTHETIC",
    "is failing": "SYNTHETIC",
    "sm": "SYNTHETIC",
    "synthetic": "SYNTHETIC",
    "fast-burn rate": "SERVICE_LEVEL",
    "slow-burn rate": "SERVICE_LEVEL",
    "compliance": "SERVICE_LEVEL",
    "sl": "SERVICE_LEVEL",
    "error rate": "APM",
    "response time": "APM",
    "throughput": "APM",
    "apdex": "APM",
    "apm": "APM",
}


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

    # Remove trailing parenthetical like (Fast-burn rate)
    burn_match = re.search(r"\s*\([^)]*\)\s*$", entity_name)
    burn_info = burn_match.group(0).strip() if burn_match else ""
    entity_name = re.sub(r"\s*\([^)]*\)\s*$", "", entity_name).strip()

    if not entity_name:
        return None

    # --- Infer entity type from title ---
    entity_type_hint = None
    full_title_lower = (entity_name + " " + burn_info).lower()
    for hint_phrase, hint_type in _TYPE_HINTS.items():
        if hint_phrase in full_title_lower:
            entity_type_hint = hint_type
            break

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
