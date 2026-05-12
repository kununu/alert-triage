from unittest.mock import patch
import pytest

from newrelic.client import (
    get_service_triage_data,
    _find_entity_via_incident,
    _pick_best_incident,
    _fetch_service_level,
)


def _mock_apm_nrql(burn_rate=2.1, error_count=50, avg_duration=300.0):
    def side_effect(nrql):
        if "newrelic.sli" in nrql:
            return [{"burn_rate": burn_rate}]
        return [{"error_count": error_count, "avg_duration": avg_duration}]
    return side_effect


def _mock_synthetic_nrql(failure_rate=40.0, failed=4, total=10):
    def side_effect(nrql):
        if "FACET locationLabel" in nrql:
            return [{"facet": "US East", "failures": 2}, {"facet": "EU West", "failures": 2}]
        return [{"total_checks": total, "failed_checks": failed, "failure_rate": failure_rate}]
    return side_effect


def test_get_service_triage_data_apm():
    fake_entity = {"entityType": "APPLICATION", "name": "payments-service", "permalink": "https://nr.com/app"}
    with (
        patch("newrelic.client._find_entity", return_value=fake_entity),
        patch("newrelic.client._run_nrql", side_effect=_mock_apm_nrql()),
    ):
        result = get_service_triage_data("payments-service")

    assert result["entity_type"] == "APM"
    assert result["burn_rate"] == 2.1
    assert result["error_count"] == 50


def test_get_service_triage_data_synthetic():
    fake_entity = {"entityType": "MONITOR", "name": "checkout-monitor", "permalink": "https://nr.com/monitor"}
    with (
        patch("newrelic.client._find_entity", return_value=fake_entity),
        patch("newrelic.client._run_nrql", side_effect=_mock_synthetic_nrql()),
    ):
        result = get_service_triage_data("checkout-monitor")

    assert result["entity_type"] == "SYNTHETIC"
    assert result["failure_rate"] == 40.0
    assert "US East" in result["failing_locations"]


def test_get_service_triage_data_not_found():
    with patch("newrelic.client._find_entity", return_value=None):
        result = get_service_triage_data("unknown-service")

    assert result is None


# ── Incident-based entity resolution ────────────────────────

def test_get_service_triage_data_uses_incident_when_timestamps_given():
    """With timestamps, triage should resolve via incident lookup and skip fuzzy search."""
    fake_entity = {"guid": "abc123", "entityType": "MONITOR", "name": "checkout-monitor", "permalink": "https://nr.com"}
    with (
        patch("newrelic.client._find_entity_via_incident", return_value=fake_entity) as mock_incident,
        patch("newrelic.client._find_entity") as mock_fuzzy,
        patch("newrelic.client._run_nrql", side_effect=_mock_synthetic_nrql()),
    ):
        result = get_service_triage_data(
            "checkout-monitor",
            time_start="2026-04-12 03:00:00",
            time_end="2026-04-12 05:00:00",
        )

    mock_incident.assert_called_once()
    mock_fuzzy.assert_not_called()
    assert result["entity_type"] == "SYNTHETIC"


def test_get_service_triage_data_falls_back_when_no_incident():
    """When incident lookup finds nothing, falls back to fuzzy name search."""
    fake_entity = {"entityType": "APPLICATION", "name": "payments-service", "permalink": "https://nr.com"}
    with (
        patch("newrelic.client._find_entity_via_incident", return_value=None),
        patch("newrelic.client._find_entity", return_value=fake_entity) as mock_fuzzy,
        patch("newrelic.client._run_nrql", side_effect=_mock_apm_nrql()),
    ):
        result = get_service_triage_data(
            "payments-service",
            entity_type_hint="APM",
            time_start="2026-04-12 03:00:00",
            time_end="2026-04-12 05:00:00",
        )

    mock_fuzzy.assert_called_once_with("payments-service", entity_type_hint="APM")
    assert result["entity_type"] == "APM"


def test_pick_best_incident_exact_match():
    rows = [
        {"entityName": "other-service", "entityGuid": "guid1"},
        {"entityName": "Culture MMI Page", "entityGuid": "guid2"},
    ]
    result = _pick_best_incident(rows, "Culture MMI Page")
    assert result["entityGuid"] == "guid2"


def test_pick_best_incident_contains_match():
    rows = [
        {"entityName": "Culture MMI Page is Down", "entityGuid": "guid1"},
    ]
    result = _pick_best_incident(rows, "Culture MMI Page")
    assert result["entityGuid"] == "guid1"


def test_pick_best_incident_empty():
    assert _pick_best_incident([], "anything") is None


def test_find_entity_via_incident_no_guid_falls_back():
    """When NrAiIncident row has no entityGuid, returns None to trigger fallback."""
    incident_row = {"entityName": "my-service", "conditionName": "some-condition"}  # no entityGuid
    with patch("newrelic.client._safe_nrql", return_value=[incident_row]):
        result = _find_entity_via_incident(
            "my-service", "2026-04-12 03:00:00", "2026-04-12 05:00:00",
        )
    assert result is None


def test_find_entity_via_incident_no_results():
    """When NrAiIncident returns nothing, returns None."""
    with patch("newrelic.client._safe_nrql", return_value=[]):
        result = _find_entity_via_incident(
            "my-service", "2026-04-12 03:00:00", "2026-04-12 05:00:00",
        )
    assert result is None


def test_find_entity_via_incident_resolves_entity():
    """Happy path: incident found with GUID → entity fetched directly."""
    incident_row = {"entityName": "my-service", "entityGuid": "guid-xyz", "conditionName": "SLI fast-burn"}
    fake_entity = {"guid": "guid-xyz", "entityType": "SERVICE_LEVEL", "name": "my-service"}
    with (
        patch("newrelic.client._safe_nrql", return_value=[incident_row]),
        patch("newrelic.client._fetch_entity_by_guid", return_value=fake_entity),
    ):
        result = _find_entity_via_incident(
            "my-service", "2026-04-12 03:00:00", "2026-04-12 05:00:00",
        )
    assert result["entityType"] == "SERVICE_LEVEL"
    assert result["guid"] == "guid-xyz"


# ── Service Level triage enrichment ──────────────────────────

def _make_sl_entity(category="largestcontentfulpaint", associated="browser-app"):
    return {
        "guid": "sl-guid-001",
        "entityType": "SERVICE_LEVEL",
        "name": "[Culture] [Culture tab] LCP",
        "permalink": "https://nr.com/sl",
        "tags": [
            {"key": "category", "values": [category]},
            {"key": "nr.sloTarget", "values": ["75.0"]},
            {"key": "nr.sliComplianceCategory", "values": ["Non-compliant"]},
            {"key": "nr.associatedEntityName", "values": [associated]},
        ],
    }


def test_fetch_service_level_lcp_fetches_js_errors():
    """LCP SLIs should fetch JS error signal and include sli_kind + quick_signals."""
    entity = _make_sl_entity(category="largestcontentfulpaint", associated="app_profiles-endpoint")

    def nrql_side_effect(nrql):
        if "ServiceLevelSnapshot" in nrql:
            return [{"current_compliance": 74.16}]
        if "JavaScriptError" in nrql:
            return [{"js_error_count": 42, "top_error_class": "UnhandledPromiseRejection", "top_error_message": "405 Method Not Allowed"}]
        if "NrAiIncident" in nrql:
            return [{"incident_count": 1, "latest_condition": "LCP Fast-burn rate"}]
        return []

    with patch("newrelic.client._run_nrql", side_effect=nrql_side_effect), \
         patch("newrelic.client._safe_nrql", side_effect=nrql_side_effect):
        result = _fetch_service_level("[Culture] [Culture tab] LCP", entity)

    assert result["entity_type"] == "SERVICE_LEVEL"
    assert result["sli_kind"] == "lcp"
    assert result["current_compliance"] == 74.16
    assert result["quick_signals"]["js_error_count"] == 42
    assert result["quick_signals"]["top_js_error_class"] == "UnhandledPromiseRejection"
    assert result["quick_signals"]["active_incident_count"] == 1


def test_fetch_service_level_availability_fetches_apm_errors():
    """Availability SLIs should fetch APM error signal instead of JS errors."""
    entity = _make_sl_entity(category="availability", associated="backend-api")

    def nrql_side_effect(nrql):
        if "ServiceLevelSnapshot" in nrql:
            return [{"current_compliance": 98.5}]
        if "Transaction" in nrql:
            return [{"error_count": 120, "error_rate": 5.3, "top_error_message": "Timeout"}]
        if "NrAiIncident" in nrql:
            return [{"incident_count": 2, "latest_condition": "Availability breach"}]
        return []

    with patch("newrelic.client._run_nrql", side_effect=nrql_side_effect), \
         patch("newrelic.client._safe_nrql", side_effect=nrql_side_effect):
        result = _fetch_service_level("[Search] Availability", entity)

    assert result["sli_kind"] == "availability"
    assert result["quick_signals"]["apm_error_count"] == 120
    assert result["quick_signals"]["apm_error_rate_pct"] == 5.3
    assert result["quick_signals"]["active_incident_count"] == 2


def test_fetch_service_level_no_associated_entity_skips_signals():
    """When no associated entity is present, quick_signals should be empty."""
    entity = _make_sl_entity(category="largestcontentfulpaint", associated="")
    entity["tags"] = [
        {"key": "category", "values": ["largestcontentfulpaint"]},
        {"key": "nr.sloTarget", "values": ["75.0"]},
    ]

    with patch("newrelic.client._run_nrql", return_value=[{"current_compliance": 80.0}]):
        result = _fetch_service_level("Some SLO", entity)

    assert result["sli_kind"] == "lcp"
    assert result["quick_signals"] == {}
