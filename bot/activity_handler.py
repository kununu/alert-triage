import re
import logging
from botbuilder.core import ActivityHandler, TurnContext
from botbuilder.schema import Activity, ActivityTypes

from ai.llm_client import extract_service_context, synthesize_triage, synthesize_investigation
from newrelic.client import get_service_triage_data, get_investigation_data
from bot.adaptive_card import build_triage_card
from bot.alert_parser import parse_alert_message
from bot.teams_graph import get_thread_root_message

logger = logging.getLogger(__name__)


class TriageActivityHandler(ActivityHandler):
    async def on_message_activity(self, turn_context: TurnContext):
        user_text = turn_context.activity.text or ""
        user_text = _strip_mention(user_text).strip()

        await turn_context.send_activity(Activity(type=ActivityTypes.typing))

        # --- Try to read the root alert from the thread ---
        alert_context = await self._try_parse_thread_alert(turn_context)

        if alert_context:
            logger.info(
                "Thread mode: entity=%s, type_hint=%s, window=%s → %s",
                alert_context["entity_name"],
                alert_context.get("entity_type_hint"),
                alert_context.get("time_start"),
                alert_context.get("time_end"),
            )
            await self._handle_thread_alert(turn_context, alert_context, user_text)
        else:
            await self._handle_direct_message(turn_context, user_text)

    # ── Thread detection & root message fetch ──────────────────

    async def _try_parse_thread_alert(self, turn_context: TurnContext) -> dict | None:
        """Try to fetch and parse the root message of a Teams thread.

        Returns parsed alert context, or None if not in a thread / can't fetch.
        """
        activity = turn_context.activity
        channel_data = activity.channel_data or {}

        # Teams puts teamsChannelId and teamsTeamId in channel_data
        teams_info = channel_data.get("teamsChannelId") or channel_data.get("channel", {}).get("id")
        team_id = channel_data.get("teamsTeamId") or channel_data.get("team", {}).get("id")

        # Check if we're in a thread (conversation.id contains ;messageid=)
        conversation_id = getattr(activity.conversation, "id", "") if activity.conversation else ""

        # Extract root message ID from conversation.id
        # Format: 19:xxx@thread.tacv2;messageid=1234567890
        root_message_id = None
        if ";messageid=" in conversation_id:
            root_message_id = conversation_id.split(";messageid=")[-1]

        if not root_message_id or not team_id or not teams_info:
            logger.debug(
                "Thread detection: root_msg_id=%s, team_id=%s, channel=%s",
                root_message_id, team_id, teams_info,
            )
            return None

        # Fetch the root message via Graph API
        root_text = get_thread_root_message(team_id, teams_info, root_message_id)
        if not root_text:
            logger.info("Could not fetch root message via Graph API")
            return None

        return parse_alert_message(root_text)

    # ── Thread-based flow (reply to alert card) ────────────────

    async def _handle_thread_alert(
        self, turn_context: TurnContext, alert_ctx: dict, user_text: str,
    ):
        """Handle a bot mention inside a thread that has an NR alert as root."""
        entity_name = alert_ctx["entity_name"]
        entity_type_hint = alert_ctx.get("entity_type_hint")
        time_start = alert_ctx.get("time_start")
        time_end = alert_ctx.get("time_end")

        # Determine intent from user's reply text
        user_lower = user_text.lower() if user_text else ""
        wants_investigation = any(
            kw in user_lower
            for kw in ("investigate", "root cause", "what happened", "why", "deep", "rca")
        )

        # Default: if we have timestamps, investigate; otherwise triage
        if not user_text or not wants_investigation:
            # User just tagged the bot → auto-triage
            await self._do_triage(
                turn_context, entity_name, entity_type_hint,
                summary=f"Alert: {entity_name}. {user_text}" if user_text else f"Alert fired for {entity_name}",
            )
        elif time_start and time_end:
            await self._do_investigation(
                turn_context, entity_name, entity_type_hint,
                time_start, time_end,
                summary=f"Investigating alert: {entity_name}. User context: {user_text}",
            )
        else:
            # No timestamps in alert card but user wants investigation
            # Fall back to the direct message flow which uses Gemini for time extraction
            combined = f"{entity_name}\n{user_text}"
            await self._handle_direct_message(turn_context, combined)

    # ── Direct message flow (original) ─────────────────────────

    async def _handle_direct_message(self, turn_context: TurnContext, alert_text: str):
        """Original flow: user typed the full alert/question in their message."""
        if not alert_text:
            await turn_context.send_activity(
                "👋 Tag me in a thread under an alert card and I'll triage it.\n\n"
                "Or send me a message like:\n"
                "• `triage [Culture] [Culture tab] LCP`\n"
                "• `investigate Culture MMI Page is Down SM — failed Friday at 16:24`"
            )
            return

        try:
            context = extract_service_context(alert_text)
            intent = context.get("intent", "triage")
            service_name = context["service_name"]
            severity = context.get("severity", "unknown")
            alert_summary = context.get("summary", alert_text[:200])
            entity_type_hint = context.get("entity_type_hint")
            logger.info(
                "Extracted: intent=%s service=%s severity=%s type_hint=%s",
                intent, service_name, severity, entity_type_hint,
            )

            if intent == "investigate":
                time_start = context.get("time_start")
                time_end = context.get("time_end")
                if not time_start or not time_end:
                    await turn_context.send_activity(
                        "I couldn't determine a time window for the investigation. "
                        "Please specify when the alert fired (e.g. 'today at 5AM')."
                    )
                    return
                await self._do_investigation(
                    turn_context, service_name, entity_type_hint,
                    time_start, time_end, summary=alert_summary,
                )
            else:
                await self._do_triage(
                    turn_context, service_name, entity_type_hint,
                    summary=alert_summary, severity=severity,
                )

        except Exception as exc:
            logger.exception("Triage failed")
            await turn_context.send_activity(
                f"Triage failed: {exc}\n\nPlease check the alert text and try again."
            )

    # ── Triage & Investigation executors ───────────────────────

    async def _do_triage(
        self, turn_context: TurnContext,
        service_name: str, entity_type_hint: str | None,
        summary: str, severity: str = "unknown",
    ):
        """Run triage for a known entity."""
        try:
            nr_data = get_service_triage_data(service_name, entity_type_hint=entity_type_hint)
            logger.info("NR triage data: %s", nr_data)

            if nr_data is None:
                await turn_context.send_activity(
                    f"Service **{service_name}** was not found in New Relic "
                    "(checked APM, Synthetic Monitors, and Service Levels). "
                    "Verify the name and try again."
                )
                return

            triage_brief = synthesize_triage(
                service_name=service_name,
                severity=severity,
                alert_summary=summary,
                nr_data=nr_data,
            )
            logger.info("Triage brief: %s", triage_brief)

            card = build_triage_card(
                service_name=service_name,
                severity=severity,
                alert_summary=summary,
                triage_brief=triage_brief,
                nr_data=nr_data,
                nr_link=nr_data["nr_link"],
            )
            await turn_context.send_activity(
                Activity(
                    type=ActivityTypes.message,
                    text=f"**[{severity.upper()}] {service_name}** ({nr_data['entity_type']})\n\n{triage_brief}",
                    attachments=[{
                        "contentType": "application/vnd.microsoft.card.adaptive",
                        "content": card,
                    }],
                )
            )
        except Exception as exc:
            logger.exception("Triage failed for %s", service_name)
            await turn_context.send_activity(f"Triage failed: {exc}")

    async def _do_investigation(
        self, turn_context: TurnContext,
        service_name: str, entity_type_hint: str | None,
        time_start: str, time_end: str, summary: str,
    ):
        """Run investigation for a known entity + time window."""
        try:
            await turn_context.send_activity(
                f"🔍 Investigating **{service_name}** between {time_start} and {time_end}… "
                "This may take a moment while I query New Relic."
            )

            investigation_data = get_investigation_data(
                service_name, time_start, time_end, entity_type_hint=entity_type_hint,
            )
            logger.info(
                "Investigation data: entity_type=%s sli_kind=%s",
                investigation_data.get("entity_type") if investigation_data else None,
                investigation_data.get("sli_kind") if investigation_data else None,
            )

            if investigation_data is None:
                await turn_context.send_activity(
                    f"Service **{service_name}** was not found in New Relic. "
                    "Verify the name and try again."
                )
                return

            analysis = synthesize_investigation(
                service_name=service_name,
                user_summary=summary,
                time_start=time_start,
                time_end=time_end,
                investigation_data=investigation_data,
            )
            logger.info("Investigation analysis: %s", analysis[:200])

            nr_link = investigation_data.get("nr_link", "")
            header = (
                f"**Investigation: {service_name}**\n"
                f"Type: {investigation_data['entity_type']} | "
                f"SLI: {investigation_data['sli_kind']} | "
                f"Window: {time_start} → {time_end}\n\n"
            )

            await turn_context.send_activity(
                Activity(
                    type=ActivityTypes.message,
                    text=header + analysis + (f"\n\n[Open in New Relic]({nr_link})" if nr_link else ""),
                )
            )
        except Exception as exc:
            logger.exception("Investigation failed for %s", service_name)
            await turn_context.send_activity(f"Investigation failed: {exc}")


def _strip_mention(text: str) -> str:
    """Remove Teams @mention XML tags from message text."""
    return re.sub(r"<at>[^<]+</at>", "", text)
