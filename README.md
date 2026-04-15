# Alert Triage Bot

AI-powered Microsoft Teams bot that triages and investigates New Relic alerts — queries live APM, Synthetic Monitor, and Service Level data via NerdGraph and returns root cause analysis directly in the alert thread.

## How it works

When a New Relic alert fires into your Teams channel, tag `@AlertTriage` in the thread. The bot:

1. Reads the root alert card from the thread via Microsoft Graph API
2. Parses the entity name, timestamps, and entity type from the card text
3. Queries New Relic NerdGraph for live observability data
4. Asks Claude to synthesise a triage brief or full root cause analysis
5. Replies in the same thread with structured findings

### Commands

| Intent | Example |
|---|---|
| **Triage** (quick status) | `@AlertTriage` (plain mention in an alert thread) |
| **Investigate** (root cause) | `@AlertTriage investigate` / `@AlertTriage what happened?` |
| **Direct message** | `triage payments-service` |

## Supported entity types

| Type | Data queried |
|---|---|
| **APM** | Error rate, latency (p95), throughput, slowest transactions, external dependencies, log correlation via traceId |
| **Synthetic Monitor** | Failure rate, failing locations, response codes, error messages, timeseries |
| **Service Level** | SLI compliance, bad/good event replay, trace drill-down, log correlation |

## Setup

### Prerequisites

- Python 3.12+
- A New Relic account (EU region) with API access
- An Anthropic enterprise API key
- A Microsoft Azure AD app registration for the bot
- Microsoft Teams admin access to install the app

### Installation

```bash
git clone git@github.com:kununu/alert-triage.git
cd alert-triage
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Configuration

Copy the example env file and fill in your values:

```bash
cp .env.example .env
```

```env
NR_API_KEY=           # New Relic user API key
NR_ACCOUNT_ID=        # New Relic account ID
ANTHROPIC_API_KEY=    # Anthropic enterprise API key
MicrosoftAppId=       # Azure AD bot app registration ID
MicrosoftAppPassword= # Azure AD bot client secret
MS_GRAPH_TENANT_ID=   # Azure AD tenant ID
```

### Running locally

```bash
python app.py
```

The bot listens on port `3978` by default. Use [ngrok](https://ngrok.com/) to expose it for local Teams testing:

```bash
ngrok http 3978
```

Update the bot's messaging endpoint in Azure Bot Service to `https://<your-ngrok-url>/api/messages`.

### Docker

```bash
docker build -t alert-triage .
docker run --env-file .env -p 3978:3978 alert-triage
```

## Architecture

```
Teams Alert Channel
       │
       ▼
Teams Bot Framework (botbuilder-core)
       │
       ├── bot/alert_parser.py      Parse alert card text
       ├── bot/teams_graph.py       Fetch thread root via Graph API
       │
       ▼
newrelic/client.py                  Entity search + data fetching
       │
       ├── NerdGraph (entity search, SLI definition)
       └── NRQL (APM, Synthetic, Service Level queries)
       │
       ▼
ai/llm_client.py                    Anthropic Claude
       │
       └── Triage brief / Root cause analysis
       │
       ▼
Teams Thread Reply (Adaptive Card)
```

## Tests

```bash
ANTHROPIC_API_KEY=test NR_API_KEY=test NR_ACCOUNT_ID=test pytest tests/ -v
```

30 tests across LLM client, alert parser, NRQL sanitization, and activity handler — all mocked, no real API keys required.

## Security

- **NRQL injection prevention** — all entity names, timestamps, and trace IDs are sanitised through `newrelic/sanitize.py` before query interpolation
- **RSC permissions** — the Teams manifest uses `ChannelMessage.Read.Group` (Resource-Specific Consent), scoping Graph API access to teams where the bot is installed only
- **No tenant-wide permissions** — `ChannelMessage.Read.All` is explicitly not used
- **Code review required** — all changes to `ai/`, `bot/`, `newrelic/`, and `config/` require PR approval before merging

## Contributing

1. Branch off `main` — `git checkout -b your-feature`
2. Make changes and run the test suite
3. Open a pull request — approval required before merge (see [CLAUDE.md](CLAUDE.md) for architectural guidance)
