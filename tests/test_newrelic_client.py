from unittest.mock import patch
import pytest

from newrelic.client import get_service_triage_data


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
