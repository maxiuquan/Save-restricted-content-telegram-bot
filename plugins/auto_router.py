#
# plugins/auto_router.py — 全自动链接路由器
# 无需命令，粘贴 URL 即可自动开始下载
#
# ═══════════════════════════════════════════════════════════════════
# 与你的 plugin 文件兼容：
#   gdl.py      → _process_gdl(client, message, url)
#   directdl.py → _process_ddl(client, message, url, status_msg)
#                 ⚠️ message.from_user.id 直接使用
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
# 模式定义
# ─────────────────────────────────────────────────────────────────

# Telegram 链接 — 由 autolink.py 处理
TELEGRAM_LINK_PATTERN = re.compile(
    r"(?:https?://)?(?:t\.me|telegram\.me)/(?:c/)?([a-zA-Z0-9_]+|\d+)/(\d+)(?:/\d+)?",
    re.IGNORECASE,
)

# Magnet 链接
MAGNET_PATTERN = re.compile(r"^magnet:\?xt=", re.IGNORECASE)

# Torrent 链接
TORRENT_PATTERN = re.compile(r"\.torrent(\?.*)?$", re.IGNORECASE)

# HLS 流媒体
HLS_PATTERN = re.compile(r"\.m3u8(\?.*)?$", re.IGNORECASE)

# 通用 URL 提取器 (http/https + magnet)
GENERIC_URL_PATTERN = re.compile(
    r"(?:https?://[^\s<>\"{}|\\^`\[\]]+|magnet:\?[^\s]+)",
    re.IGNORECASE,
)

# yt-dlp 支持的域名
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

# directdl.py 支持域名
# 使用 directdl.py 的 is_supported_site() 函数
# 同时保留此处作为 fallback
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
# 域名检测
# ─────────────────────────────────────────────────────────────────

def _get_domain(url: str) -> str:
    """从 URL 中提取干净的域名"""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return ""


# ─────────────────────────────────────────────────────────────────
# 安全用户ID提取器
# directdl.py 直接使用 message.from_user.id
# 因此 if from_user 为 None 时会崩溃 — 需要防止此情况
# ─────────────────────────────────────────────────────────────────

def _get_user_id(message: Message) -> int:
    """即使 message.from_user 为 None 也安全地返回 user_id"""
    if message.from_user:
        return message.from_user.id
    if message.sender_chat:
        return message.sender_chat.id
    return message.chat.id


def _has_valid_from_user(message: Message) -> bool:
    """
    directdl.py 及其他插件直接使用 message.from_user.id。
    若没有 from_user，这些插件会崩溃。
    """
    return message.from_user is not None


# ─────────────────────────────────────────────────────────────────
# 路由检测
# ─────────────────────────────────────────────────────────────────

def detect_route(url: str) -> str:
    """
    分析 URL 并返回正确的路由。

    返回：
        "telegram"  → autolink.py (跳过)
        "gdrive"    → gdl.py → _process_gdl()
        "aria2"     → aria2dl.py → _run_download()
        "ytdlp"     → ytdl.py
        "directdl"  → directdl.py → _process_ddl()
        "urldl"     → urldl.py
        "unknown"   → 无法处理
    """
    url = url.strip()
    if not url:
        return "unknown"

    # ── 1. Telegram 链接（最高优先级）────────────────────────────
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

    # ── 5. HLS 流媒体 → yt-dlp ───────────────────────────────────
    if HLS_PATTERN.search(url.split("?")[0]):
        return "ytdlp"

    # ── 6. 已知的 yt-dlp 站点 ─────────────────────────────────────
    for yd in YTDLP_DOMAINS:
        if domain == yd or domain.endswith(f".{yd}"):
            return "ytdlp"

    # ── 7. 使用 directdl.py 的 is_supported_site() 检查 ─────────
    # 这是最准确的 — directdl.py 了解自己的列表
    try:
        from utils.direct_links import is_supported_site
        url_for_check = url.split("::")[0].strip()
        if is_supported_site(url_for_check):
            return "directdl"
    except ImportError:
        # Fallback：手动域名检查
        for dd in DIRECTDL_DOMAINS:
            if domain == dd or domain.endswith(f".{dd}") or dd in domain:
                return "directdl"

    # ── 8. 通用 HTTP/HTTPS ────────────────────────────────────
    if url.startswith(("http://", "https://")):
        tg_domains = {"t.me", "telegram.me", "telegram.org"}
        if not any(td in domain for td in tg_domains):
            return "urldl"

    return "unknown"


# ─────────────────────────────────────────────────────────────────
# 路由信息
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
# 错误报告 — 向用户显示错误 + 记录日志
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
# 安全后台任务 — 防止静默失败
# ─────────────────────────────────────────────────────────────────

async def _safe_task(coro, message: Message, label: str):
    """
    在 asyncio.create_task() 内部出错时通常会静默失败。
    此 wrapper 可防止此情况 — 错误会向用户显示。
    """
    try:
        await coro
    except Exception as e:
        tb = traceback.format_exc()
        await _report_error(message, label, e, tb)


# ─────────────────────────────────────────────────────────────────
# 路由执行器 — 每个插件独立函数
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
                f"❌ **Google Drive 下载器加载失败！**\n\n"
                f"手动：`/gdl {url[:60]}`\n\n"
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
                "❌ **未安装 aria2c！**\n\n"
                "`sudo apt install aria2`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        # 从 aria2dl.py 导入正确的函数
        from plugins.aria2dl import (
            _run_download,
            _cancel_events,
            _is_premium,
        )

        LOGGER.info(f"[AutoRouter] → aria2dl._run_download() | url={url[:60]}")

        is_prem = await _is_premium(user_id)

        status_msg = await message.reply_text(
            f"🌊 **Torrent/Magnet 开始下载...**\n\n"
            f"`{url[:80]}`",
            parse_mode=ParseMode.MARKDOWN,
        )

        cancel_event = asyncio.Event()
        _cancel_events[status_msg.id] = cancel_event

        # 使用 _safe_task 后台运行 — 可看到错误
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
            f"❌ **Aria2 下载器加载失败！**\n\n"
            f"手动：`/dl {url[:60]}`\n\n"
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

        # Optional helpers — 没有也可以
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
        # ytdl.py 不存在时给出命令提示
        await message.reply_text(
            f"🎬 **检测到视频链接！**\n\n"
            f"下载请执行：\n`/ytdl {url[:80]}`",
            parse_mode=ParseMode.MARKDOWN,
        )
    except AttributeError as e:
        # 函数名称错误
        LOGGER.error(f"[AutoRouter] ytdl function not found: {e}")
        await message.reply_text(
            f"🎬 **检测到视频链接！**\n\n"
            f"下载请执行：\n`/ytdl {url[:80]}`",
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

    ⚠️ 关键：directdl.py 的 _process_ddl() 中：
       user_id = message.from_user.id  ← 直接访问
       因此 if from_user 为 None 时会崩溃。
       调用此函数前需要先检查 _has_valid_from_user()。

    签名：async def _process_ddl(client, message, url, status_msg) -> None
    """
    try:
        from plugins.directdl import _process_ddl

        LOGGER.info(f"[AutoRouter] → directdl._process_ddl() | url={url[:60]}")

        # 需要先创建 status_msg — _process_ddl 的参数
        status_msg = await message.reply_text(
            f"📦 **正在解析直链...**\n\n"
            f"🔗 `{url[:80]}`",
            parse_mode=ParseMode.MARKDOWN,
        )

        # 使用 _safe_task 在后台运行
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
            f"❌ **文件下载器加载失败！**\n\n"
            f"手动：`/ddl {url[:60]}`\n\n"
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
            f"❌ **URL 下载器加载失败！**\n\n"
            f"手动：`/urldl {url[:60]}`\n\n"
            f"`{e}`",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        await _report_error(message, "URL Download", e)


# ─────────────────────────────────────────────────────────────────
# 主执行器
# ─────────────────────────────────────────────────────────────────

async def execute_route(
    client: Client,
    message: Message,
    url: str,
    route: str,
):
    """
    根据路由调用正确的插件函数。
    公开方法 — 其他插件也可调用。
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
        return  # 由 autolink.py 处理করবে

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


# 向后兼容别名
_execute_route = execute_route


# ─────────────────────────────────────────────────────────────────
# 主自动检测处理器
# group=3 — autolink(1) 和 urldl(2) 之后
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

    # ── 步骤 1：基本验证 ──────────────────────────────────
    if not message.text:
        return

    text = message.text.strip()
    if not text:
        return

    # ── 步骤 2：跳过命令消息 ─────────────────────────────
    for prefix in COMMAND_PREFIX:
        if text.startswith(prefix):
            return

    # ── 步骤 3：Telegram 链接 → 交给 autolink.py 处理 ───
    if TELEGRAM_LINK_PATTERN.search(text):
        return

    # ── 步骤 4：pbatch 会话检查 ───────────────────────────────
    _pbatch = sys.modules.get("plugins.pbatch")
    if _pbatch and hasattr(_pbatch, "batch_data"):
        uid = message.from_user.id if message.from_user else -1
        state = _pbatch.batch_data.get(message.chat.id)
        if state and state.get("user_id") == uid:
            return

    # ── 步骤 5：提取 URL ──────────────────────────────────────
    url = None

    # Magnet 链接单独处理（不以 http 开头）
    if MAGNET_PATTERN.match(text):
        # 只取 magnet URL（忽略其余文本）
        url = text.split()[0]
    else:
        match = GENERIC_URL_PATTERN.search(text)
        if match:
            # 去除尾部标点符号
            url = match.group(0).rstrip(".,;!?)'\"")

    if not url:
        return

    # ── 步骤 6：确定路由 ───────────────────────────────────
    route = detect_route(url)

    LOGGER.info(
        f"[AutoRouter] Detected | "
        f"route={route} | "
        f"url={url[:60]} | "
        f"chat={message.chat.id} | "
        f"user={_get_user_id(message)}"
    )

    # ── 步骤 7：跳过路由 ───────────────────────────────────────
    if route in ("telegram", "unknown"):
        return

    # urldl 路由：
    # urldl.py 在 group=2 中已经处理了。
    # 所以只有在 urldl 模块未加载时才处理。
    if route == "urldl":
        urldl_module = sys.modules.get("plugins.urldl")
        if urldl_module is not None:
            # urldl.py 已加载 — 它会在 group=2 中自行处理
            LOGGER.debug(
                "[AutoRouter] urldl route — "
                "urldl.py already handles this in group=2, skipping"
            )
            return
        # 如果 urldl.py 不存在，则由我们来处理

    # ── 步骤 8：directdl 路由 — from_user 检查 ──────────────────
    # directdl.py 中直接访问 message.from_user.id
    # 如果 from_user 为 None 则会崩溃
    if route == "directdl" and not _has_valid_from_user(message):
        LOGGER.warning(
            f"[AutoRouter] directdl skip — "
            f"message.from_user is None | "
            f"chat={message.chat.id}"
        )
        return

    # ── 步骤 9：用户 ID ───────────────────────────────────────────
    user_id = _get_user_id(message)

    # ── 步骤 10：强制订阅检查（仅私聊） ──────────────
    if message.chat.type == ChatType.PRIVATE and message.from_user:
        try:
            if not await check_force_sub(client, user_id):
                LOGGER.debug(
                    f"[AutoRouter] Force sub failed | user={user_id}"
                )
                return
        except Exception as e:
            LOGGER.warning(f"[AutoRouter] Force sub check error: {e}")
            # 强制订阅错误不会阻止下载

    # ── 步骤 11：执行 ──────────────────────────────────────────
    await execute_route(client, message, url, route)


# ─────────────────────────────────────────────────────────────────
# /route 命令 — 分析链接并显示结果
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

    # 如果是 directdl，用 is_supported_site 验证
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
# /plugininfo 命令
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
# 设置
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

    # /route 命令
    app.add_handler(
        MessageHandler(
            _route_command_handler,
            filters=filters.command("route", prefixes=COMMAND_PREFIX)
            & (filters.private | filters.group),
        ),
        group=1,
    )

    # /plugininfo 命令
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

    # ── 主自动检测处理器 ──────────────────────────────────
    # group=3 — autolink 和 urldl 之后
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
