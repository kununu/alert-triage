"""Microsoft Graph API client for reading Teams channel messages.

Used to fetch the root message of a thread when the bot is @mentioned
in a reply. Requires MS_GRAPH_TOKEN or app-level auth.
"""

import json
import logging
import re
import threading
import time
import requests
from config.settings import (
    MICROSOFT_APP_ID,
    MICROSOFT_APP_PASSWORD,
    MS_GRAPH_TENANT_ID,
)

logger = logging.getLogger(__name__)

_token_cache: dict = {"access_token": None, "expires_at": 0}
_token_lock = threading.Lock()

_MAX_GRAPH_RESPONSE_BYTES = 1 * 1024 * 1024  # 1 MB — channel messages should never be this large


def _get_graph_token() -> str | None:
    """Get an app-only Graph token using client credentials flow.

    Thread-safe: uses a lock to prevent concurrent token refresh races.
    """
    if not MS_GRAPH_TENANT_ID or not MICROSOFT_APP_ID or not MICROSOFT_APP_PASSWORD:
        logger.info("Graph API not configured (missing tenant ID or app credentials)")
        return None

    with _token_lock:
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
        except requests.exceptions.HTTPError as e:
            logger.warning("Graph token request failed (HTTP %s)", e.response.status_code)
            return None
        except requests.exceptions.RequestException as e:
            logger.warning("Graph token request failed (network): %s", type(e).__name__)
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
            stream=True,
        )
        resp.raise_for_status()

        # Enforce response size limit before buffering
        content_length = int(resp.headers.get("Content-Length", 0))
        if content_length > _MAX_GRAPH_RESPONSE_BYTES:
            logger.warning("Graph API response too large (%d bytes), skipping", content_length)
            return None
        body_bytes = resp.content
        if len(body_bytes) > _MAX_GRAPH_RESPONSE_BYTES:
            logger.warning("Graph API response body too large (%d bytes), skipping", len(body_bytes))
            return None

        data = json.loads(body_bytes)

        # The body can be HTML or text
        body = data.get("body", {})
        content = body.get("content", "")
        content_type = body.get("contentType", "text")

        if content_type == "html":
            content = re.sub(r"<[^>]+>", " ", content)
            content = re.sub(r"\s+", " ", content).strip()

        logger.info("Graph API: fetched root message (%d chars)", len(content))
        return content if content else None

    except requests.exceptions.HTTPError as e:
        logger.warning("Graph API HTTP error (status %s)", e.response.status_code)
        return None
    except requests.exceptions.RequestException as e:
        logger.warning("Graph API network error: %s", type(e).__name__)
        return None
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning("Graph API response parse error: %s", type(e).__name__)
        return None
