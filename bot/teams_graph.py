"""Microsoft Graph API client for reading Teams channel messages.

Used to fetch the root message of a thread when the bot is @mentioned
in a reply. Requires MS_GRAPH_TOKEN or app-level auth.
"""

import logging
import requests
from config.settings import (
    MICROSOFT_APP_ID,
    MICROSOFT_APP_PASSWORD,
    MS_GRAPH_TENANT_ID,
)

logger = logging.getLogger(__name__)

_token_cache: dict = {"access_token": None, "expires_at": 0}


def _get_graph_token() -> str | None:
    """Get an app-only Graph token using client credentials flow."""
    if not MS_GRAPH_TENANT_ID or not MICROSOFT_APP_ID or not MICROSOFT_APP_PASSWORD:
        logger.info("Graph API not configured (missing tenant ID or app credentials)")
        return None

    import time
    if _token_cache["access_token"] and _token_cache["expires_at"] > time.time() + 60:
        return _token_cache["access_token"]

    try:
        resp = requests.post(
            f"https://login.microsoftonline.com/{MS_GRAPH_TENANT_ID}/oauth2/v2.0/token",
            data={
                "client_id": MICROSOFT_APP_ID,
                "client_secret": MICROSOFT_APP_PASSWORD,
                "scope": "https://graph.microsoft.com/.default",
                "grant_type": "client_credentials",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        _token_cache["access_token"] = data["access_token"]
        _token_cache["expires_at"] = time.time() + data.get("expires_in", 3600)
        return data["access_token"]
    except Exception as e:
        logger.warning("Failed to get Graph token: %s", e)
        return None


def get_thread_root_message(team_id: str, channel_id: str, message_id: str) -> str | None:
    """Fetch the text content of a specific Teams channel message via Graph API.

    Returns the plain text body of the message, or None on failure.
    """
    token = _get_graph_token()
    if not token:
        return None

    url = (
        f"https://graph.microsoft.com/v1.0"
        f"/teams/{team_id}/channels/{channel_id}/messages/{message_id}"
    )

    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        # The body can be HTML or text
        body = data.get("body", {})
        content = body.get("content", "")
        content_type = body.get("contentType", "text")

        if content_type == "html":
            # Strip HTML tags for plain text
            import re
            content = re.sub(r"<[^>]+>", " ", content)
            content = re.sub(r"\s+", " ", content).strip()

        logger.info("Graph API: fetched root message (%d chars)", len(content))
        return content if content else None

    except requests.exceptions.HTTPError as e:
        logger.warning("Graph API HTTP error: %s — %s", e.response.status_code, e.response.text[:200])
        return None
    except Exception as e:
        logger.warning("Graph API error: %s", e)
        return None
