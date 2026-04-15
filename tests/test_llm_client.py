from unittest.mock import patch
import pytest

from ai.llm_client import extract_service_context, synthesize_triage


def _mock_generate(text: str):
    return patch("ai.llm_client._generate", return_value=text)


def test_extract_service_context_valid_json():
    json_response = '{"service_name": "payments-service", "severity": "high", "summary": "Error rate spike"}'
    with _mock_generate(json_response):
        result = extract_service_context("High errors on payments-service")

    assert result["service_name"] == "payments-service"
    assert result["severity"] == "high"


def test_extract_service_context_strips_markdown_fences():
    fenced = "```json\n{\"service_name\": \"api\", \"severity\": \"low\", \"summary\": \"minor\"}\n```"
    with _mock_generate(fenced):
        result = extract_service_context("minor issue on api")

    assert result["service_name"] == "api"


def test_synthesize_triage_apm():
    with _mock_generate("Investigate immediately. Check error traces."):
        result = synthesize_triage(
            service_name="payments-service",
            severity="critical",
            alert_summary="Error rate spike",
            nr_data={"entity_type": "APM", "burn_rate": 5.0, "error_count": 200, "avg_duration_ms": 800.0},
        )
    assert isinstance(result, str) and len(result) > 0


def test_synthesize_triage_synthetic():
    with _mock_generate("Monitor failing in 2 locations. Investigate."):
        result = synthesize_triage(
            service_name="checkout-monitor",
            severity="high",
            alert_summary="Synthetic monitor failing",
            nr_data={"entity_type": "SYNTHETIC", "total_checks": 10, "failed_checks": 4,
                     "failure_rate": 40.0, "failing_locations": ["US East", "EU West"]},
        )
    assert isinstance(result, str) and len(result) > 0


def test_synthesize_triage_service_level():
    with _mock_generate("Error budget nearly exhausted. Escalate."):
        result = synthesize_triage(
            service_name="checkout-slo",
            severity="critical",
            alert_summary="SLO breach imminent",
            nr_data={"entity_type": "SERVICE_LEVEL", "current_compliance": 98.5,
                     "compliance_category": "Non-compliant", "slo_target": "99.9",
                     "associated_entity": "checkout-service"},
        )
    assert isinstance(result, str) and len(result) > 0
