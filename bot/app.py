from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings
from botbuilder.schema import Activity
from aiohttp import web

from config.settings import MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD
from bot.activity_handler import TriageActivityHandler

adapter_settings = BotFrameworkAdapterSettings(MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD)
adapter = BotFrameworkAdapter(adapter_settings)
bot = TriageActivityHandler()


async def messages(request: web.Request) -> web.Response:
    if request.content_type != "application/json":
        return web.Response(status=415)

    body = await request.json()
    activity = Activity().deserialize(body)
    auth_header = request.headers.get("Authorization", "")

    async def call_bot(turn_context):
        await bot.on_turn(turn_context)

    await adapter.process_activity(activity, auth_header, call_bot)
    return web.Response(status=200)
