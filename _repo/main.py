# Copyright @juktijol
# Channel t.me/juktijol
import sys
import asyncio

# ── uvloop: asyncio event loop 2-4x faster (Linux only) ──
try:
    import uvloop
    uvloop.install()
    print("✅ uvloop installed — event loop boosted!")
except ImportError:
    print("⚠️ uvloop not available (Windows?), using default asyncio loop")

from utils import LOGGER
from utils.force_sub import setup_force_sub_handler
from auth import setup_auth_handlers
from plugins import setup_plugins_handlers
from core import setup_start_handler, init_db
from misc import handle_callback_query

# ── Reply Keyboard button router (must be registered LAST) ────────────────
from misc.button_router import setup_button_router

from app import app

# ── Initialise database indexes (TTL etc.) on startup ────────────────────
asyncio.get_event_loop().run_until_complete(init_db())

# ── Register all handlers ──────────────────────────────────────────────────
# Force subscribe interceptor runs FIRST (group -1) to check channel membership
setup_force_sub_handler(app)

setup_plugins_handlers(app)
setup_auth_handlers(app)
setup_start_handler(app)

# Button router goes LAST so real command handlers always have priority
setup_button_router(app)


@app.on_callback_query()
async def handle_callback(client, callback_query):
    await handle_callback_query(client, callback_query)


LOGGER.info("Bot Successfully Started! 💥")
app.run()
