import pytest
from newrelic.sanitize import nrql_string, nrql_timestamp, nrql_trace_id, entity_search_string


# ── nrql_string ────────────────────────────────────────────────

def test_nrql_string_clean_value():
    assert nrql_string("payments-service") == "payments-service"


def test_nrql_string_escapes_single_quote():
    assert nrql_string("O'Brien Monitor") == "O''Brien Monitor"


def test_nrql_string_escapes_multiple_single_quotes():
    assert nrql_string("it's a test's name") == "it''s a test''s name"


def test_nrql_string_empty():
    assert nrql_string("") == ""


def test_nrql_string_injection_attempt():
    evil = "x' OR monitorName LIKE '%"
    result = nrql_string(evil)
    assert "'" not in result.replace("''", "")


# ── nrql_timestamp ─────────────────────────────────────────────

def test_nrql_timestamp_space_separator():
    assert nrql_timestamp("2024-04-15 14:30:00") == "2024-04-15 14:30:00"


def test_nrql_timestamp_T_separator():
    assert nrql_timestamp("2024-04-15T14:30:00") == "2024-04-15T14:30:00"


def test_nrql_timestamp_strips_Z_suffix():
    result = nrql_timestamp("2024-04-15T14:30:00Z")
    assert result == "2024-04-15T14:30:00"


def test_nrql_timestamp_rejects_non_timestamp():
    with pytest.raises(ValueError):
        nrql_timestamp("'; DROP TABLE SyntheticCheck; --")


def test_nrql_timestamp_rejects_empty():
    with pytest.raises(ValueError):
        nrql_timestamp("")


def test_nrql_timestamp_rejects_relative():
    with pytest.raises(ValueError):
        nrql_timestamp("1 hour ago")


# ── nrql_trace_id ──────────────────────────────────────────────

def test_nrql_trace_id_hex():
    assert nrql_trace_id("abcdef1234567890") == "abcdef1234567890"


def test_nrql_trace_id_uuid_style():
    assert nrql_trace_id("550e8400-e29b-41d4-a716-446655440000") == "550e8400-e29b-41d4-a716-446655440000"


def test_nrql_trace_id_rejects_injection():
    with pytest.raises(ValueError):
        nrql_trace_id("abc'); DROP TABLE Log; --")


def test_nrql_trace_id_rejects_too_short():
    with pytest.raises(ValueError):
        nrql_trace_id("abc")


# ── entity_search_string ───────────────────────────────────────

def test_entity_search_string_clean():
    assert entity_search_string("checkout-monitor") == "checkout-monitor"


def test_entity_search_string_escapes_quote():
    assert entity_search_string("Monitor's Test") == "Monitor''s Test"


def test_entity_search_string_injection_attempt():
    evil = "x' AND type = 'APPLICATION"
    result = entity_search_string(evil)
    assert result == "x'' AND type = ''APPLICATION"
