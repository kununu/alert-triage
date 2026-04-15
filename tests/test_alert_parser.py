import pytest
from bot.alert_parser import parse_alert_message


def test_parse_full_alert_card():
    """Test parsing a typical NR alert card message."""
    text = (
        "\U0001f7e2 [Culture] [Culture tab] LCP (Fast-burn rate)\n"
        "Started at   2026-04-12 03:41:00 UTC\n"
        "Activated at 2026-04-12 04:49:41 UTC\n"
        "Closed at    2026-04-12 04:55:40 UTC\n"
        "Duration     5m 58.874s"
    )
    result = parse_alert_message(text)
    assert result is not None
    assert result["entity_name"] == "[Culture] [Culture tab] LCP"
    assert result["entity_type_hint"] == "SERVICE_LEVEL"  # fast-burn rate
    assert result["time_start"] is not None
    assert result["time_end"] is not None
    assert "started_at" in result["raw_timestamps"]
    assert "closed_at" in result["raw_timestamps"]


def test_parse_synthetic_alert():
    """Test parsing a synthetic monitor alert."""
    text = (
        "\U0001f534 Culture MMI Page is Down\n"
        "Started at   2026-04-10 16:24:00 UTC\n"
        "Activated at 2026-04-10 16:25:00 UTC\n"
    )
    result = parse_alert_message(text)
    assert result is not None
    assert result["entity_name"] == "Culture MMI Page is Down"
    assert result["entity_type_hint"] == "SYNTHETIC"  # "is down"


def test_parse_no_timestamps():
    """Still extracts entity name even without timestamps."""
    text = "\U0001f7e2 payments-service error rate"
    result = parse_alert_message(text)
    assert result is not None
    assert "payments-service" in result["entity_name"]
    assert result["time_start"] is None
    assert result["time_end"] is None


def test_parse_empty():
    assert parse_alert_message("") is None
    assert parse_alert_message(None) is None
