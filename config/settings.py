import os
from dotenv import load_dotenv

load_dotenv()

NR_API_KEY = os.environ["NR_API_KEY"]
NR_ACCOUNT_ID = os.environ["NR_ACCOUNT_ID"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
MICROSOFT_APP_ID = os.environ.get("MicrosoftAppId", "")
MICROSOFT_APP_PASSWORD = os.environ.get("MicrosoftAppPassword", "")
MS_GRAPH_TENANT_ID = os.environ.get("MS_GRAPH_TENANT_ID", "")

NR_NERDGRAPH_URL = "https://api.eu.newrelic.com/graphql"
