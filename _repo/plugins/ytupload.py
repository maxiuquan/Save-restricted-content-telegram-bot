# Copyright @juktijol
# Channel t.me/juktijol

"""
Multi-User YouTube Direct Upload Handler
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FIXES APPLIED:
  ✅ FIX 1: redirect_uri → "http://localhost" রাখা হয়েছে কিন্তু
             yt_credentials.json এ "Desktop App" type ব্যবহার করতে হবে
  ✅ FIX 2: fetch_token() এ authorization_response ভুল parameter সরানো হয়েছে
  ✅ FIX 3: User-কে browser URL bar থেকে full URL বা শুধু code
             যেকোনোটা paste করতে বলা হয়েছে
  ✅ FIX 4: OAUTHLIB_INSECURE_TRANSPORT env set করা হয়েছে
  ✅ FIX 5: yt_credentials.json এ "installed" type check যোগ করা হয়েছে
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Features:
  • Per-user YouTube Channel OAuth — each user uploads to their own channel
  • Mode 1: Telegram Video Reply  → YouTube Upload
  • Mode 2: Any Website URL       → yt-dlp download → YouTube Upload
  • Free users  : 1 upload at a time, 5-min cooldown between uploads
  • Premium users: unlimited simultaneous uploads, 10-sec cooldown
  • Privacy control : public / private / unlisted
  • Custom title & description support
  • Referer / HLS / CDN protected stream support
  • Professional tracking & logging (matches ytdl.py style)
  • Full in-bot tutorial via /ythelp
Commands:
  /ytconnect              — Connect your YouTube Channel
  /ytcode <code>          — Submit OAuth authorization code
  /ytdisconnect           — Disconnect your YouTube Channel
  /ytme                   — View connected channel info
  /ythelp                 — Full tutorial & guide
  /ytupload <url>         — Upload from any website URL
  /ytupload <url> --private
  /ytupload <url> --unlisted
  /ytupload <url> --title "Custom Title"
  /ytupload <url> referer:<site>
  [Reply to video] /ytsend           — Upload Telegram video to YouTube
  [Reply to video] /ytsend --private
  [Reply to video] /ytsend --title "Custom Title"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import os
import re
import json
import asyncio
import tempfile
from io import BytesIO
from time import time
from datetime import datetime

# ✅ FIX: OAUTHLIB_INSECURE_TRANSPORT set করা — http://localhost allow করার জন্য
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

from pyrogram import Client, filters
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)
from pyrogram.enums import ParseMode
from pyrogram.handlers import MessageHandler

from config import COMMAND_PREFIX, LOG_GROUP_ID
from utils.logging_setup import LOGGER
from utils.helper import get_readable_file_size, get_readable_time
from core import daily_limit, prem_plan1, prem_plan2, prem_plan3

# ── Shared helpers from ytdl.py ───────────────────────────────────────────────
from plugins.ytdl import (
    download_single_video,
    get_single_video_info,
    parse_url_and_referer,
    is_hls_url,
    is_protected_cdn_url,
    cleanup_stale_files,
    _is_warp_available,
    _friendly_error,
    _make_progress_bar,
    _ytdl_progress_updater,
    DOWNLOAD_DIR,
    MAX_FILE_SIZE,
)

try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import Flow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    GOOGLE_API_AVAILABLE = True
except ImportError:
    GOOGLE_API_AVAILABLE = False
    LOGGER.error(
        "[ytupload] Google API library not found!\n"
        "Run: pip install google-api-python-client "
        "google-auth-oauthlib google-auth-httplib2"
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CONSTANTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SCOPES           = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]
CREDENTIALS_FILE = "yt_credentials.json"

# ✅ FIX: redirect_uri → http://localhost (Desktop App type-এ এটাই সঠিক)
# Google Cloud Console-এ "Desktop App" type credential তৈরি করলে
# http://localhost automatically allowed থাকে
REDIRECT_URI = "http://localhost"

YT_TITLE_MAX    = 100
YT_DESC_MAX     = 5000
YT_TAG_MAX      = 10
YT_CHUNK_SIZE   = 5 * 1024 * 1024

SESSION_EXPIRY  = 900

PRIVACY_OPTIONS = ["public", "private", "unlisted"]
PRIVACY_LABELS  = {
    "public":   "🌐 Public",
    "private":  "🔒 Private",
    "unlisted": "🔗 Unlisted",
}
PRIVACY_FROM_CB = {"pub": "public", "prv": "private", "unl": "unlisted"}

FREE_COOLDOWN    = 300
PREMIUM_COOLDOWN = 10

# ── MongoDB Collections ───────────────────────────────────────────────────────
yt_tokens_col  = daily_limit.database["yt_user_tokens"]
yt_uploads_col = daily_limit.database["yt_upload_logs"]

# ── In-memory session stores ──────────────────────────────────────────────────
ytup_sessions:        dict = {}
oauth_sessions:       dict = {}
user_last_upload:     dict = {}
active_uploads_free:  set  = set()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# RATE LIMIT CHECKER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _check_upload_rate_limit(user_id: int, is_premium: bool) -> tuple:
    cooldown = PREMIUM_COOLDOWN if is_premium else FREE_COOLDOWN

    if not is_premium and user_id in active_uploads_free:
        return False, (
            "⏳ **Upload in Progress!**\n\n"
            "You already have an upload running.\n"
            "Please wait for it to finish before starting a new one.\n\n"
            "⚡ Upgrade to Premium for unlimited simultaneous uploads → /plans"
        )

    last_time = user_last_upload.get(user_id, 0)
    elapsed   = time() - last_time

    if elapsed < cooldown:
        remaining = int(cooldown - elapsed)
        wait_str  = get_readable_time(remaining)

        if is_premium:
            return False, f"⏳ Please wait **{wait_str}** before starting another upload."
        else:
            return False, (
                f"⏳ **Cooldown Active!**\n\n"
                f"Free users must wait **{get_readable_time(FREE_COOLDOWN)}** between uploads.\n"
                f"**Time remaining:** `{wait_str}`\n\n"
                f"⚡ Upgrade to Premium for faster uploads → /plans"
            )

    return True, ""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PREMIUM CHECK
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _is_premium(user_id: int) -> bool:
    now = datetime.utcnow()
    for col in [prem_plan1, prem_plan2, prem_plan3]:
        plan = await col.find_one({"user_id": user_id})
        if plan and plan.get("expiry_date", now) > now:
            return True
    return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MONGODB — TOKEN MANAGER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _save_user_token(user_id: int, creds, channel_info: dict):
    now = datetime.utcnow()
    await yt_tokens_col.update_one(
        {"user_id": user_id},
        {
            "$set": {
                "token":        creds.to_json(),
                "channel_id":   channel_info.get("id", ""),
                "channel_name": channel_info.get("title", "Unknown"),
                "updated_at":   now,
            },
            "$setOnInsert": {"connected_at": now},
        },
        upsert=True,
    )
    LOGGER.info(f"[ytupload] Token saved for user {user_id}")


async def _load_user_token(user_id: int):
    if not GOOGLE_API_AVAILABLE:
        return None

    rec = await yt_tokens_col.find_one({"user_id": user_id})
    if not rec or not rec.get("token"):
        return None

    try:
        creds = Credentials.from_authorized_user_info(
            json.loads(rec["token"]), SCOPES
        )
    except Exception as e:
        LOGGER.warning(f"[ytupload] Token parse error for {user_id}: {e}")
        return None

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                await yt_tokens_col.update_one(
                    {"user_id": user_id},
                    {"$set": {"token": creds.to_json(), "updated_at": datetime.utcnow()}},
                )
                LOGGER.info(f"[ytupload] Token auto-refreshed for user {user_id}")
            except Exception as e:
                LOGGER.warning(f"[ytupload] Token refresh failed for {user_id}: {e}")
                return None
        else:
            return None

    return creds


async def _delete_user_token(user_id: int) -> bool:
    result = await yt_tokens_col.delete_one({"user_id": user_id})
    return result.deleted_count > 0


async def _get_user_channel_info(user_id: int):
    return await yt_tokens_col.find_one(
        {"user_id": user_id},
        {"channel_id": 1, "channel_name": 1, "connected_at": 1, "updated_at": 1},
    )


async def _is_youtube_connected(user_id: int) -> bool:
    creds = await _load_user_token(user_id)
    return creds is not None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ✅ FIX: OAUTH FLOW — সম্পূর্ণ নতুন ও সঠিক implementation
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _validate_credentials_file() -> tuple:
    """
    yt_credentials.json validate করে।
    Returns: (is_valid: bool, error_message: str)
    
    ✅ yt_credentials.json অবশ্যই "installed" type হতে হবে (Desktop App)।
    "web" type হলে redirect_uri mismatch error হবে।
    """
    if not os.path.exists(CREDENTIALS_FILE):
        return False, (
            f"❌ `{CREDENTIALS_FILE}` ফাইলটি পাওয়া যাচ্ছে না!\n\n"
            "**সমাধান:**\n"
            "1️⃣ [Google Cloud Console](https://console.cloud.google.com/) যান\n"
            "2️⃣ APIs & Services → Credentials\n"
            "3️⃣ **`+ CREATE CREDENTIALS`** → **`OAuth client ID`**\n"
            "4️⃣ Application type: **`Desktop App`** বেছে নিন\n"
            "5️⃣ JSON download করে বটের folder-এ `yt_credentials.json` নামে রাখুন"
        )
    
    try:
        with open(CREDENTIALS_FILE, "r") as f:
            data = json.load(f)
        
        # ✅ FIX: "installed" key check — Desktop App type verify করা
        if "installed" not in data:
            if "web" in data:
                return False, (
                    "❌ **ভুল Credential Type!**\n\n"
                    "`yt_credentials.json` এ **Web Application** type credential আছে।\n"
                    "এটা **Desktop App** type হতে হবে।\n\n"
                    "**সমাধান:**\n"
                    "1️⃣ [Google Cloud Console](https://console.cloud.google.com/) যান\n"
                    "2️⃣ APIs & Services → Credentials\n"
                    "3️⃣ নতুন credential তৈরি করুন: **`Desktop App`** type\n"
                    "4️⃣ JSON download করে replace করুন"
                )
            return False, (
                "❌ **Invalid Credential Format!**\n\n"
                "`yt_credentials.json` এর format সঠিক নয়।\n"
                "Google Cloud Console থেকে নতুন করে download করুন।"
            )
        
        return True, ""
    except json.JSONDecodeError:
        return False, "❌ `yt_credentials.json` valid JSON নয়। ফাইলটি corrupt হয়েছে।"
    except Exception as e:
        return False, f"❌ Credentials file error: `{str(e)[:100]}`"


def _create_oauth_flow():
    """
    ✅ FIX: Correct OAuth Flow তৈরি করা।
    - redirect_uri = "http://localhost" (Desktop App type-এ automatically allowed)
    - OAUTHLIB_INSECURE_TRANSPORT = "1" set করা হয়েছে উপরে
    """
    is_valid, err_msg = _validate_credentials_file()
    if not is_valid:
        LOGGER.error(f"[ytupload] Credentials validation failed: {err_msg}")
        return None, err_msg
    
    try:
        flow = Flow.from_client_secrets_file(
            CREDENTIALS_FILE,
            scopes=SCOPES,
            redirect_uri=REDIRECT_URI,  # "http://localhost"
        )
        return flow, ""
    except Exception as e:
        err = f"OAuth flow তৈরিতে error: `{str(e)[:150]}`"
        LOGGER.error(f"[ytupload] OAuth flow error: {e}")
        return None, err


def _fetch_channel_info_sync(creds) -> dict:
    try:
        youtube  = build("youtube", "v3", credentials=creds)
        response = youtube.channels().list(part="snippet,statistics", mine=True).execute()
        items    = response.get("items", [])
        if not items:
            return {"id": "", "title": "Unknown Channel"}
        item = items[0]
        return {
            "id":          item.get("id", ""),
            "title":       item["snippet"].get("title", "Unknown"),
            "subscribers": int(item.get("statistics", {}).get("subscriberCount", 0) or 0),
        }
    except Exception as e:
        LOGGER.warning(f"[ytupload] Channel info fetch error: {e}")
        return {"id": "", "title": "Unknown Channel"}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# YOUTUBE UPLOAD CORE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _upload_file_to_youtube_sync(
    creds,
    filepath: str,
    title: str,
    description: str = "",
    tags: list = None,
    privacy_status: str = "public",
    category_id: str = "22",
    progress_cb=None,
) -> tuple:
    youtube = build("youtube", "v3", credentials=creds)

    title       = (title or "Untitled")[:YT_TITLE_MAX]
    description = (description or "")[:YT_DESC_MAX]
    tags        = (tags or [])[:YT_TAG_MAX]

    body = {
        "snippet": {
            "title":       title,
            "description": description,
            "tags":        tags,
            "categoryId":  category_id,
        },
        "status": {
            "privacyStatus":           privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }

    try:
        media   = MediaFileUpload(filepath, mimetype="video/mp4",
                                  resumable=True, chunksize=YT_CHUNK_SIZE)
        request = youtube.videos().insert(
            part=",".join(body.keys()), body=body, media_body=media
        )
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status and progress_cb:
                try:
                    progress_cb(status.resumable_progress, status.total_size)
                except Exception:
                    pass

        video_id = response.get("id", "")
        if not video_id:
            return False, "YouTube response did not contain a video ID."

        LOGGER.info(f"[ytupload] Upload success → https://youtu.be/{video_id}")
        return True, video_id

    except Exception as e:
        err = str(e)
        LOGGER.error(f"[ytupload] Upload error: {err}")
        if "quotaExceeded" in err:
            return False, "📊 YouTube API quota exceeded. Please try again tomorrow."
        if "forbidden" in err.lower() or "403" in err:
            return False, "🔒 YouTube permission denied. Please use /ytdisconnect then /ytconnect."
        if "uploadLimitExceeded" in err:
            return False, "🚫 YouTube daily upload limit reached."
        return False, f"YouTube API error: {err[:200]}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# INPUT PARSER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _parse_upload_flags(raw: str) -> dict:
    raw = raw.strip()

    privacy = "public"
    for flag, val in [("--private", "private"), ("--unlisted", "unlisted"), ("--public", "public")]:
        if flag in raw:
            privacy = val
            raw     = raw.replace(flag, "").strip()

    custom_title = None
    for pattern in [r'--title\s+"([^"]+)"', r"--title\s+'([^']+)'"]:
        m = re.search(pattern, raw, re.IGNORECASE)
        if m:
            custom_title = m.group(1).strip()
            raw          = (raw[:m.start()] + raw[m.end():]).strip()
            break

    url, referer = parse_url_and_referer(raw) if raw else (None, None)

    return {"url": url, "referer": referer, "privacy": privacy, "custom_title": custom_title}


def _parse_ytsend_flags(raw: str) -> dict:
    parsed = _parse_upload_flags(raw)
    return {"privacy": parsed["privacy"], "custom_title": parsed["custom_title"]}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TRACKING — MongoDB Upload Log
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _save_upload_log(
    user_id: int,
    user_name: str,
    source_type: str,
    source_url: str,
    yt_video_id: str,
    yt_title: str,
    privacy: str,
    file_size: int,
    status: str,
    error_msg: str = "",
    channel_id: str = "",
    channel_name: str = "",
    elapsed_sec: float = 0,
    is_premium: bool = False,
):
    try:
        await yt_uploads_col.insert_one({
            "user_id":      user_id,
            "user_name":    user_name,
            "source_type":  source_type,
            "source_url":   (source_url or "")[:500],
            "yt_video_id":  yt_video_id,
            "yt_url":       f"https://youtu.be/{yt_video_id}" if yt_video_id else "",
            "yt_title":     (yt_title or "")[:200],
            "channel_id":   channel_id,
            "channel_name": channel_name,
            "privacy":      privacy,
            "file_size":    file_size,
            "status":       status,
            "error_msg":    (error_msg or "")[:500],
            "elapsed_sec":  round(elapsed_sec, 2),
            "is_premium":   is_premium,
            "uploaded_at":  datetime.utcnow(),
        })
    except Exception as e:
        LOGGER.warning(f"[ytupload log] DB save error: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TRACKING — Log Group Notifier
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _log_to_group(
    client,
    user,
    source_type: str,
    source_url: str,
    video_title: str,
    yt_video_id: str,
    channel_name: str,
    privacy: str,
    file_size: int,
    status: str,
    elapsed_sec: float = 0,
    error_msg: str = "",
    is_premium: bool = False,
):
    if not LOG_GROUP_ID:
        return
    try:
        user_id   = getattr(user, "id", "?")
        fname     = getattr(user, "first_name", "") or ""
        lname     = getattr(user, "last_name",  "") or ""
        full_name = f"{fname} {lname}".strip() or "Unknown"
        username  = f"@{user.username}" if getattr(user, "username", None) else "N/A"
        user_link = f"[{full_name}](tg://user?id={user_id})"

        status_icon  = "✅" if status == "success" else "❌"
        status_text  = "Success"  if status == "success" else "Failed"
        src_icon     = "📨 Telegram" if source_type == "telegram" else "🌐 URL"
        plan_badge   = "⭐ Premium" if is_premium else "🆓 Free"
        elapsed_str  = get_readable_time(int(elapsed_sec)) if elapsed_sec > 0 else "N/A"
        size_str     = get_readable_file_size(file_size) if file_size > 0 else "N/A"
        yt_url       = f"https://youtu.be/{yt_video_id}" if yt_video_id else "N/A"

        text = (
            f"📤 **YT Upload Tracker** {status_icon}\n"
            f"{'─' * 30}\n\n"
            f"**👤 User Information**\n"
            f"• **Name:** {user_link}\n"
            f"• **Username:** `{username}`\n"
            f"• **User ID:** `{user_id}`\n"
            f"• **Plan:** `{plan_badge}`\n\n"
            f"**📺 Channel Information**\n"
            f"• **YouTube Channel:** `{channel_name}`\n\n"
            f"**📤 Upload Information**\n"
            f"• **Source:** `{src_icon}`\n"
            f"• **Title:** `{video_title[:80]}`\n"
            f"• **Privacy:** `{PRIVACY_LABELS.get(privacy, privacy)}`\n"
            f"• **File Size:** `{size_str}`\n"
            f"• **Time Taken:** `{elapsed_str}`\n"
            f"• **Status:** `{status_text}`\n"
        )

        if status == "failed" and error_msg:
            text += f"• **Error:** `{error_msg[:150]}`\n"

        if yt_url != "N/A":
            text += f"\n**🔗 YouTube Link**\n`{yt_url}`"

        text += f"\n\n**📎 Source**\n`{(source_url or 'N/A')[:150]}`"

        buttons = []
        if yt_url != "N/A":
            buttons.append([InlineKeyboardButton("▶️ Watch on YouTube", url=yt_url)])

        await client.send_message(
            chat_id=LOG_GROUP_ID,
            text=text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
            disable_web_page_preview=True,
        )
    except Exception as e:
        LOGGER.warning(f"[ytupload tracker] Log group notify failed: {e}")


async def _log_failed_attempt(
    client,
    user,
    source_type: str,
    source_url: str,
    error_msg: str,
    is_premium: bool = False,
):
    if not LOG_GROUP_ID:
        return
    try:
        user_id    = getattr(user, "id", "?")
        fname      = getattr(user, "first_name", "") or ""
        lname      = getattr(user, "last_name",  "") or ""
        full_name  = f"{fname} {lname}".strip() or "Unknown"
        username   = f"@{user.username}" if getattr(user, "username", None) else "N/A"
        user_link  = f"[{full_name}](tg://user?id={user_id})"
        plan_badge = "⭐ Premium" if is_premium else "🆓 Free"
        src_icon   = "📨 Telegram" if source_type == "telegram" else "🌐 URL"

        text = (
            f"📤 **YT Upload Tracker** ❌\n"
            f"{'─' * 30}\n\n"
            f"**👤 User Information**\n"
            f"• **Name:** {user_link}\n"
            f"• **Username:** `{username}`\n"
            f"• **User ID:** `{user_id}`\n"
            f"• **Plan:** `{plan_badge}`\n\n"
            f"**📤 Upload Information**\n"
            f"• **Source:** `{src_icon}`\n"
            f"• **Status:** `Failed`\n"
            f"• **Error:** `{error_msg[:200]}`\n\n"
            f"**📎 Source URL**\n`{(source_url or 'N/A')[:200]}`"
        )
        await client.send_message(
            chat_id=LOG_GROUP_ID,
            text=text,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )
    except Exception as e:
        LOGGER.warning(f"[ytupload tracker] Failed attempt log error: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PROGRESS UPDATER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _yt_upload_progress_updater(msg, progress_data: dict):
    last_text = ""
    while not progress_data.get("done"):
        await asyncio.sleep(4)
        if progress_data.get("done"):
            break
        uploaded = progress_data.get("uploaded", 0)
        total    = progress_data.get("total",    0)
        pct      = min((uploaded / total) * 100, 100) if total > 0 else 0
        pbar     = _make_progress_bar(pct)
        text = (
            f"📤 **Uploading to YouTube...**\n\n"
            f"`{pbar}`\n"
            f"**{pct:.1f}%** | "
            f"{get_readable_file_size(uploaded)}/{get_readable_file_size(total)}"
        )
        if text != last_text:
            try:
                await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)
                last_text = text
            except Exception:
                pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# KEYBOARD BUILDERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _privacy_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🌐 Public",   callback_data=f"ytup_pub_{chat_id}"),
            InlineKeyboardButton("🔒 Private",  callback_data=f"ytup_prv_{chat_id}"),
            InlineKeyboardButton("🔗 Unlisted", callback_data=f"ytup_unl_{chat_id}"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data=f"ytup_cancel_{chat_id}")],
    ])


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# UTILITY FUNCTIONS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _cleanup_dir(dirpath: str):
    try:
        if os.path.isdir(dirpath) and not os.listdir(dirpath):
            os.rmdir(dirpath)
    except Exception:
        pass


def _user_display(user) -> str:
    fname = getattr(user, "first_name", "") or ""
    lname = getattr(user, "last_name",  "") or ""
    name  = f"{fname} {lname}".strip() or "Unknown"
    uname = getattr(user, "username", None)
    return f"{name} (@{uname})" if uname else name


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TUTORIAL TEXT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# ✅ FIX: Help text update — সঠিক নির্দেশনা যোগ করা হয়েছে
HELP_TEXT = """
📺 **YouTube Upload — Complete Guide**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Upload videos directly to your own YouTube Channel!

─────────────────────────────────────
🔐 **STEP 1 — Connect Your YouTube Channel**

1️⃣  Send: `/ytconnect`
2️⃣  Open the Google Login Link in your browser
3️⃣  Sign in and tap **Allow**
4️⃣  Browser-এ `localhost refused to connect` দেখাবে — এটা স্বাভাবিক!
5️⃣  Browser-এর **URL bar** থেকে পুরো URL টা copy করুন
     (যেমন: `http://localhost/?code=4/0AX...&scope=...`)
6️⃣  সেই URL বা শুধু code বটে পাঠান:
     `/ytcode http://localhost/?code=4/0AX...`
     অথবা শুধু `/ytcode 4/0AX...`

─────────────────────────────────────
🌐 **STEP 2A — Upload from any Website URL**

`/ytupload VIDEO_URL`
`/ytupload VIDEO_URL --private`
`/ytupload VIDEO_URL --unlisted`
`/ytupload VIDEO_URL --title "My Title"`
`/ytupload VIDEO_URL referer:https://the-website.com`

─────────────────────────────────────
📨 **STEP 2B — Upload a Telegram Video**

Reply to the video and send: `/ytsend`
`/ytsend --private`
`/ytsend --title "My Title"`

─────────────────────────────────────
📋 **Other Commands**

`/ytme`          — View connected channel info
`/ytdisconnect`  — Disconnect your YouTube channel
`/ythelp`        — Show this guide again

─────────────────────────────────────
⚠️ **Admin Setup (একবারই করতে হবে)**

`yt_credentials.json` তৈরির নিয়ম:
• Google Cloud Console → APIs & Services → Credentials
• `+ CREATE CREDENTIALS` → `OAuth client ID`
• Application type: **Desktop App** (Web নয়!)
• JSON download করে বটের folder-এ রাখুন

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""".strip()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MAIN SETUP FUNCTION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def setup_ytupload_handler(app: Client):

    # ═══════════════════════════════════════════════════════════
    # /ythelp
    # ═══════════════════════════════════════════════════════════

    async def ythelp_command(client: Client, message: Message):
        await message.reply_text(
            HELP_TEXT,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Connect YouTube Channel", callback_data="ytup_goto_connect")],
                [InlineKeyboardButton("📊 My Channel Info", callback_data="ytup_goto_me")],
            ]),
            disable_web_page_preview=True,
        )

    # ═══════════════════════════════════════════════════════════
    # /ytconnect — ✅ FIX: সম্পূর্ণ নতুন implementation
    # ═══════════════════════════════════════════════════════════

    async def ytconnect_command(client: Client, message: Message):
        user_id = message.from_user.id

        if not GOOGLE_API_AVAILABLE:
            await message.reply_text(
                "❌ **Google API library is not installed!**\n\n"
                "Admin কে বলুন:\n"
                "`pip install google-api-python-client google-auth-oauthlib google-auth-httplib2`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        # ✅ FIX: credentials file validate করা
        is_valid, err_msg = _validate_credentials_file()
        if not is_valid:
            await message.reply_text(
                err_msg,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True,
            )
            return

        existing = await _get_user_channel_info(user_id)
        if existing and existing.get("channel_name"):
            await message.reply_text(
                f"✅ **Already Connected!**\n\n"
                f"📺 **Channel:** `{existing['channel_name']}`\n\n"
                f"To connect a different channel, first use /ytdisconnect.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        # ✅ FIX: flow তৈরি করা ও error handle করা
        flow, flow_err = _create_oauth_flow()
        if not flow:
            await message.reply_text(
                f"❌ **OAuth Setup Error!**\n\n{flow_err}",
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True,
            )
            return

        auth_url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
        )

        oauth_sessions[user_id] = {"flow": flow, "created_at": time()}

        # ✅ FIX: User-কে সঠিক নির্দেশনা দেওয়া হচ্ছে
        # localhost refused হবে — এটা expected, URL থেকে code copy করতে হবে
        await message.reply_text(
            "🔐 **Connect Your YouTube Channel**\n\n"
            "**Step 1** — নিচের link-এ click করুন:\n"
            f"[👉 Sign in with Google]({auth_url})\n\n"
            "**Step 2** — Sign in করে **Allow** tap করুন\n\n"
            "**Step 3** — Browser-এ এরকম দেখাবে:\n"
            "```\nThis site can't be reached\nlocalhost refused to connect\n```\n"
            "⚠️ **এটা স্বাভাবিক! ভয় পাবেন না।**\n\n"
            "**Step 4** — Browser-এর **🔗 URL bar** থেকে\n"
            "পুরো URL টা copy করুন:\n"
            "`http://localhost/?code=4/0AX4XfW...&scope=...`\n\n"
            "**Step 5** — সেই URL বটে পাঠান:\n"
            "`/ytcode http://localhost/?code=4/0AX4XfW...`\n\n"
            "_(শুধু code-ও দিতে পারেন)_\n\n"
            "⏳ _এই session ১৫ মিনিট পর expire হবে।_",
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )

    # ═══════════════════════════════════════════════════════════
    # /ytcode — ✅ FIX: সম্পূর্ণ নতুন fetch_token implementation
    # ═══════════════════════════════════════════════════════════

    async def ytcode_command(client: Client, message: Message):
        user_id = message.from_user.id

        if len(message.command) < 2:
            await message.reply_text(
                "**Usage:** `/ytcode YOUR_CODE_OR_URL`\n\n"
                "First start with /ytconnect.\n\n"
                "**Example:**\n"
                "`/ytcode http://localhost/?code=4/0AX4XfW...`\n"
                "অথবা\n"
                "`/ytcode 4/0AX4XfW...`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        session = oauth_sessions.get(user_id)
        if not session:
            await message.reply_text(
                "❌ **No active session found.**\n\n"
                "Please start again with /ytconnect.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        if time() - session["created_at"] > SESSION_EXPIRY:
            oauth_sessions.pop(user_id, None)
            await message.reply_text(
                "⏰ **Session expired!**\n\nPlease use /ytconnect to start again.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        # ✅ FIX: user full URL বা শুধু code যেকোনোটা paste করতে পারবে
        raw_input = " ".join(message.command[1:]).strip()
        
        # Full URL থেকে code extract করার চেষ্টা
        code_match = re.search(r'[?&]code=([^&\s]+)', raw_input)
        if code_match:
            auth_code = code_match.group(1).strip()
            # URL-encoded characters decode করা
            auth_code = auth_code.replace("%2F", "/").replace("%3D", "=")
        else:
            # শুধু code দিয়েছে
            auth_code = raw_input.strip()

        if not auth_code:
            await message.reply_text(
                "❌ **Code খুঁজে পাওয়া যাচ্ছে না!**\n\n"
                "Browser URL bar থেকে পুরো URL copy করে পাঠান।\n"
                "Example: `/ytcode http://localhost/?code=4/0AX4XfW...`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        flow       = session["flow"]
        status_msg = await message.reply_text(
            "🔄 **Code verify করা হচ্ছে...**",
            parse_mode=ParseMode.MARKDOWN,
        )

        try:
            loop = asyncio.get_event_loop()
            
            # ✅ FIX: সঠিকভাবে fetch_token call করা
            # শুধু code= parameter দেওয়া হচ্ছে, authorization_response নয়
            # এটাই Desktop App flow-এর সঠিক পদ্ধতি
            await loop.run_in_executor(
                None,
                lambda: flow.fetch_token(code=auth_code)
            )
            creds = flow.credentials

            await status_msg.edit_text(
                "📺 **Channel info fetch করা হচ্ছে...**",
                parse_mode=ParseMode.MARKDOWN,
            )
            channel_info = await loop.run_in_executor(
                None, lambda: _fetch_channel_info_sync(creds)
            )

            await _save_user_token(user_id, creds, channel_info)
            oauth_sessions.pop(user_id, None)

            ch_name = channel_info.get("title", "Unknown")
            ch_id   = channel_info.get("id", "")
            ch_url  = f"https://youtube.com/channel/{ch_id}" if ch_id else ""

            await status_msg.edit_text(
                f"✅ **YouTube Connected Successfully!**\n\n"
                f"📺 **Channel:** `{ch_name}`\n"
                f"{('🔗 ' + ch_url) if ch_url else ''}\n\n"
                f"এখন আপনি use করতে পারবেন:\n"
                f"• `/ytupload <url>` — যেকোনো website থেকে upload\n"
                f"• Video reply করে `/ytsend` — Telegram থেকে upload",
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True,
            )

        except Exception as e:
            oauth_sessions.pop(user_id, None)
            err_str = str(e)
            LOGGER.error(f"[ytupload] OAuth exchange error for {user_id}: {err_str}")
            
            # ✅ FIX: User-friendly error messages
            if "invalid_grant" in err_str.lower():
                err_detail = (
                    "Code টি invalid বা expired হয়ে গেছে।\n\n"
                    "• Code একবারই use করা যায়\n"
                    "• /ytconnect দিয়ে নতুন link নিন\n"
                    "• Link open করার পরপরই code copy করুন"
                )
            elif "redirect_uri_mismatch" in err_str.lower():
                err_detail = (
                    "**Redirect URI Mismatch!**\n\n"
                    "`yt_credentials.json` এ **Desktop App** type credential নেই।\n"
                    "Google Cloud Console থেকে **Desktop App** type credential তৈরি করুন।"
                )
            elif "invalid_client" in err_str.lower():
                err_detail = (
                    "**Invalid Client!**\n\n"
                    "`yt_credentials.json` এর client_id বা client_secret ভুল।\n"
                    "Google Cloud Console থেকে নতুন করে download করুন।"
                )
            else:
                err_detail = f"`{err_str[:200]}`"
            
            await status_msg.edit_text(
                f"❌ **Code Verification Failed!**\n\n"
                f"{err_detail}\n\n"
                f"/ytconnect দিয়ে আবার চেষ্টা করুন।",
                parse_mode=ParseMode.MARKDOWN,
            )

    # ═══════════════════════════════════════════════════════════
    # /ytdisconnect
    # ═══════════════════════════════════════════════════════════

    async def ytdisconnect_command(client: Client, message: Message):
        user_id = message.from_user.id
        deleted = await _delete_user_token(user_id)

        if deleted:
            await message.reply_text(
                "✅ **YouTube Disconnected!**\n\n"
                "Your token has been removed.\n"
                "To connect again: /ytconnect",
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await message.reply_text(
                "ℹ️ **You were not connected.**\n\n"
                "Use /ytconnect to link your channel.",
                parse_mode=ParseMode.MARKDOWN,
            )

    # ═══════════════════════════════════════════════════════════
    # /ytme
    # ═══════════════════════════════════════════════════════════

    async def ytme_command(client: Client, message: Message):
        user_id    = message.from_user.id
        info       = await _get_user_channel_info(user_id)
        is_premium = await _is_premium(user_id)

        if not info or not info.get("channel_name"):
            await message.reply_text(
                "❌ **No YouTube channel connected.**\n\n"
                "Use /ytconnect to link your channel.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        creds      = await _load_user_token(user_id)
        token_ok   = creds is not None
        ch_name    = info.get("channel_name", "Unknown")
        ch_id      = info.get("channel_id",   "")
        ch_url     = f"https://youtube.com/channel/{ch_id}" if ch_id else "N/A"
        conn_at    = info.get("connected_at")
        conn_str   = conn_at.strftime("%d %b %Y, %H:%M UTC") if conn_at else "N/A"

        total_ok   = await yt_uploads_col.count_documents({"user_id": user_id, "status": "success"})
        total_fail = await yt_uploads_col.count_documents({"user_id": user_id, "status": "failed"})

        plan_text  = "⭐ Premium" if is_premium else "🆓 Free"
        cooldown   = f"{PREMIUM_COOLDOWN} seconds" if is_premium else f"{FREE_COOLDOWN // 60} minutes"

        await message.reply_text(
            f"📺 **Your YouTube Channel**\n"
            f"{'─' * 28}\n\n"
            f"**Channel:** `{ch_name}`\n"
            f"**Channel ID:** `{ch_id}`\n"
            f"**URL:** {ch_url}\n\n"
            f"**🔑 Token Status:** {'✅ Valid' if token_ok else '❌ Invalid — please reconnect'}\n"
            f"**📅 Connected:** `{conn_str}`\n\n"
            f"**📊 Your Upload Stats**\n"
            f"• ✅ Successful: `{total_ok}`\n"
            f"• ❌ Failed: `{total_fail}`\n\n"
            f"**⚡ Your Plan:** `{plan_text}`\n"
            f"**⏳ Cooldown:** `{cooldown}`",
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )

    # ═══════════════════════════════════════════════════════════
    # /ytupload — URL → YouTube
    # ═══════════════════════════════════════════════════════════

    async def ytupload_command(client: Client, message: Message):
        user_id    = message.from_user.id
        is_premium = await _is_premium(user_id)

        if not GOOGLE_API_AVAILABLE:
            await message.reply_text(
                "❌ **Google API library is not installed!**\n"
                "Please contact the bot admin.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        if len(message.command) < 2:
            await message.reply_text(
                "📤 **YouTube Direct Uploader**\n\n"
                "Upload any video from the web to your own YouTube channel!\n\n"
                "**Usage:**\n"
                "`/ytupload VIDEO_URL`\n"
                "`/ytupload VIDEO_URL --private`\n"
                "`/ytupload VIDEO_URL --unlisted`\n"
                "`/ytupload VIDEO_URL --title \"My Title\"`\n\n"
                "📖 Full guide: /ythelp\n"
                "🔗 Connect channel: /ytconnect",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        if not await _is_youtube_connected(user_id):
            await message.reply_text(
                "❌ **YouTube channel not connected!**\n\n"
                "Please connect your channel first:\n"
                "/ytconnect",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        allowed, rate_msg = await _check_upload_rate_limit(user_id, is_premium)
        if not allowed:
            await message.reply_text(rate_msg, parse_mode=ParseMode.MARKDOWN)
            return

        text_parts = message.text.split(None, 1)
        raw        = text_parts[1].strip() if len(text_parts) > 1 else ""
        parsed     = _parse_upload_flags(raw)

        url, referer = parsed["url"], parsed["referer"]
        privacy      = parsed["privacy"]
        custom_title = parsed["custom_title"]

        if not url:
            await message.reply_text(
                "❌ **Please provide a valid URL.**\n\n"
                "Example: `/ytupload https://youtu.be/xxxxx`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        if is_hls_url(url) and not referer:
            await message.reply_text(
                "⚠️ **HLS Stream (m3u8) Detected!**\n\n"
                "This type of URL may require a Referer header.\n"
                "If you get a 403 error, try:\n"
                "`/ytupload URL referer:https://the-website.com`\n\n"
                "⏳ _Trying without referer first..._",
                parse_mode=ParseMode.MARKDOWN,
            )
        elif is_protected_cdn_url(url) and not referer:
            await message.reply_text(
                "⚠️ **Protected CDN Detected!**\n\n"
                "If this fails, try adding a referer:\n"
                "`/ytupload URL referer:https://the-website.com`\n\n"
                "⏳ _Trying now..._",
                parse_mode=ParseMode.MARKDOWN,
            )

        warp_ok    = _is_warp_available()
        status_msg = await message.reply_text(
            f"🔍 **Analyzing URL...**\n"
            f"_{'🟢 WARP proxy' if warp_ok else '🟡 Direct connection'}"
            f"{' | 🔗 Referer Active' if referer else ''}_",
            parse_mode=ParseMode.MARKDOWN,
        )

        loop = asyncio.get_event_loop()
        info, err = await loop.run_in_executor(
            None, lambda: get_single_video_info(url, referer)
        )

        if not info:
            asyncio.create_task(_log_failed_attempt(
                client, message.from_user, "url", url,
                err or "Video info fetch failed", is_premium,
            ))
            err_low = (err or "").lower()
            if (is_hls_url(url) or is_protected_cdn_url(url)) and (
                "403" in err_low or "forbidden" in err_low
            ) and not referer:
                await status_msg.edit_text(
                    "❌ **403 Forbidden — Access Denied!**\n\n"
                    "This video requires a Referer. Try:\n"
                    "`/ytupload URL referer:https://the-website.com`",
                    parse_mode=ParseMode.MARKDOWN,
                )
            else:
                await status_msg.edit_text(
                    f"❌ **Could not fetch video info!**\n\n"
                    f"{_friendly_error(err) if err else 'Unknown error'}",
                    parse_mode=ParseMode.MARKDOWN,
                )
            return

        title    = (custom_title or info.get("title") or "Untitled")[:YT_TITLE_MAX]
        duration = int(info.get("duration", 0) or 0)
        uploader = (info.get("uploader") or info.get("channel") or "Unknown")[:50]
        dur_str  = get_readable_time(duration) if duration else "Unknown"

        ytup_sessions[message.chat.id] = {
            "mode":         "url",
            "user_id":      user_id,
            "url":          url,
            "referer":      referer,
            "info":         info,
            "privacy":      privacy,
            "custom_title": custom_title,
            "title":        title,
            "created_at":   time(),
            "user_obj":     message.from_user,
            "is_premium":   is_premium,
        }

        plan_note = "" if is_premium else "\n_🆓 Free user — 1 upload at a time_"

        if privacy != "public":
            await status_msg.edit_text(
                f"📹 **{title[:60]}**\n\n"
                f"👤 **Channel/Uploader:** {uploader}\n"
                f"⏱ **Duration:** {dur_str}\n"
                f"🔒 **Privacy:** {PRIVACY_LABELS[privacy]}"
                f"{plan_note}\n\n"
                f"**Confirm upload?**",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            f"✅ Upload ({PRIVACY_LABELS[privacy]})",
                            callback_data=f"ytup_confirm_{message.chat.id}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "❌ Cancel",
                            callback_data=f"ytup_cancel_{message.chat.id}",
                        )
                    ],
                ]),
                disable_web_page_preview=True,
            )
        else:
            await status_msg.edit_text(
                f"📹 **{title[:60]}**\n\n"
                f"👤 **Channel/Uploader:** {uploader}\n"
                f"⏱ **Duration:** {dur_str}"
                f"{plan_note}\n\n"
                f"**Choose privacy for your YouTube upload:**",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=_privacy_keyboard(message.chat.id),
                disable_web_page_preview=True,
            )

    # ═══════════════════════════════════════════════════════════
    # /ytsend — Telegram Video → YouTube
    # ═══════════════════════════════════════════════════════════

    async def ytsend_command(client: Client, message: Message):
        user_id    = message.from_user.id
        is_premium = await _is_premium(user_id)

        if not GOOGLE_API_AVAILABLE:
            await message.reply_text(
                "❌ **Google API library not installed!**",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        if not await _is_youtube_connected(user_id):
            await message.reply_text(
                "❌ **YouTube channel not connected!**\n\nUse /ytconnect to link your channel.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        allowed, rate_msg = await _check_upload_rate_limit(user_id, is_premium)
        if not allowed:
            await message.reply_text(rate_msg, parse_mode=ParseMode.MARKDOWN)
            return

        replied = message.reply_to_message
        if not replied:
            await message.reply_text(
                "❌ **Please reply to a video!**\n\n"
                "Reply to any video and send `/ytsend`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        media = replied.video or replied.document or replied.animation
        if not media:
            await message.reply_text(
                "❌ **The replied message has no video!**",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        text_parts   = message.text.split(None, 1)
        raw          = text_parts[1].strip() if len(text_parts) > 1 else ""
        parsed       = _parse_ytsend_flags(raw)
        privacy      = parsed["privacy"]
        custom_title = parsed["custom_title"]

        file_size  = getattr(media, "file_size", 0) or 0
        file_name  = getattr(media, "file_name", None) or ""
        duration   = getattr(media, "duration", 0) or 0
        file_id    = media.file_id

        auto_title = (
            custom_title
            or (file_name.rsplit(".", 1)[0] if file_name else "")
            or (replied.caption or "")[:80]
            or f"Telegram Video {datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        )[:YT_TITLE_MAX]

        if file_size > MAX_FILE_SIZE:
            await message.reply_text(
                f"❌ **File too large!**\n"
                f"📦 `{get_readable_file_size(file_size)}` exceeds the limit.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        ytup_sessions[message.chat.id] = {
            "mode":         "telegram",
            "user_id":      user_id,
            "file_id":      file_id,
            "file_size":    file_size,
            "duration":     duration,
            "file_name":    file_name,
            "privacy":      privacy,
            "custom_title": custom_title,
            "title":        auto_title,
            "created_at":   time(),
            "user_obj":     message.from_user,
            "is_premium":   is_premium,
        }

        dur_str   = get_readable_time(duration) if duration else "Unknown"
        size_str  = get_readable_file_size(file_size) if file_size else "Unknown"
        plan_note = "" if is_premium else "\n_🆓 Free user — 1 upload at a time_"

        if privacy != "public":
            await message.reply_text(
                f"📨 **Telegram Video → YouTube**\n\n"
                f"📝 **Title:** `{auto_title[:60]}`\n"
                f"⏱ **Duration:** {dur_str}\n"
                f"📦 **Size:** {size_str}\n"
                f"🔒 **Privacy:** {PRIVACY_LABELS[privacy]}"
                f"{plan_note}\n\n"
                f"**Confirm upload?**",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            f"✅ Upload ({PRIVACY_LABELS[privacy]})",
                            callback_data=f"ytup_confirm_{message.chat.id}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "❌ Cancel",
                            callback_data=f"ytup_cancel_{message.chat.id}",
                        )
                    ],
                ]),
            )
        else:
            await message.reply_text(
                f"📨 **Telegram Video → YouTube**\n\n"
                f"📝 **Title:** `{auto_title[:60]}`\n"
                f"⏱ **Duration:** {dur_str}\n"
                f"📦 **Size:** {size_str}"
                f"{plan_note}\n\n"
                f"**Choose privacy for your YouTube upload:**",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=_privacy_keyboard(message.chat.id),
            )

    # ═══════════════════════════════════════════════════════════
    # CORE — URL Upload Execution
    # ═══════════════════════════════════════════════════════════

    async def _execute_url_upload(client, cq: CallbackQuery, session: dict, privacy: str):
        chat_id    = cq.message.chat.id
        user_id    = session["user_id"]
        url        = session["url"]
        referer    = session.get("referer")
        info       = session.get("info", {})
        title      = session.get("title", "Untitled")
        user_obj   = session.get("user_obj", cq.from_user)
        is_premium = session.get("is_premium", False)

        description = (info.get("description") or "")[:YT_DESC_MAX]
        tags        = (info.get("tags") or [])[:YT_TAG_MAX]

        if not is_premium:
            active_uploads_free.add(user_id)

        creds = await _load_user_token(user_id)
        if not creds:
            await cq.message.edit_text(
                "❌ **YouTube token is invalid!**\n\n"
                "Please use /ytdisconnect then /ytconnect again.",
                parse_mode=ParseMode.MARKDOWN,
            )
            ytup_sessions.pop(chat_id, None)
            if not is_premium:
                active_uploads_free.discard(user_id)
            return

        ch_info  = await _get_user_channel_info(user_id)
        ch_name  = (ch_info or {}).get("channel_name", "Unknown")
        ch_id    = (ch_info or {}).get("channel_id",   "")

        overall_start = time()
        warp_ok       = _is_warp_available()
        user_dir      = os.path.join(DOWNLOAD_DIR, f"ytup_{user_id}")
        os.makedirs(user_dir, exist_ok=True)

        await cq.message.edit_text(
            f"📥 **Downloading...**\n"
            f"_{'🟢 WARP' if warp_ok else '🟡 Direct'}"
            f"{' | 🔗 Referer' if referer else ''}_\n\n"
            f"🎬 `{title[:50]}`",
            parse_mode=ParseMode.MARKDOWN,
        )

        loop          = asyncio.get_event_loop()
        progress_data = {"downloaded": 0, "total": 0, "speed": 0, "eta": 0, "done": False}
        prog_task     = asyncio.create_task(
            _ytdl_progress_updater(cq.message, progress_data)
        )

        try:
            dl_ok, dl_result = await loop.run_in_executor(
                None,
                lambda: download_single_video(
                    url, user_dir, None, False,
                    progress_data, True, referer,
                ),
            )
        finally:
            progress_data["done"] = True
            try:
                await prog_task
            except Exception:
                pass

        if not dl_ok:
            await cq.message.edit_text(
                f"❌ **Download failed!**\n\n{_friendly_error(dl_result)}",
                parse_mode=ParseMode.MARKDOWN,
            )
            asyncio.create_task(_log_to_group(
                client, user_obj, "url", url, title, "", ch_name,
                privacy, 0, "failed", 0, _friendly_error(dl_result), is_premium,
            ))
            asyncio.create_task(_save_upload_log(
                user_id, _user_display(user_obj), "url", url, "", title,
                privacy, 0, "failed", _friendly_error(dl_result),
                ch_id, ch_name, time() - overall_start, is_premium,
            ))
            _cleanup_dir(user_dir)
            ytup_sessions.pop(chat_id, None)
            user_last_upload[user_id] = time()
            if not is_premium:
                active_uploads_free.discard(user_id)
            return

        filepath  = dl_result
        file_size = os.path.getsize(filepath)

        if file_size > MAX_FILE_SIZE:
            os.remove(filepath)
            await cq.message.edit_text(
                f"❌ **File too large!**\n"
                f"📦 `{get_readable_file_size(file_size)}` exceeds the limit.",
                parse_mode=ParseMode.MARKDOWN,
            )
            _cleanup_dir(user_dir)
            ytup_sessions.pop(chat_id, None)
            user_last_upload[user_id] = time()
            if not is_premium:
                active_uploads_free.discard(user_id)
            return

        yt_progress = {"uploaded": 0, "total": file_size, "done": False}

        def _prog_cb(uploaded, total):
            yt_progress["uploaded"] = uploaded or 0
            yt_progress["total"]    = total or file_size

        await cq.message.edit_text(
            f"📤 **Uploading to YouTube...**\n\n"
            f"📺 **Channel:** `{ch_name}`\n"
            f"🎬 `{title[:50]}`\n"
            f"📦 `{get_readable_file_size(file_size)}`\n"
            f"🔒 `{PRIVACY_LABELS[privacy]}`",
            parse_mode=ParseMode.MARKDOWN,
        )

        yt_prog_task = asyncio.create_task(
            _yt_upload_progress_updater(cq.message, yt_progress)
        )

        try:
            yt_ok, yt_result = await loop.run_in_executor(
                None,
                lambda: _upload_file_to_youtube_sync(
                    creds, filepath, title, description,
                    tags, privacy, "22", _prog_cb,
                ),
            )
        finally:
            yt_progress["done"] = True
            try:
                await yt_prog_task
            except Exception:
                pass

        try:
            os.remove(filepath)
        except Exception:
            pass
        _cleanup_dir(user_dir)
        ytup_sessions.pop(chat_id, None)
        user_last_upload[user_id] = time()
        if not is_premium:
            active_uploads_free.discard(user_id)

        elapsed = time() - overall_start

        if yt_ok:
            video_id = yt_result
            yt_url   = f"https://youtu.be/{video_id}"

            await cq.message.edit_text(
                f"✅ **YouTube Upload Successful!**\n\n"
                f"📺 **Channel:** `{ch_name}`\n"
                f"🎬 **{title[:60]}**\n\n"
                f"🔗 **Link:** {yt_url}\n"
                f"🔒 **Privacy:** {PRIVACY_LABELS[privacy]}\n"
                f"📦 **Size:** `{get_readable_file_size(file_size)}`\n"
                f"⏱ **Time:** `{get_readable_time(int(elapsed))}`",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("▶️ Watch on YouTube", url=yt_url)],
                ]),
            )

            asyncio.create_task(_log_to_group(
                client, user_obj, "url", url, title,
                video_id, ch_name, privacy, file_size, "success", elapsed,
                is_premium=is_premium,
            ))
            asyncio.create_task(_save_upload_log(
                user_id, _user_display(user_obj), "url", url,
                video_id, title, privacy, file_size, "success",
                "", ch_id, ch_name, elapsed, is_premium,
            ))
        else:
            await cq.message.edit_text(
                f"❌ **YouTube Upload Failed!**\n\n{yt_result}",
                parse_mode=ParseMode.MARKDOWN,
            )
            asyncio.create_task(_log_to_group(
                client, user_obj, "url", url, title, "", ch_name,
                privacy, file_size, "failed", elapsed, yt_result, is_premium,
            ))
            asyncio.create_task(_save_upload_log(
                user_id, _user_display(user_obj), "url", url,
                "", title, privacy, file_size, "failed",
                yt_result, ch_id, ch_name, elapsed, is_premium,
            ))

    # ═══════════════════════════════════════════════════════════
    # CORE — Telegram Upload Execution
    # ═══════════════════════════════════════════════════════════

    async def _execute_telegram_upload(client, cq: CallbackQuery, session: dict, privacy: str):
        chat_id    = cq.message.chat.id
        user_id    = session["user_id"]
        file_id    = session["file_id"]
        file_size  = session.get("file_size", 0)
        title      = session.get("title", "Untitled")
        duration   = session.get("duration", 0)
        user_obj   = session.get("user_obj", cq.from_user)
        is_premium = session.get("is_premium", False)

        if not is_premium:
            active_uploads_free.add(user_id)

        creds = await _load_user_token(user_id)
        if not creds:
            await cq.message.edit_text(
                "❌ **YouTube token is invalid!**\n\n"
                "Please use /ytdisconnect then /ytconnect.",
                parse_mode=ParseMode.MARKDOWN,
            )
            ytup_sessions.pop(chat_id, None)
            if not is_premium:
                active_uploads_free.discard(user_id)
            return

        ch_info  = await _get_user_channel_info(user_id)
        ch_name  = (ch_info or {}).get("channel_name", "Unknown")
        ch_id    = (ch_info or {}).get("channel_id",   "")

        overall_start = time()

        await cq.message.edit_text(
            f"📥 **Downloading from Telegram...**\n\n"
            f"📺 **Channel:** `{ch_name}`\n"
            f"🎬 `{title[:50]}`\n"
            f"📦 `{get_readable_file_size(file_size)}`",
            parse_mode=ParseMode.MARKDOWN,
        )

        user_dir = os.path.join(DOWNLOAD_DIR, f"tgup_{user_id}")
        os.makedirs(user_dir, exist_ok=True)
        filepath = os.path.join(user_dir, f"{file_id[:20]}.mp4")

        try:
            await client.download_media(file_id, file_name=filepath)
        except Exception as e:
            await cq.message.edit_text(
                f"❌ **Telegram download failed!**\n`{str(e)[:150]}`",
                parse_mode=ParseMode.MARKDOWN,
            )
            _cleanup_dir(user_dir)
            ytup_sessions.pop(chat_id, None)
            user_last_upload[user_id] = time()
            if not is_premium:
                active_uploads_free.discard(user_id)
            return

        if not os.path.exists(filepath):
            await cq.message.edit_text(
                "❌ **File was not downloaded!** Please try again.",
                parse_mode=ParseMode.MARKDOWN,
            )
            _cleanup_dir(user_dir)
            ytup_sessions.pop(chat_id, None)
            user_last_upload[user_id] = time()
            if not is_premium:
                active_uploads_free.discard(user_id)
            return

        actual_size = os.path.getsize(filepath)
        yt_progress = {"uploaded": 0, "total": actual_size, "done": False}

        def _prog_cb(uploaded, total):
            yt_progress["uploaded"] = uploaded or 0
            yt_progress["total"]    = total or actual_size

        await cq.message.edit_text(
            f"📤 **Uploading to YouTube...**\n\n"
            f"📺 **Channel:** `{ch_name}`\n"
            f"🎬 `{title[:50]}`\n"
            f"📦 `{get_readable_file_size(actual_size)}`\n"
            f"🔒 `{PRIVACY_LABELS[privacy]}`",
            parse_mode=ParseMode.MARKDOWN,
        )

        loop         = asyncio.get_event_loop()
        yt_prog_task = asyncio.create_task(
            _yt_upload_progress_updater(cq.message, yt_progress)
        )

        try:
            yt_ok, yt_result = await loop.run_in_executor(
                None,
                lambda: _upload_file_to_youtube_sync(
                    creds, filepath, title, "", [], privacy, "22", _prog_cb,
                ),
            )
        finally:
            yt_progress["done"] = True
            try:
                await yt_prog_task
            except Exception:
                pass

        try:
            os.remove(filepath)
        except Exception:
            pass
        _cleanup_dir(user_dir)
        ytup_sessions.pop(chat_id, None)
        user_last_upload[user_id] = time()
        if not is_premium:
            active_uploads_free.discard(user_id)

        elapsed = time() - overall_start

        if yt_ok:
            video_id = yt_result
            yt_url   = f"https://youtu.be/{video_id}"

            await cq.message.edit_text(
                f"✅ **YouTube Upload Successful!**\n\n"
                f"📺 **Channel:** `{ch_name}`\n"
                f"🎬 **{title[:60]}**\n\n"
                f"🔗 **Link:** {yt_url}\n"
                f"🔒 **Privacy:** {PRIVACY_LABELS[privacy]}\n"
                f"📦 **Size:** `{get_readable_file_size(actual_size)}`\n"
                f"⏱ **Time:** `{get_readable_time(int(elapsed))}`",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("▶️ Watch on YouTube", url=yt_url)],
                ]),
            )

            asyncio.create_task(_log_to_group(
                client, user_obj, "telegram", file_id, title,
                video_id, ch_name, privacy, actual_size, "success", elapsed,
                is_premium=is_premium,
            ))
            asyncio.create_task(_save_upload_log(
                user_id, _user_display(user_obj), "telegram", file_id,
                video_id, title, privacy, actual_size, "success",
                "", ch_id, ch_name, elapsed, is_premium,
            ))
        else:
            await cq.message.edit_text(
                f"❌ **YouTube Upload Failed!**\n\n{yt_result}",
                parse_mode=ParseMode.MARKDOWN,
            )
            asyncio.create_task(_log_to_group(
                client, user_obj, "telegram", file_id, title, "", ch_name,
                privacy, actual_size, "failed", elapsed, yt_result, is_premium,
            ))
            asyncio.create_task(_save_upload_log(
                user_id, _user_display(user_obj), "telegram", file_id,
                "", title, privacy, actual_size, "failed",
                yt_result, ch_id, ch_name, elapsed, is_premium,
            ))

    # ═══════════════════════════════════════════════════════════
    # CALLBACKS
    # ═══════════════════════════════════════════════════════════

    @app.on_callback_query(filters.regex(r"^ytup_(pub|prv|unl)_\d+$"))
    async def ytup_privacy_callback(client, cq: CallbackQuery):
        data    = cq.data
        chat_id = cq.message.chat.id
        user_id = cq.from_user.id

        session = ytup_sessions.get(chat_id)
        if not session or session["user_id"] != user_id:
            await cq.answer("❌ Session expired! Please start again.", show_alert=True)
            return

        is_premium = session.get("is_premium", False)
        allowed, rate_msg = await _check_upload_rate_limit(user_id, is_premium)
        if not allowed:
            await cq.message.edit_text(rate_msg, parse_mode=ParseMode.MARKDOWN)
            ytup_sessions.pop(chat_id, None)
            return

        parts   = data.split("_")
        key     = parts[1] if len(parts) > 1 else "pub"
        privacy = PRIVACY_FROM_CB.get(key, "public")

        await cq.answer(f"✅ {PRIVACY_LABELS[privacy]} selected!")

        mode = session.get("mode", "url")
        if mode == "url":
            await _execute_url_upload(client, cq, session, privacy)
        else:
            await _execute_telegram_upload(client, cq, session, privacy)

    @app.on_callback_query(filters.regex(r"^ytup_confirm_\d+$"))
    async def ytup_confirm_callback(client, cq: CallbackQuery):
        chat_id = cq.message.chat.id
        user_id = cq.from_user.id

        session = ytup_sessions.get(chat_id)
        if not session or session["user_id"] != user_id:
            await cq.answer("❌ Session expired! Please start again.", show_alert=True)
            return

        is_premium = session.get("is_premium", False)
        allowed, rate_msg = await _check_upload_rate_limit(user_id, is_premium)
        if not allowed:
            await cq.message.edit_text(rate_msg, parse_mode=ParseMode.MARKDOWN)
            ytup_sessions.pop(chat_id, None)
            return

        await cq.answer("⏳ Starting upload...")
        privacy = session.get("privacy", "public")

        mode = session.get("mode", "url")
        if mode == "url":
            await _execute_url_upload(client, cq, session, privacy)
        else:
            await _execute_telegram_upload(client, cq, session, privacy)

    @app.on_callback_query(filters.regex(r"^ytup_cancel_\d+$"))
    async def ytup_cancel_callback(client, cq: CallbackQuery):
        chat_id = cq.message.chat.id
        user_id = cq.from_user.id

        session = ytup_sessions.get(chat_id)
        if session and session["user_id"] != user_id:
            await cq.answer("❌ This is not your session!", show_alert=True)
            return

        ytup_sessions.pop(chat_id, None)
        active_uploads_free.discard(user_id)

        await cq.message.edit_text(
            "❌ **Cancelled.**",
            parse_mode=ParseMode.MARKDOWN,
        )
        await cq.answer()

    @app.on_callback_query(filters.regex(r"^ytup_goto_connect$"))
    async def ytup_goto_connect_callback(client, cq: CallbackQuery):
        await cq.answer()
        await cq.message.reply_text(
            "Use the command /ytconnect to start connecting your YouTube channel.",
            parse_mode=ParseMode.MARKDOWN,
        )

    @app.on_callback_query(filters.regex(r"^ytup_goto_me$"))
    async def ytup_goto_me_callback(client, cq: CallbackQuery):
        await cq.answer()
        await cq.message.reply_text(
            "Use /ytme to see your connected channel information.",
            parse_mode=ParseMode.MARKDOWN,
        )

    # ═══════════════════════════════════════════════════════════
    # HANDLER REGISTRATION
    # ═══════════════════════════════════════════════════════════

    _f = filters.private | filters.group

    app.add_handler(MessageHandler(ythelp_command,
        filters.command("ythelp", COMMAND_PREFIX) & _f), group=2)

    app.add_handler(MessageHandler(ytconnect_command,
        filters.command("ytconnect", COMMAND_PREFIX) & _f), group=2)

    app.add_handler(MessageHandler(ytcode_command,
        filters.command("ytcode", COMMAND_PREFIX) & _f), group=2)

    app.add_handler(MessageHandler(ytdisconnect_command,
        filters.command("ytdisconnect", COMMAND_PREFIX) & _f), group=2)

    app.add_handler(MessageHandler(ytme_command,
        filters.command("ytme", COMMAND_PREFIX) & _f), group=2)

    app.add_handler(MessageHandler(ytupload_command,
        filters.command("ytupload", COMMAND_PREFIX) & _f), group=2)

    app.add_handler(MessageHandler(ytsend_command,
        filters.command("ytsend", COMMAND_PREFIX) & _f), group=2)

    LOGGER.info("[ytupload] Handler registered ✅")
