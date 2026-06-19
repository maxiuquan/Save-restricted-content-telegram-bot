# Copyright @juktijol
# Channel t.me/juktijol
#
# plugins/auto_router.py — Fully Automatic Link Router
# কোনো command ছাড়াই URL paste করলে অটো download শুরু হবে
#
# ═══════════════════════════════════════════════════════════════════
# আপনার plugin files এর সাথে compatible:
#   gdl.py      → _process_gdl(client, message, url)
#   directdl.py → _process_ddl(client, message, url, status_msg)
#                 ⚠️ message.from_user.id directly use করে
#   aria2dl.py  → _run_download(...) + _cancel_events + _is_premium
# ═══════════════════════════════════════════════════════════════════

import re
import asyncio
import sys
import traceback
from urllib.parse import urlparse
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import ParseMode, ChatType

from config import COMMAND_PREFIX
from utils import LOGGER
from utils.force_sub import check_force_sub

# ─────────────────────────────────────────────────────────────────
# PATTERN DEFINITIONS
# ─────────────────────────────────────────────────────────────────

# Telegram লিংক — autolink.py handle করবে
TELEGRAM_LINK_PATTERN = re.compile(
    r"(?:https?://)?(?:t\.me|telegram\.me)/(?:c/)?([a-zA-Z0-9_]+|\d+)/(\d+)(?:/\d+)?",
    re.IGNORECASE,
)

# Magnet লিংক
MAGNET_PATTERN = re.compile(r"^magnet:\?xt=", re.IGNORECASE)

# Torrent URL
TORRENT_PATTERN = re.compile(r"\.torrent(\?.*)?$", re.IGNORECASE)

# HLS stream
HLS_PATTERN = re.compile(r"\.m3u8(\?.*)?$", re.IGNORECASE)

# Generic URL extractor (http/https + magnet)
GENERIC_URL_PATTERN = re.compile(
    r"(?:https?://[^\s<>\"{}|\\^`\[\]]+|magnet:\?[^\s]+)",
    re.IGNORECASE,
)

# yt-dlp supported domains
YTDLP_DOMAINS = {
    "youtube.com", "youtu.be", "m.youtube.com",
    "music.youtube.com", "vimeo.com", "dailymotion.com",
    "twitch.tv", "tiktok.com", "vm.tiktok.com",
    "instagram.com", "twitter.com", "x.com", "t.co",
    "facebook.com", "fb.watch", "soundcloud.com",
    "bandcamp.com", "reddit.com", "v.redd.it",
    "bilibili.com", "b23.tv", "nicovideo.jp", "nico.ms",
    "mixcloud.com", "vk.com", "rumble.com", "odysee.com",
    "ok.ru", "coub.com", "streamable.com", "ted.com",
    "bbc.co.uk", "bbc.com", "cnn.com", "nbc.com",
    "abc.net.au", "arte.tv", "zdf.de", "ard.de",
    "crunchyroll.com", "funimation.com",
    "pornhub.com", "xvideos.com", "xnxx.com",
    "9gag.com", "liveleak.com", "izlesene.com",
    "vidio.com", "kakao.com", "vlive.tv",
    "naver.com", "daum.net", "imdb.com",
}

# directdl.py supported domains
# directdl.py এর is_supported_site() function use করব
# তবু fallback এর জন্য এখানেও রাখা হলো
DIRECTDL_DOMAINS = {
    "mediafire.com", "gofile.io", "pixeldrain.com", "pixeldra.in",
    "1fichier.com", "streamtape.com", "wetransfer.com", "we.tl",
    "swisstransfer.com", "qiwi.gg", "mp4upload.com", "buzzheavier.com",
    "send.cm", "linkbox.to", "lbx.to", "krakenfiles.com",
    "solidfiles.com", "upload.ee", "tmpsend.com", "easyupload.io",
    "streamvid.net", "streamhub.ink", "streamhub.to",
    "u.pcloud.link", "berkasdrive.com", "akmfiles.com", "akmfls.xyz",
    "hxfile.co", "1drv.ms", "osdn.net",
    "yadi.sk", "disk.yandex.com", "disk.yandex.ru",
    "devuploads.com", "uploadhaven.com", "fuckingfast.co",
    "mediafile.cc", "lulacloud.com", "shrdsk.me", "transfer.it",
    "terabox.com", "nephobox.com", "4funbox.com", "teraboxapp.com",
    "1024tera.com", "freeterabox.com",
    "filelions.co", "filelions.site", "filelions.live",
    "streamwish.to", "embedwish.com",
    "dood.watch", "doodstream.com", "dood.to", "dood.so",
    "ds2play.com", "dood.cx", "racaty.net", "racaty.io",
}


# ─────────────────────────────────────────────────────────────────
# DOMAIN HELPER
# ─────────────────────────────────────────────────────────────────

def _get_domain(url: str) -> str:
    """URL থেকে clean domain বের করে"""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return ""


# ─────────────────────────────────────────────────────────────────
# SAFE USER ID EXTRACTOR
# directdl.py message.from_user.id directly use করে
# তাই from_user None হলে crash হবে — এটা prevent করতে হবে
# ─────────────────────────────────────────────────────────────────

def _get_user_id(message: Message) -> int:
    """message.from_user None হলেও safe ভাবে user_id return করে"""
    if message.from_user:
        return message.from_user.id
    if message.sender_chat:
        return message.sender_chat.id
    return message.chat.id


def _has_valid_from_user(message: Message) -> bool:
    """
    directdl.py ও অন্য plugins message.from_user.id directly use করে।
    from_user না থাকলে সেই plugins crash করবে।
    """
    return message.from_user is not None


# ─────────────────────────────────────────────────────────────────
# ROUTE DETECTION
# ─────────────────────────────────────────────────────────────────

def detect_route(url: str) -> str:
    """
    URL বিশ্লেষণ করে সঠিক route return করে।

    Returns:
        "telegram"  → autolink.py (skip করব)
        "gdrive"    → gdl.py → _process_gdl()
        "aria2"     → aria2dl.py → _run_download()
        "ytdlp"     → ytdl.py
        "directdl"  → directdl.py → _process_ddl()
        "urldl"     → urldl.py
        "unknown"   → handle করা যাবে না
    """
    url = url.strip()
    if not url:
        return "unknown"

    # ── 1. Telegram লিংক (সর্বোচ্চ priority) ────────────────────
    if TELEGRAM_LINK_PATTERN.search(url):
        return "telegram"

    domain = _get_domain(url)

    # ── 2. Google Drive ──────────────────────────────────────────
    if "drive.google.com" in domain or "docs.google.com" in domain:
        return "gdrive"

    # ── 3. Magnet ────────────────────────────────────────────────
    if MAGNET_PATTERN.match(url):
        return "aria2"

    # ── 4. Torrent URL ───────────────────────────────────────────
    if TORRENT_PATTERN.search(url.split("?")[0]):
        return "aria2"

    # ── 5. HLS stream → yt-dlp ───────────────────────────────────
    if HLS_PATTERN.search(url.split("?")[0]):
        return "ytdlp"

    # ── 6. Known yt-dlp site ─────────────────────────────────────
    for yd in YTDLP_DOMAINS:
        if domain == yd or domain.endswith(f".{yd}"):
            return "ytdlp"

    # ── 7. directdl.py এর is_supported_site() দিয়ে চেক ─────────
    # এটাই সবচেয়ে accurate — directdl.py নিজের list জানে
    try:
        from utils.direct_links import is_supported_site
        url_for_check = url.split("::")[0].strip()
        if is_supported_site(url_for_check):
            return "directdl"
    except ImportError:
        # Fallback: manual domain check
        for dd in DIRECTDL_DOMAINS:
            if domain == dd or domain.endswith(f".{dd}") or dd in domain:
                return "directdl"

    # ── 8. Generic HTTP/HTTPS ────────────────────────────────────
    if url.startswith(("http://", "https://")):
        tg_domains = {"t.me", "telegram.me", "telegram.org"}
        if not any(td in domain for td in tg_domains):
            return "urldl"

    return "unknown"


# ─────────────────────────────────────────────────────────────────
# ROUTE INFO
# ─────────────────────────────────────────────────────────────────

ROUTE_INFO = {
    "telegram": {"icon": "📨", "label": "Telegram Link"},
    "gdrive":   {"icon": "☁️",  "label": "Google Drive"},
    "aria2":    {"icon": "🌊", "label": "Torrent / Magnet"},
    "ytdlp":    {"icon": "🎬", "label": "Video Site (yt-dlp)"},
    "directdl": {"icon": "📦", "label": "File Hosting"},
    "urldl":    {"icon": "🔗", "label": "Direct URL"},
}


# ─────────────────────────────────────────────────────────────────
# ERROR REPORTER — user-কে error দেখায় + log করে
# ─────────────────────────────────────────────────────────────────

async def _report_error(
    message: Message,
    label: str,
    error: Exception,
    tb: str = "",
):
    LOGGER.error(
        f"[AutoRouter] {label} error: {type(error).__name__}: {error}\n"
        f"{tb or traceback.format_exc()}"
    )
    try:
        await message.reply_text(
            f"❌ **{label} Error:**\n\n"
            f"`{type(error).__name__}: {str(error)[:250]}`",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────
# SAFE BACKGROUND TASK — silent fail prevent করে
# ─────────────────────────────────────────────────────────────────

async def _safe_task(coro, message: Message, label: str):
    """
    asyncio.create_task() এর ভেতরে error হলে সাধারণত silent fail হয়।
    এই wrapper সেটা prevent করে — error user-কে দেখায়।
    """
    try:
        await coro
    except Exception as e:
        tb = traceback.format_exc()
        await _report_error(message, label, e, tb)


# ─────────────────────────────────────────────────────────────────
# ROUTE EXECUTORS — প্রতিটি plugin আলাদা function
# ─────────────────────────────────────────────────────────────────

async def _exec_gdrive(client: Client, message: Message, url: str):
    """
    gdl.py → _process_gdl(client, message, url)
    Signature: async def _process_gdl(client, message, url)
    """
    try:
        from plugins.gdl import _process_gdl

        LOGGER.info(f"[AutoRouter] → gdl._process_gdl() | url={url[:60]}")
        await _process_gdl(client, message, url)

    except ImportError as e:
        LOGGER.error(f"[AutoRouter] gdl import failed: {e}")
        await message.reply_text(
            f"❌ **Google Drive downloader load হয়নি!**\n\n"
            f"Manual: `/gdl {url[:60]}`\n\n"
            f"`{e}`",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        await _report_error(message, "Google Drive", e)


async def _exec_aria2(
    client: Client,
    message: Message,
    url: str,
    user_id: int,
):
    """
    aria2dl.py → _run_download(client, message, url, None, status_msg, is_prem, cancel_event)
    Signature confirmed from aria2dl.py
    """
    try:
        import shutil as _shutil
        if not _shutil.which("aria2c"):
            await message.reply_text(
                "❌ **aria2c ইনস্টল নেই!**\n\n"
                "`sudo apt install aria2`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        # aria2dl.py থেকে সঠিক functions import
        from plugins.aria2dl import (
            _run_download,
            _cancel_events,
            _is_premium,
        )

        LOGGER.info(f"[AutoRouter] → aria2dl._run_download() | url={url[:60]}")

        is_prem = await _is_premium(user_id)

        status_msg = await message.reply_text(
            f"🌊 **Torrent/Magnet download শুরু হচ্ছে...**\n\n"
            f"`{url[:80]}`",
            parse_mode=ParseMode.MARKDOWN,
        )

        cancel_event = asyncio.Event()
        _cancel_events[status_msg.id] = cancel_event

        # _safe_task দিয়ে চালাই — error দেখা যাবে
        asyncio.create_task(
            _safe_task(
                _run_download(
                    client,
                    message,
                    url,          # source_url
                    None,         # torrent_path
                    status_msg,
                    is_prem,
                    cancel_event,
                ),
                message,
                "Aria2 Download",
            )
        )

    except ImportError as e:
        LOGGER.error(f"[AutoRouter] aria2dl import failed: {e}")
        await message.reply_text(
            f"❌ **Aria2 downloader load হয়নি!**\n\n"
            f"Manual: `/dl {url[:60]}`\n\n"
            f"`{e}`",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        await _report_error(message, "Aria2", e)


async def _exec_ytdlp(
    client: Client,
    message: Message,
    url: str,
    user_id: int,
):
    """
    ytdl.py → _handle_single_video_initiate_public()
    """
    try:
        from plugins.ytdl import _handle_single_video_initiate_public

        LOGGER.info(f"[AutoRouter] → ytdl._handle_single_video_initiate_public() | url={url[:60]}")

        # Optional helpers — না থাকলেও চলবে
        try:
            from plugins.ytdl import parse_url_and_referer
            url_clean, referer = parse_url_and_referer(url)
        except (ImportError, AttributeError):
            url_clean, referer = url, None

        try:
            from plugins.ytdl import is_premium_user
            is_prem = await is_premium_user(user_id)
        except (ImportError, AttributeError):
            is_prem = False

        try:
            from plugins.ytdl import _check_rate_limit
            allowed, rate_msg = await _check_rate_limit(user_id, is_prem)
            if not allowed:
                await message.reply_text(
                    rate_msg,
                    parse_mode=ParseMode.MARKDOWN,
                )
                return
        except (ImportError, AttributeError):
            pass

        await _handle_single_video_initiate_public(
            client, message, url_clean, user_id, is_prem, referer
        )

    except ImportError as e:
        LOGGER.error(f"[AutoRouter] ytdl import failed: {e}")
        # ytdl.py না থাকলে command hint দাও
        await message.reply_text(
            f"🎬 **Video link detected!**\n\n"
            f"Download করতে:\n`/ytdl {url[:80]}`",
            parse_mode=ParseMode.MARKDOWN,
        )
    except AttributeError as e:
        # Function নাম ভুল হলে
        LOGGER.error(f"[AutoRouter] ytdl function not found: {e}")
        await message.reply_text(
            f"🎬 **Video link detected!**\n\n"
            f"Download করতে:\n`/ytdl {url[:80]}`",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        await _report_error(message, "yt-dlp", e)


async def _exec_directdl(
    client: Client,
    message: Message,
    url: str,
):
    """
    directdl.py → _process_ddl(client, message, url, status_msg)

    ⚠️ CRITICAL: directdl.py এর _process_ddl() তে:
       user_id = message.from_user.id  ← directly access করে
       তাই from_user None হলে crash হবে।
       এই function call করার আগে _has_valid_from_user() check করতে হবে।

    Signature: async def _process_ddl(client, message, url, status_msg) -> None
    """
    try:
        from plugins.directdl import _process_ddl

        LOGGER.info(f"[AutoRouter] → directdl._process_ddl() | url={url[:60]}")

        # status_msg আগে তৈরি করতে হবে — _process_ddl এর parameter
        status_msg = await message.reply_text(
            f"📦 **Direct link resolve করছে...**\n\n"
            f"🔗 `{url[:80]}`",
            parse_mode=ParseMode.MARKDOWN,
        )

        # _safe_task দিয়ে background এ চালাই
        asyncio.create_task(
            _safe_task(
                _process_ddl(client, message, url, status_msg),
                message,
                "DirectDL",
            )
        )

    except ImportError as e:
        LOGGER.error(f"[AutoRouter] directdl import failed: {e}")
        await message.reply_text(
            f"❌ **File downloader load হয়নি!**\n\n"
            f"Manual: `/ddl {url[:60]}`\n\n"
            f"`{e}`",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        await _report_error(message, "DirectDL", e)


async def _exec_urldl(client: Client, message: Message, url: str):
    """
    urldl.py → _process_url_download(client, message, url)
    """
    try:
        from plugins.urldl import _process_url_download

        LOGGER.info(f"[AutoRouter] → urldl._process_url_download() | url={url[:60]}")
        await _process_url_download(client, message, url)

    except ImportError as e:
        LOGGER.error(f"[AutoRouter] urldl import failed: {e}")
        await message.reply_text(
            f"❌ **URL downloader load হয়নি!**\n\n"
            f"Manual: `/urldl {url[:60]}`\n\n"
            f"`{e}`",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        await _report_error(message, "URL Download", e)


# ─────────────────────────────────────────────────────────────────
# MAIN EXECUTOR
# ─────────────────────────────────────────────────────────────────

async def execute_route(
    client: Client,
    message: Message,
    url: str,
    route: str,
):
    """
    Route অনুযায়ী সঠিক plugin function call করে।
    Public — অন্য plugin থেকেও call করা যাবে।
    """
    user_id = _get_user_id(message)
    info = ROUTE_INFO.get(route, {"icon": "❓", "label": route})

    LOGGER.info(
        f"[AutoRouter] ▶ Execute | "
        f"route={route} | "
        f"url={url[:60]} | "
        f"user={user_id}"
    )

    if route == "telegram":
        return  # autolink.py handle করবে

    elif route == "gdrive":
        await _exec_gdrive(client, message, url)

    elif route == "aria2":
        await _exec_aria2(client, message, url, user_id)

    elif route == "ytdlp":
        await _exec_ytdlp(client, message, url, user_id)

    elif route == "directdl":
        # ⚠️ directdl.py তে message.from_user.id directly use হয়
        # from_user None হলে plugin crash করবে
        if not _has_valid_from_user(message):
            LOGGER.warning(
                f"[AutoRouter] Skipping directdl — "
                f"message.from_user is None (channel/bot message?)"
            )
            return
        await _exec_directdl(client, message, url)

    elif route == "urldl":
        await _exec_urldl(client, message, url)

    else:
        LOGGER.warning(
            f"[AutoRouter] ⚠ No handler for route={route} | url={url[:60]}"
        )


# Backward compatibility alias
_execute_route = execute_route


# ─────────────────────────────────────────────────────────────────
# MAIN AUTO-DETECT HANDLER
# group=3 — autolink(1) ও urldl(2) এর পরে
# ─────────────────────────────────────────────────────────────────

async def _auto_detect_handler(client: Client, message: Message):
    """
    যেকোনো URL paste করলে অটোমেটিক সঠিক downloader-এ পাঠাবে।
    কোনো command বা confirm button ছাড়াই কাজ করে।

    Skip conditions:
    ─────────────────
    • Command message (/gdl, /ddl, etc.)
    • Telegram লিংক (autolink.py handle করবে)
    • pbatch session active
    • from_user None এবং route directdl (crash prevent)
    • Force sub fail
    """

    # ── Step 1: Basic validation ──────────────────────────────────
    if not message.text:
        return

    text = message.text.strip()
    if not text:
        return

    # ── Step 2: Command message skip ─────────────────────────────
    for prefix in COMMAND_PREFIX:
        if text.startswith(prefix):
            return

    # ── Step 3: Telegram লিংক → autolink.py এর উপর ছেড়ে দাও ───
    if TELEGRAM_LINK_PATTERN.search(text):
        return

    # ── Step 4: pbatch session চেক ───────────────────────────────
    _pbatch = sys.modules.get("plugins.pbatch")
    if _pbatch and hasattr(_pbatch, "batch_data"):
        uid = message.from_user.id if message.from_user else -1
        state = _pbatch.batch_data.get(message.chat.id)
        if state and state.get("user_id") == uid:
            return

    # ── Step 5: URL বের করা ──────────────────────────────────────
    url = None

    # Magnet লিংক আলাদা (http দিয়ে শুরু না)
    if MAGNET_PATTERN.match(text):
        # শুধু magnet URL নাও (বাকি text বাদ)
        url = text.split()[0]
    else:
        match = GENERIC_URL_PATTERN.search(text)
        if match:
            # Trailing punctuation সরাও
            url = match.group(0).rstrip(".,;!?)'\"")

    if not url:
        return

    # ── Step 6: Route নির্ধারণ ───────────────────────────────────
    route = detect_route(url)

    LOGGER.info(
        f"[AutoRouter] Detected | "
        f"route={route} | "
        f"url={url[:60]} | "
        f"chat={message.chat.id} | "
        f"user={_get_user_id(message)}"
    )

    # ── Step 7: Skip routes ───────────────────────────────────────
    if route in ("telegram", "unknown"):
        return

    # urldl route:
    # urldl.py group=2 তে আগেই handle করে।
    # তাই শুধু তখনই handle করব যখন urldl module load নেই।
    if route == "urldl":
        urldl_module = sys.modules.get("plugins.urldl")
        if urldl_module is not None:
            # urldl.py loaded — সে নিজেই handle করবে group=2 তে
            LOGGER.debug(
                "[AutoRouter] urldl route — "
                "urldl.py already handles this in group=2, skipping"
            )
            return
        # urldl.py না থাকলে আমরাই handle করব

    # ── Step 8: directdl route — from_user check ──────────────────
    # directdl.py তে message.from_user.id directly access হয়
    # from_user None হলে crash হবে
    if route == "directdl" and not _has_valid_from_user(message):
        LOGGER.warning(
            f"[AutoRouter] directdl skip — "
            f"message.from_user is None | "
            f"chat={message.chat.id}"
        )
        return

    # ── Step 9: User ID ───────────────────────────────────────────
    user_id = _get_user_id(message)

    # ── Step 10: Force sub check (private chat only) ──────────────
    if message.chat.type == ChatType.PRIVATE and message.from_user:
        try:
            if not await check_force_sub(client, user_id):
                LOGGER.debug(
                    f"[AutoRouter] Force sub failed | user={user_id}"
                )
                return
        except Exception as e:
            LOGGER.warning(f"[AutoRouter] Force sub check error: {e}")
            # force sub error হলে download block করব না

    # ── Step 11: Execute ──────────────────────────────────────────
    await execute_route(client, message, url, route)


# ─────────────────────────────────────────────────────────────────
# /route COMMAND — লিংক বিশ্লেষণ করে দেখাবে
# ─────────────────────────────────────────────────────────────────

async def _route_command_handler(client: Client, message: Message):
    """/route <URL> — কোন downloader ব্যবহার হবে দেখাবে"""
    parts = message.text.split(None, 1)

    if len(parts) < 2:
        await message.reply_text(
            "**🗺 Link Router**\n\n"
            "**Usage:** `/route <URL>`\n\n"
            "**উদাহরণ:**\n"
            "`/route https://youtu.be/xxxxx`\n"
            "`/route https://drive.google.com/file/d/xxx`\n"
            "`/route https://mediafire.com/file/xxx`\n"
            "`/route magnet:?xt=urn:btih:xxx`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    url = parts[1].strip()
    route = detect_route(url)
    info = ROUTE_INFO.get(route, {"icon": "❓", "label": "Unknown"})

    # directdl হলে is_supported_site দিয়ে verify করো
    extra = ""
    if route == "directdl":
        try:
            from utils.direct_links import is_supported_site
            if is_supported_site(url.split("::")[0]):
                extra = "\n✅ `is_supported_site()` confirmed"
        except ImportError:
            pass

    await message.reply_text(
        f"**🗺 Link Analysis**\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"🔗 **URL:** `{url[:80]}`\n\n"
        f"{info['icon']} **Route:** `{info['label']}`\n"
        f"📌 **Route ID:** `{route}`"
        f"{extra}",
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True,
    )


# ─────────────────────────────────────────────────────────────────
# /plugininfo COMMAND
# ─────────────────────────────────────────────────────────────────

async def _plugininfo_command_handler(client: Client, message: Message):
    await message.reply_text(
        "**📋 Auto Router — সব লিংক অটো কাজ করে**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "**💡 কোনো command লাগবে না!**\n"
        "যেকোনো লিংক সরাসরি paste করুন।\n\n"
        "**📨 Telegram লিংক** → অটো forward\n"
        "**☁️ Google Drive** → অটো download\n"
        "**🎬 YouTube/TikTok/1000+ সাইট** → অটো download\n"
        "**📦 MediaFire/GoFile/etc** → অটো download\n"
        "**🌊 Magnet/Torrent** → অটো download\n"
        "**🔗 Direct HTTP file** → অটো download\n\n"
        "**Manual Commands (প্রয়োজনে):**\n"
        "• `/gdl <URL>` — Google Drive\n"
        "• `/ytdl <URL>` — Video sites\n"
        "• `/ddl <URL>` — File hosting\n"
        "• `/dl <magnet>` — Torrent/Magnet\n"
        "• `/urldl <URL>` — Direct URL\n"
        "• `/route <URL>` — Route analyzer",
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True,
    )


# ─────────────────────────────────────────────────────────────────
# SETUP
# ─────────────────────────────────────────────────────────────────

def setup_auto_router(app: Client):
    """
    Auto Router handlers register করে।

    Handler Groups:
    ───────────────
    group=1 → autolink.py (Telegram লিংক)
    group=2 → urldl.py (generic HTTP auto-detect)
    group=3 → এই file (gdrive, ytdlp, directdl, aria2)
              urldl fallback যদি urldl.py না থাকে

    Plugin Function Signatures (confirmed):
    ───────────────────────────────────────
    gdl.py      → _process_gdl(client, message, url)
    directdl.py → _process_ddl(client, message, url, status_msg)
    aria2dl.py  → _run_download(client, message, source_url, torrent_path,
                                status_msg, is_premium, cancel_event)
    """
    from pyrogram.handlers import MessageHandler

    # /route command
    app.add_handler(
        MessageHandler(
            _route_command_handler,
            filters=filters.command("route", prefixes=COMMAND_PREFIX)
            & (filters.private | filters.group),
        ),
        group=1,
    )

    # /plugininfo command
    app.add_handler(
        MessageHandler(
            _plugininfo_command_handler,
            filters=filters.command(
                ["plugininfo", "commands", "allcmds"],
                prefixes=COMMAND_PREFIX,
            )
            & (filters.private | filters.group),
        ),
        group=1,
    )

    # ── মূল Auto-Detect Handler ──────────────────────────────────
    # group=3 — autolink ও urldl এর পরে
    app.add_handler(
        MessageHandler(
            _auto_detect_handler,
            filters=filters.text & (filters.private | filters.group),
        ),
        group=3,
    )

    LOGGER.info(
        "[AutoRouter] ✅ Setup complete:\n"
        "  Plugins confirmed:\n"
        "    gdl.py      → _process_gdl(client, message, url)\n"
        "    directdl.py → _process_ddl(client, message, url, status_msg)\n"
        "    aria2dl.py  → _run_download(...)\n"
        "  Auto-detect: group=3\n"
        "  from_user=None protection: ✅\n"
        "  is_supported_site() integration: ✅\n"
        "  _safe_task error visibility: ✅\n"
        "  urldl fallback: ✅"
    )
