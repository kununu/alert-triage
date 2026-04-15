# CLAUDE.md — Alert Triage Bot

This file gives Claude Code context about the project structure, conventions, and key decisions.

## What this project does

A Microsoft Teams bot that reads New Relic alert cards posted in channel threads, queries live observability data via NerdGraph (New Relic's GraphQL API), and returns AI-generated triage briefs or deep root cause analyses — directly in the alert thread.

## Running tests

```bash
ANTHROPIC_API_KEY=test NR_API_KEY=test NR_ACCOUNT_ID=test .venv/bin/pytest tests/ -v
```

All 30 tests should pass. Tests use `unittest.mock.patch` to mock the LLM and New Relic calls — no real API keys needed.

## Project structure

```
ai/
  llm_client.py       # Anthropic Claude wrapper — _generate(), extract_service_context(),
                      # synthesize_triage(), synthesize_investigation()
  prompts.py          # All LLM prompt templates (extraction, triage, investigation)

bot/
  activity_handler.py # Teams bot entry point — routes messages, thread-aware logic
  alert_parser.py     # Parses NR alert card text → entity name, timestamps, type hint
  adaptive_card.py    # Builds Teams Adaptive Card JSON for triage responses
  teams_graph.py      # Microsoft Graph API client — fetches thread root messages
  app.py              # aiohttp app setup

newrelic/
  client.py           # All NerdGraph + NRQL logic — entity search, triage fetchers,
                      # investigation flows per entity type
  queries.py          # NRQL and GraphQL query string constants
  sanitize.py         # Input sanitization — nrql_string(), nrql_timestamp(),
                      # nrql_trace_id(), entity_search_string()

config/
  settings.py         # Loads env vars — NR_API_KEY, NR_ACCOUNT_ID, ANTHROPIC_API_KEY,
                      # MicrosoftAppId, MicrosoftAppPassword, MS_GRAPH_TENANT_ID

teams-manifest/
  manifest.json       # Teams App manifest with RSC permission (ChannelMessage.Read.Group)
```

## Environment variables

Copy `.env.example` to `.env` and fill in:

```
NR_API_KEY=           # New Relic user API key (EU account)
NR_ACCOUNT_ID=        # New Relic account ID
ANTHROPIC_API_KEY=    # Anthropic enterprise API key
MicrosoftAppId=       # Azure AD bot app registration ID
MicrosoftAppPassword= # Azure AD bot client secret
MS_GRAPH_TENANT_ID=   # Azure AD tenant ID (for Graph API thread fetching)
```

## Key architectural decisions

**Entity type routing** — `newrelic/client.py` maps NerdGraph `entityType` strings to three internal types (`APM`, `SYNTHETIC`, `SERVICE_LEVEL`) via `_ENTITY_TYPE_MAP`. Each type has a dedicated investigation flow (`_investigate_apm`, `_investigate_synthetic`, and the SERVICE_LEVEL path in `get_investigation_data`).

**Progressive fuzzy entity search** — `_find_entity()` tries increasingly loose NRQL-style LIKE patterns before giving up. If a type hint is provided (e.g. `SYNTHETIC`), it tries that type first, then falls back to all types. `_pick_best_entity()` scores candidates by exact match → name contains search term → closest length.

**NRQL sanitization** — All user-originated or externally-sourced values interpolated into NRQL queries must go through `newrelic/sanitize.py`. Never use raw `.format()` with untrusted strings in queries.

**Thread-aware bot** — When @mentioned in a thread reply, the bot detects the thread via `;messageid=` in `conversation.id`, fetches the root alert card via Graph API (`teams_graph.py`), parses it with `alert_parser.py`, and runs triage or investigation automatically. Direct messages use the original Gemini-style extraction flow via the LLM.

**LLM client** — `ai/llm_client.py` is the single integration point for Claude. All prompts are in `ai/prompts.py`. The `_generate()` function handles retries on 429 (rate limit) and 529 (overload). Never call the Anthropic SDK directly from outside this module.

**RSC permissions** — The Teams manifest uses `ChannelMessage.Read.Group` (Resource-Specific Consent) instead of the tenant-wide `ChannelMessage.Read.All`. This limits the bot to reading messages only from teams where it is explicitly installed.

## Adding a new entity type

1. Add the NerdGraph `entityType` string(s) to `_ENTITY_TYPE_MAP` in `newrelic/client.py`
2. Add a new `_investigate_<type>()` function
3. Add a corresponding `_fetch_<type>()` for triage
4. Add routing in `get_investigation_data()` and `get_service_triage_data()`
5. Add type-specific prompt guidance in `INVESTIGATION_SYNTHESIS_PROMPT` in `ai/prompts.py`
6. Add NRQL query constants in `newrelic/queries.py`

## Code review policy

All changes to `ai/`, `bot/`, `newrelic/`, and `config/` require at least one approved PR review before merging to `main`. The bot operates with privileged credentials (New Relic API key, Microsoft Graph token, Anthropic API key).
