#
# plugins/tgdl.py — Telegram 文件下载器
#
# 功能：
#   • /tgdl — 回复任何 Telegram 文件/文档/视频/音频以下载并重新上传
#   • 用途：通过用户会话从受限频道抓取文件
#   • 支持所有 Telegram 媒体类型
#
# ✅ 实时进度条
# ✅ 付费/免费文件大小检查
# ✅ 自定义缩略图支持
# ✅ 自动媒体类型检测（视频/音频/文档）
# ✅ 记录到群组
# ✅ 优化：非阻塞进度 + 全局信号量 + 响应式 UI

import os
import asyncio
from time import time
from datetime import datetime

from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import ParseMode
from pyrogram.errors import FloodWait
from pyrogram.handlers import MessageHandler
from pyleaves import Leaves

from config import COMMAND_PREFIX, LOG_GROUP_ID
from utils import LOGGER, progressArgs, log_file_to_group
from utils.helper import (
    get_readable_file_size,
    get_readable_time,
    get_video_thumbnail,
    safe_edit_progress,
    GLOBAL_DOWNLOAD_SEMAPHORE,
    GLOBAL_UPLOAD_SEMAPHORE,
)
from core import prem_plan1, prem_plan2, prem_plan3, user_activity_collection

# ─────────────────────────────────────────────────────────────────────────────
# 常量
# ─────────────────────────────────────────────────────────────────────────────

MAX_FILE_SIZE   = 2 * 1024 ** 3    # 2 GB
FREE_FILE_LIMIT = 500 * 1024 ** 2  # 500 MB
DOWNLOAD_DIR    = "tgdl_downloads"
PROGRESS_DELAY  = 2.5              # ✅ 从 3s 降低以获得更平滑的更新

os.makedirs(DOWNLOAD_DIR, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# 辅助函数
# ─────────────────────────────────────────────────────────────────────────────

async def _is_premium(user_id: int) -> bool:
    now = datetime.utcnow()
    for col in [prem_plan1, prem_plan2, prem_plan3]:
        doc = await col.find_one({"user_id": user_id})
        if doc and doc.get("expiry_date", now) > now:
            return True
    return False


def _get_media_obj(message: Message):
    """从 Pyrogram Message 中返回 (media_object, media_type_str)。"""
    if message.document:
        return message.document, "document"
    if message.video or message.animation or message.video_note:
        return (message.video or message.animation or message.video_note), "video"
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
# 上传辅助函数
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
    """将文件上传回 Telegram 并显示实时进度。"""
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
            await safe_edit_progress(
                status_msg,
                f"📤 **Upload হচ্ছে...**\n\n"
                f"`[{bar}]` {pct:.1f}%\n\n"
                f"📦 `{get_readable_file_size(current)}` / `{get_readable_file_size(total)}`\n"
                f"⚡ **Speed:** `{get_readable_file_size(speed)}/s`\n"
                f"⏳ **ETA:** `{get_readable_time(int(eta))}`",
            )
            last_edit[0] = now
        except Exception:
            pass

    video_exts = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv", ".m4v"}
    audio_exts = {".mp3", ".flac", ".ogg", ".opus", ".m4a", ".wav", ".aac"}
    ext        = os.path.splitext(file_path)[1].lower()

    # ✅ 使用全局上传信号量防止 MTProto 洪水并保持机器人响应能力
    async with GLOBAL_UPLOAD_SEMAPHORE:
        try:
            # 带有 FloodWait 重试逻辑的发送媒体函数
            async def send_with_retry(send_func):
                max_retries = 3
                retry_count = 0
                while retry_count < max_retries:
                    try:
                        return await send_func()
                    except FloodWait as e:
                        wait_time = e.value
                        retry_count += 1
                        LOGGER.warning(
                            f"[TgDL] FloodWait: waiting {wait_time} seconds "
                            f"(retry {retry_count}/{max_retries})"
                        )
                        try:
                            await safe_edit_progress(
                                status_msg,
                                f"**⏳ Telegram 需要等待 {wait_time} 秒...**\n"
                                f"__(重试 {retry_count}/{max_retries})__",
                            )
                        except Exception:
                            pass
                        await asyncio.sleep(wait_time)
                # 最后一次尝试，不进行重试
                return await send_func()

            if media_type == "video" or ext in video_exts:
                thumb = thumbnail_path
                if not thumb:
                    try:
                        thumb = await get_video_thumbnail(file_path, None)
                    except Exception:
                        thumb = None
                async def send_video():
                    return await client.send_video(
                        chat_id=chat_id,
                        video=file_path,
                        caption=caption,
                        thumb=thumb,
                        supports_streaming=True,
                        parse_mode=ParseMode.MARKDOWN,
                        progress=_progress,
                    )
                await send_with_retry(send_video)
                if thumb and thumb != thumbnail_path and os.path.exists(thumb):
                    os.remove(thumb)

            elif media_type == "audio" or ext in audio_exts:
                async def send_audio():
                    return await client.send_audio(
                        chat_id=chat_id,
                        audio=file_path,
                        caption=caption,
                        thumb=thumbnail_path,
                        parse_mode=ParseMode.MARKDOWN,
                        progress=_progress,
                    )
                await send_with_retry(send_audio)

            elif media_type == "photo":
                async def send_photo():
                    return await client.send_photo(
                        chat_id=chat_id,
                        photo=file_path,
                        caption=caption,
                        parse_mode=ParseMode.MARKDOWN,
                    )
                await send_with_retry(send_photo)

            else:
                # 默认：以文件形式发送
                async def send_document():
                    return await client.send_document(
                        chat_id=chat_id,
                        document=file_path,
                        caption=caption,
                        thumb=thumbnail_path,
                        parse_mode=ParseMode.MARKDOWN,
                        progress=_progress,
                    )
                await send_with_retry(send_document)

            elapsed = get_readable_time(int(time() - start_ts))
            await safe_edit_progress(
                status_msg,
                f"✅ **সফলভাবে পাঠানো হয়েছে!**\n\n"
                f"📦 `{get_readable_file_size(file_size)}`\n"
                f"⏱ সময়: `{elapsed}`",
            )

        except Exception as e:
            LOGGER.error(f"[TgDL] Upload failed: {e}")
            try:
                await safe_edit_progress(
                    status_msg,
                    f"❌ **Upload ব্যর্থ:**\n`{str(e)[:200]}`",
                )
            except Exception:
                pass
            raise


# ─────────────────────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────────────────────

async def _process_tg_download(
    client: Client,
    message: Message,
    source_msg: Message,
    status_msg: Message,
    is_premium: bool,
):
    """下载 Telegram 文件并重新上传到聊天中。"""
    user_id = message.from_user.id
    chat_id = message.chat.id

    media_obj, media_type = _get_media_obj(source_msg)
    if media_obj is None:
        await safe_edit_progress(
            status_msg,
            "❌ এই message-এ কোনো downloadable ফাইল নেই।",
        )
        return

    # ── 文件大小检查 ───────────────────────────────────────────────────────
    file_size   = getattr(media_obj, "file_size", 0) or 0
    max_allowed = MAX_FILE_SIZE if is_premium else FREE_FILE_LIMIT

    if file_size > max_allowed:
        await safe_edit_progress(
            status_msg,
            f"❌ **ফাইল অনেক বড়!**\n\n"
            f"📦 ফাইল: `{get_readable_file_size(file_size)}`\n"
            f"🚫 সীমা: `{get_readable_file_size(max_allowed)}`\n\n"
            + ("💎 Premium এ আপগ্রেড করুন: /plans" if not is_premium else ""),
        )
        return

    # ── 下载 ──────────────────────────────────────────────────────────────
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
            await safe_edit_progress(
                status_msg,
                f"⬇️ **Download হচ্ছে...**\n\n"
                f"`[{bar}]` {pct:.1f}%\n\n"
                f"📥 `{get_readable_file_size(current)}` / `{get_readable_file_size(total)}`\n"
                f"⚡ **Speed:** `{get_readable_file_size(speed)}/s`\n"
                f"⏳ **ETA:** `{get_readable_time(int(eta))}`",
            )
            last_edit[0] = now
        except Exception:
            pass

    try:
        # ✅ 使用全局下载信号量以防止过载
        async with GLOBAL_DOWNLOAD_SEMAPHORE:
            file_path = await source_msg.download(
                file_name=user_dir + "/",
                progress=_dl_progress,
            )
    except Exception as e:
        LOGGER.error(f"[TgDL] Download failed for user {user_id}: {e}")
        await safe_edit_progress(
            status_msg,
            f"❌ **Download ব্যর্থ:**\n`{str(e)[:200]}`",
        )
        return

    if not file_path or not os.path.exists(file_path):
        await safe_edit_progress(
            status_msg,
            "❌ ফাইল download সম্পন্ন হয়নি।",
        )
        return

    # ── 上传 ────────────────────────────────────────────────────────────────
    await safe_edit_progress(
        status_msg,
        "✅ **Download সম্পন্ন!**\n\n📤 Upload করা হচ্ছে...",
    )

    # 获取用户的自定义缩略图
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

        # 记录
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
# 注册处理函数
# ─────────────────────────────────────────────────────────────────────────────

def setup_tgdl_handler(app: Client):

    @app.on_message(
        filters.command("tgdl", prefixes=COMMAND_PREFIX)
        & (filters.private | filters.group)
    )
    async def tgdl_command(client: Client, message: Message):
        user_id = message.from_user.id

        # 必须回复包含文件的消息
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
