# Copyright @juktijol
# Channel t.me/juktijol

from .auto_router import setup_auto_router
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
from .fbdl import setup_fbdl_handler
from .ytdl_yt import setup_ytdl_yt_handler   # ← bug fix: handle → handler


def setup_plugins_handlers(app):
    setup_plan_handler(app)

    # ─── YouTube handlers ──────────────────────────────────────────────────
    # yt.py: /yt /video /mp4 /mp3 /song /aud commands  (group=0)
    setup_ytdl_handler(app)

    # ytdl_yt.py: /dl command + YouTube link auto-detect (group=0 & group=2)
    # NOTE: yt.py-তে /dl already আছে, তাই ytdl_yt.py সেটা override করবে না —
    #       /dl এর জন্য ytdl_yt.py ব্যবহার হবে (Video/Audio choice দেখাবে)
    setup_ytdl_yt_handler(app)

    # ─── Other handlers ────────────────────────────────────────────────────
    setup_auto_router(app)        # group=3 — বাকি সব লিংক route করো
    setup_gdl_handler(app)
    setup_directdl_handler(app)
    setup_info_handler(app)
    setup_thumb_handler(app)
    setup_login_handler(app)
    setup_pbatch_handler(app)
    setup_ytupload_handler(app)
    setup_refresh_handler(app)
    setup_settings_handler(app)
    setup_autolink_handler(app)   # group=1 — Telegram লিংক handle করে
    setup_referral_handler(app)
    setup_fbdl_handler(app)
