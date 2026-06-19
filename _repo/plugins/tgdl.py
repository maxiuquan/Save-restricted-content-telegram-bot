# Copyright @juktijol
# Channel t.me/juktijol
#
# plugins/tgdl.py — Telegram File Downloader
#
# Handles:
#   • /tgdl — reply to any Telegram file/document/video/audio to download + re-upload
#   • Useful for: leeching files from restricted channels through a user session
#   • Supports all Telegram media types
#
# ✅ Real-time progress bar
# ✅ Premium / free file-size check
# ✅ Custom thumbnail support
# ✅ Auto media type detection (video / audio / document)
# ✅ Log to group

import os
import asyncio
from time import time
from datetime import datetime

from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import ParseMode
from pyrogram.handlers import MessageHandler
from pyleaves import Leaves

from config import COMMAND_PREFIX, LOG_GROUP_ID
from utils import LOGGER, progressArgs, log_file_to_group
from utils.helper import get_readable_file_size, get_readable_time, get_video_thumbnail
from core import prem_plan1, prem_plan2, prem_plan3, user_activity_collection

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

MAX_FILE_SIZE   = 2 * 1024 ** 3    # 2 GB
FREE_FILE_LIMIT = 500 * 1024 ** 2  # 500 MB
DOWNLOAD_DIR    = "tgdl_downloads"
PROGRESS_DELAY  = 3

os.makedirs(DOWNLOAD_DIR, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

async def _is_premium(user_id: int) -> bool:
    now = datetime.utcnow()
    for col in [prem_plan1, prem_plan2, prem_plan3]:
        doc = await col.find_one({"user_id": user_id})
        if doc and doc.get("expiry_date", now) > now:
            return True
    return False


def _get_media_obj(message: Message):
    """Return (media_object, media_type_str) from a Pyrogram Message."""
    if message.document:
        return message.document, "document"
    if message.video:
        return message.video, "video"
    if message.audio:
        return message.audio, "audio"
    if message.voice:
        return message.voice, "voice"
    if message.video_note:
        return message.video_note, "video_note"
    if message.photo:
        return message.photo, "photo"
    if message.sticker:
        return message.sticker, "sticker"
    if message.animation:
        return message.animation, "animation"
    return None, None


def _progress_bar(pct: float, length: int = 20) -> str:
    filled = int(length * pct / 100)
    return "▓" * filled + "░" * (length - filled)


# ─────────────────────────────────────────────────────────────────────────────
# UPLOAD HELPER
# ─────────────────────────────────────────────────────────────────────────────

async def _upload_file(
    client: Client,
    chat_id: int,
    file_path: str,
    media_type: str,
    caption: str,
    status_msg: Message,
    start_ts: float,
    thumbnail_path: str | None = None,
):
    """Upload a file back to Telegram with live progress."""
    file_size = os.path.getsize(file_path)
    last_edit = [0.0]
    start_up  = [time()]

    async def _progress(current: int, total: int):
        now = time()
        if now - last_edit[0] < PROGRESS_DELAY and current < total:
            return
        elapsed = now - start_up[0]
        speed   = current / elapsed if elapsed > 0 else 0
        eta     = (total - current) / speed if speed > 0 else 0
        pct     = (current / total * 100) if total > 0 else 0
        bar     = _progress_bar(pct)
        try:
            await status_msg.edit_text(
                f"📤 **Upload হচ্ছে...**\n\n"
                f"`[{bar}]` {pct:.1f}%\n\n"
                f"📦 `{get_readable_file_size(current)}` / `{get_readable_file_size(total)}`\n"
                f"⚡ **Speed:** `{get_readable_file_size(speed)}/s`\n"
                f"⏳ **ETA:** `{get_readable_time(int(eta))}`",
                parse_mode=ParseMode.MARKDOWN,
            )
            last_edit[0] = now
        except Exception:
            pass

    video_exts = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv", ".m4v"}
    audio_exts = {".mp3", ".flac", ".ogg", ".opus", ".m4a", ".wav", ".aac"}
    ext        = os.path.splitext(file_path)[1].lower()

    try:
        if media_type == "video" or ext in video_exts:
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
                progress=_progress,
            )
            if thumb and thumb != thumbnail_path and os.path.exists(thumb):
                os.remove(thumb)

        elif media_type == "audio" or ext in audio_exts:
            await client.send_audio(
                chat_id=chat_id,
                audio=file_path,
                caption=caption,
                thumb=thumbnail_path,
                parse_mode=ParseMode.MARKDOWN,
                progress=_progress,
            )

        elif media_type == "photo":
            await client.send_photo(
                chat_id=chat_id,
                photo=file_path,
                caption=caption,
                parse_mode=ParseMode.MARKDOWN,
            )

        else:
            # Default: send as document
            await client.send_document(
                chat_id=chat_id,
                document=file_path,
                caption=caption,
                thumb=thumbnail_path,
                parse_mode=ParseMode.MARKDOWN,
                progress=_progress,
            )

        elapsed = get_readable_time(int(time() - start_ts))
        await status_msg.edit_text(
            f"✅ **সফলভাবে পাঠানো হয়েছে!**\n\n"
            f"📦 `{get_readable_file_size(file_size)}`\n"
            f"⏱ সময়: `{elapsed}`",
            parse_mode=ParseMode.MARKDOWN,
        )

    except Exception as e:
        LOGGER.error(f"[TgDL] Upload failed: {e}")
        try:
            await status_msg.edit_text(
                f"❌ **Upload ব্যর্থ:**\n`{str(e)[:200]}`",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass
        raise


# ─────────────────────────────────────────────────────────────────────────────
# MAIN PIPELINE
# ─────────────────────────────────────────────────────────────────────────────

async def _process_tg_download(
    client: Client,
    message: Message,
    source_msg: Message,
    status_msg: Message,
    is_premium: bool,
):
    """Download a Telegram file and re-upload it to the chat."""
    user_id = message.from_user.id
    chat_id = message.chat.id

    media_obj, media_type = _get_media_obj(source_msg)
    if media_obj is None:
        await status_msg.edit_text(
            "❌ এই message-এ কোনো downloadable ফাইল নেই।",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # ── File size check ───────────────────────────────────────────────────────
    file_size   = getattr(media_obj, "file_size", 0) or 0
    max_allowed = MAX_FILE_SIZE if is_premium else FREE_FILE_LIMIT

    if file_size > max_allowed:
        await status_msg.edit_text(
            f"❌ **ফাইল অনেক বড়!**\n\n"
            f"📦 ফাইল: `{get_readable_file_size(file_size)}`\n"
            f"🚫 সীমা: `{get_readable_file_size(max_allowed)}`\n\n"
            + ("💎 Premium এ আপগ্রেড করুন: /plans" if not is_premium else ""),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # ── Download ──────────────────────────────────────────────────────────────
    user_dir = os.path.join(DOWNLOAD_DIR, str(user_id))
    os.makedirs(user_dir, exist_ok=True)

    start_ts  = time()
    last_edit = [0.0]

    async def _dl_progress(current: int, total: int):
        now = time()
        if now - last_edit[0] < PROGRESS_DELAY and current < total:
            return
        elapsed = now - start_ts
        speed   = current / elapsed if elapsed > 0 else 0
        eta     = (total - current) / speed if speed > 0 else 0
        pct     = (current / total * 100) if total > 0 else 0
        bar     = "▓" * int(20 * pct / 100) + "░" * (20 - int(20 * pct / 100))
        try:
            await status_msg.edit_text(
                f"⬇️ **Download হচ্ছে...**\n\n"
                f"`[{bar}]` {pct:.1f}%\n\n"
                f"📥 `{get_readable_file_size(current)}` / `{get_readable_file_size(total)}`\n"
                f"⚡ **Speed:** `{get_readable_file_size(speed)}/s`\n"
                f"⏳ **ETA:** `{get_readable_time(int(eta))}`",
                parse_mode=ParseMode.MARKDOWN,
            )
            last_edit[0] = now
        except Exception:
            pass

    try:
        file_path = await source_msg.download(
            file_name=user_dir + "/",
            progress=_dl_progress,
        )
    except Exception as e:
        LOGGER.error(f"[TgDL] Download failed for user {user_id}: {e}")
        await status_msg.edit_text(
            f"❌ **Download ব্যর্থ:**\n`{str(e)[:200]}`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if not file_path or not os.path.exists(file_path):
        await status_msg.edit_text(
            "❌ ফাইল download সম্পন্ন হয়নি।",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # ── Upload ────────────────────────────────────────────────────────────────
    await status_msg.edit_text(
        "✅ **Download সম্পন্ন!**\n\n📤 Upload করা হচ্ছে...",
        parse_mode=ParseMode.MARKDOWN,
    )

    # Fetch user's custom thumbnail
    thumbnail_path = None
    try:
        user_data = await user_activity_collection.find_one({"user_id": user_id})
        thumbnail_path = user_data.get("thumbnail_path") if user_data else None
        if thumbnail_path and not os.path.exists(thumbnail_path):
            thumbnail_path = None
    except Exception:
        thumbnail_path = None

    file_name = os.path.basename(file_path)
    caption   = (
        f"📄 **{file_name}**\n"
        f"📦 `{get_readable_file_size(os.path.getsize(file_path))}`"
    )

    try:
        await _upload_file(
            client, chat_id, file_path, media_type,
            caption, status_msg, start_ts, thumbnail_path
        )

        # Log
        if LOG_GROUP_ID:
            try:
                await log_file_to_group(
                    bot=client,
                    log_group_id=LOG_GROUP_ID,
                    user=message.from_user,
                    url="[Telegram File]",
                    file_path=file_path,
                    media_type=media_type,
                    caption_original=caption,
                    channel_name=None,
                    thumbnail_path=thumbnail_path,
                )
            except Exception as log_err:
                LOGGER.warning(f"[TgDL] Log error: {log_err}")

    finally:
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass

    LOGGER.info(f"[TgDL] User {user_id} downloaded and re-uploaded: {file_name}")


# ─────────────────────────────────────────────────────────────────────────────
# SETUP
# ─────────────────────────────────────────────────────────────────────────────

def setup_tgdl_handler(app: Client):

    @app.on_message(
        filters.command("tgdl", prefixes=COMMAND_PREFIX)
        & (filters.private | filters.group)
    )
    async def tgdl_command(client: Client, message: Message):
        user_id = message.from_user.id

        # Must reply to a message containing a file
        if not message.reply_to_message:
            await message.reply_text(
                "**📥 Telegram File Downloader**\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "একটি Telegram ফাইলে reply করে `/tgdl` দিন।\n\n"
                "**Supported types:**\n"
                "• Document, Video, Audio, Voice\n"
                "• Photo, Sticker, Animation\n\n"
                "**Example:** কোনো ফাইল forward করুন, তারপর reply করে `/tgdl`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        source_msg = message.reply_to_message
        media_obj, media_type = _get_media_obj(source_msg)

        if media_obj is None:
            await message.reply_text(
                "❌ এই message-এ কোনো downloadable ফাইল নেই।\n"
                "Document, Video, Audio, বা Photo-তে reply করুন।",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        is_premium = await _is_premium(user_id)
        file_size  = getattr(media_obj, "file_size", 0) or 0

        status_msg = await message.reply_text(
            f"🔄 **Telegram file download শুরু হচ্ছে...**\n\n"
            f"📦 আকার: `{get_readable_file_size(file_size)}`\n"
            f"🎭 ধরন: `{media_type}`",
            parse_mode=ParseMode.MARKDOWN,
        )

        asyncio.create_task(
            _process_tg_download(
                client, message, source_msg, status_msg, is_premium
            )
        )

    LOGGER.info("[TgDL] /tgdl command handler registered.")
