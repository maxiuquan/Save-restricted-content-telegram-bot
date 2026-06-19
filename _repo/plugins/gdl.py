# Copyright @juktijol
# Channel t.me/juktijol
#
# plugins/gdl.py — Google Drive Downloader
#
# ✅ Works in 3 ways:
#    1. /gdl <url>          — Give URL directly
#    2. /gdl               — Bot asks for link, then downloads
#    3. reply + /gdl       — Reply to a message that has a Drive link
#
# ❌ Auto-detect without command is fully OFF
# ✅ NO user account / phone number required — BOT_TOKEN only
# ✅ Uses Pyrofork MTProto directly → up to 2 GB upload
# ✅ Downloads the file to disk from Google Drive, then uploads via MTProto
# ✅ Supports single files AND full folders (recursive)
# ✅ Real-time progress bar for both download and upload phases
# ✅ Cleans up temp files after every operation
# ✅ SMART UPLOAD — photo/video/audio/animation/document based on MIME type
# ✅ Fallback to document if specific media upload fails
# ✅ Professional tracking system → LOG_GROUP_ID
# ✅ Free user: 5-minute cooldown between downloads, NO folder download
# ✅ Premium user: unlimited downloads, folder support

import os
import re
import io
import json
import asyncio
import subprocess
from time import time
from datetime import datetime

from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode, MessageEntityType

from config import COMMAND_PREFIX, LOG_GROUP_ID
from utils import LOGGER
from core import prem_plan1, prem_plan2, prem_plan3, daily_limit

# ── Google API (service-account auth) ────────────────────────────────────────
try:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload
    from google.oauth2 import service_account
    GDRIVE_AVAILABLE = True
except ImportError:
    GDRIVE_AVAILABLE = False
    LOGGER.warning(
        "[GDL] google-api-python-client not installed — /gdl disabled."
    )

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

SERVICE_ACCOUNT_FILE  = "service_account_key.json"
DOWNLOAD_DIR          = "gdl_downloads"
MAX_FILE_SIZE_BYTES   = 2 * 1024 * 1024 * 1024   # 2 GB
PROGRESS_UPDATE_SEC   = 3
WAIT_FOR_URL_TIMEOUT  = 60
COOLDOWN_SECONDS      = 300   # 5 minutes for free users
DB_TIMEOUT            = 5.0

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
# PENDING USERS TRACKER
# ─────────────────────────────────────────────────────────────────────────────

_pending_url_requests: dict[tuple[int, int], Message] = {}

# ─────────────────────────────────────────────────────────────────────────────
# MEDIA TYPE CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
PHOTO_MIMES = {"image/jpeg", "image/png", "image/bmp", "image/webp"}
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm",
                    ".m4v", ".3gp", ".ts", ".mpg", ".mpeg", ".vob"}
AUDIO_EXTENSIONS = {".mp3", ".flac", ".wav", ".aac", ".ogg", ".m4a",
                    ".wma", ".opus", ".amr"}
ANIMATION_EXTENSIONS = {".gif"}
ANIMATION_MIMES = {"image/gif"}
PHOTO_SIZE_LIMIT = 10 * 1024 * 1024

# ─────────────────────────────────────────────────────────────────────────────
# DRIVE URL REGEX
# ─────────────────────────────────────────────────────────────────────────────

_DRIVE_URL_RE = re.compile(
    r"https?://(?:drive|docs)\.google\.com/\S+",
    re.IGNORECASE,
)

# ─────────────────────────────────────────────────────────────────────────────
# PREMIUM CHECK
# ─────────────────────────────────────────────────────────────────────────────

async def _is_premium(user_id: int) -> bool:
    now = datetime.utcnow()
    for col in [prem_plan1, prem_plan2, prem_plan3]:
        try:
            doc = await asyncio.wait_for(
                col.find_one({"user_id": user_id}),
                timeout=DB_TIMEOUT,
            )
            if doc and doc.get("expiry_date", now) > now:
                return True
        except Exception:
            pass
    return False


# ─────────────────────────────────────────────────────────────────────────────
# COOLDOWN HELPER (free users)
# ─────────────────────────────────────────────────────────────────────────────

async def _check_and_set_cooldown(user_id: int) -> int:
    """
    Returns remaining cooldown seconds (0 = can download now).
    Also sets/updates the last_download timestamp.
    """
    try:
        now = datetime.utcnow()
        record = await asyncio.wait_for(
            daily_limit.find_one({"user_id": user_id}),
            timeout=DB_TIMEOUT,
        )
        if record:
            last_dl = record.get("last_gdl_download")
            if last_dl:
                elapsed = (now - last_dl).total_seconds()
                if elapsed < COOLDOWN_SECONDS:
                    return int(COOLDOWN_SECONDS - elapsed)

        await asyncio.wait_for(
            daily_limit.update_one(
                {"user_id": user_id},
                {
                    "$set":  {"last_gdl_download": now},
                    "$inc":  {"total_downloads": 1},
                },
                upsert=True,
            ),
            timeout=DB_TIMEOUT,
        )
        return 0
    except Exception as e:
        LOGGER.warning(f"[GDL] Cooldown check error for {user_id}: {e}")
        return 0


# ─────────────────────────────────────────────────────────────────────────────
# PROFESSIONAL TRACKING SYSTEM
# ─────────────────────────────────────────────────────────────────────────────

async def _log_gdl_to_group(
    client: Client,
    user,
    drive_url: str,
    file_name: str,
    file_size: int,
    mime_type: str,
    is_folder: bool,
    status: str,           # "success" | "failed"
    error_msg: str = "",
    elapsed_sec: float = 0,
    is_premium: bool = False,
):
    """Send a professional GDL tracking log to LOG_GROUP_ID."""
    if not LOG_GROUP_ID:
        return

    try:
        user_id    = user.id if hasattr(user, "id") else "?"
        first_name = getattr(user, "first_name", "") or ""
        last_name  = getattr(user, "last_name",  "") or ""
        full_name  = f"{first_name} {last_name}".strip() or "Unknown"
        username   = f"@{user.username}" if getattr(user, "username", None) else "N/A"
        user_link  = f"[{full_name}](tg://user?id={user_id})"

        def _fmt_size(n):
            if not n:
                return "N/A"
            for unit in ("B", "KB", "MB", "GB"):
                if n < 1024:
                    return f"{n:.2f} {unit}"
                n /= 1024
            return f"{n:.2f} TB"

        def _fmt_time(s):
            s = int(s)
            if s < 60:
                return f"{s}s"
            m, sec = divmod(s, 60)
            if m < 60:
                return f"{m}m {sec}s"
            h, mn = divmod(m, 60)
            return f"{h}h {mn}m {sec}s"

        status_icon = "✅" if status == "success" else "❌"
        status_text = "Success" if status == "success" else "Failed"
        type_icon   = "📁" if is_folder else "📄"
        type_label  = "Folder" if is_folder else "File"
        plan_label  = "💎 Premium" if is_premium else "🆓 Free"

        text = (
            f"☁️ **GDrive Tracker** {status_icon}\n"
            f"{'─' * 30}\n\n"

            f"**👤 User Information**\n"
            f"• **Name:** {user_link}\n"
            f"• **Username:** `{username}`\n"
            f"• **User ID:** `{user_id}`\n"
            f"• **Plan:** {plan_label}\n\n"

            f"**{type_icon} File Information**\n"
            f"• **Name:** `{file_name[:80]}`\n"
            f"• **Type:** `{type_label}`\n"
            f"• **MIME:** `{mime_type or 'N/A'}`\n"
            f"• **Size:** `{_fmt_size(file_size)}`\n\n"

            f"**📥 Download Information**\n"
            f"• **Time Taken:** `{_fmt_time(elapsed_sec) if elapsed_sec > 0 else 'N/A'}`\n"
            f"• **Status:** `{status_text}`\n"
        )

        if status == "failed" and error_msg:
            text += f"• **Error:** `{error_msg[:150]}`\n"

        text += (
            f"\n**🔗 Drive Link**\n"
            f"`{drive_url[:200]}`"
        )

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("☁️ Open in Drive", url=drive_url)],
        ])

        await client.send_message(
            chat_id=LOG_GROUP_ID,
            text=text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )

    except Exception as e:
        LOGGER.warning(f"[GDLTracker] Failed to send log: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# DRIVE ID + FOLDER DETECTION
# ─────────────────────────────────────────────────────────────────────────────

def _extract_drive_id(url: str) -> str | None:
    patterns = [
        r"/file/d/([a-zA-Z0-9_-]{10,})",
        r"/folders/([a-zA-Z0-9_-]{10,})",
        r"[?&]id=([a-zA-Z0-9_-]{10,})",
        r"/open\?id=([a-zA-Z0-9_-]{10,})",
        r"/d/([a-zA-Z0-9_-]{10,})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def _is_folder_url(url: str) -> bool:
    return bool(re.search(r"/folders/", url, re.IGNORECASE))


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _readable_size(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"


def _readable_time(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours, mins = divmod(minutes, 60)
    return f"{hours}h {mins}m {secs}s"


def _progress_bar(pct: float, length: int = 20) -> str:
    filled = int(length * pct / 100)
    return "▓" * filled + "░" * (length - filled)


def _clean_url(url: str) -> str:
    return re.sub(r"[)>\].,;!?\"']+$", "", url.strip())


# ─────────────────────────────────────────────────────────────────────────────
# DRIVE URL EXTRACTOR
# ─────────────────────────────────────────────────────────────────────────────

def _extract_all_drive_urls(message: Message) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []

    def _add(url: str) -> None:
        url = _clean_url(url)
        if not url:
            return
        if url in seen:
            return
        if "drive.google.com" in url or "docs.google.com" in url:
            seen.add(url)
            urls.append(url)

    entities = []
    if message.entities:
        entities.extend(message.entities)
    if message.caption_entities:
        entities.extend(message.caption_entities)

    text = message.text or message.caption or ""

    for entity in entities:
        if entity.type == MessageEntityType.TEXT_LINK and entity.url:
            _add(entity.url)

    for entity in entities:
        if entity.type == MessageEntityType.URL:
            chunk = text[entity.offset: entity.offset + entity.length]
            _add(chunk)

    for match in _DRIVE_URL_RE.finditer(text):
        _add(match.group(0))

    return urls


# ─────────────────────────────────────────────────────────────────────────────
# MEDIA TYPE DETECTION + METADATA
# ─────────────────────────────────────────────────────────────────────────────

def _detect_media_type(file_path: str, mime_type: str = "") -> str:
    ext = os.path.splitext(file_path)[1].lower()

    if ext in ANIMATION_EXTENSIONS or mime_type in ANIMATION_MIMES:
        return "animation"
    if ext in PHOTO_EXTENSIONS or mime_type in PHOTO_MIMES:
        try:
            if os.path.getsize(file_path) <= PHOTO_SIZE_LIMIT:
                return "photo"
        except OSError:
            pass
        return "document"
    if ext in VIDEO_EXTENSIONS or mime_type.startswith("video/"):
        return "video"
    if ext in AUDIO_EXTENSIONS or mime_type.startswith("audio/"):
        return "audio"
    return "document"


def _get_video_metadata(file_path: str) -> dict:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", file_path],
            capture_output=True, text=True, timeout=30,
        )
        data     = json.loads(result.stdout)
        duration = int(float(data.get("format", {}).get("duration", 0)))
        width = height = 0
        for stream in data.get("streams", []):
            if stream.get("codec_type") == "video":
                width  = int(stream.get("width",  0))
                height = int(stream.get("height", 0))
                if not duration:
                    duration = int(float(stream.get("duration", 0)))
                break
        return {"duration": duration, "width": width, "height": height}
    except Exception:
        return {"duration": 0, "width": 0, "height": 0}


def _get_audio_duration(file_path: str) -> int:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", file_path],
            capture_output=True, text=True, timeout=30,
        )
        data = json.loads(result.stdout)
        return int(float(data.get("format", {}).get("duration", 0)))
    except Exception:
        return 0


def _generate_thumbnail(file_path: str) -> str | None:
    thumb = file_path + "_thumb.jpg"
    try:
        subprocess.run(
            ["ffmpeg", "-i", file_path, "-ss", "00:00:01",
             "-vframes", "1", "-vf", "scale=320:-1", "-y", thumb],
            capture_output=True, timeout=30,
        )
        if os.path.exists(thumb) and os.path.getsize(thumb) > 0:
            return thumb
    except Exception:
        pass
    try:
        if os.path.exists(thumb):
            os.remove(thumb)
    except OSError:
        pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# GOOGLE DRIVE UTILITIES
# ─────────────────────────────────────────────────────────────────────────────

def _build_drive_service():
    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        raise FileNotFoundError(
            f"Service account key not found: {SERVICE_ACCOUNT_FILE}\n"
            "Please place the Google service account JSON in the project root."
        )
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=["https://www.googleapis.com/auth/drive.readonly"],
    )
    return build("drive", "v3", credentials=creds)


def _get_file_metadata(service, file_id: str) -> dict:
    return service.files().get(
        fileId=file_id,
        fields="id,name,mimeType,size",
        supportsAllDrives=True,
    ).execute()


def _list_folder_recursive(service, folder_id: str, parent_path: str = "") -> list[dict]:
    results: list[dict] = []
    query      = f"'{folder_id}' in parents and trashed=false"
    page_token = None

    while True:
        response = service.files().list(
            q=query,
            fields="nextPageToken, files(id, name, mimeType, size)",
            pageToken=page_token,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()

        for item in response.get("files", []):
            current_path = (
                os.path.join(parent_path, item["name"])
                if parent_path else item["name"]
            )
            if item.get("mimeType") == "application/vnd.google-apps.folder":
                results.extend(
                    _list_folder_recursive(service, item["id"], current_path)
                )
            else:
                item["relative_path"] = current_path
                results.append(item)

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return results


def _is_google_doc(mime_type: str) -> tuple[bool, str, str]:
    export_map = {
        "application/vnd.google-apps.document": (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".docx",
        ),
        "application/vnd.google-apps.spreadsheet": (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".xlsx",
        ),
        "application/vnd.google-apps.presentation": (
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            ".pptx",
        ),
        "application/vnd.google-apps.drawing": ("image/png", ".png"),
    }
    if mime_type in export_map:
        export_mime, ext = export_map[mime_type]
        return True, export_mime, ext
    return False, "", ""


# ─────────────────────────────────────────────────────────────────────────────
# DOWNLOAD A SINGLE FILE FROM DRIVE → DISK
# ─────────────────────────────────────────────────────────────────────────────

async def _download_drive_file(
    service,
    file_id: str,
    file_name: str,
    mime_type: str,
    local_path: str,
    status_msg: Message,
) -> tuple[str, str]:
    is_doc, export_mime, ext = _is_google_doc(mime_type)
    effective_mime = mime_type

    if is_doc:
        if not file_name.endswith(ext):
            file_name += ext
        local_path     = os.path.splitext(local_path)[0] + ext
        request        = service.files().export_media(fileId=file_id, mimeType=export_mime)
        effective_mime = export_mime
    else:
        request = service.files().get_media(fileId=file_id, supportsAllDrives=True)

    os.makedirs(os.path.dirname(local_path), exist_ok=True)

    downloader = MediaIoBaseDownload(
        io.FileIO(local_path, "wb"), request, chunksize=8 * 1024 * 1024,
    )

    start_ts  = time()
    last_edit = 0.0
    done      = False

    while not done:
        status, done = await asyncio.get_event_loop().run_in_executor(
            None, downloader.next_chunk,
        )
        pct = status.progress() * 100
        now = time()

        if now - last_edit >= PROGRESS_UPDATE_SEC or done:
            elapsed    = now - start_ts
            downloaded = status.resumable_progress
            speed      = downloaded / elapsed if elapsed > 0 else 0

            try:
                await status_msg.edit_text(
                    f"📥 **⚡ Downloading from Google Drive…**\n\n"
                    f"`[{_progress_bar(pct)}]` {pct:.1f}%\n\n"
                    f"📦 **Downloaded:** `{_readable_size(downloaded)}`\n"
                    f"⚡ **Speed:** `{_readable_size(speed)}/s`\n"
                    f"⏱ **Elapsed:** `{_readable_time(elapsed)}`\n\n"
                    f"📄 `{file_name}`",
                    parse_mode=ParseMode.MARKDOWN,
                )
                last_edit = now
            except Exception:
                pass

    return local_path, effective_mime


# ─────────────────────────────────────────────────────────────────────────────
# UPLOAD A LOCAL FILE → TELEGRAM
# ─────────────────────────────────────────────────────────────────────────────

async def _upload_to_telegram(
    client: Client,
    chat_id: int,
    local_path: str,
    file_name: str,
    caption: str,
    status_msg: Message,
    mime_type: str = "",
) -> bool:
    media_type = _detect_media_type(local_path, mime_type)
    start_ts   = [time()]
    last_edit  = [0.0]

    LOGGER.info(
        f"[GDL] Uploading '{file_name}' as {media_type} "
        f"(mime={mime_type}, ext={os.path.splitext(file_name)[1]})"
    )

    async def _progress(current: int, total: int):
        now = time()
        if now - last_edit[0] < PROGRESS_UPDATE_SEC and current < total:
            return
        elapsed = now - start_ts[0]
        speed   = current / elapsed if elapsed > 0 else 0
        eta     = (total - current) / speed if speed > 0 else 0
        pct     = (current / total * 100) if total > 0 else 0
        try:
            await status_msg.edit_text(
                f"📤 **⚡ Uploading to Telegram…** `[{media_type}]`\n\n"
                f"`[{_progress_bar(pct)}]` {pct:.1f}%\n\n"
                f"📦 `{_readable_size(current)}` / `{_readable_size(total)}`\n"
                f"⚡ `{_readable_size(speed)}/s`  "
                f"⏳ ETA `{_readable_time(eta)}`\n\n"
                f"📄 `{file_name}`",
                parse_mode=ParseMode.MARKDOWN,
            )
            last_edit[0] = now
        except Exception:
            pass

    thumb_path = None

    async def _send(mtype: str) -> None:
        nonlocal thumb_path
        if mtype == "photo":
            await client.send_photo(
                chat_id=chat_id, photo=local_path, caption=caption,
                parse_mode=ParseMode.MARKDOWN, progress=_progress,
            )
        elif mtype == "video":
            vmeta      = _get_video_metadata(local_path)
            thumb_path = _generate_thumbnail(local_path)
            kwargs     = dict(
                chat_id=chat_id, video=local_path, caption=caption,
                file_name=file_name, supports_streaming=True,
                parse_mode=ParseMode.MARKDOWN, progress=_progress,
            )
            if vmeta["duration"]: kwargs["duration"] = vmeta["duration"]
            if vmeta["width"]:    kwargs["width"]    = vmeta["width"]
            if vmeta["height"]:   kwargs["height"]   = vmeta["height"]
            if thumb_path:        kwargs["thumb"]    = thumb_path
            await client.send_video(**kwargs)
        elif mtype == "audio":
            dur    = _get_audio_duration(local_path)
            kwargs = dict(
                chat_id=chat_id, audio=local_path, caption=caption,
                file_name=file_name, parse_mode=ParseMode.MARKDOWN, progress=_progress,
            )
            if dur: kwargs["duration"] = dur
            await client.send_audio(**kwargs)
        elif mtype == "animation":
            await client.send_animation(
                chat_id=chat_id, animation=local_path, caption=caption,
                parse_mode=ParseMode.MARKDOWN, progress=_progress,
            )
        else:
            await client.send_document(
                chat_id=chat_id, document=local_path, file_name=file_name,
                caption=caption, parse_mode=ParseMode.MARKDOWN, progress=_progress,
            )

    try:
        await _send(media_type)
        return True
    except Exception as e:
        LOGGER.warning(f"[GDL] Upload as '{media_type}' failed for '{file_name}': {e}")
        if media_type != "document":
            try:
                LOGGER.info(f"[GDL] Falling back to 'document' for '{file_name}'")
                start_ts[0]  = time()
                last_edit[0] = 0.0
                await _send("document")
                return True
            except Exception as e2:
                LOGGER.error(f"[GDL] Fallback upload also failed: {e2}")
        return False
    finally:
        if thumb_path and os.path.exists(thumb_path):
            try:
                os.remove(thumb_path)
            except OSError:
                pass


# ─────────────────────────────────────────────────────────────────────────────
# CORE PIPELINE
# ─────────────────────────────────────────────────────────────────────────────

async def _process_gdl(client: Client, message: Message, url: str):
    """Full pipeline: validate → metadata → download → upload → track → cleanup"""

    user_id = (
        message.from_user.id if message.from_user
        else message.sender_chat.id if message.sender_chat
        else message.chat.id
    )
    chat_id    = message.chat.id
    user       = message.from_user
    overall_start = time()

    if not GDRIVE_AVAILABLE:
        await message.reply_text(
            "❌ **Google Drive support is not available.**\n\n"
            "⚡ Please install the required packages:\n"
            "`pip install google-api-python-client google-auth-oauthlib`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    is_folder  = _is_folder_url(url)
    is_premium = await _is_premium(user_id)

    # ── Free user: block folder downloads ───────────────────────────────────
    if is_folder and not is_premium:
        await message.reply_text(
            "❌ **Folder download is for Premium users only!**\n\n"
            "Folders can contain many files which requires premium access.\n\n"
            "💎 Upgrade to premium: /plans",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # ── Free user: cooldown check ────────────────────────────────────────────
    if not is_premium:
        remaining = await _check_and_set_cooldown(user_id)
        if remaining > 0:
            mins, secs = divmod(remaining, 60)
            await message.reply_text(
                f"**⏳ Please wait {mins}m {secs}s before your next download.**\n\n"
                f"__Upgrade to premium for instant unlimited downloads: /plans__",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
    else:
        # Premium: just increment counter
        try:
            await asyncio.wait_for(
                daily_limit.update_one(
                    {"user_id": user_id},
                    {"$inc": {"total_downloads": 1}},
                    upsert=True,
                ),
                timeout=DB_TIMEOUT,
            )
        except Exception:
            pass

    # ── Extract Drive ID ──────────────────────────────────────────────────────
    file_id = _extract_drive_id(url)
    if not file_id:
        LOGGER.error(f"[GDL] Could not extract ID from URL: {url}")
        await message.reply_text(
            "❌ **Could not find the Google Drive ID.**\n\n"
            "Supported formats:\n"
            "• `https://drive.google.com/file/d/<ID>/view`\n"
            "• `https://drive.google.com/folders/<ID>`\n"
            "• `https://drive.google.com/drive/folders/<ID>`\n"
            "• `https://drive.google.com/open?id=<ID>`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    LOGGER.info(
        f"[GDL] Resolved — ID: {file_id} | "
        f"Type: {'folder' if is_folder else 'file'} | URL: {url}"
    )

    # ── Build Drive service ───────────────────────────────────────────────────
    try:
        service = await asyncio.get_event_loop().run_in_executor(
            None, _build_drive_service,
        )
    except FileNotFoundError as e:
        await message.reply_text(
            f"❌ **Service account key not found!**\n\n`{e}`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    except Exception as e:
        await message.reply_text(
            f"❌ **Could not connect to Google Drive.**\n\n`{e}`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    status_msg = await message.reply_text(
        "🔍 **⚡ Getting file info from Google Drive…**",
        parse_mode=ParseMode.MARKDOWN,
    )

    try:
        if is_folder:
            # ── FOLDER MODE (premium only — already checked above) ───────────
            folder_meta = await asyncio.get_event_loop().run_in_executor(
                None, _get_file_metadata, service, file_id,
            )
            folder_name = folder_meta.get("name", "Untitled Folder")

            await status_msg.edit_text(
                f"📁 **Scanning folder:** `{folder_name}`\n"
                "⚡ Please wait…",
                parse_mode=ParseMode.MARKDOWN,
            )

            files = await asyncio.get_event_loop().run_in_executor(
                None, _list_folder_recursive, service, file_id, folder_name,
            )

            if not files:
                await status_msg.edit_text(
                    f"🤷 **Folder `{folder_name}` is empty.**",
                    parse_mode=ParseMode.MARKDOWN,
                )
                asyncio.create_task(_log_gdl_to_group(
                    client, user, url, folder_name, 0, "folder",
                    True, "failed", "Folder is empty",
                    time() - overall_start, is_premium,
                ))
                return

            total_size = sum(int(f.get("size", 0)) for f in files)
            if total_size > MAX_FILE_SIZE_BYTES:
                await status_msg.edit_text(
                    f"❌ **Folder is too big to send.**\n\n"
                    f"Total size: `{_readable_size(total_size)}`\n"
                    f"Limit: `{_readable_size(MAX_FILE_SIZE_BYTES)}`",
                    parse_mode=ParseMode.MARKDOWN,
                )
                asyncio.create_task(_log_gdl_to_group(
                    client, user, url, folder_name, total_size, "folder",
                    True, "failed", "Folder too large",
                    time() - overall_start, is_premium,
                ))
                return

            await status_msg.edit_text(
                f"📁 **Folder:** `{folder_name}`\n"
                f"📊 **Files:** `{len(files)}`\n"
                f"📦 **Total size:** `{_readable_size(total_size)}`\n\n"
                "⚡ Starting download…",
                parse_mode=ParseMode.MARKDOWN,
            )

            success_count = 0
            fail_count    = 0

            for idx, item in enumerate(files, 1):
                item_name     = item["name"]
                item_id       = item["id"]
                item_mime     = item.get("mimeType", "")
                item_size     = int(item.get("size", 0))
                relative_path = item.get("relative_path", item_name)
                local_path    = os.path.join(
                    DOWNLOAD_DIR, str(user_id), relative_path,
                )

                await status_msg.edit_text(
                    f"📁 **{folder_name}**\n"
                    f"📊 Progress: `{idx}/{len(files)}`\n"
                    f"📄 Current: `{item_name}`\n"
                    f"📦 Size: `{_readable_size(item_size)}`",
                    parse_mode=ParseMode.MARKDOWN,
                )

                try:
                    local_path, eff_mime = await _download_drive_file(
                        service, item_id, item_name,
                        item_mime, local_path, status_msg,
                    )

                    caption = (
                        f"📄 **{os.path.basename(local_path)}**\n"
                        f"📁 Path: `{relative_path}`\n"
                        f"📦 Size: `{_readable_size(os.path.getsize(local_path))}`\n"
                        f"🔗 [Google Drive]({url})\n\n"
                        f"__Downloaded by @juktijol Bot__"
                    )

                    ok = await _upload_to_telegram(
                        client, chat_id, local_path,
                        os.path.basename(local_path), caption,
                        status_msg, mime_type=eff_mime,
                    )
                    if ok:
                        success_count += 1
                    else:
                        fail_count += 1

                except Exception as item_err:
                    LOGGER.error(f"[GDL] Failed to process '{item_name}': {item_err}")
                    fail_count += 1
                    await message.reply_text(
                        f"⚠️ **Skipped:** `{item_name}`\n`{str(item_err)[:150]}`",
                        parse_mode=ParseMode.MARKDOWN,
                    )
                finally:
                    if os.path.exists(local_path):
                        try:
                            os.remove(local_path)
                        except OSError:
                            pass

            elapsed = time() - overall_start
            final_status = "success" if fail_count == 0 else ("failed" if success_count == 0 else "success")

            await status_msg.edit_text(
                f"✅ **Folder download done!**\n\n"
                f"📁 `{folder_name}`\n"
                f"✅ Success: `{success_count}`\n"
                f"❌ Failed: `{fail_count}`",
                parse_mode=ParseMode.MARKDOWN,
            )

            # ── Tracking log ─────────────────────────────────────────────
            asyncio.create_task(_log_gdl_to_group(
                client, user, url, folder_name, total_size, "folder",
                True, final_status, "" if success_count > 0 else "All files failed",
                elapsed, is_premium,
            ))

        else:
            # ── SINGLE FILE MODE ──────────────────────────────────────────
            meta      = await asyncio.get_event_loop().run_in_executor(
                None, _get_file_metadata, service, file_id,
            )
            file_name = meta.get("name", "downloaded_file")
            mime_type = meta.get("mimeType", "application/octet-stream")
            file_size = int(meta.get("size", 0))

            is_doc, _, _ = _is_google_doc(mime_type)
            if not is_doc and file_size > MAX_FILE_SIZE_BYTES:
                await status_msg.edit_text(
                    f"❌ **File is too big to send to Telegram.**\n\n"
                    f"Size: `{_readable_size(file_size)}`\n"
                    f"Limit: `{_readable_size(MAX_FILE_SIZE_BYTES)}`",
                    parse_mode=ParseMode.MARKDOWN,
                )
                asyncio.create_task(_log_gdl_to_group(
                    client, user, url, file_name, file_size, mime_type,
                    False, "failed", "File too large",
                    time() - overall_start, is_premium,
                ))
                return

            await status_msg.edit_text(
                f"📄 **{file_name}**\n"
                f"📦 Size: `{_readable_size(file_size)}`\n\n"
                "⚡ Downloading from Google Drive…",
                parse_mode=ParseMode.MARKDOWN,
            )

            local_path = os.path.join(DOWNLOAD_DIR, str(user_id), file_name)
            os.makedirs(os.path.dirname(local_path), exist_ok=True)

            upload_ok = False
            try:
                local_path, eff_mime = await _download_drive_file(
                    service, file_id, file_name,
                    mime_type, local_path, status_msg,
                )

                actual_size = os.path.getsize(local_path)

                if actual_size > MAX_FILE_SIZE_BYTES:
                    await status_msg.edit_text(
                        f"❌ **Exported file is too big:** `{_readable_size(actual_size)}`",
                        parse_mode=ParseMode.MARKDOWN,
                    )
                    asyncio.create_task(_log_gdl_to_group(
                        client, user, url, file_name, actual_size, eff_mime,
                        False, "failed", "Exported file too large",
                        time() - overall_start, is_premium,
                    ))
                    return

                caption = (
                    f"📄 **{os.path.basename(local_path)}**\n"
                    f"📦 Size: `{_readable_size(actual_size)}`\n"
                    f"🔗 [Google Drive]({url})\n\n"
                    f"__Downloaded by @juktijol Bot__"
                )

                upload_ok = await _upload_to_telegram(
                    client, chat_id, local_path,
                    os.path.basename(local_path), caption,
                    status_msg, mime_type=eff_mime,
                )

                detected = _detect_media_type(local_path, eff_mime)

                if upload_ok:
                    await status_msg.edit_text(
                        f"✅ **Done!** `{os.path.basename(local_path)}`\n"
                        f"📦 `{_readable_size(actual_size)}` — sent as **{detected}**",
                        parse_mode=ParseMode.MARKDOWN,
                    )
                else:
                    await status_msg.edit_text(
                        "❌ **Upload to Telegram failed.** Please try again.",
                        parse_mode=ParseMode.MARKDOWN,
                    )

                # ── Tracking log ──────────────────────────────────────────
                asyncio.create_task(_log_gdl_to_group(
                    client, user, url,
                    os.path.basename(local_path),
                    actual_size, eff_mime,
                    False,
                    "success" if upload_ok else "failed",
                    "" if upload_ok else "Upload failed",
                    time() - overall_start,
                    is_premium,
                ))

            finally:
                if os.path.exists(local_path):
                    try:
                        os.remove(local_path)
                    except OSError:
                        pass

    except Exception as e:
        LOGGER.error(f"[GDL] Unhandled error: {e}")
        # ── Tracking log for unexpected error ─────────────────────────────
        asyncio.create_task(_log_gdl_to_group(
            client, user if user else message.from_user,
            url, "Unknown", 0, "",
            is_folder, "failed", str(e)[:200],
            time() - overall_start, is_premium,
        ))
        try:
            await status_msg.edit_text(
                f"❌ **Something went wrong:**\n`{str(e)[:300]}`",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# USAGE MESSAGES
# ─────────────────────────────────────────────────────────────────────────────

_USAGE_TEXT = """**📥 ⚡ Google Drive Downloader**
━━━━━━━━━━━━━━━━━━

You can use it in **3 easy ways:**

**Method 1 — Give the link directly:**
`/gdl https://drive.google.com/file/d/<ID>/view`

**Method 2 — Just send the command:**
Type `/gdl` and I will ask you for the link.
__(I'll wait 60 seconds for your input)__

**Method 3 — Reply to a message:**
Forward a message that has a Drive link,
then reply to it with `/gdl`

━━━━━━━━━━━━━━━━━━
**Supported links:**
• `drive.google.com/file/d/<ID>/view`
• `drive.google.com/folders/<ID>`
• `drive.google.com/drive/folders/<ID>`
• `drive.google.com/open?id=<ID>`
• `docs.google.com/document/d/<ID>`

**Features:**
• Max: `2 GB`
• Google Docs → Office format auto-convert
• Smart upload: video/audio/photo/animation/doc
• Full folder download __(💎 Premium only)__

**⚠️ Notes:**
• 🆓 Free users: 5-minute cooldown between downloads
• 🆓 Free users: folder download **not available**
• 💎 Premium users: unlimited, folders supported"""

_ASK_URL_TEXT = """📎 **⚡ Send me the Google Drive link!**

⏳ I'll wait `{timeout}` seconds for your link.

❌ To cancel, type `/cancel`."""


# ─────────────────────────────────────────────────────────────────────────────
# HANDLER SETUP
# ─────────────────────────────────────────────────────────────────────────────

def setup_gdl_handler(app: Client):
    """
    Registers all handlers.

    ⚠️  IMPORTANT: Auto-detect is fully OFF.
        Only works via the /gdl command.

    Three methods:
    ① /gdl <url>          — inline URL
    ② /gdl               — bot asks for URL, user sends it
    ③ reply + /gdl        — reply to any message with a Drive link
    """

    # ════════════════════════════════════════════════════════════════════════
    # HANDLER ①②③ — /gdl command (all methods in one handler)
    # ════════════════════════════════════════════════════════════════════════

    @app.on_message(
        filters.command("gdl", prefixes=COMMAND_PREFIX)
        & (filters.private | filters.group)
    )
    async def gdl_command(client: Client, message: Message):
        user_id = (
            message.from_user.id if message.from_user
            else message.chat.id
        )
        chat_id = message.chat.id

        # ─── cancel any existing pending request, start fresh ────────────
        pending_key = (chat_id, user_id)
        if pending_key in _pending_url_requests:
            old_status = _pending_url_requests.pop(pending_key)
            try:
                await old_status.edit_text(
                    "🔄 **Got a new /gdl command. Cancelled the old one.**",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass

        url = None

        # ── Method ①: /gdl <url> ─────────────────────────────────────────
        if len(message.command) >= 2:
            candidate = " ".join(message.command[1:]).strip()
            urls_in_command = []
            for match in _DRIVE_URL_RE.finditer(candidate):
                urls_in_command.append(_clean_url(match.group(0)))
            if urls_in_command:
                url = urls_in_command[0]
            elif "drive.google.com" in candidate or "docs.google.com" in candidate:
                url = _clean_url(candidate)

        # ── also try entities from this message ───────────────────────────
        if not url:
            found = _extract_all_drive_urls(message)
            if found:
                url = found[0]

        # ── Method ③: get URL from replied message ────────────────────────
        if not url and message.reply_to_message:
            found = _extract_all_drive_urls(message.reply_to_message)
            if found:
                url = found[0]
                LOGGER.info(f"[GDL] URL found in replied message: {url}")

        # ── if URL found, process directly ────────────────────────────────
        if url:
            if "drive.google.com" not in url and "docs.google.com" not in url:
                await message.reply_text(
                    "❌ **This doesn't look like a Google Drive link.**\n\n"
                    "Please send a valid `drive.google.com` URL.",
                    parse_mode=ParseMode.MARKDOWN,
                )
                return

            LOGGER.info(f"[GDL] /gdl — user {user_id} — URL: {url}")
            await _process_gdl(client, message, url)
            return

        # ── Method ②: no URL → first send full usage guide, then ask ─────

        # Message 1: Full usage guide
        await message.reply_text(
            _USAGE_TEXT,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )

        # Message 2: Ask for the link
        ask_msg = await message.reply_text(
            _ASK_URL_TEXT.format(timeout=WAIT_FOR_URL_TIMEOUT),
            parse_mode=ParseMode.MARKDOWN,
        )

        LOGGER.info(f"[GDL] /gdl — user {user_id} — asking for URL")

        # register as pending
        _pending_url_requests[pending_key] = ask_msg

        # remove from pending after timeout
        async def _timeout_cleanup():
            await asyncio.sleep(WAIT_FOR_URL_TIMEOUT)
            if pending_key in _pending_url_requests:
                _pending_url_requests.pop(pending_key)
                try:
                    await ask_msg.edit_text(
                        f"⏰ **Time's up!** No link received in "
                        f"`{WAIT_FOR_URL_TIMEOUT}` seconds.\n\n"
                        "Try again with `/gdl`.",
                        parse_mode=ParseMode.MARKDOWN,
                    )
                except Exception:
                    pass

        asyncio.create_task(_timeout_cleanup())

    # ════════════════════════════════════════════════════════════════════════
    # HANDLER — Pending URL receiver
    # ════════════════════════════════════════════════════════════════════════

    @app.on_message(
        (filters.private | filters.group)
        & ~filters.command(
            ["gdl", "cancel", "start", "help"],
            prefixes=COMMAND_PREFIX,
        ),
        group=10,
    )
    async def pending_url_receiver(client: Client, message: Message):
        user_id = (
            message.from_user.id if message.from_user
            else message.chat.id
        )
        chat_id = message.chat.id
        key     = (chat_id, user_id)

        if key not in _pending_url_requests:
            return

        ask_msg = _pending_url_requests.pop(key)

        urls = _extract_all_drive_urls(message)

        if not urls:
            raw = (message.text or message.caption or "").strip()
            if "drive.google.com" in raw or "docs.google.com" in raw:
                cleaned = _clean_url(raw)
                if cleaned:
                    urls = [cleaned]

        if not urls:
            _pending_url_requests[key] = ask_msg
            await message.reply_text(
                "❌ **This doesn't look like a Google Drive link.**\n\n"
                "Please send a valid Drive URL.\n"
                "Example:\n"
                "`https://drive.google.com/file/d/<ID>/view`\n\n"
                "❌ To cancel, type `/cancel`.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        url = urls[0]
        LOGGER.info(f"[GDL] Pending URL received — user {user_id}: {url}")

        try:
            await ask_msg.edit_text(
                f"✅ **Got the link!** ⚡ Starting now…\n\n"
                f"🔗 `{url}`",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass

        await _process_gdl(client, message, url)

    # ════════════════════════════════════════════════════════════════════════
    # HANDLER — /cancel
    # ════════════════════════════════════════════════════════════════════════

    @app.on_message(
        filters.command("cancel", prefixes=COMMAND_PREFIX)
        & (filters.private | filters.group)
    )
    async def cancel_pending(client: Client, message: Message):
        user_id = (
            message.from_user.id if message.from_user
            else message.chat.id
        )
        key = (message.chat.id, user_id)

        if key in _pending_url_requests:
            ask_msg = _pending_url_requests.pop(key)
            try:
                await ask_msg.edit_text(
                    "❌ **Cancelled.**\n\nType `/gdl` to start again.",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass
            await message.reply_text(
                "✅ **GDL request cancelled.**",
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await message.reply_text(
                "ℹ️ **No active GDL request found.**",
                parse_mode=ParseMode.MARKDOWN,
            )

    LOGGER.info(
        "[GDL] Handler registered — /gdl command only mode active.\n"
        "       Auto-detect: DISABLED ✗\n"
        "       Method ①: /gdl <url> ✓\n"
        "       Method ②: /gdl → sends usage guide + asks for URL ✓\n"
        "       Method ③: reply to message + /gdl ✓\n"
        "       Tracking: LOG_GROUP_ID ✓\n"
        "       Free cooldown: 5 min ✓\n"
        "       Folder: Premium only ✓"
    )
