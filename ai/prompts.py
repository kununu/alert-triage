EXTRACT_SERVICE_PROMPT = """\
You are an on-call triage assistant. Extract structured information from the user message below.

User message:
{alert_text}

Determine the intent:
- "triage" — user is sharing a live alert and wants a quick status check
- "investigate" — user wants a deeper investigation of a past alert (they mention a specific time, "what happened", "investigate", "root cause", etc.)

Reply with a JSON object only — no markdown, no explanation:
{{
  "intent": "<triage | investigate>",
  "service_name": "<exact name of the affected service, monitor, or SLO — preserve brackets, special characters, and casing exactly as they appear>",
  "entity_type_hint": "<APM | SYNTHETIC | SERVICE_LEVEL | null>",
  "severity": "<critical | high | medium | low>",
  "summary": "<one sentence describing what is firing or what the user wants to investigate>",
  "time_start": "<ISO 8601 datetime of the start of the investigation window, or null if triage>",
  "time_end": "<ISO 8601 datetime of the end of the investigation window, or null if triage>"
}}

Important:
- Copy the service/monitor/SLO name character-for-character from the message. Do not simplify, shorten, or remove brackets or punctuation.
- CRITICAL: Phrases like "is Down", "is Failing", "is Slow", "is Broken" ARE part of the Synthetic Monitor name when they appear right after the service name. For example in "Culture MMI Page is Down SM", the monitor name is "Culture MMI Page is Down" (not "Culture MMI Page"). Only strip the type hint abbreviation (SM/SL/APM), NOT the status suffix.
- For entity_type_hint: infer from context clues like "SM" or "synthetic monitor" → SYNTHETIC, "SL" or "service level" → SERVICE_LEVEL, "APM" or "application" → APM. Use null if unclear. Do NOT include the type hint abbreviation (SM, SL, APM) in the service_name — strip it out.
- For "investigate" intent, infer a time window of 1 hour before and 1 hour after the time the user mentions (e.g. "around 5AM" → start 4:00AM, end 6:00AM). Use today's date ({today}) if the user says "today".
- If no specific time is mentioned but intent is investigate, use the last 3 hours as the window.
"""

# ── Triage prompts ──────────────────────────────────────────

TRIAGE_SYNTHESIS_PROMPT_APM = """\
You are an on-call triage assistant. A {severity} alert fired for APM service "{service_name}".

Alert summary: {alert_summary}

New Relic APM data (last 30–60 minutes):
- Burn rate: {burn_rate}x  (1.0 = consuming error budget at exactly the allowed rate)
- Recent error count: {error_count}
- Average error duration: {avg_duration_ms} ms

Write a concise triage brief for the on-call engineer. Include:
1. Whether this is likely a real incident or noise (based on burn rate and error count)
2. Immediate action recommendation (investigate / page secondary / escalate / monitor)
3. One or two specific things to check first in New Relic

Keep the response under 150 words. Use plain language.
"""

TRIAGE_SYNTHESIS_PROMPT_SYNTHETIC = """\
You are an on-call triage assistant. A {severity} alert fired for Synthetic monitor "{service_name}".

Alert summary: {alert_summary}

New Relic Synthetics data (last 30 minutes):
- Total checks run: {total_checks}
- Failed checks: {failed_checks}
- Failure rate: {failure_rate}%
- Failing locations: {failing_locations}

Write a concise triage brief for the on-call engineer. Include:
1. Whether this looks like a real outage or a location-specific / flaky monitor issue
2. Immediate action recommendation (investigate / page secondary / escalate / monitor)
3. One or two specific things to check (e.g. single-location vs multi-location failure, recent deployments)

Keep the response under 150 words. Use plain language.
"""

TRIAGE_SYNTHESIS_PROMPT_SERVICE_LEVEL = """\
You are an on-call triage assistant. A {severity} alert fired for Service Level "{service_name}".

Alert summary: {alert_summary}

New Relic Service Level data:
- Current SLI compliance: {current_compliance}%
- Compliance status: {compliance_category}
- SLO target: {slo_target}
- Associated service: {associated_entity}

Write a concise triage brief for the on-call engineer. Include:
1. Whether the SLO is breached or at risk based on compliance vs target
2. Immediate action recommendation (investigate / page secondary / escalate / monitor)
3. One or two specific things to check first (e.g. the associated service, recent deployments)

Keep the response under 150 words. Use plain language.
"""

# ── Investigation prompts ───────────────────────────────────

INVESTIGATION_SYNTHESIS_PROMPT = """\
You are a senior on-call engineer performing root cause analysis. An alert fired for "{service_name}" \
({entity_type}, SLI kind: {sli_kind}) around {time_start} to {time_end}.

User's question: {user_summary}

Here is the evidence collected from New Relic:

## SLI Definition (the actual NRQL that defines what "good" and "bad" means for this SLI)
{sli_definition}

## SLI Replay (the SLI's own queries re-run against the investigation time window)
{sli_replay}

## Alert Incidents
{alert_incidents}

## {cwv_section_title}
{cwv_data}

## Log Correlation (logs from the actual bad event traces)
{log_correlation}

## JavaScript Errors
{js_errors}

## Deployments
{deployments}

Based on all the evidence above, write a root cause analysis:

1. **What happened**: Describe what failed and when, using the actual data.
2. **Most likely root cause**: Correlate the evidence. What changed or broke?
3. **Affected scope**: Which transactions, pages, endpoints, or locations are impacted?
4. **Recommended actions**: Concrete next steps.

Entity-type-specific guidance:

If this is a **Synthetic Monitor** (entity_type=SYNTHETIC):
- Look at the failure rate, which locations failed, and whether the failure is location-specific or global.
- Check the error messages and HTTP response codes from the failure details.
- If only 1-2 locations failed, this is likely a probe/network issue, not a real outage.
- If all locations failed, this is likely a real outage — check response codes (5xx = server error, \
timeout = infrastructure issue, SSL error = certificate problem).
- Look at the failure timeline to determine if it was a brief blip or sustained outage.

If this is an **APM Application** (entity_type=APM):
- Start with the overview stats: is the error rate elevated? Is latency (p95) abnormally high?
- Check the slowest transactions and error breakdown — which endpoints are failing or slow?
- Look at the throughput timeline — did traffic spike? Did RPM drop to zero (crash)?
- Check external service calls — is a downstream dependency slow or failing?
- Correlate error traces and logs to find the actual error messages and root cause.
- Check if any deployments happened in or just before the window.

If this is a **Service Level** (entity_type=SERVICE_LEVEL):
- Start with the SLI Replay data — this tells you exactly what events the SLI considers "bad" \
and how many occurred. Name specific transactions, pages, or endpoints with the most bad events.
- Correlate SLI bad events with errors, slow transactions, JS errors, or deployments.

If this is a **Web Core Vital** (LCP/INP/CLS):
- For LCP: which page and element type (image, text block) is slow? Is there a specific elementUrl?
- For INP: which interaction (click, keypress) on which target (CSS selector) is slow?
- For CLS: which pages have the highest shift scores?

Be specific and evidence-based. If the data is insufficient to determine root cause, say so and \
suggest what additional data to look at. Keep it under 300 words.
"""
