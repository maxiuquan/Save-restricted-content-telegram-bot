import sys
import asyncio

# ── uvloop：asyncio 事件循环提速 2-4 倍（仅 Linux）──
try:
    import uvloop
    uvloop.install()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    print("✅ uvloop installed — event loop boosted!")
except ImportError:
    print("⚠️ uvloop not available (Windows?), using default asyncio loop")

from utils import LOGGER
from utils.force_sub import setup_force_sub_handler
from auth import setup_auth_handlers
from plugins import setup_plugins_handlers
from core import setup_start_handler, init_db
from misc import handle_callback_query

# ── 回复键盘按钮路由器（必须最后注册）────────────────
from misc.button_router import setup_button_router

from app import app

# ── 启动时初始化数据库索引（TTL 等）────────────────────
asyncio.get_event_loop().run_until_complete(init_db())

# ── 注册所有处理器 ──────────────────────────────────────────────────
# 强制订阅拦截器最先执行（group -1），检查频道成员身份
setup_force_sub_handler(app)

setup_plugins_handlers(app)
setup_auth_handlers(app)
setup_start_handler(app)

# 按钮路由器放在最后，确保命令处理器始终具有优先权
setup_button_router(app)


@app.on_callback_query()
async def handle_callback(client, callback_query):
    await handle_callback_query(client, callback_query)


LOGGER.info("Bot Successfully Started! 💥")
app.run()
