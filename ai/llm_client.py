import json
import re
import time
import logging
from datetime import date
from google import genai
from google.genai.errors import ClientError, ServerError
from config.settings import GEMINI_API_KEY
from ai.prompts import (
    EXTRACT_SERVICE_PROMPT,
    TRIAGE_SYNTHESIS_PROMPT_APM,
    TRIAGE_SYNTHESIS_PROMPT_SYNTHETIC,
    TRIAGE_SYNTHESIS_PROMPT_SERVICE_LEVEL,
    INVESTIGATION_SYNTHESIS_PROMPT,
)

logger = logging.getLogger(__name__)

_client = genai.Client(api_key=GEMINI_API_KEY)
_MODEL = "gemini-2.0-flash"

_MAX_RETRIES = 3
_DEFAULT_RETRY_DELAY = 30


def _parse_retry_delay(error: ClientError) -> float:
    match = re.search(r"retryDelay.*?(\d+)s", str(error))
    if match:
        return float(match.group(1))
    return _DEFAULT_RETRY_DELAY


def _generate(prompt: str) -> str:
    """Send a prompt to Gemini and return the text response, with retry on overload."""
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            response = _client.models.generate_content(model=_MODEL, contents=prompt)
            return response.text.strip()
        except ServerError as e:
            code = getattr(e, "code", None) or getattr(e, "status_code", None)
            if attempt < _MAX_RETRIES:
                logger.warning(
                    "Gemini model unavailable/overloaded (attempt %d/%d). Retrying in %ds…",
                    attempt, _MAX_RETRIES, _DEFAULT_RETRY_DELAY,
                )
                time.sleep(_DEFAULT_RETRY_DELAY)
            else:
                raise RuntimeError(
                    f"Gemini model is unavailable (HTTP {code}). "
                    "The model may be overloaded — please try again in a moment."
                ) from e
        except ClientError as e:
            code = getattr(e, "code", None) or getattr(e, "status_code", None)
            if code == 429 and attempt < _MAX_RETRIES:
                delay = _parse_retry_delay(e)
                logger.warning(
                    "Gemini rate limited (attempt %d/%d). Retrying in %.0fs…",
                    attempt, _MAX_RETRIES, delay,
                )
                time.sleep(delay)
            elif code == 403:
                raise RuntimeError(
                    "Gemini API key is invalid or has been revoked. "
                    "Please rotate your GEMINI_API_KEY in the .env file."
                ) from e
            elif code == 400 and "API_KEY_INVALID" in str(e):
                raise RuntimeError(
                    "Gemini API key is expired or invalid (400). "
                    "Please check your GEMINI_API_KEY in the .env file — "
                    "new keys can take a few minutes to activate."
                ) from e
            else:
                raise


_VALID_INTENTS = {"triage", "investigate"}
_VALID_TYPE_HINTS = {"APM", "SYNTHETIC", "SERVICE_LEVEL", None}
_MAX_FIELD_LEN = 256


def _validate_context(data: dict) -> dict:
    """Validate and normalise the JSON returned by the LLM extraction prompt.

    Raises ValueError with a descriptive message if a required field is missing
    or has an unexpected type/value — preventing malformed LLM output from
    propagating into downstream query or UI logic.
    """
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object, got {type(data).__name__}")

    # service_name — required, must be a non-empty string within reasonable length
    service_name = data.get("service_name")
    if not isinstance(service_name, str) or not service_name.strip():
        raise ValueError("Missing or empty 'service_name' in LLM response")
    if len(service_name) > _MAX_FIELD_LEN:
        raise ValueError(f"'service_name' exceeds max length ({_MAX_FIELD_LEN})")

    # severity — optional, coerce to lowercase string or default
    severity = data.get("severity", "unknown")
    if not isinstance(severity, str):
        severity = "unknown"
    data["severity"] = severity.lower()[:32]

    # summary — optional but must be a string if present
    summary = data.get("summary", "")
    if not isinstance(summary, str):
        summary = ""
    data["summary"] = summary[:1024]

    # intent — must be one of the known values
    intent = data.get("intent", "triage")
    if intent not in _VALID_INTENTS:
        intent = "triage"
    data["intent"] = intent

    # entity_type_hint — must be a known type or absent
    type_hint = data.get("entity_type_hint")
    if type_hint not in _VALID_TYPE_HINTS:
        type_hint = None
    data["entity_type_hint"] = type_hint

    # time_start / time_end — optional; validate format if present
    for field in ("time_start", "time_end"):
        val = data.get(field)
        if val is not None:
            if not isinstance(val, str):
                data[field] = None
            elif len(val) > 32:
                data[field] = None

    return data


def extract_service_context(alert_text: str) -> dict:
    """Parse raw alert text into structured service context (triage or investigation)."""
    raw = _generate(
        EXTRACT_SERVICE_PROMPT.format(alert_text=alert_text, today=date.today().isoformat())
    )
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    data = json.loads(raw.strip())
    return _validate_context(data)


def synthesize_triage(service_name: str, severity: str, alert_summary: str, nr_data: dict) -> str:
    """Return a human-readable triage brief based on entity type."""
    entity_type = nr_data.get("entity_type")

    if entity_type == "APM":
        prompt = TRIAGE_SYNTHESIS_PROMPT_APM.format(
            service_name=service_name,
            severity=severity,
            alert_summary=alert_summary,
            burn_rate=f"{nr_data['burn_rate']:.2f}" if nr_data.get("burn_rate") is not None else "N/A",
            error_count=nr_data.get("error_count") if nr_data.get("error_count") is not None else "N/A",
            avg_duration_ms=f"{nr_data['avg_duration_ms']:.0f}" if nr_data.get("avg_duration_ms") is not None else "N/A",
        )
    elif entity_type == "SYNTHETIC":
        locations = ", ".join(nr_data["failing_locations"]) if nr_data.get("failing_locations") else "N/A"
        prompt = TRIAGE_SYNTHESIS_PROMPT_SYNTHETIC.format(
            service_name=service_name,
            severity=severity,
            alert_summary=alert_summary,
            total_checks=nr_data.get("total_checks") if nr_data.get("total_checks") is not None else "N/A",
            failed_checks=nr_data.get("failed_checks") if nr_data.get("failed_checks") is not None else "N/A",
            failure_rate=f"{nr_data['failure_rate']:.1f}" if nr_data.get("failure_rate") is not None else "N/A",
            failing_locations=locations,
        )
    elif entity_type == "SERVICE_LEVEL":
        compliance = nr_data.get("current_compliance")
        prompt = TRIAGE_SYNTHESIS_PROMPT_SERVICE_LEVEL.format(
            service_name=service_name,
            severity=severity,
            alert_summary=alert_summary,
            current_compliance=f"{compliance:.2f}" if compliance is not None else "N/A",
            compliance_category=nr_data.get("compliance_category", "Unknown"),
            slo_target=nr_data.get("slo_target", "N/A"),
            associated_entity=nr_data.get("associated_entity", "N/A"),
        )
    else:
        raise ValueError(f"Unknown entity_type: {entity_type}")

    return _generate(prompt)


def synthesize_investigation(
    service_name: str,
    user_summary: str,
    time_start: str,
    time_end: str,
    investigation_data: dict,
) -> str:
    """Return a root cause analysis based on investigation evidence."""
    prompt = INVESTIGATION_SYNTHESIS_PROMPT.format(
        service_name=service_name,
        entity_type=investigation_data.get("entity_type", "unknown"),
        sli_kind=investigation_data.get("sli_kind", "unknown"),
        time_start=time_start,
        time_end=time_end,
        user_summary=user_summary,
        sli_definition=investigation_data.get("sli_definition", "Not available."),
        sli_replay=investigation_data.get("sli_replay", "Not available."),
        alert_incidents=investigation_data.get("alert_incidents", "No data."),
        cwv_section_title=investigation_data.get("cwv_section_title", "Metric Data"),
        cwv_data=investigation_data.get("cwv_data", "No data."),
        log_correlation=investigation_data.get("log_correlation", "No data."),
        js_errors=investigation_data.get("js_errors", "No data."),
        deployments=investigation_data.get("deployments", "No data."),
    )
    return _generate(prompt)
