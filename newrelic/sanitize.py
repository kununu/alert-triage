"""
NRQL and NerdGraph input sanitization.

All user-originated or externally-sourced values that are interpolated into
NRQL queries or NerdGraph entitySearch strings must pass through the
appropriate function here before use.
"""
import re

# Matches the leading part of any valid ISO 8601 / NR datetime we produce:
# "2024-04-15 14:30:00" or "2024-04-15T14:30:00"
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}")

# Allowlist for timestamp characters after validation
_UNSAFE_TS_CHARS = re.compile(r"[^\d\-: T]")

# New Relic trace IDs are hex strings (16–64 hex chars), optionally with dashes (UUID style)
_TRACE_ID_RE = re.compile(r"^[a-f0-9\-]{16,64}$", re.IGNORECASE)


def nrql_string(value: str) -> str:
    """Escape a string value for safe interpolation inside NRQL single-quote delimiters.

    NRQL uses single quotes as string delimiters. A literal single quote inside
    the value is escaped by doubling it — the same convention as standard SQL.

    Example:
        WHERE monitorName = '{nrql_string(name)}'
    """
    return str(value).replace("'", "''")


def nrql_timestamp(value: str) -> str:
    """Validate and sanitize a timestamp string for use in NRQL SINCE/UNTIL clauses.

    Accepts strings in the form "YYYY-MM-DD HH:MM:SS" or "YYYY-MM-DDTHH:MM:SS"
    (with an optional trailing UTC offset / fractional seconds that are stripped).

    Raises ValueError if the value does not match the expected format.
    """
    s = str(value).strip()
    if not _TIMESTAMP_RE.match(s):
        raise ValueError(
            f"Invalid timestamp for NRQL query: {s!r}. "
            "Expected 'YYYY-MM-DD HH:MM:SS' or 'YYYY-MM-DDTHH:MM:SS'."
        )
    # Strip any characters that aren't digits, dashes, colons, spaces, or 'T'
    return _UNSAFE_TS_CHARS.sub("", s)


def nrql_trace_id(value: str) -> str:
    """Validate a trace ID for safe inclusion inside a NRQL IN() clause.

    New Relic trace IDs are hex strings (16–64 hex characters), sometimes
    formatted as UUIDs with dashes.

    Raises ValueError if the value does not match the expected format.
    """
    cleaned = str(value).strip()
    if not _TRACE_ID_RE.match(cleaned):
        raise ValueError(f"Invalid trace ID (unexpected characters): {cleaned!r}")
    return cleaned


def entity_search_string(value: str) -> str:
    """Escape a string for safe interpolation in a NerdGraph entitySearch query.

    NerdGraph entitySearch uses single-quote delimiters for string values,
    identical to NRQL. This is a named alias to make the call-site intent clear.
    """
    return str(value).replace("'", "''")
