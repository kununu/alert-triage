from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from bot.activity_handler import TriageActivityHandler, _strip_mention


def test_strip_mention():
    raw = "<at>AlertTriage</at> High error rate on payments-service"
    assert _strip_mention(raw) == " High error rate on payments-service"


@pytest.mark.asyncio
async def test_empty_message_sends_help():
    handler = TriageActivityHandler()
    turn_context = MagicMock()
    turn_context.activity.text = "   "
    turn_context.activity.conversation = MagicMock()
    turn_context.activity.conversation.id = "19:general@thread.tacv2"
    turn_context.activity.channel_data = {}
    turn_context.send_activity = AsyncMock()

    await handler.on_message_activity(turn_context)

    # typing indicator + help message
    assert turn_context.send_activity.call_count == 2
    help_call = turn_context.send_activity.call_args_list[1][0][0]
    assert "Tag me" in help_call


@pytest.mark.asyncio
async def test_full_triage_flow():
    handler = TriageActivityHandler()
    turn_context = MagicMock()
    turn_context.activity.text = "High error rate on payments-service"
    turn_context.activity.conversation = MagicMock()
    turn_context.activity.conversation.id = "19:general@thread.tacv2"
    turn_context.activity.channel_data = {}
    turn_context.send_activity = AsyncMock()

    fake_context = {
        "intent": "triage",
        "service_name": "payments-service",
        "severity": "high",
        "summary": "High error rate detected",
        "entity_type_hint": None,
    }
    fake_nr_data = {
        "entity_type": "APM",
        "service_name": "payments-service",
        "burn_rate": 3.5,
        "error_count": 120,
        "avg_duration_ms": 450.0,
        "nr_link": "https://one.newrelic.com/nr1-core?account=123",
    }

    with (
        patch("bot.activity_handler.extract_service_context", return_value=fake_context),
        patch("bot.activity_handler.get_service_triage_data", return_value=fake_nr_data),
        patch("bot.activity_handler.synthesize_triage", return_value="Investigate immediately."),
        patch("bot.activity_handler.build_triage_card", return_value={}),
    ):
        await handler.on_message_activity(turn_context)

    # typing indicator + card = 2 calls
    assert turn_context.send_activity.call_count == 2
