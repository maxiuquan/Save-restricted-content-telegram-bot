# Copyright @juktijol
# Channel t.me/juktijol

"""
yt-dlp ভিত্তিক Telegram Bot Handler
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Features:
  • Single video + Playlist download
  • Protected HLS/m3u8 stream support (Referer header)
  • Bunny CDN সহ যেকোনো CDN-এর protected stream
  • WARP proxy support
  • pybalt fallback engine
  • Rate limiting (Free/Premium)
  • Daily download limit
  • MTProto দিয়ে 2GB পর্যন্ত upload
  • Professional tracking/logging

Referer Usage:
  /ytdl <video_url> referer:<referer_url>

Example:
  /ytdl https://vz-f95995a2.b-cdn.net/video.m3u8 referer:https://academic.apa.org
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import os
import re
import shutil
import asyncio
import tempfile
import socket
from time import time
from datetime import datetime

from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode
from pyrogram.handlers import MessageHandler

from pyleaves import Leaves
from config import COMMAND_PREFIX, LOG_GROUP_ID
from utils.logging_setup import LOGGER
from utils.helper import (
    get_readable_file_size,
    get_readable_time,
    get_video_thumbnail,
    progressArgs,
)
from core import daily_limit, prem_plan1, prem_plan2, prem_plan3

try:
    import yt_dlp
    YTDLP_AVAILABLE = True
except ImportError:
    YTDLP_AVAILABLE = False
    LOGGER.error("yt-dlp not installed!")

try:
    from pybalt import download as pybalt_download_func
    PYBALT_AVAILABLE = True
except ImportError:
    PYBALT_AVAILABLE = False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CONSTANTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

DOWNLOAD_DIR     = os.path.join(tempfile.gettempdir(), "ytdl_downloads")
MAX_FILE_SIZE    = 2 * 1024 * 1024 * 1024   # 2 GB (MTProto limit)
FREE_FILE_SIZE   = 2 * 1024 * 1024 * 1024   # 2 GB (Free user-ও MTProto তে 2GB)
FREE_DAILY_LIMIT = 5                          # Free user প্রতিদিন ৫টি download
SESSION_EXPIRY   = 600                        # 10 মিনিট পর session expire
STALE_FILE_AGE   = 1800                       # 30 মিনিট পুরনো file cleanup
WARP_PROXY       = "socks5://127.0.0.1:40000"
BGUTIL_POT_URL   = os.environ.get("BGUTIL_POT_URL", "http://127.0.0.1:4416")

# Rate limiting
FREE_COOLDOWN    = 300   # Free: 5 মিনিট cooldown
PREMIUM_COOLDOWN = 10    # Premium: মাত্র 10 সেকেন্ড

# Playlist limits
FREE_PLAYLIST_LIMIT    = 0    # Free user playlist download করতে পারবে না
PREMIUM_PLAYLIST_LIMIT = 50   # Premium user সর্বোচ্চ 50টি ভিডিও

os.makedirs(DOWNLOAD_DIR, exist_ok=True)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STATE STORES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ytdl_sessions: dict      = {}   # chat_id → session data (url, info, referer ...)
user_last_download: dict = {}   # user_id → timestamp of last completed download
active_downloads: set    = set()  # user_id → currently downloading flag


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# WARP / PROXY HELPERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _is_warp_available() -> bool:
    """WARP SOCKS5 proxy (port 40000) available কিনা check করে।"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        result = s.connect_ex(("127.0.0.1", 40000))
        s.close()
        return result == 0
    except Exception:
        return False


# WARP auto-connect attempt at startup
try:
    if _is_warp_available():
        LOGGER.info("[WARP] Proxy available on port 40000 ✅")
    else:
        import subprocess
        subprocess.run(["warp-cli", "connect"], timeout=10, capture_output=True)
        import time as _t
        _t.sleep(2)
        if _is_warp_available():
            LOGGER.info("[WARP] Proxy started and available ✅")
        else:
            LOGGER.warning("[WARP] Proxy not available — Direct connection will be used")
except Exception:
    pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# YT-DLP OPTIONS BUILDER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _build_ydl_opts(
    use_proxy:  bool = True,
    noplaylist: bool = True,
    referer:    str  = None,
) -> dict:
    """
    Base yt-dlp options তৈরি করে।

    Parameters
    ──────────
    use_proxy  : WARP SOCKS5 proxy ব্যবহার করবে কিনা।
    noplaylist : True হলে single video mode; False হলে playlist।
    referer    : HTTP Referer header — protected HLS/m3u8 stream-এর জন্য।
                 Bunny CDN, Vimeo private, academic platforms ইত্যাদিতে
                 403 Forbidden bypass করতে এটি দরকার।

    Returns
    ───────
    dict : yt_dlp.YoutubeDL() constructor-এ পাস করার উপযুক্ত options dict।
    """

    # ── Default HTTP headers ──────────────────────────────────────────────
    http_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept":          "*/*",
    }

    # ── Referer inject (protected stream support) ─────────────────────────
    if referer:
        # Referer header সেট করলে CDN server মনে করে request browser থেকে আসছে
        http_headers["Referer"] = referer.strip().rstrip("/") + "/"
        # Origin header অনেক CDN check করে
        try:
            from urllib.parse import urlparse
            parsed = urlparse(referer)
            origin = f"{parsed.scheme}://{parsed.netloc}"
            http_headers["Origin"] = origin
        except Exception:
            pass
        LOGGER.debug(f"[ydl_opts] Referer set → {http_headers['Referer']}")

    opts = {
        "quiet":               True,
        "no_warnings":         True,
        "noplaylist":          noplaylist,
        "geo_bypass":          True,
        "nocheckcertificate":  True,   # SSL cert error bypass
        "socket_timeout":      30,
        "retries":             5,
        "extractor_retries":   3,
        "fragment_retries":    5,      # HLS fragment retry (m3u8-এর জন্য গুরুত্বপূর্ণ)
        "http_headers":        http_headers,
        "extractor_args": {
            "youtubepot-bgutilhttp": {
                "base_url": [BGUTIL_POT_URL],
            },
        },
        "buffersize":                    1024 * 16,
        "concurrent_fragment_downloads": 1,
    }

    # ── Proxy ─────────────────────────────────────────────────────────────
    if use_proxy and _is_warp_available():
        opts["proxy"] = WARP_PROXY

    return opts


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# URL PARSER — Referer extraction
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def parse_url_and_referer(raw_input: str) -> tuple[str, str | None]:
    """
    User input থেকে video URL এবং optional Referer আলাদা করে।

    Supported format:
        <video_url> referer:<referer_url>

    Examples:
        https://vz-f95.b-cdn.net/video.m3u8 referer:https://example.com
        https://youtu.be/abc123
        https://example.com/video.m3u8 referer:https://academic.apa.org

    Parameters
    ──────────
    raw_input : /ytdl কমান্ডের পরের পুরো text।

    Returns
    ───────
    (url: str, referer: str | None)
        url     — normalize করা video URL
        referer — Referer URL (পেলে), নাহলে None
    """
    raw_input = raw_input.strip()
    referer   = None

    # ── "referer:" keyword খোঁজা (case-insensitive) ─────────────────────
    # Pattern: <url> referer:<referer_url>
    referer_match = re.search(r"\breferer:(\S+)", raw_input, re.IGNORECASE)

    if referer_match:
        referer_raw = referer_match.group(1).strip().rstrip("/")
        # referer: prefix সহ পুরো অংশ URL থেকে বাদ দাও
        url = raw_input[: referer_match.start()].strip()

        # Referer URL validate/normalize
        if referer_raw and not referer_raw.startswith(("http://", "https://")):
            referer_raw = "https://" + referer_raw
        referer = referer_raw if referer_raw else None
    else:
        url = raw_input

    url = normalize_url(url)
    return url, referer


def is_hls_url(url: str) -> bool:
    """
    URL টি HLS stream (.m3u8) কিনা check করে।
    এই ধরনের URL-এ প্রায়ই Referer দরকার হয়।
    """
    return bool(re.search(r"\.m3u8(\?.*)?$", url, re.IGNORECASE))


def is_protected_cdn_url(url: str) -> bool:
    """
    Known protected CDN URL patterns check করে।
    এগুলোতে সাধারণত Referer ছাড়া 403 পাওয়া যায়।
    """
    protected_patterns = [
        r"b-cdn\.net",          # Bunny CDN
        r"bunnycdn\.com",       # Bunny CDN alternate
        r"vz-[a-f0-9-]+\.",    # Bunny Stream subdomain pattern
        r"cdn\.jwplayer\.com",  # JW Player CDN
        r"fastly\.net",         # Fastly CDN (protected)
        r"vimeocdn\.com",       # Vimeo CDN
        r"akamaized\.net",      # Akamai CDN
    ]
    for pattern in protected_patterns:
        if re.search(pattern, url, re.IGNORECASE):
            return True
    return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CLEANUP HELPERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def cleanup_stale_files():
    """30 মিনিটের বেশি পুরনো temporary file delete করে।"""
    now     = time()
    cleaned = 0
    try:
        for root, dirs, files in os.walk(DOWNLOAD_DIR):
            for fname in files:
                fpath = os.path.join(root, fname)
                try:
                    if now - os.path.getmtime(fpath) > STALE_FILE_AGE:
                        os.remove(fpath)
                        cleaned += 1
                except OSError:
                    pass
            for dname in dirs:
                dpath = os.path.join(root, dname)
                try:
                    if not os.listdir(dpath):
                        os.rmdir(dpath)
                except OSError:
                    pass
        if cleaned:
            LOGGER.info(f"[ytdl cleanup] {cleaned} stale file(s) removed")
    except Exception as e:
        LOGGER.warning(f"[ytdl cleanup] error: {e}")


def cleanup_expired_sessions():
    """10 মিনিটের বেশি পুরনো session memory থেকে সরিয়ে দেয়।"""
    now     = time()
    expired = [
        k for k, v in ytdl_sessions.items()
        if now - v.get("created_at", 0) > SESSION_EXPIRY
    ]
    for k in expired:
        ytdl_sessions.pop(k, None)


cleanup_stale_files()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ERROR HELPERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _friendly_error(raw_error: str) -> str:
    """Raw yt-dlp error কে user-friendly বাংলা বার্তায় convert করে।"""
    err = raw_error.lower()

    if "403" in err or "forbidden" in err:
        return (
            "🔒 **403 Forbidden** — সার্ভার access দিচ্ছে না।\n"
            "💡 **সমাধান:** Referer যোগ করুন:\n"
            "`/ytdl <url> referer:<website_url>`\n\n"
            "উদাহরণ:\n"
            "`/ytdl https://cdn.example.com/video.m3u8 referer:https://example.com`"
        )
    if "sign in" in err or "not a bot" in err:
        return "🔒 YouTube bot detection। কিছুক্ষণ পরে আবার চেষ্টা করুন।"
    if "age" in err and ("restrict" in err or "verif" in err):
        return "🔞 Age-restricted ভিডিও।"
    if "private" in err:
        return "🔒 Private ভিডিও।"
    if "copyright" in err or "blocked" in err:
        return "🚫 Copyright block।"
    if "not available" in err or "unavailable" in err:
        return "🚫 ভিডিওটি available নয়।"
    if "live" in err and "not supported" in err:
        return "📺 Live stream download হয় না।"
    if "connection refused" in err or "socks" in err:
        return "🌐 Proxy error। Bot restart করুন।"
    if "timeout" in err:
        return "🌐 Timeout। আবার চেষ্টা করুন।"

    clean = raw_error.replace("ERROR: ", "").strip()
    return f"⚠️ {clean[:200]}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# USER / PLAN HELPERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def is_premium_user(user_id: int) -> bool:
    """MongoDB-তে premium plan check করে।"""
    current_time = datetime.utcnow()
    for col in [prem_plan1, prem_plan2, prem_plan3]:
        plan = await col.find_one({"user_id": user_id})
        if plan and plan.get("expiry_date", current_time) > current_time:
            return True
    return False


def normalize_url(url: str) -> str:
    """URL normalize করে (http/https add, mobile URLs fix)।"""
    url = url.strip()
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    if "m.facebook.com/" in url:
        url = url.replace("m.facebook.com/", "www.facebook.com/", 1)
    return url


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PLAYLIST DETECTION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _is_playlist_url(url: str) -> bool:
    """
    URL-এ playlist indicator আছে কিনা check করে।
    YouTube: list= parameter | SoundCloud: /sets/ | Generic: /playlist/
    """
    patterns = [
        r"[?&]list=",       # YouTube playlist
        r"/playlist\b",     # Generic playlist path
        r"/sets/",          # SoundCloud sets
        r"/collection",     # Various platforms
        r"playlist_id=",    # Various platforms
    ]
    for pat in patterns:
        if re.search(pat, url, re.IGNORECASE):
            return True
    return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CORE: VIDEO INFO FETCHERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_playlist_info(url: str, referer: str = None) -> tuple:
    """
    Playlist-এর metadata fetch করে।

    Parameters
    ──────────
    url     : Playlist URL।
    referer : Optional Referer header (protected streams-এর জন্য)।

    Returns
    ───────
    (info: dict | None, error: str)
    """
    url = normalize_url(url)

    for attempt, use_proxy in enumerate([True, False], 1):
        opts = {
            **_build_ydl_opts(
                use_proxy=use_proxy,
                noplaylist=False,
                referer=referer,  # Referer pass করো
            ),
            "skip_download": True,
            "extract_flat":  True,                  # শুধু metadata, download নয়
            "playlistend":   PREMIUM_PLAYLIST_LIMIT, # সর্বোচ্চ 50 entry
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if info and info.get("_type") == "playlist":
                    LOGGER.info(f"[ytdl] Playlist info OK (attempt {attempt}) ✅")
                    return info, ""
                if info:
                    return info, ""
        except Exception as e:
            LOGGER.warning(
                f"[ytdl] Playlist info attempt {attempt}: {type(e).__name__} — {e}"
            )
            last_error = str(e)

    return None, locals().get("last_error", "Unknown error")


def get_single_video_info(url: str, referer: str = None) -> tuple:
    """
    Single video-র metadata fetch করে।

    Parameters
    ──────────
    url     : Video URL (regular বা m3u8)।
    referer : Optional Referer header।
              Bunny CDN / academic platforms-এর protected m3u8-এ দরকার।

    Returns
    ───────
    (info: dict | None, error: str)
    """
    url        = normalize_url(url)
    last_error = ""

    for attempt, use_proxy in enumerate([True, False, True], 1):
        opts = {
            **_build_ydl_opts(
                use_proxy=use_proxy,
                noplaylist=True,
                referer=referer,  # Referer inject
            ),
            "skip_download": True,
        }
        # তৃতীয় attempt-এ timeout বাড়াও
        if attempt == 3:
            opts["socket_timeout"] = 60

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if info:
                    LOGGER.info(
                        f"[ytdl] Single info OK "
                        f"(attempt {attempt}, referer={'✅' if referer else '❌'}) ✅"
                    )
                    return info, ""
        except Exception as e:
            last_error = str(e)
            LOGGER.warning(
                f"[ytdl] Single info attempt {attempt}: {type(e).__name__}"
            )

    return None, last_error


def download_single_video(
    url:           str,
    output_path:   str,
    format_id:     str  = None,
    audio_only:    bool = False,
    progress_data: dict = None,
    noplaylist:    bool = True,
    referer:       str  = None,
) -> tuple:
    """
    একটি ভিডিও download করে।

    Parameters
    ──────────
    url           : Video URL।
    output_path   : Download directory।
    format_id     : yt-dlp format ID (None হলে best auto-select)।
    audio_only    : True হলে audio-only MP3 download।
    progress_data : Dict — progress updater callback-এর জন্য।
    noplaylist    : True → single video mode।
    referer       : Optional Referer header (protected HLS/m3u8)।

    Returns
    ───────
    (success: bool, filepath_or_error: str)
    """
    url     = normalize_url(url)
    outtmpl = os.path.join(output_path, "%(title).50s.%(ext)s")

    def _fmt(fid):
        """Format string — user choice + fallback chain।"""
        if audio_only:
            return "bestaudio/best"
        if fid and fid != "best":
            return (
                f"{fid}+bestaudio/best"
                f"/bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]"
                f"/best"
            )
        return (
            "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/"
            "bestvideo[height<=1080]+bestaudio/"
            "best[height<=1080][ext=mp4]/best[height<=1080]/best"
        )

    downloaded_file = []

    def progress_hook(d):
        """yt-dlp progress hook — real-time progress tracking।"""
        if d["status"] == "finished":
            downloaded_file.append(d.get("filename", ""))
        elif d["status"] == "downloading" and progress_data is not None:
            progress_data["downloaded"] = d.get("downloaded_bytes", 0) or 0
            progress_data["total"]      = (
                d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            )
            progress_data["speed"] = d.get("speed") or 0
            progress_data["eta"]   = d.get("eta") or 0

    def _find_file():
        """Download শেষে actual file path খোঁজে বের করে।"""
        if downloaded_file:
            fp = downloaded_file[-1]
            if audio_only and not fp.endswith(".mp3"):
                fp = os.path.splitext(fp)[0] + ".mp3"
            if os.path.exists(fp):
                return fp
        # Fallback: directory-র সবচেয়ে নতুন file
        files = [
            os.path.join(output_path, f)
            for f in os.listdir(output_path)
            if os.path.isfile(os.path.join(output_path, f))
        ]
        return max(files, key=os.path.getmtime) if files else None

    postprocessors = (
        [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]
        if audio_only else []
    )
    postprocessor_args = {} if audio_only else {"ffmpeg": ["-movflags", "+faststart"]}
    last_error         = ""

    for attempt, use_proxy in enumerate([True, True, False], 1):
        # attempt=2 → format fallback (quality match fail হলে best নাও)
        fmt = "bestaudio/best" if (audio_only or attempt == 2) else _fmt(format_id)

        opts = {
            **_build_ydl_opts(
                use_proxy=use_proxy and _is_warp_available(),
                noplaylist=noplaylist,
                referer=referer,   # প্রতিটি attempt-এ Referer পাস করো
            ),
            "format":              fmt,
            "outtmpl":             outtmpl,
            "merge_output_format": "mp4" if not audio_only else None,
            "postprocessors":      postprocessors,
            "postprocessor_args":  postprocessor_args,
            "progress_hooks":      [progress_hook],
        }

        try:
            downloaded_file.clear()
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.extract_info(url, download=True)
            fp = _find_file()
            if fp:
                LOGGER.info(
                    f"[ytdl] Download OK "
                    f"(attempt {attempt}, referer={'✅' if referer else '❌'}) → {fp}"
                )
                return True, fp
        except Exception as e:
            last_error = str(e)
            LOGGER.warning(f"[ytdl] Download attempt {attempt}: {type(e).__name__}")

    return False, last_error


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# UI HELPERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def build_quality_keyboard(
    info:        dict,
    chat_id:     int,
    is_playlist: bool = False,
) -> InlineKeyboardMarkup:
    """
    Video quality selection inline keyboard তৈরি করে।

    Parameters
    ──────────
    info        : yt-dlp info dict।
    chat_id     : Telegram chat ID (callback data-তে embed করা হয়)।
    is_playlist : True হলে "ytpl_" prefix, False হলে "ytdl_"।
    """
    prefix  = "ytpl" if is_playlist else "ytdl"
    formats = info.get("formats", [])
    seen, video_rows = set(), []

    for f in formats:
        height = f.get("height")
        fid    = f.get("format_id", "")
        vcodec = f.get("vcodec", "none")
        ext    = f.get("ext", "")
        if (
            height
            and vcodec != "none"
            and height not in seen
            and ext in ("mp4", "webm", "")
        ):
            seen.add(height)
            video_rows.append((height, fid))

    # Height অনুযায়ী descending sort করো
    video_rows.sort(key=lambda x: x[0], reverse=True)
    buttons = []

    for height, fid in video_rows[:4]:  # সর্বোচ্চ 4টি quality option
        label = f"🎬 {height}p HD" if height >= 720 else f"🎬 {height}p"
        buttons.append([
            InlineKeyboardButton(
                label,
                callback_data=f"{prefix}_v_{chat_id}_{fid}",
            )
        ])

    if not buttons:
        # কোনো format পাওয়া না গেলে Best quality default
        buttons.append([
            InlineKeyboardButton(
                "🎬 Best Quality",
                callback_data=f"{prefix}_v_{chat_id}_best",
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "🎵 Audio Only (MP3)",
            callback_data=f"{prefix}_a_{chat_id}",
        )
    ])
    buttons.append([
        InlineKeyboardButton(
            "❌ Cancel",
            callback_data=f"{prefix}_cancel_{chat_id}",
        )
    ])
    return InlineKeyboardMarkup(buttons)


def _make_progress_bar(pct: float, length: int = 20) -> str:
    """ASCII progress bar তৈরি করে।"""
    filled = int(length * pct / 100)
    return "▓" * filled + "░" * (length - filled)


async def _ytdl_progress_updater(msg, progress_data: dict):
    """
    Download চলার সময় Telegram message-এ real-time progress দেখায়।
    প্রতি 3 সেকেন্ডে একবার update করে।
    """
    last_text = ""
    while not progress_data.get("done"):
        await asyncio.sleep(3)
        if progress_data.get("done"):
            break

        dl    = progress_data.get("downloaded", 0)
        total = progress_data.get("total", 0)
        spd   = progress_data.get("speed", 0)
        eta   = progress_data.get("eta", 0)
        pct   = min((dl / total) * 100, 100) if total > 0 else 0

        text = (
            f"📥 **Downloading**\n\n"
            f"`{_make_progress_bar(pct)}`\n"
            f"**Progress:** {pct:.2f}% | "
            f"{get_readable_file_size(dl)}/{get_readable_file_size(total)}\n"
            f"**Speed:** {get_readable_file_size(spd)}/s  "
            f"**ETA:** {get_readable_time(int(eta)) if eta else '...'}"
        )
        if text != last_text:
            try:
                await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)
                last_text = text
            except Exception:
                pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PYBALT FALLBACK ENGINE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def pybalt_fallback_download(
    url:        str,
    output_path: str,
    audio_only:  bool = False,
) -> tuple:
    """
    yt-dlp fail করলে Cobalt (pybalt) engine দিয়ে fallback download।
    Protected m3u8 streams-এ কাজ নাও করতে পারে।
    """
    if not PYBALT_AVAILABLE:
        return False, "pybalt not installed"
    try:
        kwargs = {"url": url}
        if audio_only:
            kwargs.update({
                "downloadMode": "audio",
                "audioFormat":  "mp3",
                "audioBitrate": "128",
            })
        result = None
        for folder_kwarg in ("folder_path", "path_folder"):
            try:
                result = await pybalt_download_func(**kwargs, **{folder_kwarg: output_path})
                break
            except TypeError as te:
                if folder_kwarg in str(te):
                    continue
                raise

        if result is None:
            result = await pybalt_download_func(**kwargs)
            if result and os.path.exists(str(result)):
                dest = os.path.join(output_path, os.path.basename(str(result)))
                shutil.move(str(result), dest)
                result = dest

        filepath = str(result) if result else None
        if filepath and os.path.exists(filepath):
            return True, filepath
        return False, "pybalt: file not found after download"

    except Exception as e:
        LOGGER.error(f"[pybalt] Error: {e}")
        return False, str(e)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TRACKING LOGGER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _log_ytdl_to_group(
    client:         Client,
    user,
    url:            str,
    video_info:     dict,
    media_type:     str,
    file_size:      int,
    status:         str,
    error_msg:      str   = "",
    elapsed_sec:    float = 0,
    is_playlist:    bool  = False,
    playlist_title: str   = "",
    playlist_index: int   = 0,
    playlist_total: int   = 0,
    referer:        str   = None,
):
    """LOG_GROUP_ID-তে professional download tracking log পাঠায়।"""
    if not LOG_GROUP_ID:
        return
    try:
        user_id    = user.id if hasattr(user, "id") else "?"
        first_name = getattr(user, "first_name", "") or ""
        last_name  = getattr(user, "last_name",  "") or ""
        full_name  = f"{first_name} {last_name}".strip() or "Unknown"
        username   = f"@{user.username}" if getattr(user, "username", None) else "N/A"
        user_link  = f"[{full_name}](tg://user?id={user_id})"

        title      = (video_info.get("title")    or "Unknown Title")[:80]
        uploader   = (video_info.get("uploader") or video_info.get("channel") or "Unknown")[:50]
        duration   = int(video_info.get("duration", 0) or 0)
        view_count = video_info.get("view_count", 0) or 0
        like_count = video_info.get("like_count", 0) or 0
        webpage    = video_info.get("webpage_url") or url
        platform   = (
            video_info.get("extractor_key")
            or video_info.get("extractor")
            or "Unknown"
        )
        upload_date_raw = video_info.get("upload_date", "")

        upload_date_str = "N/A"
        if upload_date_raw and len(upload_date_raw) == 8:
            try:
                dt = datetime.strptime(upload_date_raw, "%Y%m%d")
                upload_date_str = dt.strftime("%d %b %Y")
            except ValueError:
                upload_date_str = upload_date_raw

        duration_str = get_readable_time(duration) if duration else "N/A"

        def _fmt_num(n):
            if not n:
                return "N/A"
            if n >= 1_000_000:
                return f"{n/1_000_000:.1f}M"
            if n >= 1_000:
                return f"{n/1_000:.1f}K"
            return str(n)

        status_icon  = "✅" if status == "success" else "❌"
        status_text  = "Success" if status == "success" else "Failed"
        media_icon   = "🎬" if media_type == "video" else "🎵"
        media_label  = "Video" if media_type == "video" else "Audio (MP3)"
        elapsed_str  = get_readable_time(int(elapsed_sec)) if elapsed_sec > 0 else "N/A"
        size_str     = get_readable_file_size(file_size) if file_size > 0 else "N/A"
        referer_line = f"• **Referer:** `{referer[:80]}`\n" if referer else ""

        # Playlist extra info
        playlist_line = ""
        if is_playlist and playlist_title:
            playlist_line = (
                f"\n**📋 Playlist Information**\n"
                f"• **Playlist:** `{playlist_title[:60]}`\n"
                f"• **Video:** `{playlist_index}/{playlist_total}`\n"
            )

        text = (
            f"{media_icon} **YTDL Tracker** {status_icon}"
            f"{'  📋 *[Playlist]*' if is_playlist else ''}\n"
            f"{'─' * 30}\n\n"
            f"**👤 User Information**\n"
            f"• **Name:** {user_link}\n"
            f"• **Username:** `{username}`\n"
            f"• **User ID:** `{user_id}`\n"
            f"{playlist_line}\n"
            f"**🎬 Video Information**\n"
            f"• **Title:** `{title}`\n"
            f"• **Platform:** `{platform}`\n"
            f"• **Channel:** `{uploader}`\n"
            f"• **Duration:** `{duration_str}`\n"
            f"• **Upload Date:** `{upload_date_str}`\n"
            f"• **Views:** `{_fmt_num(view_count)}`\n"
            f"• **Likes:** `{_fmt_num(like_count)}`\n\n"
            f"**📥 Download Information**\n"
            f"• **Type:** `{media_label}`\n"
            f"• **File Size:** `{size_str}`\n"
            f"• **Time Taken:** `{elapsed_str}`\n"
            f"• **Status:** `{status_text}`\n"
            f"{referer_line}"
        )

        if status == "failed" and error_msg:
            text += f"• **Error:** `{error_msg[:150]}`\n"

        text += f"\n**🔗 Video Link**\n`{webpage[:100]}`"

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"▶️ Open {platform}", url=webpage)],
        ])

        await client.send_message(
            chat_id=LOG_GROUP_ID,
            text=text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
    except Exception as e:
        LOGGER.warning(f"[YTDLTracker] Failed to send log: {e}")


async def _log_ytdl_failed(
    client,
    user,
    url:        str,
    error_msg:  str,
    media_type: str = "video",
    referer:    str = None,
):
    """Info fetch fail হলে minimal error log পাঠায়।"""
    if not LOG_GROUP_ID:
        return
    try:
        user_id    = user.id if hasattr(user, "id") else "?"
        first_name = getattr(user, "first_name", "") or ""
        last_name  = getattr(user, "last_name",  "") or ""
        full_name  = f"{first_name} {last_name}".strip() or "Unknown"
        username   = f"@{user.username}" if getattr(user, "username", None) else "N/A"
        user_link  = f"[{full_name}](tg://user?id={user_id})"
        media_icon = "🎬" if media_type == "video" else "🎵"
        referer_line = f"• **Referer:** `{referer[:80]}`\n" if referer else ""

        text = (
            f"{media_icon} **YTDL Tracker** ❌\n"
            f"{'─' * 30}\n\n"
            f"**👤 User Information**\n"
            f"• **Name:** {user_link}\n"
            f"• **Username:** `{username}`\n"
            f"• **User ID:** `{user_id}`\n\n"
            f"**📥 Download Information**\n"
            f"• **Status:** `Failed`\n"
            f"• **Error:** `{error_msg[:200]}`\n"
            f"{referer_line}\n"
            f"**🔗 Requested URL**\n"
            f"`{url[:200]}`"
        )
        await client.send_message(
            chat_id=LOG_GROUP_ID,
            text=text,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )
    except Exception as e:
        LOGGER.warning(f"[YTDLTracker] Failed failure log: {e}")


async def _log_playlist_summary(
    client:        Client,
    user,
    playlist_info: dict,
    success_count: int,
    fail_count:    int,
    total:         int,
    elapsed_sec:   float,
):
    """Playlist download সম্পূর্ণ হওয়ার পর summary log।"""
    if not LOG_GROUP_ID:
        return
    try:
        user_id    = user.id if hasattr(user, "id") else "?"
        first_name = getattr(user, "first_name", "") or ""
        last_name  = getattr(user, "last_name",  "") or ""
        full_name  = f"{first_name} {last_name}".strip() or "Unknown"
        user_link  = f"[{full_name}](tg://user?id={user_id})"
        pl_title   = (playlist_info.get("title") or "Unknown Playlist")[:60]
        pl_url     = playlist_info.get("webpage_url") or ""
        elapsed    = get_readable_time(int(elapsed_sec))

        text = (
            f"📋 **Playlist Download Summary**\n"
            f"{'─' * 30}\n\n"
            f"**👤 User:** {user_link}\n\n"
            f"**📋 Playlist:** `{pl_title}`\n"
            f"**🎬 Total:** `{total}`\n"
            f"**✅ Success:** `{success_count}`\n"
            f"**❌ Failed:** `{fail_count}`\n"
            f"**⏱ Total Time:** `{elapsed}`\n\n"
            f"**🔗 Playlist URL:**\n`{pl_url[:150]}`"
        )
        await client.send_message(
            chat_id=LOG_GROUP_ID,
            text=text,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )
    except Exception as e:
        LOGGER.warning(f"[YTDLTracker] Playlist summary log failed: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# RATE LIMIT CHECKER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _check_rate_limit(
    user_id:    int,
    is_premium: bool,
) -> tuple[bool, str]:
    """
    Rate limit check করে।

    Returns
    ───────
    (allowed: bool, reason_message: str)
    """
    # Active download check — একসাথে দুটো download নয়
    if user_id in active_downloads:
        return False, (
            "⏳ **আপনার একটি download চলছে!**\n"
            "সেটি শেষ হওয়ার পরে নতুন link দিন।"
        )

    # Cooldown check
    last_time = user_last_download.get(user_id, 0)
    elapsed   = time() - last_time
    cooldown  = PREMIUM_COOLDOWN if is_premium else FREE_COOLDOWN

    if elapsed < cooldown:
        remaining = int(cooldown - elapsed)
        wait_str  = get_readable_time(remaining)
        if is_premium:
            return False, f"⏳ **{wait_str}** পরে আবার চেষ্টা করুন।"
        else:
            return False, (
                f"⏳ **Cooldown চলছে!**\n\n"
                f"Free users {get_readable_time(FREE_COOLDOWN)} পর পর download করতে পারেন।\n"
                f"**অপেক্ষা করুন:** `{wait_str}`\n\n"
                f"⚡ Premium নিলে মাত্র {PREMIUM_COOLDOWN} সেকেন্ড! → /plans"
            )

    return True, ""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# VIDEO UPLOAD HELPER (MTProto — 2GB support)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _upload_video_file(
    client:       Client,
    chat_id:      int,
    filepath:     str,
    caption:      str,
    duration:     int,
    info:         dict,
    progress_msg,
    is_audio:     bool = False,
) -> bool:
    """
    Pyrogram MTProto দিয়ে file upload করে।
    Bot API-র 50MB limit bypass করে 2GB পর্যন্ত send করতে পারে।
    """
    start_t = time()
    try:
        if is_audio or filepath.endswith(".mp3"):
            title = (info.get("title") or "Audio")[:50]
            await client.send_audio(
                chat_id       = chat_id,
                audio         = filepath,
                caption       = caption,
                duration      = duration,
                title         = title,
                parse_mode    = ParseMode.MARKDOWN,
                progress      = Leaves.progress_for_pyrogram,
                progress_args = progressArgs("📤 Uploading", progress_msg, start_t),
            )
        else:
            thumb_path = None
            try:
                thumb_path = await get_video_thumbnail(filepath, duration)
            except Exception:
                pass
            try:
                await client.send_video(
                    chat_id            = chat_id,
                    video              = filepath,
                    caption            = caption,
                    duration           = duration,
                    thumb              = thumb_path,
                    parse_mode         = ParseMode.MARKDOWN,
                    supports_streaming = True,
                    progress           = Leaves.progress_for_pyrogram,
                    progress_args      = progressArgs("📤 Uploading", progress_msg, start_t),
                )
            finally:
                if thumb_path and os.path.exists(thumb_path):
                    os.remove(thumb_path)
        return True
    except Exception as e:
        LOGGER.error(f"[Upload] Failed: {e}")
        return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MAIN SETUP FUNCTION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def setup_ytdl_handler(app: Client):
    """
    সমস্ত ytdl handler এবং callback Pyrogram app-এ register করে।
    এই function টি bot startup-এ একবার call করতে হবে।
    """

    # ─────────────────────────────────────────────────────────────────────
    # /ytdl COMMAND HANDLER
    # ─────────────────────────────────────────────────────────────────────

    async def ytdl_command(client: Client, message: Message):
        """
        /ytdl কমান্ড handle করে।

        Supported formats:
            /ytdl <url>
            /ytdl <url> referer:<referer_url>
            /ytdl <playlist_url>
            /ytdl <m3u8_url> referer:<site_url>
        """
        user_id = message.from_user.id

        if not YTDLP_AVAILABLE:
            await message.reply_text(
                "❌ **yt-dlp ইনস্টল নেই!**",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        # ── Usage help ─────────────────────────────────────────────────────
        if len(message.command) < 2:
            await message.reply_text(
                "🌐 **YouTube / 1000+ Sites Downloader**\n\n"
                "**Usage:**\n"
                "`/ytdl <URL>`\n"
                "`/ytdl <URL> referer:<Referer URL>`\n\n"
                "**Supported:** YouTube, Instagram, TikTok, Twitter/X, "
                "Facebook, Vimeo এবং 1000+ site!\n"
                "**Playlist:** YouTube playlist URL দিলে auto-detect হবে!\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "**📌 Protected Stream (m3u8)?**\n"
                "Bunny CDN বা অন্য protected HLS stream-এর জন্য:\n"
                "`/ytdl https://cdn.example.com/video.m3u8 "
                "referer:https://example.com`\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "**Examples:**\n"
                "`/ytdl https://youtu.be/xxxxx`\n"
                "`/ytdl https://youtube.com/playlist?list=xxxxx`\n"
                "`/ytdl https://vz-abc.b-cdn.net/video.m3u8 "
                "referer:https://academic.apa.org`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        # ── Input parse ────────────────────────────────────────────────────
        text_parts = message.text.split(None, 1)
        raw_input  = text_parts[1].strip() if len(text_parts) > 1 else ""

        if not raw_input:
            await message.reply_text(
                "**Usage:** `/ytdl <URL>` বা `/ytdl <URL> referer:<Referer>`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        # URL এবং Referer আলাদা করো
        url, referer = parse_url_and_referer(raw_input)

        if not url:
            await message.reply_text(
                "❌ **Valid URL দিন।**",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        is_premium = await is_premium_user(user_id)

        # ── HLS/m3u8 detection — Referer suggest ──────────────────────────
        if is_hls_url(url) and not referer:
            # Referer ছাড়া HLS stream নিয়ে সম্ভাব্য সমস্যা সম্পর্কে warn করো
            await message.reply_text(
                "⚠️ **HLS Stream (m3u8) Detected!**\n\n"
                "এই ধরনের link সরাসরি কাজ নাও করতে পারে।\n"
                "যদি **403 Forbidden** error আসে, তাহলে Referer যোগ করুন:\n\n"
                f"`/ytdl {url} referer:<website_url>`\n\n"
                "উদাহরণ:\n"
                f"`/ytdl {url} referer:https://example.com`\n\n"
                "⏳ _এখন Referer ছাড়াই চেষ্টা করা হচ্ছে..._",
                parse_mode=ParseMode.MARKDOWN,
            )

        # ── Protected CDN detection — Referer suggest ─────────────────────
        elif is_protected_cdn_url(url) and not referer:
            await message.reply_text(
                "⚠️ **Protected CDN URL Detected!**\n\n"
                "Bunny CDN বা similar CDN-এর জন্য Referer প্রায়ই দরকার হয়।\n"
                "Error হলে আবার চেষ্টা করুন Referer সহ:\n\n"
                f"`/ytdl {url} referer:<website_url>`\n\n"
                "⏳ _প্রথমে Referer ছাড়াই চেষ্টা করা হচ্ছে..._",
                parse_mode=ParseMode.MARKDOWN,
            )

        # ── Rate limit check ───────────────────────────────────────────────
        allowed, rate_msg = await _check_rate_limit(user_id, is_premium)
        if not allowed:
            await message.reply_text(rate_msg, parse_mode=ParseMode.MARKDOWN)
            return

        # ── Daily limit check (free user) ─────────────────────────────────
        if not is_premium:
            today = datetime.utcnow().replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            rec        = await daily_limit.find_one({"user_id": user_id})
            ytdl_count = 0
            if rec and rec.get("date") and rec["date"] >= today:
                ytdl_count = rec.get("ytdl_downloads", 0)
            if ytdl_count >= FREE_DAILY_LIMIT:
                await message.reply_text(
                    f"🚫 **Daily limit reached!** (Free: {FREE_DAILY_LIMIT}/day)\n"
                    f"Upgrade করুন: /plans",
                    parse_mode=ParseMode.MARKDOWN,
                )
                return

        # ── Route: Playlist vs Single video ───────────────────────────────
        is_playlist = _is_playlist_url(url)

        if is_playlist:
            await _handle_playlist_initiate(
                client, message, url, user_id, is_premium, referer
            )
        else:
            await _handle_single_video_initiate(
                client, message, url, user_id, is_premium, referer
            )

    # ─────────────────────────────────────────────────────────────────────
    # SINGLE VIDEO: Info fetch → Quality keyboard
    # ─────────────────────────────────────────────────────────────────────

    async def _handle_single_video_initiate(
        client, message, url, user_id, is_premium, referer=None
    ):
        """
        Single video info fetch করে quality keyboard দেখায়।
        referer থাকলে HLS/CDN stream-এও info পাওয়া যাবে।
        """
        warp_ok = _is_warp_available()
        referer_hint = f" | 🔗 Referer: `{referer[:40]}...`" if referer else ""

        status_msg = await message.reply_text(
            f"🔍 **Analyzing...**\n"
            f"_{'🟢 WARP active' if warp_ok else '🟡 Direct connection'}"
            f"{referer_hint}_",
            parse_mode=ParseMode.MARKDOWN,
        )

        loop = asyncio.get_event_loop()
        # Referer সহ info fetch করো
        info, error_msg = await loop.run_in_executor(
            None,
            lambda: get_single_video_info(url, referer),
        )

        if not info:
            # ── Info fetch failed ──────────────────────────────────────────
            asyncio.create_task(
                _log_ytdl_failed(
                    client, message.from_user, url,
                    error_msg or "Info fetch failed",
                    referer=referer,
                )
            )

            # HLS URL-এ 403 হলে referer suggest করো
            error_lower = (error_msg or "").lower()
            if (is_hls_url(url) or is_protected_cdn_url(url)) and (
                "403" in error_lower or "forbidden" in error_lower
            ) and not referer:
                await status_msg.edit_text(
                    "❌ **403 Forbidden — Access Denied!**\n\n"
                    "এই protected stream-এর জন্য **Referer** দরকার।\n\n"
                    "**আবার চেষ্টা করুন Referer সহ:**\n"
                    f"`/ytdl {url} referer:<website_url>`\n\n"
                    "**উদাহরণ:**\n"
                    f"`/ytdl {url} referer:https://example.com`",
                    parse_mode=ParseMode.MARKDOWN,
                )
                return

            # pybalt fallback (non-HLS URL-এর জন্য)
            if PYBALT_AVAILABLE and not is_hls_url(url):
                cleanup_expired_sessions()
                ytdl_sessions[message.chat.id] = {
                    "user_id":    user_id,
                    "url":        url,
                    "info":       {},
                    "message_id": message.id,
                    "created_at": time(),
                    "use_pybalt": True,
                    "user_obj":   message.from_user,
                    "type":       "single",
                    "referer":    referer,
                }
                await status_msg.edit_text(
                    "📹 **Video Found (Cobalt Engine)**\n\n"
                    "👇 **Quality বেছে নিন:**",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton(
                            "🎬 Best Quality",
                            callback_data=f"ytdl_v_{message.chat.id}_best",
                        )],
                        [InlineKeyboardButton(
                            "🎵 Audio Only",
                            callback_data=f"ytdl_a_{message.chat.id}",
                        )],
                        [InlineKeyboardButton(
                            "❌ Cancel",
                            callback_data=f"ytdl_cancel_{message.chat.id}",
                        )],
                    ]),
                )
                return

            # সব fallback শেষ — error দেখাও
            await status_msg.edit_text(
                f"❌ **Download failed!**\n\n"
                f"{_friendly_error(error_msg) if error_msg else 'Unknown error'}",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        # ── Info fetch সফল → Quality keyboard দেখাও ──────────────────────
        title        = (info.get("title", "Unknown") or "Unknown")[:60]
        duration     = info.get("duration", 0) or 0
        uploader     = info.get("uploader", "Unknown") or "Unknown"
        duration_str = get_readable_time(int(duration)) if duration else "Unknown"
        referer_info = f"\n🔗 **Referer:** `{referer[:50]}`" if referer else ""

        cleanup_expired_sessions()
        ytdl_sessions[message.chat.id] = {
            "user_id":    user_id,
            "url":        url,
            "info":       info,
            "message_id": message.id,
            "created_at": time(),
            "user_obj":   message.from_user,
            "type":       "single",
            "referer":    referer,   # Session-এ referer store করো
        }

        await status_msg.edit_text(
            f"📹 **{title}**\n\n"
            f"👤 **Channel:** {uploader}\n"
            f"⏱ **Duration:** {duration_str}"
            f"{referer_info}\n\n"
            f"👇 **Quality বেছে নিন:**",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=build_quality_keyboard(info, message.chat.id, is_playlist=False),
            disable_web_page_preview=True,
        )

    # ─────────────────────────────────────────────────────────────────────
    # PLAYLIST: Info fetch → Quality keyboard অথবা block (free user)
    # ─────────────────────────────────────────────────────────────────────

    async def _handle_playlist_initiate(
        client, message, url, user_id, is_premium, referer=None
    ):
        """Playlist info fetch করে quality keyboard দেখায়।"""

        # Free user playlist block করো
        if not is_premium:
            await message.reply_text(
                "🚫 **Playlist Download — Premium Only!**\n\n"
                "Free ইউজাররা playlist download করতে পারবেন না।\n\n"
                "⚡ **Premium নিন** এবং পুরো playlist একসাথে download করুন!\n"
                "👉 /plans",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        warp_ok    = _is_warp_available()
        status_msg = await message.reply_text(
            f"🔍 **Playlist Analyzing...**\n"
            f"_{'🟢 WARP active' if warp_ok else '🟡 Direct connection'}_",
            parse_mode=ParseMode.MARKDOWN,
        )

        loop = asyncio.get_event_loop()
        info, error_msg = await loop.run_in_executor(
            None,
            lambda: get_playlist_info(url, referer),
        )

        if not info:
            asyncio.create_task(
                _log_ytdl_failed(
                    client, message.from_user, url,
                    error_msg or "Playlist info fetch failed",
                    referer=referer,
                )
            )
            await status_msg.edit_text(
                f"❌ **Playlist load failed!**\n\n"
                f"{_friendly_error(error_msg) if error_msg else 'Unknown error'}",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        # Single video হিসেবে detect হলে single flow-এ পাঠাও
        if info.get("_type") != "playlist":
            ytdl_sessions[message.chat.id] = {
                "user_id":    user_id,
                "url":        url,
                "info":       info,
                "message_id": message.id,
                "created_at": time(),
                "user_obj":   message.from_user,
                "type":       "single",
                "referer":    referer,
            }
            title        = (info.get("title", "Unknown") or "Unknown")[:60]
            duration     = info.get("duration", 0) or 0
            duration_str = get_readable_time(int(duration)) if duration else "Unknown"
            uploader     = info.get("uploader", "Unknown") or "Unknown"

            await status_msg.edit_text(
                f"📹 **{title}**\n\n"
                f"👤 **Channel:** {uploader}\n"
                f"⏱ **Duration:** {duration_str}\n\n"
                f"👇 **Quality বেছে নিন:**",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=build_quality_keyboard(info, message.chat.id, is_playlist=False),
                disable_web_page_preview=True,
            )
            return

        # ── Playlist found ─────────────────────────────────────────────────
        pl_title = (info.get("title") or "Unknown Playlist")[:60]
        entries  = [e for e in (info.get("entries") or []) if e]
        total    = len(entries)

        if total == 0:
            await status_msg.edit_text(
                "❌ **Playlist-এ কোনো ভিডিও পাওয়া যায়নি।**",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        # প্রথম valid entry থেকে quality options নাও
        first_entry_info = {}
        for entry in entries[:5]:
            entry_url = entry.get("url") or entry.get("webpage_url") or ""
            if entry_url:
                try:
                    fi, _ = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda u=entry_url: get_single_video_info(u, referer),
                    )
                    if fi and fi.get("formats"):
                        first_entry_info = fi
                        break
                except Exception:
                    pass

        cleanup_expired_sessions()
        ytdl_sessions[message.chat.id] = {
            "user_id":    user_id,
            "url":        url,
            "info":       info,
            "entries":    entries,
            "first_info": first_entry_info,
            "message_id": message.id,
            "created_at": time(),
            "user_obj":   message.from_user,
            "type":       "playlist",
            "cancelled":  False,
            "referer":    referer,   # Playlist-এর প্রতিটি video-তে পাস হবে
        }

        await status_msg.edit_text(
            f"📋 **Playlist Found!**\n\n"
            f"📝 **Playlist:** {pl_title}\n"
            f"🎬 **Total Videos:** {total} "
            f"(max {PREMIUM_PLAYLIST_LIMIT})\n\n"
            f"👇 **সব ভিডিওর জন্য একটি quality বেছে নিন:**\n"
            f"_(উপলব্ধ না হলে best quality auto-select হবে)_",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=build_quality_keyboard(
                first_entry_info if first_entry_info else info,
                message.chat.id,
                is_playlist=True,
            ),
            disable_web_page_preview=True,
        )

    # ─────────────────────────────────────────────────────────────────────
    # CALLBACK: Single video quality selection
    # ─────────────────────────────────────────────────────────────────────

    @app.on_callback_query(filters.regex(r"^ytdl_(v|a|cancel)_"))
    async def ytdl_callback(client, callback_query):
        """Single video quality/audio/cancel callback।"""
        data    = callback_query.data
        chat_id = callback_query.message.chat.id
        user_id = callback_query.from_user.id

        session = ytdl_sessions.get(chat_id)
        if not session or session["user_id"] != user_id:
            await callback_query.answer("❌ Session expired!", show_alert=True)
            return

        # ── Cancel ────────────────────────────────────────────────────────
        if data.startswith("ytdl_cancel_"):
            ytdl_sessions.pop(chat_id, None)
            active_downloads.discard(user_id)
            await callback_query.message.edit_text(
                "❌ **Cancelled.**",
                parse_mode=ParseMode.MARKDOWN,
            )
            await callback_query.answer()
            return

        # ── Session data ──────────────────────────────────────────────────
        url      = session["url"]
        info     = session.get("info", {})
        user_obj = session.get("user_obj", callback_query.from_user)
        referer  = session.get("referer")           # Session থেকে referer নাও
        is_audio = data.startswith("ytdl_a_")

        format_id = None
        if data.startswith("ytdl_v_"):
            prefix    = f"ytdl_v_{chat_id}_"
            format_id = data[len(prefix):]
            if format_id == "best":
                format_id = None

        await callback_query.answer("⏳ শুরু হচ্ছে...")

        is_premium = await is_premium_user(user_id)

        # ── Rate limit re-check ────────────────────────────────────────────
        allowed, rate_msg = await _check_rate_limit(user_id, is_premium)
        if not allowed:
            await callback_query.message.edit_text(
                rate_msg,
                parse_mode=ParseMode.MARKDOWN,
            )
            ytdl_sessions.pop(chat_id, None)
            return

        active_downloads.add(user_id)

        # ── Daily limit update ─────────────────────────────────────────────
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        if not is_premium:
            rec        = await daily_limit.find_one({"user_id": user_id})
            ytdl_count = 0
            if rec and rec.get("date") and rec["date"] >= today:
                ytdl_count = rec.get("ytdl_downloads", 0)
            await daily_limit.update_one(
                {"user_id": user_id},
                {
                    "$set": {"ytdl_downloads": ytdl_count + 1, "date": today},
                    "$inc": {"total_downloads": 1},
                },
                upsert=True,
            )
        else:
            await daily_limit.update_one(
                {"user_id": user_id},
                {"$inc": {"total_downloads": 1}},
                upsert=True,
            )

        warp_ok     = _is_warp_available()
        referer_txt = f" | 🔗 Referer Active" if referer else ""
        await callback_query.message.edit_text(
            f"📥 **Downloading...**\n"
            f"_{'🟢 WARP proxy' if warp_ok else '🟡 Direct'}{referer_txt}_",
            parse_mode=ParseMode.MARKDOWN,
        )

        cleanup_stale_files()
        user_dir = os.path.join(DOWNLOAD_DIR, str(user_id))
        os.makedirs(user_dir, exist_ok=True)

        loop          = asyncio.get_event_loop()
        overall_start = time()
        use_pybalt    = session.get("use_pybalt", False)
        media_type    = "audio" if is_audio else "video"

        try:
            if use_pybalt:
                # pybalt-এ referer support সীমিত, তবু try করো
                success, result = await pybalt_fallback_download(
                    url, user_dir, is_audio
                )
            else:
                # ── yt-dlp download with Referer ──────────────────────────
                progress_data = {
                    "downloaded": 0,
                    "total":      0,
                    "speed":      0,
                    "eta":        0,
                    "done":       False,
                }
                progress_task = asyncio.create_task(
                    _ytdl_progress_updater(callback_query.message, progress_data)
                )
                try:
                    success, result = await loop.run_in_executor(
                        None,
                        lambda: download_single_video(
                            url,
                            user_dir,
                            format_id,
                            is_audio,
                            progress_data,
                            True,      # noplaylist
                            referer,   # Referer pass করো ✅
                        ),
                    )
                finally:
                    progress_data["done"] = True
                    try:
                        await progress_task
                    except Exception:
                        pass

                # yt-dlp fail → pybalt fallback (non-HLS only)
                if not success and PYBALT_AVAILABLE and not is_hls_url(url):
                    await callback_query.message.edit_text(
                        "⚠️ **Cobalt engine দিয়ে চেষ্টা...**",
                        parse_mode=ParseMode.MARKDOWN,
                    )
                    success, result = await pybalt_fallback_download(
                        url, user_dir, is_audio
                    )

            if not success:
                asyncio.create_task(
                    _log_ytdl_to_group(
                        client, user_obj, url, info,
                        media_type=media_type,
                        file_size=0,
                        status="failed",
                        error_msg=_friendly_error(result),
                        elapsed_sec=time() - overall_start,
                        referer=referer,
                    )
                )
                await callback_query.message.edit_text(
                    f"❌ **Download failed!**\n\n{_friendly_error(result)}",
                    parse_mode=ParseMode.MARKDOWN,
                )
                return

            filepath  = result
            file_size = os.path.getsize(filepath)

            # ── 2GB size check ────────────────────────────────────────────
            if file_size > MAX_FILE_SIZE:
                os.remove(filepath)
                asyncio.create_task(
                    _log_ytdl_to_group(
                        client, user_obj, url, info,
                        media_type=media_type,
                        file_size=file_size,
                        status="failed",
                        error_msg=f"File too large: {get_readable_file_size(file_size)}",
                        elapsed_sec=time() - overall_start,
                        referer=referer,
                    )
                )
                await callback_query.message.edit_text(
                    f"❌ **File অনেক বড়!**\n"
                    f"📦 `{get_readable_file_size(file_size)}` / "
                    f"Limit: `{get_readable_file_size(MAX_FILE_SIZE)}`",
                    parse_mode=ParseMode.MARKDOWN,
                )
                return

            await callback_query.message.edit_text(
                f"📤 **Uploading...**\n📦 `{get_readable_file_size(file_size)}`",
                parse_mode=ParseMode.MARKDOWN,
            )

            title   = (info.get("title") or "Downloaded Media")[:50]
            caption = f"**{title}**\n\n📥 Downloaded by @juktijol Bot"
            duration = int(info.get("duration", 0) or 0)

            upload_success = await _upload_video_file(
                client, chat_id, filepath, caption, duration,
                info, callback_query.message, is_audio,
            )

            elapsed = get_readable_time(int(time() - overall_start))
            if upload_success:
                await callback_query.message.edit_text(
                    f"✅ **সফল!**\n"
                    f"⏱ `{elapsed}` | 📦 `{get_readable_file_size(file_size)}`",
                    parse_mode=ParseMode.MARKDOWN,
                )
            else:
                await callback_query.message.edit_text(
                    "❌ **Upload failed!**",
                    parse_mode=ParseMode.MARKDOWN,
                )

            asyncio.create_task(
                _log_ytdl_to_group(
                    client, user_obj, url, info,
                    media_type=media_type,
                    file_size=file_size,
                    status="success" if upload_success else "failed",
                    error_msg="" if upload_success else "Upload failed",
                    elapsed_sec=time() - overall_start,
                    referer=referer,
                )
            )

        finally:
            user_last_download[user_id] = time()
            active_downloads.discard(user_id)

            filepath_local = locals().get("filepath")
            if filepath_local and os.path.exists(filepath_local):
                os.remove(filepath_local)
            try:
                if not os.listdir(user_dir):
                    os.rmdir(user_dir)
            except Exception:
                pass
            ytdl_sessions.pop(chat_id, None)

    # ─────────────────────────────────────────────────────────────────────
    # CALLBACK: Playlist quality selection
    # ─────────────────────────────────────────────────────────────────────

    @app.on_callback_query(filters.regex(r"^ytpl_(v|a|cancel)_"))
    async def ytpl_callback(client, callback_query):
        """Playlist video quality/audio/cancel callback।"""
        data    = callback_query.data
        chat_id = callback_query.message.chat.id
        user_id = callback_query.from_user.id

        session = ytdl_sessions.get(chat_id)
        if not session or session["user_id"] != user_id:
            await callback_query.answer("❌ Session expired!", show_alert=True)
            return

        # ── Cancel ────────────────────────────────────────────────────────
        if data.startswith("ytpl_cancel_"):
            session["cancelled"] = True
            ytdl_sessions.pop(chat_id, None)
            active_downloads.discard(user_id)
            await callback_query.message.edit_text(
                "❌ **Playlist download cancelled.**",
                parse_mode=ParseMode.MARKDOWN,
            )
            await callback_query.answer()
            return

        is_audio  = data.startswith("ytpl_a_")
        format_id = None
        if data.startswith("ytpl_v_"):
            prefix    = f"ytpl_v_{chat_id}_"
            format_id = data[len(prefix):]
            if format_id == "best":
                format_id = None

        await callback_query.answer("⏳ Playlist download শুরু হচ্ছে...")

        is_premium = await is_premium_user(user_id)
        if not is_premium:
            await callback_query.message.edit_text(
                "🚫 **Playlist Download — Premium Only!**\n\n/plans",
                parse_mode=ParseMode.MARKDOWN,
            )
            ytdl_sessions.pop(chat_id, None)
            return

        allowed, rate_msg = await _check_rate_limit(user_id, is_premium)
        if not allowed:
            await callback_query.message.edit_text(
                rate_msg,
                parse_mode=ParseMode.MARKDOWN,
            )
            ytdl_sessions.pop(chat_id, None)
            return

        playlist_info = session.get("info", {})
        entries       = session.get("entries", [])
        user_obj      = session.get("user_obj", callback_query.from_user)
        referer       = session.get("referer")      # Playlist session থেকে referer
        pl_title      = (playlist_info.get("title") or "Playlist")[:60]
        total         = len(entries)
        media_type    = "audio" if is_audio else "video"

        active_downloads.add(user_id)
        session["cancelled"] = False

        cancel_kb = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "⏹ Cancel Playlist",
                callback_data=f"ytpl_stop_{chat_id}",
            )
        ]])

        status_msg = callback_query.message
        await status_msg.edit_text(
            f"📋 **Playlist Download শুরু হচ্ছে...**\n\n"
            f"📝 `{pl_title}`\n"
            f"🎬 **Total:** {total} videos\n\n"
            f"⏳ প্রস্তুতি নেওয়া হচ্ছে...",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=cancel_kb,
        )

        overall_start = time()
        success_count = 0
        fail_count    = 0
        user_dir      = os.path.join(DOWNLOAD_DIR, str(user_id))
        os.makedirs(user_dir, exist_ok=True)
        loop          = asyncio.get_event_loop()

        try:
            for idx, entry in enumerate(entries, 1):

                # ── Cancel check per video ─────────────────────────────────
                current_session = ytdl_sessions.get(chat_id, {})
                if current_session.get("cancelled", False):
                    await status_msg.edit_text(
                        f"⏹ **Playlist cancelled!**\n\n"
                        f"✅ Downloaded: {success_count}\n"
                        f"❌ Failed: {fail_count}\n"
                        f"📊 Total processed: {idx - 1}/{total}",
                        parse_mode=ParseMode.MARKDOWN,
                    )
                    break

                entry_url = (
                    entry.get("url")
                    or entry.get("webpage_url")
                    or ""
                )
                entry_title = (entry.get("title") or f"Video {idx}")[:50]

                if not entry_url:
                    fail_count += 1
                    continue

                # Progress update
                pct  = ((idx - 1) / total) * 100
                pbar = _make_progress_bar(pct)
                try:
                    await status_msg.edit_text(
                        f"📋 **Downloading Playlist**\n\n"
                        f"📝 `{pl_title}`\n"
                        f"`{pbar}`\n"
                        f"**{idx}/{total}** | {pct:.0f}%\n\n"
                        f"🎬 **Now:** `{entry_title}`\n"
                        f"✅ Success: {success_count}  "
                        f"❌ Failed: {fail_count}",
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=cancel_kb,
                    )
                except Exception:
                    pass

                video_start = time()
                filepath    = None

                try:
                    # Single video info with Referer
                    video_info, _ = await loop.run_in_executor(
                        None,
                        lambda u=entry_url: get_single_video_info(u, referer),
                    )

                    progress_data = {
                        "downloaded": 0,
                        "total":      0,
                        "speed":      0,
                        "eta":        0,
                        "done":       False,
                    }

                    # Download with Referer ✅
                    dl_success, dl_result = await loop.run_in_executor(
                        None,
                        lambda u=entry_url: download_single_video(
                            u,
                            user_dir,
                            format_id,
                            is_audio,
                            progress_data,
                            True,       # noplaylist
                            referer,    # Referer pass করো ✅
                        ),
                    )

                    if not dl_success:
                        fail_count += 1
                        asyncio.create_task(
                            _log_ytdl_to_group(
                                client, user_obj, entry_url,
                                video_info or {},
                                media_type=media_type,
                                file_size=0,
                                status="failed",
                                error_msg=_friendly_error(dl_result),
                                elapsed_sec=time() - video_start,
                                is_playlist=True,
                                playlist_title=pl_title,
                                playlist_index=idx,
                                playlist_total=total,
                                referer=referer,
                            )
                        )
                        continue

                    filepath  = dl_result
                    file_size = os.path.getsize(filepath)

                    # 2GB check per video
                    if file_size > MAX_FILE_SIZE:
                        os.remove(filepath)
                        filepath = None
                        fail_count += 1
                        continue

                    v_title  = ((video_info or {}).get("title") or entry_title)[:50]
                    caption  = (
                        f"**{v_title}**\n"
                        f"📋 Playlist: `{pl_title}`\n"
                        f"🎬 {idx}/{total}\n\n"
                        f"📥 Downloaded by @juktijol Bot"
                    )
                    duration = int((video_info or {}).get("duration", 0) or 0)

                    upload_ok = await _upload_video_file(
                        client, chat_id, filepath, caption, duration,
                        video_info or {}, status_msg, is_audio,
                    )

                    if upload_ok:
                        success_count += 1
                    else:
                        fail_count += 1

                    asyncio.create_task(
                        _log_ytdl_to_group(
                            client, user_obj, entry_url,
                            video_info or {},
                            media_type=media_type,
                            file_size=file_size,
                            status="success" if upload_ok else "failed",
                            error_msg="" if upload_ok else "Upload failed",
                            elapsed_sec=time() - video_start,
                            is_playlist=True,
                            playlist_title=pl_title,
                            playlist_index=idx,
                            playlist_total=total,
                            referer=referer,
                        )
                    )

                except Exception as e:
                    LOGGER.error(f"[Playlist] Entry {idx} error: {e}")
                    fail_count += 1
                finally:
                    if filepath and os.path.exists(filepath):
                        os.remove(filepath)

                # Videos-এর মাঝে Premium cooldown
                if idx < total and not current_session.get("cancelled", False):
                    await asyncio.sleep(PREMIUM_COOLDOWN)

            # ── Playlist complete summary ───────────────────────────────────
            elapsed_total = time() - overall_start
            final_pct     = (
                100 if success_count == total
                else (success_count / total * 100 if total > 0 else 0)
            )

            try:
                await status_msg.edit_text(
                    f"{'✅' if fail_count == 0 else '⚠️'} "
                    f"**Playlist Download Complete!**\n\n"
                    f"📝 `{pl_title}`\n"
                    f"`{_make_progress_bar(final_pct)}`\n\n"
                    f"✅ **Success:** {success_count}/{total}\n"
                    f"❌ **Failed:** {fail_count}\n"
                    f"⏱ **Total Time:** "
                    f"`{get_readable_time(int(elapsed_total))}`",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass

            asyncio.create_task(
                _log_playlist_summary(
                    client, user_obj, playlist_info,
                    success_count, fail_count, total, elapsed_total,
                )
            )

        finally:
            user_last_download[user_id] = time()
            active_downloads.discard(user_id)
            try:
                if not os.listdir(user_dir):
                    os.rmdir(user_dir)
            except Exception:
                pass
            ytdl_sessions.pop(chat_id, None)

    # ─────────────────────────────────────────────────────────────────────
    # CALLBACK: Playlist stop button
    # ─────────────────────────────────────────────────────────────────────

    @app.on_callback_query(filters.regex(r"^ytpl_stop_"))
    async def ytpl_stop_callback(client, callback_query):
        """Playlist download loop stop করার জন্য cancel flag set করে।"""
        chat_id = callback_query.message.chat.id
        user_id = callback_query.from_user.id

        session = ytdl_sessions.get(chat_id)
        if not session or session["user_id"] != user_id:
            await callback_query.answer("❌ Session নেই!", show_alert=True)
            return

        # Loop-এর next iteration-এ এই flag দেখে থামবে
        session["cancelled"] = True
        await callback_query.answer(
            "⏹ Cancel request পাঠানো হয়েছে! চলমান video শেষ হলে থামবে।",
            show_alert=True,
        )

    # ─────────────────────────────────────────────────────────────────────
    # HANDLER REGISTRATION
    # ─────────────────────────────────────────────────────────────────────

    app.add_handler(
        MessageHandler(
            ytdl_command,
            filters=(
                filters.command("ytdl", prefixes=COMMAND_PREFIX)
                & (filters.private | filters.group)
            ),
        ),
        group=1,
    )

    LOGGER.info("[ytdl] Handler registered ✅ (Referer support enabled)")
