# Copyright @juktijol
# Channel t.me/juktijol
#
# plugins/directdl.py  ─  File Hosting Sites Downloader (Enhanced v2.0)
#
# /ddl <URL>              ─ Download from any supported hosting site
# /ddl <URL>::<password>  ─ Password-protected links
#
# Supported (via utils/direct_links.py):
#   MediaFire (files + folders), GoFile, TeraBox, Pixeldrain, 1Fichier,
#   StreamTape, WeTransfer, SwissTransfer, qiwi.gg, mp4upload,
#   BuzzHeavier, Send.cm, LinkBox, Doodstream family, Racaty,
#   KrakenFiles, Solidfiles, Upload.ee, TmpSend, EasyUpload,
#   StreamVid, StreamHub, pCloud, AkmFiles, Shrdsk, FileLions,
#   StreamWish, HxFile, OneDrive, GitHub Releases, OSDN,
#   Yandex Disk, devuploads, UploadHaven, FuckingFast,
#   Lulacloud, MediaFile, BerkasDrive, Transfer.it  and more.

import os
import asyncio
import shutil
import tempfile
import hashlib
from concurrent.futures import ThreadPoolExecutor
from time import time
from datetime import datetime
from urllib.parse import urlparse, unquote, quote
from typing import Optional

import aiohttp
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import ParseMode
from pyrogram.handlers import MessageHandler

from config import COMMAND_PREFIX
from utils import LOGGER
from utils.direct_links import generate_direct_link, DirectLinkException, is_supported_site
from utils.helper import get_readable_file_size, get_readable_time, get_video_thumbnail
from core import prem_plan1, prem_plan2, prem_plan3, user_activity_collection

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

DOWNLOAD_DIR      = os.path.join(tempfile.gettempdir(), "directdl_downloads")
MAX_FILE_SIZE     = 2 * 1024 ** 3       # 2 GB  (premium users)
FREE_FILE_LIMIT   = 500 * 1024 ** 2     # 500 MB (free users)
PROGRESS_DELAY    = 3                   # seconds between progress edits
CHUNK_SIZE        = 8 * 1024 * 1024     # 8 MB per chunk
MAX_RETRIES       = 3                   # retry count on download failure
RETRY_DELAY       = 5                   # seconds between retries

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Thread pool for synchronous direct_links.py functions
_THREAD_POOL = ThreadPoolExecutor(max_workers=4)

# ─────────────────────────────────────────────────────────────────────────────
# BOT VERSION INFO
# ─────────────────────────────────────────────────────────────────────────────

BOT_VERSION   = "2.0.0"
BOT_CHANNEL   = "t.me/juktijol"
BOT_AUTHOR    = "@juktijol"

# ─────────────────────────────────────────────────────────────────────────────
# SUPPORTED SITES DISPLAY TEXT
# ─────────────────────────────────────────────────────────────────────────────

SUPPORTED_SITES_TEXT = """
╔══════════════════════════════════╗
║   📥 Direct Link Downloader v{ver}   ║
║        {ch}        ║
╚══════════════════════════════════╝

**⚙️ ব্যবহার পদ্ধতি:**
┌──────────────────────────────────
│ `/ddl <URL>`
│ `/ddl <URL>::<password>`
└──────────────────────────────────

**🌐 Supported Sites (40+):**

🗂 **Cloud Storage:**
  • MediaFire (File + Folder)
  • Google Drive _(via index)_
  • OneDrive
  • pCloud
  • Yandex Disk
  • TeraBox
  • GoFile

💾 **Direct Hosts:**
  • Pixeldrain
  • KrakenFiles
  • Solidfiles
  • Upload.ee
  • AkmFiles
  • qiwi.gg
  • BuzzHeavier
  • HxFile

📦 **Transfer Services:**
  • WeTransfer
  • SwissTransfer
  • Send.cm
  • TmpSend
  • EasyUpload
  • Transfer.it
  • LinkBox

🎬 **Video Hosts:**
  • Doodstream (& family)
  • StreamTape
  • StreamHub
  • StreamVid
  • StreamWish
  • FileLions
  • mp4upload
  • Racaty

🔧 **Dev / Other:**
  • GitHub Releases
  • OSDN
  • Shrdsk
  • devuploads
  • UploadHaven
  • FuckingFast
  • Lulacloud
  • MediaFile
  • BerkasDrive

**📋 উদাহরণ:**
```
/ddl https://www.mediafire.com/file/xxx
/ddl https://gofile.io/d/xxxxx
/ddl https://pixeldrain.com/u/xxxxxxxx
/ddl https://1fichier.com/?xxx::mypass
```

**ℹ️ সীমাবদ্ধতা:**
┌──────────────────────────────────
│ 👤 Free User  → সর্বোচ্চ 500 MB
│ 👑 Premium    → সর্বোচ্চ 2 GB
└──────────────────────────────────
_Powered by {author} | {ch}_
""".format(ver=BOT_VERSION, ch=BOT_CHANNEL, author=BOT_AUTHOR)


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

async def _is_premium(user_id: int) -> bool:
    """Check if user has any active premium plan."""
    now = datetime.utcnow()
    for col in [prem_plan1, prem_plan2, prem_plan3]:
        doc = await col.find_one({"user_id": user_id})
        if doc and doc.get("expiry_date", now) > now:
            return True
    return False


def _progress_bar(pct: float, length: int = 18) -> str:
    """Return a styled Unicode progress bar."""
    filled = int(length * pct / 100)
    bar    = "█" * filled + "░" * (length - filled)
    return bar


def _normalize_url(url: str) -> str:
    """
    Fix double-encoded URLs before resolving.
    Example: %25E0%25B8%25AD  →  %E0%B8%AD  (MediaFire Thai filename fix)
    Password part (::pass) is safely preserved.
    """
    if "::" in url:
        url_part, sep, password = url.partition("::")
        return _normalize_url(url_part) + sep + password

    decoded = unquote(url)
    # Accept decoded version only if it is still a valid HTTP URL
    if decoded != url and decoded.startswith(("http://", "https://")):
        return decoded
    return url


def _parse_headers(raw) -> dict:
    """
    Convert header data from direct_links.py into an aiohttp-compatible dict.
    Accepts: str | list | dict
    """
    if not raw:
        return {}
    if isinstance(raw, dict):
        return dict(raw)

    items = [raw] if isinstance(raw, str) else list(raw)
    headers = {}
    for item in items:
        item = item.strip()
        if not item or ":" not in item:
            continue
        key, _, value = item.partition(":")
        headers[key.strip()] = value.strip()
    return headers


async def _resolve_link_async(url: str):
    """Run generate_direct_link() in a thread so it won't block the event loop."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_THREAD_POOL, generate_direct_link, url)


def _safe_filename(name: str) -> str:
    """Sanitize filename for safe disk usage."""
    keep = " abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.()"
    return "".join(c if c in keep else "_" for c in name).strip() or "file"


def _guess_filename_from_url(url: str) -> str:
    """Extract and sanitize filename from a direct URL."""
    path = urlparse(url).path
    name = unquote(os.path.basename(path))
    return _safe_filename(name) if name else "downloaded_file"


def _get_file_type_icon(filename: str) -> str:
    """Return a relevant emoji for the file type."""
    ext = os.path.splitext(filename)[1].lower()
    icons = {
        # Video
        ".mp4": "🎬", ".mkv": "🎬", ".avi": "🎬", ".mov": "🎬",
        ".webm": "🎬", ".flv": "🎬", ".wmv": "🎬", ".m4v": "🎬",
        # Audio
        ".mp3": "🎵", ".flac": "🎵", ".ogg": "🎵", ".opus": "🎵",
        ".m4a": "🎵", ".wav": "🎵", ".aac": "🎵",
        # Image
        ".jpg": "🖼", ".jpeg": "🖼", ".png": "🖼", ".gif": "🖼",
        ".webp": "🖼", ".svg": "🖼", ".bmp": "🖼",
        # Archive
        ".zip": "🗜", ".rar": "🗜", ".7z": "🗜", ".tar": "🗜",
        ".gz": "🗜", ".bz2": "🗜", ".xz": "🗜",
        # APK
        ".apk": "📱",
        # Document
        ".pdf": "📄", ".doc": "📝", ".docx": "📝",
        ".xls": "📊", ".xlsx": "📊", ".ppt": "📊",
        # Code
        ".py": "🐍", ".js": "📜", ".html": "🌐", ".css": "🎨",
        # Executable
        ".exe": "⚙️", ".msi": "⚙️", ".sh": "⚙️",
        # ISO
        ".iso": "💿",
    }
    return icons.get(ext, "📦")


def _get_domain(url: str) -> str:
    """Extract clean domain name from URL."""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.replace("www.", "")
        return domain
    except Exception:
        return "Unknown"


async def _get_user_thumbnail(user_id: int) -> Optional[str]:
    """Return the stored thumbnail path for a user, or None."""
    try:
        doc = await user_activity_collection.find_one({"user_id": user_id})
        if doc:
            tp = doc.get("thumbnail_path")
            if tp and os.path.exists(tp):
                return tp
    except Exception:
        pass
    return None


async def _log_activity(user_id: int, url: str, file_size: int, status: str) -> None:
    """Log download activity to DB."""
    try:
        await user_activity_collection.update_one(
            {"user_id": user_id},
            {
                "$inc": {
                    "total_downloads": 1,
                    "total_bytes": file_size,
                },
                "$set": {
                    "last_download_url":    url[:200],
                    "last_download_status": status,
                    "last_download_time":   datetime.utcnow(),
                },
            },
            upsert=True,
        )
    except Exception as e:
        LOGGER.warning(f"[DirectDL] Activity log failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# DOWNLOAD  (streaming + retry + speed smoothing)
# ─────────────────────────────────────────────────────────────────────────────

async def _stream_download(
    url: str,
    dest_path: str,
    extra_headers: dict,
    status_msg: Message,
    display_name: str,
    max_size: int,
    attempt: int = 1,
) -> bool:
    """
    Stream-download *url* to *dest_path* in chunks.
    Auto-retries up to MAX_RETRIES on transient failures.
    Returns True on success, False on failure.
    """
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)

    base_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) "
            "Gecko/20100101 Firefox/124.0"
        ),
        "Accept":          "*/*",
        "Accept-Encoding": "identity",
        "Connection":      "keep-alive",
    }
    base_headers.update(extra_headers)

    file_icon = _get_file_type_icon(display_name)

    try:
        connector = aiohttp.TCPConnector(ssl=False, limit=10)
        timeout   = aiohttp.ClientTimeout(total=None, connect=30, sock_read=120)

        async with aiohttp.ClientSession(
            connector=connector, timeout=timeout
        ) as session:
            async with session.get(
                url, headers=base_headers, allow_redirects=True
            ) as resp:

                # ── HTTP error check ──────────────────────────────────────────
                if resp.status not in (200, 206):
                    if resp.status in (429, 503) and attempt <= MAX_RETRIES:
                        await status_msg.edit_text(
                            f"⚠️ **Server busy (HTTP {resp.status})**\n"
                            f"🔄 Retry `{attempt}/{MAX_RETRIES}` — "
                            f"`{RETRY_DELAY}s` পরে আবার চেষ্টা হবে...",
                            parse_mode=ParseMode.MARKDOWN,
                        )
                        await asyncio.sleep(RETRY_DELAY)
                        return await _stream_download(
                            url, dest_path, extra_headers,
                            status_msg, display_name, max_size, attempt + 1,
                        )
                    await status_msg.edit_text(
                        f"❌ **Server Error: HTTP `{resp.status}`**\n\n"
                        f"🔗 `{url[:80]}`",
                        parse_mode=ParseMode.MARKDOWN,
                    )
                    return False

                total = int(resp.headers.get("Content-Length", 0))

                # ── Content-Type display ──────────────────────────────────────
                content_type = resp.headers.get("Content-Type", "অজানা").split(";")[0]

                # ── Size guard ────────────────────────────────────────────────
                if total > 0 and total > max_size:
                    await status_msg.edit_text(
                        f"❌ **ফাইল সীমার বেশি বড়!**\n\n"
                        f"{file_icon} `{display_name[:50]}`\n"
                        f"📦 Size:  `{get_readable_file_size(total)}`\n"
                        f"🚫 Limit: `{get_readable_file_size(max_size)}`\n\n"
                        f"💡 _Premium upgrade করলে 2 GB পর্যন্ত ডাউনলোড করা যাবে।_",
                        parse_mode=ParseMode.MARKDOWN,
                    )
                    return False

                downloaded = 0
                start_ts   = time()
                last_edit  = 0.0

                # Speed smoothing: keep last N speed samples
                speed_samples: list[float] = []

                with open(dest_path, "wb") as fh:
                    async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                        if not chunk:
                            break
                        fh.write(chunk)
                        downloaded += len(chunk)

                        now     = time()
                        elapsed = now - start_ts

                        # Smooth speed calculation
                        if elapsed > 0:
                            current_speed = downloaded / elapsed
                            speed_samples.append(current_speed)
                            if len(speed_samples) > 10:
                                speed_samples.pop(0)
                            speed = sum(speed_samples) / len(speed_samples)
                        else:
                            speed = 0

                        if now - last_edit >= PROGRESS_DELAY:
                            eta = (
                                int((total - downloaded) / speed)
                                if (speed > 0 and total > downloaded)
                                else 0
                            )
                            pct = (downloaded / total * 100) if total > 0 else 0
                            bar = _progress_bar(pct)

                            size_text = (
                                f"`{get_readable_file_size(downloaded)}`"
                                f" / `{get_readable_file_size(total)}`"
                                if total > 0
                                else f"`{get_readable_file_size(downloaded)}`"
                            )

                            # Dynamic percentage display
                            pct_text = f" {pct:.1f}%" if total > 0 else " --.--%"

                            try:
                                await status_msg.edit_text(
                                    f"⬇️ **ডাউনলোড হচ্ছে...**\n"
                                    f"┌─────────────────────────\n"
                                    f"│ `[{bar}]{pct_text}`\n"
                                    f"├─────────────────────────\n"
                                    f"│ {file_icon} `{display_name[:45]}`\n"
                                    f"│ 📥 **Downloaded:** {size_text}\n"
                                    f"│ ⚡ **Speed:**  `{get_readable_file_size(speed)}/s`\n"
                                    f"│ ⏳ **ETA:**    `{get_readable_time(eta) if eta else '...'}`\n"
                                    f"│ ⏱ **Elapsed:** `{get_readable_time(int(elapsed))}`\n"
                                    f"│ 🗂 **Type:**   `{content_type}`\n"
                                    f"└─────────────────────────",
                                    parse_mode=ParseMode.MARKDOWN,
                                )
                                last_edit = now
                            except Exception:
                                pass

        return True

    except asyncio.TimeoutError:
        LOGGER.warning(f"[DirectDL] Timeout: {url[:60]}")
        if attempt <= MAX_RETRIES:
            await status_msg.edit_text(
                f"⏰ **Download timeout!**\n"
                f"🔄 Retry `{attempt}/{MAX_RETRIES}`...",
                parse_mode=ParseMode.MARKDOWN,
            )
            await asyncio.sleep(RETRY_DELAY)
            return await _stream_download(
                url, dest_path, extra_headers,
                status_msg, display_name, max_size, attempt + 1,
            )
        await status_msg.edit_text(
            "❌ **Download timeout!** সার্ভার সাড়া দিচ্ছে না।",
            parse_mode=ParseMode.MARKDOWN,
        )
        return False

    except aiohttp.ClientError as e:
        LOGGER.error(f"[DirectDL] Network error {url[:60]}: {e}")
        if attempt <= MAX_RETRIES:
            await status_msg.edit_text(
                f"🌐 **Network error!** Retry `{attempt}/{MAX_RETRIES}`...\n"
                f"`{str(e)[:100]}`",
                parse_mode=ParseMode.MARKDOWN,
            )
            await asyncio.sleep(RETRY_DELAY)
            return await _stream_download(
                url, dest_path, extra_headers,
                status_msg, display_name, max_size, attempt + 1,
            )
        await status_msg.edit_text(
            f"❌ **Network error:**\n`{str(e)[:200]}`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return False


# ─────────────────────────────────────────────────────────────────────────────
# UPLOAD  (Pyrogram MTProto with live progress)
# ─────────────────────────────────────────────────────────────────────────────

_VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv", ".m4v", ".ts"}
_AUDIO_EXTS = {".mp3", ".flac", ".ogg", ".opus", ".m4a", ".wav", ".aac", ".wma"}
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


async def _upload_to_telegram(
    client: Client,
    chat_id: int,
    file_path: str,
    caption: str,
    status_msg: Message,
    start_ts: float,
    thumbnail_path: Optional[str] = None,
) -> None:
    """
    Upload *file_path* to Telegram (up to 2 GB via MTProto).
    Shows live upload progress bar in *status_msg*.
    """
    file_size = os.path.getsize(file_path)
    ext       = os.path.splitext(file_path)[1].lower()
    file_icon = _get_file_type_icon(file_path)
    last_edit = [0.0]
    up_start  = [time()]

    async def _on_progress(current: int, total: int) -> None:
        now = time()
        if now - last_edit[0] < PROGRESS_DELAY and current < total:
            return
        elapsed = now - up_start[0]
        speed   = current / elapsed if elapsed > 0 else 0
        eta     = (total - current) / speed if speed > 0 else 0
        pct     = (current / total * 100) if total > 0 else 0
        bar     = _progress_bar(pct)
        try:
            await status_msg.edit_text(
                f"📤 **Telegram-এ Upload হচ্ছে...**\n"
                f"┌─────────────────────────\n"
                f"│ `[{bar}]` {pct:.1f}%\n"
                f"├─────────────────────────\n"
                f"│ {file_icon} `{os.path.basename(file_path)[:45]}`\n"
                f"│ 📦 `{get_readable_file_size(current)}` / `{get_readable_file_size(total)}`\n"
                f"│ ⚡ **Speed:**  `{get_readable_file_size(speed)}/s`\n"
                f"│ ⏳ **ETA:**    `{get_readable_time(int(eta))}`\n"
                f"│ ⏱ **Elapsed:** `{get_readable_time(int(elapsed))}`\n"
                f"└─────────────────────────",
                parse_mode=ParseMode.MARKDOWN,
            )
            last_edit[0] = now
        except Exception:
            pass

    if ext in _VIDEO_EXTS:
        thumb = thumbnail_path
        if not thumb:
            try:
                thumb = await get_video_thumbnail(file_path, None)
            except Exception:
                thumb = None
        await client.send_video(
            chat_id=chat_id,
            video=file_path,
            caption=caption,
            thumb=thumb,
            supports_streaming=True,
            parse_mode=ParseMode.MARKDOWN,
            progress=_on_progress,
        )
        if thumb and thumb != thumbnail_path and os.path.exists(thumb):
            os.remove(thumb)

    elif ext in _AUDIO_EXTS:
        await client.send_audio(
            chat_id=chat_id,
            audio=file_path,
            caption=caption,
            thumb=thumbnail_path,
            parse_mode=ParseMode.MARKDOWN,
            progress=_on_progress,
        )

    elif ext in _IMAGE_EXTS:
        await client.send_photo(
            chat_id=chat_id,
            photo=file_path,
            caption=caption,
            parse_mode=ParseMode.MARKDOWN,
        )

    else:
        await client.send_document(
            chat_id=chat_id,
            document=file_path,
            caption=caption,
            thumb=thumbnail_path,
            parse_mode=ParseMode.MARKDOWN,
            progress=_on_progress,
        )

    elapsed = get_readable_time(int(time() - start_ts))
    await status_msg.edit_text(
        f"✅ **সফলভাবে পাঠানো হয়েছে!**\n\n"
        f"{file_icon} `{os.path.basename(file_path)[:60]}`\n"
        f"📦 `{get_readable_file_size(file_size)}`\n"
        f"⏱ মোট সময়: `{elapsed}`",
        parse_mode=ParseMode.MARKDOWN,
    )


# ─────────────────────────────────────────────────────────────────────────────
# SINGLE-FILE PIPELINE
# ─────────────────────────────────────────────────────────────────────────────

async def _download_single(
    client: Client,
    message: Message,
    direct_url: str,
    extra_headers: dict,
    status_msg: Message,
    user_dir: str,
    max_size: int,
    start_ts: float,
    original_url: str,
    thumbnail_path: Optional[str],
) -> None:
    """Download one file and upload it to Telegram."""
    chat_id   = message.chat.id
    user_id   = message.from_user.id
    file_name = _guess_filename_from_url(direct_url)
    file_icon = _get_file_type_icon(file_name)
    domain    = _get_domain(original_url)
    dest_path = os.path.join(user_dir, file_name)

    await status_msg.edit_text(
        f"⬇️ **Download শুরু হচ্ছে...**\n\n"
        f"{file_icon} `{file_name[:55]}`\n"
        f"🌐 Source: `{domain}`",
        parse_mode=ParseMode.MARKDOWN,
    )

    ok = await _stream_download(
        direct_url, dest_path, extra_headers,
        status_msg, file_name, max_size,
    )
    if not ok:
        await _log_activity(user_id, original_url, 0, "failed")
        return

    if not os.path.exists(dest_path) or os.path.getsize(dest_path) == 0:
        await status_msg.edit_text(
            "❌ **Downloaded ফাইল পাওয়া যায়নি বা খালি।**",
            parse_mode=ParseMode.MARKDOWN,
        )
        await _log_activity(user_id, original_url, 0, "empty")
        return

    file_sz = os.path.getsize(dest_path)

    await status_msg.edit_text(
        f"✅ **Download সম্পন্ন!** Upload হচ্ছে...\n\n"
        f"{file_icon} `{file_name[:55]}`\n"
        f"📦 `{get_readable_file_size(file_sz)}`",
        parse_mode=ParseMode.MARKDOWN,
    )

    caption = (
        f"{file_icon} **{file_name[:70]}**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 **Size:**   `{get_readable_file_size(file_sz)}`\n"
        f"🌐 **Source:** `{domain}`\n"
        f"🔗 **Link:**   `{original_url[:60]}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"_Downloaded via {BOT_CHANNEL}_"
    )

    await _upload_to_telegram(
        client, chat_id, dest_path,
        caption, status_msg, start_ts, thumbnail_path,
    )
    await _log_activity(user_id, original_url, file_sz, "success")


# ─────────────────────────────────────────────────────────────────────────────
# FOLDER / MULTI-FILE PIPELINE
# ─────────────────────────────────────────────────────────────────────────────

async def _download_folder(
    client: Client,
    message: Message,
    details: dict,
    status_msg: Message,
    user_dir: str,
    max_size: int,
    start_ts: float,
    thumbnail_path: Optional[str],
) -> None:
    """
    Download every file in a folder result dict and upload each to Telegram.

    details = {
        "title":       str,
        "total_size":  int,
        "contents":    [{"filename": str, "path": str, "url": str}, ...],
        "header":      str | list | None,
    }
    """
    chat_id  = message.chat.id
    user_id  = message.from_user.id
    title    = details.get("title", "Folder") or "Folder"
    contents = details.get("contents") or []
    total_sz = int(details.get("total_size") or 0)
    raw_hdr  = details.get("header")

    extra_headers = _parse_headers(raw_hdr)

    if not contents:
        await status_msg.edit_text(
            "❌ **Folder empty — কোনো file নেই।**",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if total_sz > 0 and total_sz > max_size:
        await status_msg.edit_text(
            f"❌ **Folder অনেক বড়!**\n\n"
            f"📁 `{title[:50]}`\n"
            f"📦 Size:  `{get_readable_file_size(total_sz)}`\n"
            f"🚫 Limit: `{get_readable_file_size(max_size)}`\n\n"
            f"💡 _Premium upgrade করলে 2 GB পর্যন্ত ডাউনলোড করা যাবে।_",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # File-type breakdown for info display
    type_counts: dict[str, int] = {}
    for item in contents:
        ext = os.path.splitext(item.get("filename") or "")[1].lower() or "other"
        type_counts[ext] = type_counts.get(ext, 0) + 1

    type_summary = "  ".join(
        f"`{ext}×{cnt}`" for ext, cnt in list(type_counts.items())[:5]
    )

    await status_msg.edit_text(
        f"📁 **{title[:55]}**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Files: `{len(contents)}`\n"
        f"📦 Total: `{get_readable_file_size(total_sz) if total_sz else 'অজানা'}`\n"
        f"🗂 Types: {type_summary}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        "⬇️ Download শুরু হচ্ছে...",
        parse_mode=ParseMode.MARKDOWN,
    )

    success  = 0
    failed   = 0
    total_up = 0

    for idx, item in enumerate(contents, start=1):
        item_url  = (item.get("url") or "").strip()
        item_name = _safe_filename(item.get("filename") or f"file_{idx}")
        item_sub  = item.get("path") or ""
        file_icon = _get_file_type_icon(item_name)

        if not item_url:
            LOGGER.warning(f"[DirectDL] Folder item {idx} has no URL, skipping.")
            failed += 1
            continue

        local_folder = os.path.join(user_dir, item_sub) if item_sub else user_dir
        os.makedirs(local_folder, exist_ok=True)
        dest = os.path.join(local_folder, item_name)

        await status_msg.edit_text(
            f"📁 **{title[:45]}**\n"
            f"┌─────────────────────────\n"
            f"│ 📊 Progress: `{idx}/{len(contents)}`\n"
            f"│ ✅ Done: `{success}` | ❌ Failed: `{failed}`\n"
            f"├─────────────────────────\n"
            f"│ {file_icon} `{item_name[:50]}`\n"
            f"└─────────────────────────",
            parse_mode=ParseMode.MARKDOWN,
        )

        ok = await _stream_download(
            item_url, dest, extra_headers,
            status_msg, item_name, max_size,
        )

        if not ok or not os.path.exists(dest) or os.path.getsize(dest) == 0:
            failed += 1
            await message.reply_text(
                f"⚠️ **Skipped:** {file_icon} `{item_name}`",
                parse_mode=ParseMode.MARKDOWN,
            )
            if os.path.exists(dest):
                os.remove(dest)
            continue

        item_sz  = os.path.getsize(dest)
        total_up += item_sz

        caption = (
            f"{file_icon} **{item_name[:70]}**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 **Size:** `{get_readable_file_size(item_sz)}`\n"
            f"📁 **Folder:** `{title[:40]}`"
            + (f"\n📂 **Sub:** `{item_sub[:40]}`" if item_sub else "") +
            f"\n📊 **File:** `{idx}/{len(contents)}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"_Downloaded via {BOT_CHANNEL}_"
        )

        try:
            await status_msg.edit_text(
                f"📤 **Upload হচ্ছে `{idx}/{len(contents)}`...**\n\n"
                f"{file_icon} `{item_name[:50]}`\n"
                f"📦 `{get_readable_file_size(item_sz)}`",
                parse_mode=ParseMode.MARKDOWN,
            )
            await _upload_to_telegram(
                client, chat_id, dest,
                caption, status_msg, start_ts, thumbnail_path,
            )
            success += 1

        except Exception as exc:
            LOGGER.error(f"[DirectDL] Upload failed '{item_name}': {exc}")
            failed += 1
            await message.reply_text(
                f"⚠️ **Upload failed:** `{item_name}`\n`{str(exc)[:100]}`",
                parse_mode=ParseMode.MARKDOWN,
            )
        finally:
            if os.path.exists(dest):
                os.remove(dest)

    # ── Final summary ─────────────────────────────────────────────────────────
    elapsed = get_readable_time(int(time() - start_ts))
    icon    = "✅" if failed == 0 else ("⚠️" if success > 0 else "❌")

    await status_msg.edit_text(
        f"{icon} **Folder Download সম্পন্ন!**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📁 `{title[:55]}`\n"
        f"✅ **Sent:**   `{success}` files\n"
        f"❌ **Failed:** `{failed}` files\n"
        f"📦 **Uploaded:** `{get_readable_file_size(total_up)}`\n"
        f"⏱ **Time:**   `{elapsed}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"_Powered by {BOT_CHANNEL}_",
        parse_mode=ParseMode.MARKDOWN,
    )
    await _log_activity(user_id, title, total_up, "folder_done")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN PIPELINE
# ─────────────────────────────────────────────────────────────────────────────

async def _process_ddl(
    client: Client,
    message: Message,
    url: str,
    status_msg: Message,
) -> None:
    """
    Complete pipeline for one /ddl request:
      1. Normalize + sanitize URL (fixes double-encoding)
      2. Resolve indirect link via direct_links.py
      3. Route to single-file or folder handler
      4. Clean up temp directory
    """
    user_id    = message.from_user.id
    is_premium = await _is_premium(user_id)
    max_size   = MAX_FILE_SIZE if is_premium else FREE_FILE_LIMIT
    start_ts   = time()
    plan_label = "👑 Premium" if is_premium else "👤 Free"

    user_dir = os.path.join(DOWNLOAD_DIR, str(user_id))
    os.makedirs(user_dir, exist_ok=True)

    thumbnail_path = await _get_user_thumbnail(user_id)

    # ── Fix double-encoded URLs (e.g. MediaFire Thai filename) ───────────────
    url          = _normalize_url(url)
    original_url = url
    domain       = _get_domain(url.split("::")[0])

    try:
        # ── Step 1: Resolve ───────────────────────────────────────────────────
        await status_msg.edit_text(
            f"🔍 **Link resolve করা হচ্ছে...**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🌐 **Site:**  `{domain}`\n"
            f"🔗 **URL:**   `{url[:60]}`\n"
            f"💼 **Plan:**  {plan_label} "
            f"(`{get_readable_file_size(max_size)}` limit)\n"
            f"━━━━━━━━━━━━━━━━━━━━",
            parse_mode=ParseMode.MARKDOWN,
        )

        try:
            result = await _resolve_link_async(url)
        except DirectLinkException as exc:
            err_msg = str(exc)
            await status_msg.edit_text(
                f"❌ **Link resolve ব্যর্থ!**\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🌐 **Site:** `{domain}`\n"
                f"⚠️ **Error:** `{err_msg[:300]}`\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"💡 _Link টি সঠিক ও accessible কিনা যাচাই করুন।_",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        except Exception as exc:
            LOGGER.error(f"[DirectDL] Resolve error user={user_id}: {exc}")
            await status_msg.edit_text(
                f"❌ **Unexpected resolve error:**\n`{str(exc)[:300]}`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        # ── Step 2: Unpack tuple (direct_url + headers) ──────────────────────
        extra_headers: dict = {}

        if isinstance(result, tuple) and len(result) == 2:
            direct_url, raw_header = result
            extra_headers          = _parse_headers(raw_header)
            result                 = direct_url

        # ── Step 3: Route ────────────────────────────────────────────────────
        if isinstance(result, dict):
            await _download_folder(
                client, message, result, status_msg,
                user_dir, max_size, start_ts, thumbnail_path,
            )
            return

        if not isinstance(result, str) or not result.startswith("http"):
            await status_msg.edit_text(
                "❌ **Valid direct link পাওয়া যায়নি।**\n\n"
                "🌐 Link টি সঠিক কিনা যাচাই করুন অথবা পরে আবার চেষ্টা করুন।",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        await _download_single(
            client, message, result, extra_headers,
            status_msg, user_dir, max_size,
            start_ts, original_url, thumbnail_path,
        )

    except Exception as exc:
        LOGGER.error(f"[DirectDL] Pipeline error user={user_id}: {exc}")
        try:
            await status_msg.edit_text(
                f"❌ **Unexpected error:**\n`{str(exc)[:300]}`",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass

    finally:
        try:
            shutil.rmtree(user_dir, ignore_errors=True)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# COMMAND HANDLER SETUP
# ─────────────────────────────────────────────────────────────────────────────

def setup_directdl_handler(app: Client) -> None:
    """Register /ddl and /directdl command handlers."""

    @app.on_message(
        filters.command(["ddl", "directdl"], prefixes=COMMAND_PREFIX)
        & (filters.private | filters.group)
    )
    async def ddl_command(client: Client, message: Message) -> None:
        """
        /ddl <URL>
        /ddl <URL>::<password>

        Resolves links from 40+ hosting sites and downloads files
        directly to Telegram (up to 2 GB via MTProto).
        """
        url = ""

        if len(message.command) > 1:
            url = " ".join(message.command[1:]).strip()
        elif message.reply_to_message:
            replied_text = (message.reply_to_message.text or "").strip()
            if replied_text:
                url = replied_text.split()[0]

        # ── Show help if no URL ───────────────────────────────────────────────
        if not url:
            await message.reply_text(
                SUPPORTED_SITES_TEXT,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True,
            )
            return

        # ── Normalize before validation (fixes double-encoding) ───────────────
        url           = _normalize_url(url)
        url_for_check = url.split("::")[0].strip()

        # ── Basic URL sanity check ────────────────────────────────────────────
        if not url_for_check.startswith(("http://", "https://")):
            await message.reply_text(
                "❌ **Invalid URL!**\n\n"
                "`http://` বা `https://` দিয়ে শুরু হওয়া URL দিন।",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        # ── Supported site check ──────────────────────────────────────────────
        if not is_supported_site(url_for_check):
            domain = _get_domain(url_for_check)
            await message.reply_text(
                f"⚠️ **`{domain}` supported নয়।**\n\n"
                "সমস্ত supported site দেখতে শুধু `/ddl` লিখুন।",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        LOGGER.info(
            f"[DirectDL] user={message.from_user.id} "
            f"site={_get_domain(url_for_check)} url={url_for_check[:80]}"
        )

        status_msg = await message.reply_text(
            "🔄 **Processing...**",
            parse_mode=ParseMode.MARKDOWN,
        )

        asyncio.create_task(
            _process_ddl(client, message, url, status_msg)
        )

    LOGGER.info(
        f"[DirectDL] v{BOT_VERSION} — /ddl & /directdl handlers registered."
    )
