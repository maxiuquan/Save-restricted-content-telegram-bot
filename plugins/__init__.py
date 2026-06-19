from .auto_router import setup_auto_router   # ← 新增
from .plan import setup_plan_handler
from .info import setup_info_handler
from .thumb import setup_thumb_handler
from .login import setup_login_handler
from .pbatch import setup_pbatch_handler
from .ytdl import setup_ytdl_handler
from .ytupload import setup_ytupload_handler
from .refresh import setup_refresh_handler
from .settings import setup_settings_handler
from .autolink import setup_autolink_handler
from .referral import setup_referral_handler
from .gdl import setup_gdl_handler
from .directdl import setup_directdl_handler
from .urldl import setup_urldl_handler

def setup_plugins_handlers(app):
    setup_plan_handler(app)
    setup_auto_router(app)        # group=3 — 其余所有链接路由
    setup_gdl_handler(app)
    setup_directdl_handler(app)
    setup_urldl_handler(app)      # group=2 — 通用 HTTP URL（跳过已路由的域名）
    setup_info_handler(app)
    setup_thumb_handler(app)
    setup_login_handler(app)
    setup_pbatch_handler(app)
    setup_ytdl_handler(app)
    setup_ytupload_handler(app)
    setup_refresh_handler(app)
    setup_settings_handler(app)
    setup_autolink_handler(app)   # group=1 — Telegram 链接处理
    setup_referral_handler(app)
