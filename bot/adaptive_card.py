from typing import Any

_SEVERITY_COLORS = {
    "critical": "attention",
    "high": "warning",
    "medium": "accent",
    "low": "good",
}

_ENTITY_TYPE_LABELS = {
    "APM": "APM Service",
    "SYNTHETIC": "Synthetic Monitor",
    "SERVICE_LEVEL": "Service Level",
}


def build_triage_card(
    service_name: str,
    severity: str,
    alert_summary: str,
    triage_brief: str,
    nr_data: dict,
    nr_link: str,
) -> dict[str, Any]:
    color = _SEVERITY_COLORS.get(severity.lower(), "accent")
    entity_type = nr_data.get("entity_type", "")
    type_label = _ENTITY_TYPE_LABELS.get(entity_type, entity_type)

    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": [
            {
                "type": "TextBlock",
                "text": f"Alert Triage — {service_name}",
                "weight": "Bolder",
                "size": "Large",
                "color": color,
            },
            {
                "type": "TextBlock",
                "text": alert_summary,
                "wrap": True,
                "isSubtle": True,
            },
            {
                "type": "ColumnSet",
                "columns": [
                    _fact_column("Severity", severity.upper()),
                    _fact_column("Type", type_label),
                    *_entity_metric_columns(nr_data),
                ],
            },
            {"type": "TextBlock", "text": "Triage Brief", "weight": "Bolder", "spacing": "Medium"},
            {"type": "TextBlock", "text": triage_brief, "wrap": True},
        ],
        "actions": [
            {
                "type": "Action.OpenUrl",
                "title": "Open in New Relic",
                "url": nr_link,
            }
        ],
    }


def _entity_metric_columns(nr_data: dict) -> list[dict]:
    entity_type = nr_data.get("entity_type")

    if entity_type == "APM":
        burn = nr_data.get("burn_rate")
        errors = nr_data.get("error_count")
        return [
            _fact_column("Burn Rate", f"{burn:.2f}x" if burn is not None else "N/A"),
            _fact_column("Errors (30m)", str(errors) if errors is not None else "N/A"),
        ]

    if entity_type == "SYNTHETIC":
        rate = nr_data.get("failure_rate")
        failed = nr_data.get("failed_checks")
        total = nr_data.get("total_checks")
        return [
            _fact_column("Failure Rate", f"{rate:.1f}%" if rate is not None else "N/A"),
            _fact_column("Checks", f"{failed}/{total}" if None not in (failed, total) else "N/A"),
        ]

    if entity_type == "SERVICE_LEVEL":
        compliance = nr_data.get("current_compliance")
        category = nr_data.get("compliance_category", "Unknown")
        target = nr_data.get("slo_target", "N/A")
        return [
            _fact_column("Compliance", f"{compliance:.2f}%" if compliance is not None else "N/A"),
            _fact_column("Status", category),
            _fact_column("Target", target),
        ]

    return []


def _fact_column(label: str, value: str) -> dict:
    return {
        "type": "Column",
        "width": "stretch",
        "items": [
            {"type": "TextBlock", "text": label, "isSubtle": True, "size": "Small"},
            {"type": "TextBlock", "text": value, "weight": "Bolder"},
        ],
    }
