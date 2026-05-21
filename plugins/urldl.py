# plugins/urldl.py
# External URL → Direct Telegram Upload (URLUploader integration)
# গুরুত্বপূর্ণ: t.me লিংক autolink.py handle করবে, বাকি সব external URL urldl.py handle করবে — কোনো conflict নেই।

import os
import re
import uuid
import time
import asyncio
import aiohttp
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)
from pyrogram.enums import ParseMode

from config import COMMAND_PREFIX, LOG_GROUP_ID
from utils import LOGGER
from utils.helper import get_readable_file_size, get_readable_time, get_video_thumbnail
from core import prem_plan1, prem_plan2, prem_plan3, daily_limit, user_activity_collection

# ─────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────

DOWNLOAD_DIR     = "url_downloads"
MAX_FILE_SIZE    = 2 * 1024 ** 3      # 2 GB (Premium)
FREE_FILE_LIMIT  = 500 * 1024 ** 2    # 500 MB (Free)
FREE_COOLDOWN    = 300                 # 5 minutes
PROGRESS_DELAY   = 3                   # seconds between edits
DB_TIMEOUT       = 5.0

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# URL Regex — http/https যেকোনো সরাসরি লিংক
URL_REGEX = re.compile(
    r'https?://[^\s<>"{}|\\^`\[\]]+'
)

# In-memory stores
_pending_downloads: dict = {}   # unique_id → {url, filename}
_pending_renames: dict   = {}   # user_id → url (rename mode)
_active_downloads: set   = set() # user_id → download চলছে


# ─────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────

async def _is_premium(user_id: int) -> bool:
    now = datetime.utcnow()
    for col in [prem_plan1, prem_plan2, prem_plan3]:
        try:
            doc = await asyncio.wait_for(
                col.find_one({"user_id": user_id}),
                timeout=DB_TIMEOUT
            )
            if doc and doc.get("expiry_date", now) > now:
                return True
        except Exception:
            pass
    return False


async def _check_cooldown(user_id: int, is_premium: bool) -> int:
    """Returns remaining cooldown in seconds (0 = can download)."""
    if is_premium:
        return 0
    try:
        now = datetime.utcnow()
        rec = await asyncio.wait_for(
            daily_limit.find_one({"user_id": user_id}),
            timeout=DB_TIMEOUT
        )
        if rec and rec.get("last_urldl"):
            elapsed = (now - rec["last_urldl"]).total_seconds()
            if elapsed < FREE_COOLDOWN:
                return int(FREE_COOLDOWN - elapsed)
        await asyncio.wait_for(
            daily_limit.update_one(
                {"user_id": user_id},
                {"$set": {"last_urldl": now}, "$inc": {"total_downloads": 1}},
                upsert=True
            ),
            timeout=DB_TIMEOUT
        )
    except Exception as e:
        LOGGER.warning(f"[URLDl] Cooldown error: {e}")
    return 0


def _progress_bar(pct: float, length: int = 20) -> str:
    filled = int(length * pct / 100)
    return "▓" * filled + "░" * (length - filled)


async def _get_file_info(url: str) -> tuple[int, str]:
    """Returns (file_size, filename)."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.head(
                url, allow_redirects=True,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                size = int(resp.headers.get("Content-Length", 0))

                # Content-Disposition থেকে filename
                cd = resp.headers.get("Content-Disposition", "")
                fn_match = re.findall(r'filename=["\']?([^"\';\n]+)', cd)
                if fn_match:
                    filename = fn_match[0].strip()
                else:
                    filename = url.split("/")[-1].split("?")[0] or "downloaded_file"

                return size, filename
    except Exception as e:
        LOGGER.warning(f"[URLDl] Head request failed: {e}")
        filename = url.split("/")[-1].split("?")[0] or "downloaded_file"
        return 0, filename


async def _stream_download(
    url: str,
    dest_path: str,
    status_msg: Message,
    display_name: str,
    max_size: int,
) -> bool:
    """aiohttp দিয়ে streaming download, real-time progress।"""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) "
            "Gecko/20100101 Firefox/122.0"
        )
    }
    try:
        connector = aiohttp.TCPConnector(ssl=False)
        timeout   = aiohttp.ClientTimeout(total=None, connect=30, sock_read=60)

        async with aiohttp.ClientSession(
            connector=connector, timeout=timeout
        ) as session:
            async with session.get(url, headers=headers, allow_redirects=True) as resp:

                if resp.status not in (200, 206):
                    await status_msg.edit_text(
                        f"❌ **服务器错误：HTTP {resp.status}**",
                        parse_mode=ParseMode.MARKDOWN
                    )
                    return False

                total    = int(resp.headers.get("Content-Length", 0))
                if total > 0 and total > max_size:
                    await status_msg.edit_text(
                        f"❌ **文件过大！**\n\n"
                        f"📦 大小：`{get_readable_file_size(total)}`\n"
                        f"🚫 限制：`{get_readable_file_size(max_size)}`",
                        parse_mode=ParseMode.MARKDOWN
                    )
                    return False

                downloaded = 0
                start_ts   = time.time()
                last_edit  = 0.0

                os.makedirs(os.path.dirname(dest_path), exist_ok=True)

                with open(dest_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(1024 * 512):
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)

                        now = time.time()
                        if now - last_edit >= PROGRESS_DELAY:
                            elapsed = now - start_ts
                            speed   = downloaded / elapsed if elapsed > 0 else 0
                            eta     = int((total - downloaded) / speed) if (speed > 0 and total > downloaded) else 0
                            pct     = (downloaded / total * 100) if total > 0 else 0
                            bar     = _progress_bar(pct)

                            size_txt = (
                                f"`{get_readable_file_size(downloaded)}` / `{get_readable_file_size(total)}`"
                                if total > 0 else
                                f"`{get_readable_file_size(downloaded)}`"
                            )
                            try:
                                await status_msg.edit_text(
                                    f"⬇️ **下载中...**\n\n"
                                    f"`[{bar}]`"
                                    + (f" {pct:.1f}%" if total > 0 else "") + "\n\n"
                                    f"📥 {size_txt}\n"
                                    f"⚡ **速度：** `{get_readable_file_size(speed)}/s`\n"
                                    f"⏳ **预计：** `{get_readable_time(eta) if eta else '...'}`\n\n"
                                    f"📄 `{display_name[:60]}`",
                                    parse_mode=ParseMode.MARKDOWN
                                )
                                last_edit = now
                            except Exception:
                                pass
        return True

    except asyncio.TimeoutError:
        try:
            await status_msg.edit_text(
                "❌ **下载超时！** 请重试。",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception:
            pass
        return False

    except Exception as e:
        LOGGER.error(f"[URLDl] Download error: {e}")
        try:
            await status_msg.edit_text(
                f"❌ **下载失败！**\n`{str(e)[:150]}`",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception:
            pass
        return False


async def _upload_to_telegram(
    client: Client,
    chat_id: int,
    file_path: str,
    caption: str,
    status_msg: Message,
    start_ts: float,
    thumbnail_path: str | None = None,
):
    """Pyrogram MTProto দিয়ে upload — 2GB পর্যন্ত সাপোর্ট।"""
    file_size = os.path.getsize(file_path)
    ext       = os.path.splitext(file_path)[1].lower()
    last_edit = [0.0]
    up_start  = [time.time()]

    async def _progress(current: int, total: int):
        now = time.time()
        if now - last_edit[0] < PROGRESS_DELAY and current < total:
            return
        elapsed = now - up_start[0]
        speed   = current / elapsed if elapsed > 0 else 0
        eta     = int((total - current) / speed) if speed > 0 else 0
        pct     = (current / total * 100) if total > 0 else 0
        bar     = _progress_bar(pct)
        try:
            await status_msg.edit_text(
                f"📤 **正在上传到 Telegram...**\n\n"
                f"`[{bar}]` {pct:.1f}%\n\n"
                f"📦 `{get_readable_file_size(current)}` / `{get_readable_file_size(total)}`\n"
                f"⚡ **速度：** `{get_readable_file_size(speed)}/s`\n"
                f"⏳ **预计：** `{get_readable_time(eta)}`",
                parse_mode=ParseMode.MARKDOWN
            )
            last_edit[0] = now
        except Exception:
            pass

    VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv", ".m4v"}
    AUDIO_EXTS = {".mp3", ".flac", ".ogg", ".opus", ".m4a", ".wav", ".aac"}

    if ext in VIDEO_EXTS:
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

    elif ext in AUDIO_EXTS:
        await client.send_audio(
            chat_id=chat_id,
            audio=file_path,
            caption=caption,
            parse_mode=ParseMode.MARKDOWN,
            progress=_progress,
        )
    else:
        await client.send_document(
            chat_id=chat_id,
            document=file_path,
            caption=caption,
            parse_mode=ParseMode.MARKDOWN,
            progress=_progress,
        )

    elapsed = get_readable_time(int(time.time() - start_ts))
    await status_msg.edit_text(
        f"✅ **上传成功！**\n\n"
        f"📦 `{get_readable_file_size(file_size)}` | ⏱ `{elapsed}`",
        parse_mode=ParseMode.MARKDOWN,
    )


# ─────────────────────────────────────────────────────────────────
# CORE PIPELINE
# ─────────────────────────────────────────────────────────────────

async def _process_url_download(
    client: Client,
    message: Message,
    url: str,
    filename: str,
    status_msg: Message,
    is_premium: bool,
):
    user_id = message.from_user.id
    chat_id = message.chat.id

    max_size   = MAX_FILE_SIZE if is_premium else FREE_FILE_LIMIT
    start_ts   = time.time()
    dest_path  = os.path.join(DOWNLOAD_DIR, str(user_id), filename)
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)

    # Thumbnail
    thumbnail_path = None
    try:
        user_data = await asyncio.wait_for(
            user_activity_collection.find_one({"user_id": user_id}),
            timeout=DB_TIMEOUT
        )
        thumbnail_path = user_data.get("thumbnail_path") if user_data else None
        if thumbnail_path and not os.path.exists(thumbnail_path):
            thumbnail_path = None
    except Exception:
        thumbnail_path = None

    try:
        # ── Download ──────────────────────────────────────────────
        ok = await _stream_download(url, dest_path, status_msg, filename, max_size)
        if not ok:
            return

        if not os.path.exists(dest_path) or os.path.getsize(dest_path) == 0:
            await status_msg.edit_text(
                "❌ **下载的文件为空或不存在。**",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        file_sz = os.path.getsize(dest_path)
        caption = (
            f"📄 **{filename}**\n"
            f"📦 `{get_readable_file_size(file_sz)}`\n"
            f"🔗 `{url[:80]}`"
        )

        # ── Upload ──────────────────────────────────────────────
        await status_msg.edit_text(
            f"✅ **下载完成！** 正在上传...\n\n"
            f"📄 `{filename}`\n"
            f"📦 `{get_readable_file_size(file_sz)}`",
            parse_mode=ParseMode.MARKDOWN
        )

        await _upload_to_telegram(
            client, chat_id, dest_path,
            caption, status_msg, start_ts, thumbnail_path
        )

        # Log to group
        if LOG_GROUP_ID:
            try:
                from utils.tracker import log_file_to_group
                await log_file_to_group(
                    bot=client,
                    log_group_id=LOG_GROUP_ID,
                    user=message.from_user,
                    url=url,
                    file_path=dest_path,
                    media_type="document",
                    caption_original=caption,
                )
            except Exception as e:
                LOGGER.warning(f"[URLDl] Log error: {e}")

    except Exception as e:
        LOGGER.error(f"[URLDl] Pipeline error: {e}")
        try:
            await status_msg.edit_text(
                f"❌ **意外错误！**\n`{str(e)[:200]}`",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception:
            pass
    finally:
        _active_downloads.discard(user_id)
        if os.path.exists(dest_path):
            try:
                os.remove(dest_path)
            except Exception:
                pass
        user_dir = os.path.join(DOWNLOAD_DIR, str(user_id))
        try:
            if os.path.isdir(user_dir) and not os.listdir(user_dir):
                os.rmdir(user_dir)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────
# SETUP
# ─────────────────────────────────────────────────────────────────

def setup_urldl_handler(app: Client):

    @app.on_message(
        filters.text & (filters.private | filters.group) &
        filters.create(lambda _, __, msg: (
            msg.text and URL_REGEX.search(msg.text) and
            not msg.text.strip().startswith("/") and
            "t.me" not in msg.text and
            "telegram.me" not in msg.text
        )),
        group=2
    )
    async def url_auto_detect(client: Client, message: Message):
        """
        Non-Telegram URL auto-detect।
        t.me লিংক autolink.py handle করবে, এখানে নয়।
        """
        user_id = message.from_user.id

        # pbatch session চেক
        import sys
        _pbatch = sys.modules.get("plugins.pbatch")
        if _pbatch:
            state = _pbatch.batch_data.get(message.chat.id)
            if state and state.get("user_id") == user_id:
                return

        url_match = URL_REGEX.search(message.text)
        if not url_match:
            return

        url = url_match.group(0).strip()
        is_premium = await _is_premium(user_id)

        # Cooldown check
        remaining = await _check_cooldown(user_id, is_premium)
        if remaining > 0:
            mins, secs = divmod(remaining, 60)
            await message.reply_text(
                f"⏳ **请等待 {mins}分{secs}秒后再进行下一次下载。**\n\n"
                f"💎 升级以取消冷却：/plans",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        if user_id in _active_downloads:
            await message.reply_text(
                "⏳ **你已有一个活跃的下载！**\n"
                "请等待它完成。",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        # File info fetch
        file_size, filename = await _get_file_info(url)
        max_size = MAX_FILE_SIZE if is_premium else FREE_FILE_LIMIT

        if file_size == 0:
            # Size পাওয়া যায়নি — warn করে তবু allow
            size_display = "大小未知"
        else:
            size_display = get_readable_file_size(file_size)
            if file_size > max_size:
                await message.reply_text(
                    f"❌ **文件大小超出你的套餐限制！**\n\n"
                    f"📦 **大小：** `{size_display}`\n"
                    f"🚫 **你的限制：** `{get_readable_file_size(max_size)}`\n\n"
                    + ("💎 升级到高级版：/plans" if not is_premium else ""),
                    parse_mode=ParseMode.MARKDOWN
                )
                return

        unique_id = str(uuid.uuid4())[:8]
        _pending_downloads[unique_id] = {"url": url, "filename": filename}

        await message.reply_text(
            f"🔗 **检测到链接！**\n\n"
            f"📄 **文件：** `{filename}`\n"
            f"📦 **大小：** `{size_display}`\n\n"
            f"**你想如何下载？**",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "⬇️ 下载",
                        callback_data=f"urldl_default_{unique_id}"
                    ),
                    InlineKeyboardButton(
                        "✏️ 重命名",
                        callback_data=f"urldl_rename_{unique_id}"
                    ),
                ],
                [InlineKeyboardButton("❌ 取消", callback_data=f"urldl_cancel_{unique_id}")]
            ])
        )

    @app.on_message(
        filters.command("urldl", prefixes=COMMAND_PREFIX) &
        (filters.private | filters.group)
    )
    async def urldl_command(client: Client, message: Message):
        """/urldl <URL> — সরাসরি command দিয়ে download।"""
        if len(message.command) < 2:
            await message.reply_text(
                "**📥 链接下载**\n\n"
                "**用法：** `/urldl <URL>`\n\n"
                "从任意直链将文件直接上传到 Telegram！\n\n"
                "**示例：**\n"
                "`/urldl https://example.com/file.mp4`\n\n"
                "**或者** 直接粘贴链接 — 机器人会自动检测！",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        user_id    = message.from_user.id
        url        = message.command[1].strip()
        is_premium = await _is_premium(user_id)

        remaining = await _check_cooldown(user_id, is_premium)
        if remaining > 0:
            mins, secs = divmod(remaining, 60)
            await message.reply_text(
                f"⏳ **等待 {mins}分{secs}秒后再进行下一次下载。**",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        if user_id in _active_downloads:
            await message.reply_text(
                "⏳ **下载正在进行中！** 请等待。",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        file_size, filename = await _get_file_info(url)
        max_size = MAX_FILE_SIZE if is_premium else FREE_FILE_LIMIT

        size_display = get_readable_file_size(file_size) if file_size > 0 else "未知"

        if file_size > max_size:
            await message.reply_text(
                f"❌ **文件过大！**\n"
                f"📦 `{size_display}` > `{get_readable_file_size(max_size)}`",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        unique_id = str(uuid.uuid4())[:8]
        _pending_downloads[unique_id] = {"url": url, "filename": filename}

        await message.reply_text(
            f"🔗 **链接准备下载**\n\n"
            f"📄 `{filename}`\n"
            f"📦 `{size_display}`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "⬇️ 下载",
                        callback_data=f"urldl_default_{unique_id}"
                    ),
                    InlineKeyboardButton(
                        "✏️ 重命名",
                        callback_data=f"urldl_rename_{unique_id}"
                    ),
                ],
                [InlineKeyboardButton("❌ 取消", callback_data=f"urldl_cancel_{unique_id}")]
            ])
        )

    @app.on_callback_query(filters.regex(r"^urldl_(default|rename|cancel)_(.+)$"))
    async def urldl_callback(client: Client, callback_query: CallbackQuery):
        data      = callback_query.data
        user_id   = callback_query.from_user.id
        chat_id   = callback_query.message.chat.id
        parts     = data.split("_", 3)
        action    = parts[1]
        unique_id = parts[2]

        info = _pending_downloads.get(unique_id)

        if action == "cancel":
            _pending_downloads.pop(unique_id, None)
            try:
                await callback_query.message.delete()
            except Exception:
                pass
            await callback_query.answer("已取消。")
            return

        if not info:
            await callback_query.answer("会话已过期！请重试。", show_alert=True)
            return

        url      = info["url"]
        filename = info["filename"]

        if action == "rename":
            _pending_renames[user_id] = {"url": url, "filename": filename, "unique_id": unique_id}
            try:
                await callback_query.message.edit_text(
                    "**✏️ 发送新文件名**（不含扩展名）：\n\n"
                    f"_当前：`{filename}`_",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception:
                pass
            await callback_query.answer()
            return

        if action == "default":
            _pending_downloads.pop(unique_id, None)
            _active_downloads.add(user_id)
            is_premium = await _is_premium(user_id)

            try:
                await callback_query.message.delete()
            except Exception:
                pass

            status_msg = await client.send_message(
                chat_id=chat_id,
                text=f"⬇️ **开始下载...**\n📄 `{filename}`",
                parse_mode=ParseMode.MARKDOWN
            )

            await callback_query.answer("开始下载...")

            asyncio.create_task(
                _process_url_download(
                    client, callback_query.message, url,
                    filename, status_msg, is_premium
                )
            )

    @app.on_message(
        filters.text & (filters.private | filters.group) &
        filters.create(lambda _, __, msg: (
            msg.from_user and msg.from_user.id in _pending_renames
        )),
        group=3
    )
    async def handle_rename_input(client: Client, message: Message):
        user_id = message.from_user.id
        rename_info = _pending_renames.get(user_id)
        if not rename_info:
            return

        new_name   = message.text.strip()
        url        = rename_info["url"]
        old_name   = rename_info["filename"]
        unique_id  = rename_info["unique_id"]

        # Extension সংরক্ষণ
        _, ext = os.path.splitext(old_name)
        new_filename = f"{new_name}{ext}" if ext else new_name

        _pending_renames.pop(user_id, None)
        _pending_downloads.pop(unique_id, None)
        _active_downloads.add(user_id)

        is_premium = await _is_premium(user_id)

        status_msg = await message.reply_text(
            f"⬇️ **开始下载...**\n📄 `{new_filename}`",
            parse_mode=ParseMode.MARKDOWN
        )

        asyncio.create_task(
            _process_url_download(
                client, message, url,
                new_filename, status_msg, is_premium
            )
        )
