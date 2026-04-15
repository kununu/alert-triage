import json
import logging
import re
import requests
from config.settings import NR_API_KEY, NR_ACCOUNT_ID, NR_NERDGRAPH_URL
from newrelic.sanitize import nrql_string, nrql_timestamp, nrql_trace_id, entity_search_string

logger = logging.getLogger(__name__)
from newrelic.queries import (
    NERDGRAPH_NRQL_QUERY,
    ENTITY_SEARCH_QUERY,
    SLI_DEFINITION_QUERY,
    APM_BURN_RATE_NRQL,
    APM_ERRORS_NRQL,
    SYNTHETIC_STATS_NRQL,
    SYNTHETIC_LOCATIONS_NRQL,
    SL_COMPLIANCE_NRQL,
    INVESTIGATION_ALERTS_NRQL,
    INVESTIGATION_DEPLOYMENTS_NRQL,
    INVESTIGATION_JS_ERRORS_NRQL,
    INVESTIGATION_LCP_DETAIL_NRQL,
    INVESTIGATION_LCP_ELEMENTS_NRQL,
    INVESTIGATION_INP_DETAIL_NRQL,
    INVESTIGATION_INP_INTERACTIONS_NRQL,
    INVESTIGATION_CLS_DETAIL_NRQL,
    INVESTIGATION_CLS_WORST_NRQL,
    INVESTIGATION_APM_ERRORS_NRQL,
    INVESTIGATION_APM_SLOW_NRQL,
    INVESTIGATION_APM_OVERVIEW_NRQL,
    INVESTIGATION_APM_TIMESERIES_NRQL,
    INVESTIGATION_APM_ERROR_TRACES_NRQL,
    INVESTIGATION_APM_THROUGHPUT_NRQL,
    INVESTIGATION_APM_EXTERNAL_NRQL,
    INVESTIGATION_SYNTHETIC_CHECKS_NRQL,
    INVESTIGATION_SYNTHETIC_STATS_NRQL,
    INVESTIGATION_SYNTHETIC_LOCATIONS_NRQL,
    INVESTIGATION_SYNTHETIC_TIMESERIES_NRQL,
    INVESTIGATION_SYNTHETIC_FAILURES_NRQL,
    INVESTIGATION_SYNTHETIC_REQUESTS_NRQL,
)

# Maps NerdGraph entityType values to our internal labels.
# NerdGraph returns various entityType strings depending on the entity domain;
# we map all known variants to our internal labels.
_ENTITY_TYPE_MAP = {
    "APPLICATION": "APM",
    "APM_APPLICATION_ENTITY": "APM",
    "MONITOR": "SYNTHETIC",
    "SYNTHETIC_MONITOR": "SYNTHETIC",
    "SYNTHETIC_MONITOR_ENTITY": "SYNTHETIC",
    "EXTERNAL_ENTITY": "SERVICE_LEVEL",
    "SERVICE_LEVEL": "SERVICE_LEVEL",
    "WORKLOAD_ENTITY": "SERVICE_LEVEL",
}

# Maps the NR category tag to our SLI kind
_CATEGORY_KIND_MAP = {
    "largestcontentfulpaint": "lcp",
    "interactiontonextpaint": "inp",
    "cumulativelayoutshift": "cls",
    "latency": "latency",
    "availability": "availability",
    "success": "success",
    "error": "error",
    "pageload": "pageload",
}


def _nerdgraph(query: str, variables: dict) -> dict:
    response = requests.post(
        NR_NERDGRAPH_URL,
        json={"query": query, "variables": variables},
        headers={"Api-Key": NR_API_KEY, "Content-Type": "application/json"},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if "errors" in payload:
        raise RuntimeError(f"NerdGraph errors: {payload['errors']}")
    return payload["data"]


def _run_nrql(nrql: str) -> list[dict]:
    data = _nerdgraph(
        NERDGRAPH_NRQL_QUERY,
        {"accountId": int(NR_ACCOUNT_ID), "nrql": nrql},
    )
    return data["actor"]["account"]["nrql"]["results"]


def _safe_nrql(nrql: str) -> list[dict]:
    """Run NRQL but return empty list on error instead of raising."""
    try:
        return _run_nrql(nrql)
    except Exception as e:
        logger.warning("NRQL query failed: %s — %s", e, nrql[:120])
        return []


def _tags_to_dict(tags: list[dict]) -> dict[str, str]:
    """Convert NerdGraph tags list to a flat dict (first value per key)."""
    result = {}
    for tag in tags or []:
        key = tag.get("key", "")
        values = tag.get("values", [])
        if key and values:
            result[key] = values[0]
    return result


_TYPE_HINT_MAP = {
    "APM": "type = 'APPLICATION'",
    "SYNTHETIC": "type = 'MONITOR'",
    "SERVICE_LEVEL": "type = 'SERVICE_LEVEL'",
}


def _find_entity(service_name: str, entity_type_hint: str | None = None) -> dict | None:
    """Return the first matching entity across APM, Synthetics, and Service Levels."""
    all_types = "type IN ('APPLICATION', 'MONITOR', 'SERVICE_LEVEL')"
    # If the user specified the entity type, search that type first
    hint_types = _TYPE_HINT_MAP.get(entity_type_hint) if entity_type_hint else None
    types = hint_types or all_types
    stripped = re.sub(r"\[|\]", "", service_name).strip()

    # Split into meaningful words and join with wildcards for fuzzy LIKE
    # e.g. "MMI Thank You Page Availability" → "%MMI%Thank%You%Page%Availability%"
    words = stripped.split()
    fuzzy = "%" + "%".join(words) + "%" if words else f"%{stripped}%"

    # Sanitize all string values before interpolation into entitySearch query strings.
    # NerdGraph entitySearch uses single-quote delimiters — same escaping as NRQL.
    safe_name = entity_search_string(service_name)
    safe_stripped = entity_search_string(stripped)
    safe_fuzzy = entity_search_string(fuzzy)

    candidates = [
        f"name = '{safe_name}' AND {types}",
        f"name LIKE '%{safe_name}%' AND {types}",
    ]
    if stripped != service_name:
        candidates.append(f"name LIKE '%{safe_stripped}%' AND {types}")
    candidates.append(f"name LIKE '{safe_fuzzy}' AND {types}")

    # If we have a type hint, try progressively shorter fuzzy patterns
    # before falling back to all types. This handles cases where the AI
    # strips words from the entity name (e.g. "Culture MMI Page" when the
    # monitor is actually called "Culture MMI Page is Down").
    if hint_types and len(words) > 1:
        for n in range(len(words) - 1, 0, -1):
            shorter = entity_search_string("%" + "%".join(words[:n]) + "%")
            candidates.append(f"name LIKE '{shorter}' AND {hint_types}")

    # If we had a type hint, also try without it as fallback
    if hint_types:
        candidates.append(f"name LIKE '{safe_fuzzy}' AND {all_types}")
    # Last resort: no type filter
    candidates.append(f"name LIKE '{safe_fuzzy}'")

    for query in candidates:
        logger.info("Entity search: %s", query)
        data = _nerdgraph(ENTITY_SEARCH_QUERY, {"query": query})
        entities = data["actor"]["entitySearch"]["results"]["entities"]
        if entities:
            best = _pick_best_entity(entities, service_name)
            logger.info("Found entity: %s (from %d candidates)", best.get("name"), len(entities))
            return best

    return None


def _pick_best_entity(entities: list[dict], search_name: str) -> dict:
    """Pick the entity whose name most closely matches the search term.

    Prefers exact match > closest length match (avoids picking a shorter
    or longer entity when multiple match a LIKE query).
    """
    if len(entities) == 1:
        return entities[0]

    search_lower = search_name.lower()

    # Exact match first
    for e in entities:
        if e.get("name", "").lower() == search_lower:
            return e

    # Score by how close the name length is + whether it contains the search term
    def score(e):
        name = e.get("name", "").lower()
        # Prefer names that contain the search term
        contains = search_lower in name
        # Among those, prefer closest length (penalise both shorter and longer)
        length_diff = abs(len(name) - len(search_lower))
        return (not contains, length_diff)

    return min(entities, key=score)


# ── Triage fetchers ─────────────────────────────────────────

def _fetch_apm(name: str, entity: dict) -> dict:
    safe_name = nrql_string(name)
    burn_results = _run_nrql(APM_BURN_RATE_NRQL.format(name=safe_name))
    error_results = _run_nrql(APM_ERRORS_NRQL.format(name=safe_name))
    return {
        "entity_type": "APM",
        "service_name": name,
        "burn_rate": burn_results[0].get("burn_rate") if burn_results else None,
        "error_count": error_results[0].get("error_count") if error_results else None,
        "avg_duration_ms": error_results[0].get("avg_duration") if error_results else None,
        "nr_link": entity.get("permalink") or (
            f"https://one.eu.newrelic.com/nr1-core?account={NR_ACCOUNT_ID}"
            f"&filters=(appName%3D%27{name}%27)"
        ),
    }


def _fetch_synthetic(name: str, entity: dict) -> dict:
    safe_name = nrql_string(name)
    stats_results = _run_nrql(SYNTHETIC_STATS_NRQL.format(name=safe_name))
    loc_results = _run_nrql(SYNTHETIC_LOCATIONS_NRQL.format(name=safe_name))

    stats = stats_results[0] if stats_results else {}
    failing_locations = [r.get("facet") for r in loc_results if r.get("facet")]

    return {
        "entity_type": "SYNTHETIC",
        "service_name": name,
        "total_checks": stats.get("total_checks"),
        "failed_checks": stats.get("failed_checks"),
        "failure_rate": stats.get("failure_rate"),
        "failing_locations": failing_locations or None,
        "nr_link": entity.get("permalink") or (
            f"https://one.eu.newrelic.com/synthetics/monitor-list?account={NR_ACCOUNT_ID}"
        ),
    }


def _fetch_service_level(name: str, entity: dict) -> dict:
    guid = entity["guid"]
    tags = _tags_to_dict(entity.get("tags", []))

    results = _run_nrql(SL_COMPLIANCE_NRQL.format(guid=nrql_string(guid)))
    row = results[0] if results else {}
    current_compliance = row.get("current_compliance")

    compliance_category = tags.get("nr.sliComplianceCategory", "Unknown")
    slo_target = tags.get("nr.sloTarget")
    associated_entity = tags.get("nr.associatedEntityName")

    return {
        "entity_type": "SERVICE_LEVEL",
        "service_name": name,
        "current_compliance": current_compliance,
        "compliance_category": compliance_category,
        "slo_target": slo_target,
        "associated_entity": associated_entity,
        "nr_link": entity.get("permalink") or (
            f"https://one.eu.newrelic.com/nr1-core?account={NR_ACCOUNT_ID}"
        ),
    }


def get_service_triage_data(service_name: str, entity_type_hint: str | None = None) -> dict | None:
    """Return triage data for the service, or None if not found in New Relic."""
    entity = _find_entity(service_name, entity_type_hint=entity_type_hint)
    if not entity:
        return None

    raw_entity_type = entity.get("entityType", "")
    entity_type = _ENTITY_TYPE_MAP.get(raw_entity_type, "UNKNOWN")
    if entity_type == "UNKNOWN":
        logger.warning(
            "Unmapped entityType '%s' for entity '%s' (guid=%s). "
            "Add it to _ENTITY_TYPE_MAP.",
            raw_entity_type, entity.get("name"), entity.get("guid"),
        )

    if entity_type == "APM":
        return _fetch_apm(service_name, entity)
    if entity_type == "SYNTHETIC":
        return _fetch_synthetic(service_name, entity)
    if entity_type == "SERVICE_LEVEL":
        return _fetch_service_level(service_name, entity)

    logger.warning("Unknown entity type: %s for %s", entity.get("entityType"), service_name)
    return None


# ── SLI definition fetcher ──────────────────────────────────

def _get_sli_definition(guid: str) -> dict | None:
    """Fetch the SLI valid/bad events definition via NerdGraph."""
    try:
        data = _nerdgraph(SLI_DEFINITION_QUERY, {"guid": guid})
        entity = data.get("actor", {}).get("entity")
        if not entity:
            logger.warning("SLI definition: entity returned None for guid=%s", guid)
            return None

        service_level = entity.get("serviceLevel")
        if not service_level:
            logger.warning("SLI definition: no serviceLevel field on entity. Raw: %s", json.dumps(entity, default=str)[:500])
            return None

        indicators = service_level.get("indicators") or []
        if not indicators:
            logger.warning("SLI definition: empty indicators list for guid=%s", guid)
            return None

        ind = indicators[0]
        events = ind.get("events", {})
        sli_def = {
            "name": ind.get("name"),
            "valid_from": events.get("validEvents", {}).get("from"),
            "valid_where": events.get("validEvents", {}).get("where"),
            "bad_from": (events.get("badEvents") or {}).get("from"),
            "bad_where": (events.get("badEvents") or {}).get("where"),
            "good_from": (events.get("goodEvents") or {}).get("from"),
            "good_where": (events.get("goodEvents") or {}).get("where"),
            "target": (ind.get("objectives") or [{}])[0].get("target"),
        }
        logger.info("SLI definition fetched: %s", json.dumps(sli_def, default=str))
        return sli_def
    except Exception as e:
        logger.error("Failed to fetch SLI definition for %s: %s", guid, e)
        return None


def _build_sli_investigation_queries(
    sli_def: dict, start: str, end: str,
) -> list[tuple[str, str]]:
    """Build investigation NRQL queries from the SLI definition.

    Returns a list of (label, nrql) tuples.
    """
    queries = []

    if sli_def.get("bad_from") and sli_def.get("bad_where"):
        bad_from = sli_def["bad_from"]
        bad_where = sli_def["bad_where"]
        queries.append((
            "SLI Bad Events — count",
            f"SELECT count(*) AS bad_events "
            f"FROM {bad_from} "
            f"WHERE {bad_where} "
            f"SINCE '{start}' UNTIL '{end}'",
        ))
        queries.append((
            "SLI Bad Events — by transaction",
            f"SELECT count(*) AS bad_events "
            f"FROM {bad_from} "
            f"WHERE {bad_where} "
            f"FACET name "
            f"SINCE '{start}' UNTIL '{end}' "
            f"LIMIT 15",
        ))
        queries.append((
            "SLI Bad Events — timeseries",
            f"SELECT count(*) AS bad_events "
            f"FROM {bad_from} "
            f"WHERE {bad_where} "
            f"SINCE '{start}' UNTIL '{end}' "
            f"TIMESERIES 5 minutes",
        ))
        # Individual bad events with traceId for drill-down
        queries.append((
            "SLI Bad Events — individual traces",
            f"SELECT traceId, name, duration, httpResponseCode, "
            f"error.message, error.class, request.uri "
            f"FROM {bad_from} "
            f"WHERE {bad_where} "
            f"SINCE '{start}' UNTIL '{end}' "
            f"LIMIT 20",
        ))
    elif sli_def.get("good_from") and sli_def.get("good_where"):
        good_from = sli_def["good_from"]
        good_where = sli_def["good_where"]
        valid_from = sli_def.get("valid_from", good_from)
        valid_where = sli_def.get("valid_where", "")
        valid_clause = f"WHERE {valid_where}" if valid_where else ""

        queries.append((
            "SLI Total vs Good Events",
            f"SELECT filter(count(*), WHERE {good_where}) AS good_events, "
            f"count(*) AS total_events "
            f"FROM {valid_from} "
            f"{valid_clause} "
            f"SINCE '{start}' UNTIL '{end}'",
        ))
        queries.append((
            "SLI Non-Good Events — by transaction (what's failing?)",
            f"SELECT count(*) AS non_good_events "
            f"FROM {valid_from} "
            f"WHERE {valid_where + ' AND ' if valid_where else ''}"
            f"NOT ({good_where}) "
            f"FACET name "
            f"SINCE '{start}' UNTIL '{end}' "
            f"LIMIT 15",
        ))
        # Individual non-good events with traceId
        queries.append((
            "SLI Non-Good Events — individual traces",
            f"SELECT traceId, name, duration, httpResponseCode, "
            f"error.message, error.class, request.uri "
            f"FROM {valid_from} "
            f"WHERE {valid_where + ' AND ' if valid_where else ''}"
            f"NOT ({good_where}) "
            f"SINCE '{start}' UNTIL '{end}' "
            f"LIMIT 20",
        ))

    if sli_def.get("valid_from") and sli_def.get("valid_where"):
        queries.append((
            "SLI Valid Events — total count",
            f"SELECT count(*) AS valid_events "
            f"FROM {sli_def['valid_from']} "
            f"WHERE {sli_def['valid_where']} "
            f"SINCE '{start}' UNTIL '{end}'",
        ))

    return queries


def _extract_trace_ids(replay_results: list[dict]) -> list[str]:
    """Extract traceId values from SLI replay result rows."""
    trace_ids = []
    for row in replay_results:
        tid = row.get("traceId")
        if tid and tid not in trace_ids:
            trace_ids.append(tid)
    return trace_ids[:15]  # cap to avoid huge IN clauses


def _query_logs_by_traces(trace_ids: list[str], start: str, end: str) -> list[dict]:
    """Query Log events correlated to the given traceIds."""
    if not trace_ids:
        return []
    # Validate each trace ID before embedding in the IN() clause.
    # nrql_trace_id() raises ValueError on unexpected characters; skip bad IDs.
    safe_ids = []
    for t in trace_ids:
        try:
            safe_ids.append(nrql_trace_id(t))
        except ValueError:
            logger.warning("Skipping invalid trace ID: %r", t)
    if not safe_ids:
        return []
    ids_str = ", ".join(f"'{t}'" for t in safe_ids)
    nrql = (
        f"SELECT timestamp, message, level, error.message, error.class, "
        f"httpResponseCode, request.uri "
        f"FROM Log "
        f"WHERE trace.id IN ({ids_str}) "
        f"SINCE '{start}' UNTIL '{end}' "
        f"LIMIT 50"
    )
    return _safe_nrql(nrql)


# ── Investigation fetcher ───────────────────────────────────

def _results_to_text(results: list[dict], max_rows: int = 20) -> str:
    """Format NRQL results as readable text for the LLM prompt."""
    if not results:
        return "No data found."
    truncated = results[:max_rows]
    lines = [json.dumps(r, default=str) for r in truncated]
    text = "\n".join(lines)
    if len(results) > max_rows:
        text += f"\n... ({len(results) - max_rows} more rows)"
    return text


def get_investigation_data(service_name: str, time_start: str, time_end: str, entity_type_hint: str | None = None) -> dict | None:
    """Collect all evidence for a deep investigation around a time window.

    Returns a dict with entity info + all query results as formatted text,
    or None if the entity is not found.
    """
    entity = _find_entity(service_name, entity_type_hint=entity_type_hint)
    if not entity:
        return None

    raw_entity_type = entity.get("entityType", "")
    entity_type = _ENTITY_TYPE_MAP.get(raw_entity_type, "UNKNOWN")
    if entity_type == "UNKNOWN":
        logger.warning(
            "Unmapped entityType '%s' for entity '%s' (guid=%s). "
            "Add it to _ENTITY_TYPE_MAP.",
            raw_entity_type, entity.get("name"), entity.get("guid"),
        )
    tags = _tags_to_dict(entity.get("tags", []))
    guid = entity["guid"]
    entity_name = entity.get("name", service_name)

    # Validate and sanitize timestamps before any NRQL interpolation.
    # nrql_timestamp() raises ValueError on malformed input.
    nrql_start = nrql_timestamp(time_start.replace("T", " ").replace("Z", ""))
    nrql_end = nrql_timestamp(time_end.replace("T", " ").replace("Z", ""))

    # Route to entity-type-specific investigation
    if entity_type == "SYNTHETIC":
        return _investigate_synthetic(
            entity, entity_name, service_name, nrql_start, nrql_end,
        )

    if entity_type == "APM":
        return _investigate_apm(
            entity, entity_name, service_name, nrql_start, nrql_end,
        )

    # For Service Levels, the associated Browser/APM app is the one we query
    associated_app = tags.get("nr.associatedEntityName", "")
    category = tags.get("category", "")
    sli_kind = _CATEGORY_KIND_MAP.get(category.lower(), category.lower() or "unknown")

    # Determine which app name to use for NRQL queries
    if entity_type == "SERVICE_LEVEL" and associated_app:
        app_name = associated_app
    else:
        app_name = service_name

    logger.info(
        "Investigation: entity_type=%s, sli_kind=%s, app_name=%s, window=%s → %s",
        entity_type, sli_kind, app_name, time_start, time_end,
    )

    # Sanitize entity/app names before any NRQL interpolation
    safe_service = nrql_string(service_name)
    safe_app = nrql_string(app_name)

    # 1. Alert incidents
    alert_incidents = _safe_nrql(
        INVESTIGATION_ALERTS_NRQL.format(
            entity_name=safe_service, start=nrql_start, end=nrql_end,
        )
    )

    # 2. Deployments
    deployments = _safe_nrql(
        INVESTIGATION_DEPLOYMENTS_NRQL.format(
            app_name=safe_app, start=nrql_start, end=nrql_end,
        )
    )

    # 3. JS errors (relevant for browser-based SLIs)
    js_errors = _safe_nrql(
        INVESTIGATION_JS_ERRORS_NRQL.format(
            app_name=safe_app, start=nrql_start, end=nrql_end,
        )
    )

    # 4. SLI-specific data — depends on the SLI kind
    cwv_section_title = ""
    cwv_data = []
    cwv_detail = []

    if sli_kind == "lcp":
        cwv_section_title = "LCP (Largest Contentful Paint) — Page Breakdown"
        cwv_data = _safe_nrql(
            INVESTIGATION_LCP_DETAIL_NRQL.format(
                app_name=safe_app, start=nrql_start, end=nrql_end,
            )
        )
        cwv_detail = _safe_nrql(
            INVESTIGATION_LCP_ELEMENTS_NRQL.format(
                app_name=safe_app, start=nrql_start, end=nrql_end,
            )
        )
    elif sli_kind == "inp":
        cwv_section_title = "INP (Interaction to Next Paint) — Page Breakdown"
        cwv_data = _safe_nrql(
            INVESTIGATION_INP_DETAIL_NRQL.format(
                app_name=safe_app, start=nrql_start, end=nrql_end,
            )
        )
        cwv_detail = _safe_nrql(
            INVESTIGATION_INP_INTERACTIONS_NRQL.format(
                app_name=safe_app, start=nrql_start, end=nrql_end,
            )
        )
    elif sli_kind == "cls":
        cwv_section_title = "CLS (Cumulative Layout Shift) — Page Breakdown"
        cwv_data = _safe_nrql(
            INVESTIGATION_CLS_DETAIL_NRQL.format(
                app_name=safe_app, start=nrql_start, end=nrql_end,
            )
        )
        cwv_detail = _safe_nrql(
            INVESTIGATION_CLS_WORST_NRQL.format(
                app_name=safe_app, start=nrql_start, end=nrql_end,
            )
        )
    elif sli_kind in ("latency", "availability", "success", "error"):
        cwv_section_title = "APM Transaction Data"
        cwv_data = _safe_nrql(
            INVESTIGATION_APM_SLOW_NRQL.format(
                app_name=safe_app, start=nrql_start, end=nrql_end,
            )
        )
        cwv_detail = _safe_nrql(
            INVESTIGATION_APM_ERRORS_NRQL.format(
                app_name=safe_app, start=nrql_start, end=nrql_end,
            )
        )
    else:
        cwv_section_title = "Metric Data"

    all_cwv = cwv_data + cwv_detail

    # 5. SLI definition analysis — fetch the actual valid/bad NRQL and replay it
    sli_definition_text = "No SLI definition available."
    sli_replay_text = "No SLI replay data."
    log_correlation_text = "No log correlation data."

    if entity_type == "SERVICE_LEVEL":
        sli_def = _get_sli_definition(guid)
        if sli_def:
            sli_definition_text = json.dumps(sli_def, indent=2, default=str)
            logger.info("SLI definition: %s", sli_definition_text)

            replay_queries = _build_sli_investigation_queries(
                sli_def, nrql_start, nrql_end,
            )
            replay_parts = []
            all_trace_ids = []
            for label, nrql in replay_queries:
                results = _safe_nrql(nrql)
                replay_parts.append(f"### {label}\nQuery: {nrql}\n{_results_to_text(results)}")
                # Collect traceIds from individual trace queries
                if "individual traces" in label:
                    all_trace_ids.extend(_extract_trace_ids(results))
            sli_replay_text = "\n\n".join(replay_parts) if replay_parts else "No queries generated."

            # 6. Log correlation — use traceIds from bad events to find log entries
            if all_trace_ids:
                logger.info("Correlating %d traceIds with logs", len(all_trace_ids))
                log_results = _query_logs_by_traces(all_trace_ids, nrql_start, nrql_end)
                log_correlation_text = _results_to_text(log_results, max_rows=30)

    return {
        "entity_type": entity_type,
        "service_name": service_name,
        "sli_kind": sli_kind,
        "associated_app": app_name,
        "nr_link": entity.get("permalink", ""),
        "alert_incidents": _results_to_text(alert_incidents),
        "cwv_section_title": cwv_section_title or "Metric Data",
        "cwv_data": _results_to_text(all_cwv),
        "js_errors": _results_to_text(js_errors),
        "deployments": _results_to_text(deployments),
        "sli_definition": sli_definition_text,
        "sli_replay": sli_replay_text,
        "log_correlation": log_correlation_text,
    }


def _investigate_synthetic(
    entity: dict, entity_name: str, service_name: str,
    nrql_start: str, nrql_end: str,
) -> dict:
    """Collect investigation evidence for a Synthetic Monitor."""
    monitor_name = entity_name
    safe_monitor = nrql_string(monitor_name)

    logger.info(
        "Synthetic investigation: monitor=%s, window=%s → %s",
        monitor_name, nrql_start, nrql_end,
    )

    # 1. Alert incidents
    alert_incidents = _safe_nrql(
        INVESTIGATION_ALERTS_NRQL.format(
            entity_name=safe_monitor, start=nrql_start, end=nrql_end,
        )
    )

    # 2. Overall stats for the window
    stats = _safe_nrql(
        INVESTIGATION_SYNTHETIC_STATS_NRQL.format(
            name=safe_monitor, start=nrql_start, end=nrql_end,
        )
    )

    # 3. Failure breakdown by location
    locations = _safe_nrql(
        INVESTIGATION_SYNTHETIC_LOCATIONS_NRQL.format(
            name=safe_monitor, start=nrql_start, end=nrql_end,
        )
    )

    # 4. Timeseries — failure pattern over the window
    timeseries = _safe_nrql(
        INVESTIGATION_SYNTHETIC_TIMESERIES_NRQL.format(
            name=safe_monitor, start=nrql_start, end=nrql_end,
        )
    )

    # 5. Individual failure details (error messages, response codes)
    failures = _safe_nrql(
        INVESTIGATION_SYNTHETIC_FAILURES_NRQL.format(
            name=safe_monitor, start=nrql_start, end=nrql_end,
        )
    )

    # 6. Failed HTTP requests from SyntheticRequest (for scripted monitors)
    failed_requests = _safe_nrql(
        INVESTIGATION_SYNTHETIC_REQUESTS_NRQL.format(
            name=safe_monitor, start=nrql_start, end=nrql_end,
        )
    )

    # Build the synthetic-specific data sections
    synthetic_summary = _results_to_text(stats)
    location_breakdown = _results_to_text(locations)
    failure_details = _results_to_text(failures + failed_requests, max_rows=30)
    timeseries_text = _results_to_text(timeseries)

    return {
        "entity_type": "SYNTHETIC",
        "service_name": service_name,
        "sli_kind": "synthetic_monitor",
        "associated_app": monitor_name,
        "nr_link": entity.get("permalink", ""),
        "alert_incidents": _results_to_text(alert_incidents),
        "cwv_section_title": "Synthetic Monitor — Check Results",
        "cwv_data": (
            f"### Overall Stats\n{synthetic_summary}\n\n"
            f"### Failure by Location\n{location_breakdown}\n\n"
            f"### Failure Timeline\n{timeseries_text}\n\n"
            f"### Failure Details (errors, response codes)\n{failure_details}"
        ),
        "js_errors": "N/A — Synthetic Monitor",
        "deployments": "N/A — Synthetic Monitor",
        "sli_definition": "N/A — Synthetic Monitor (no SLI definition; uses SyntheticCheck events)",
        "sli_replay": "N/A — see Synthetic Monitor Check Results section above.",
        "log_correlation": "N/A — Synthetic Monitor",
    }


def _investigate_apm(
    entity: dict, entity_name: str, service_name: str,
    nrql_start: str, nrql_end: str,
) -> dict:
    """Collect investigation evidence for an APM application."""
    app_name = entity_name
    safe_app = nrql_string(app_name)

    logger.info(
        "APM investigation: app=%s, window=%s → %s",
        app_name, nrql_start, nrql_end,
    )

    # 1. Alert incidents
    alert_incidents = _safe_nrql(
        INVESTIGATION_ALERTS_NRQL.format(
            entity_name=safe_app, start=nrql_start, end=nrql_end,
        )
    )

    # 2. Overview stats (error rate, avg/p95 duration, throughput)
    overview = _safe_nrql(
        INVESTIGATION_APM_OVERVIEW_NRQL.format(
            app_name=safe_app, start=nrql_start, end=nrql_end,
        )
    )

    # 3. Slowest transactions
    slow_transactions = _safe_nrql(
        INVESTIGATION_APM_SLOW_NRQL.format(
            app_name=safe_app, start=nrql_start, end=nrql_end,
        )
    )

    # 4. Error breakdown by transaction and class
    errors = _safe_nrql(
        INVESTIGATION_APM_ERRORS_NRQL.format(
            app_name=safe_app, start=nrql_start, end=nrql_end,
        )
    )

    # 5. Timeseries — error and latency pattern over the window
    timeseries = _safe_nrql(
        INVESTIGATION_APM_TIMESERIES_NRQL.format(
            app_name=safe_app, start=nrql_start, end=nrql_end,
        )
    )

    # 6. Throughput timeseries (RPM)
    throughput = _safe_nrql(
        INVESTIGATION_APM_THROUGHPUT_NRQL.format(
            app_name=safe_app, start=nrql_start, end=nrql_end,
        )
    )

    # 7. External service calls (slow dependencies)
    externals = _safe_nrql(
        INVESTIGATION_APM_EXTERNAL_NRQL.format(
            app_name=safe_app, start=nrql_start, end=nrql_end,
        )
    )

    # 8. Individual error traces with traceId
    error_traces = _safe_nrql(
        INVESTIGATION_APM_ERROR_TRACES_NRQL.format(
            app_name=safe_app, start=nrql_start, end=nrql_end,
        )
    )

    # 9. Deployments in the window
    deployments = _safe_nrql(
        INVESTIGATION_DEPLOYMENTS_NRQL.format(
            app_name=safe_app, start=nrql_start, end=nrql_end,
        )
    )

    # 10. Log correlation from error traces
    trace_ids = _extract_trace_ids(error_traces)
    log_results = _query_logs_by_traces(trace_ids, nrql_start, nrql_end) if trace_ids else []

    return {
        "entity_type": "APM",
        "service_name": service_name,
        "sli_kind": "apm_application",
        "associated_app": app_name,
        "nr_link": entity.get("permalink", ""),
        "alert_incidents": _results_to_text(alert_incidents),
        "cwv_section_title": "APM Application — Transaction Data",
        "cwv_data": (
            f"### Overview (error rate, latency, throughput)\n{_results_to_text(overview)}\n\n"
            f"### Slowest Transactions (by avg duration)\n{_results_to_text(slow_transactions)}\n\n"
            f"### Error Breakdown (by transaction & class)\n{_results_to_text(errors)}\n\n"
            f"### Transaction Timeline (5-min buckets)\n{_results_to_text(timeseries)}\n\n"
            f"### Throughput (RPM)\n{_results_to_text(throughput)}\n\n"
            f"### External Service Calls (slow dependencies)\n{_results_to_text(externals)}"
        ),
        "js_errors": "N/A — APM Application",
        "deployments": _results_to_text(deployments),
        "sli_definition": "N/A — APM Application (uses Transaction/TransactionError events)",
        "sli_replay": (
            f"### Error Traces (individual errors with traceId)\n"
            f"{_results_to_text(error_traces)}"
        ),
        "log_correlation": _results_to_text(log_results, max_rows=30),
    }
