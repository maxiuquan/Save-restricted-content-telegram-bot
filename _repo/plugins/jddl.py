# Copyright @juktijol
# Channel t.me/juktijol
#
# plugins/jddl.py — JDownloader Downloader
#
# Handles:
#   • /jd <URL>  — Any JDownloader-supported link
#
# ✅ Real-time download progress
# ✅ Premium / free file size check
# ✅ Supports any site JDownloader supports (1000+ sites)
# ✅ Auto upload to Telegram after download
# ✅ Cancel button support
# ✅ Cleanup after operation

import os
import shutil
import asyncio
import tempfile
from time import time
from datetime import datetime
from json import loads, dumps, JSONDecodeError

import aiohttp
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode
from pyrogram.handlers import MessageHandler

from config import COMMAND_PREFIX, LOG_GROUP_ID
from utils import LOGGER, log_file_to_group
from utils.helper import get_readable_file_size, get_readable_time, get_video_thumbnail
from core import (
    prem_plan1, prem_plan2, prem_plan3,
    daily_limit, user_activity_collection,
)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG  (set these in your .env or config.py)
# ─────────────────────────────────────────────────────────────────────────────

JD_HOST     = os.environ.get("JD_HOST",     "http://127.0.0.1:3128")
DOWNLOAD_DIR = os.path.join(tempfile.gettempdir(), "jddl_downloads")
PROGRESS_DELAY = 4
POLL_INTERVAL  = 3
MAX_WAIT_SECS  = 3600 * 6
MAX_FILE_SIZE   = 2 * 1024 ** 3
FREE_FILE_LIMIT = 500 * 1024 ** 2

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

_cancel_flags: dict = {}


# ─────────────────────────────────────────────────────────────────────────────
# JDownloader My.JD Local API CLIENT  (uses the local REST API on port 3128)
# ─────────────────────────────────────────────────────────────────────────────

class JDClient:
    """Thin async wrapper around JDownloader local API."""

    def __init__(self, host: str = JD_HOST):
        self.host = host.rstrip("/")

    async def _call(self, path: str, params=None) -> dict | list | None:
        payload = {"params": params or []}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.host}{path}",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        raise RuntimeError(f"JD API error {resp.status}: {text[:200]}")
                    data = await resp.json(content_type=None)
            return data.get("data")
        except aiohttp.ClientConnectorError:
            raise RuntimeError(
                "JDownloader Local API-তে connect করা যাচ্ছে না!\n"
                "নিশ্চিত করুন JDownloader চালু আছে এবং local API enabled।"
            )
        except Exception as e:
            raise RuntimeError(f"JD API call failed ({path}): {e}") from e

    async def clear_linkgrabber(self):
        return await self._call("/linkgrabberv2/clearList")

    async def add_links(self, url: str, dest_folder: str) -> bool:
        params = [{
            "autostart":             False,
            "links":                 url,
            "packageName":           None,
            "extractPassword":       None,
            "priority":              "DEFAULT",
            "downloadPassword":      None,
            "destinationFolder":     dest_folder,
            "overwritePackagizerRules": True,
            "deepDecrypt":           True,
        }]
        await self._call("/linkgrabberv2/addLinks", params)
        return True

    async def is_collecting(self) -> bool:
        result = await self._call("/linkgrabberv2/isCollecting")
        return bool(result)

    async def get_grabber_packages(self) -> list:
        params = [{
            "bytesTotal":  True, "childCount": True, "comment": True,
            "enabled":     True, "hosts":      True, "maxResults": -1,
            "saveTo":      True, "status":     True,
            "availableOnlineCount":  True,
            "availableOfflineCount": True,
        }]
        result = await self._call("/linkgrabberv2/queryPackages", params)
        return result or []

    async def move_to_downloadlist(self, package_ids: list):
        return await self._call("/linkgrabberv2/moveToDownloadlist",
                                 [[], package_ids])

    async def get_download_packages(self) -> list:
        params = [{
            "bytesLoaded": True, "bytesTotal": True, "childCount": True,
            "enabled":     True, "eta":        True, "finished":   True,
            "hosts":       True, "maxResults": -1,   "running":    True,
            "saveTo":      True, "speed":      True, "status":     True,
        }]
        result = await self._call("/downloadsV2/queryPackages", params)
        return result or []

    async def force_download(self, package_ids: list):
        return await self._call("/downloadsV2/forceDownload", [[], package_ids])

    async def remove_packages(self, package_ids: list, delete_files: bool = True):
        action         = "DELETE_ALL"
        mode           = "REMOVE_LINKS_AND_DELETE_FILES" if delete_files else "REMOVE_LINKS_ONLY"
        selection_type = "SELECTED"
        return await self._call("/downloadsV2/cleanup",
                                 [[], package_ids, action, mode, selection_type])

    async def start_downloads(self):
        return await self._call("/downloadcontroller/start")

    async def get_speed(self) -> int:
        result = await self._call("/downloadcontroller/getSpeedInBps")
        return int(result) if result else 0


jd = JDClient()


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


def _progress_bar(pct: float, length: int = 20) -> str:
    filled = int(length * pct / 100)
    return "▓" * filled + "░" * (length - filled)


def _find_downloaded_file(save_path: str) -> str | None:
    """Return path of largest file inside the download directory."""
    all_files = []
    for root, _, files in os.walk(save_path):
        for f in files:
            if not f.endswith((".aria2", ".part", ".crdownload")):
                fp = os.path.join(root, f)
                all_files.append(fp)
    return max(all_files, key=os.path.getsize) if all_files else None


# ─────────────────────────────────────────────────────────────────────────────
# UPLOAD HELPER
# ─────────────────────────────────────────────────────────────────────────────

async def _upload_to_telegram(
    client: Client, chat_id: int, file_path: str, caption: str,
    status_msg: Message, start_ts: float, thumbnail_path: str | None = None,
):
    file_size = os.path.getsize(file_path)
    ext       = os.path.splitext(file_path)[1].lower()
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

    video_exts = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv"}
    audio_exts = {".mp3", ".flac", ".ogg", ".opus", ".m4a", ".wav", ".aac"}

    if ext in video_exts:
        thumb = thumbnail_path
        if not thumb:
            try:
                thumb = await get_video_thumbnail(file_path, None)
            except Exception:
                thumb = None
        await client.send_video(
            chat_id=chat_id, video=file_path, caption=caption,
            thumb=thumb, supports_streaming=True,
            parse_mode=ParseMode.MARKDOWN, progress=_progress,
        )
        if thumb and thumb != thumbnail_path and os.path.exists(thumb):
            os.remove(thumb)
    elif ext in audio_exts:
        await client.send_audio(
            chat_id=chat_id, audio=file_path, caption=caption,
            thumb=thumbnail_path, parse_mode=ParseMode.MARKDOWN, progress=_progress,
        )
    else:
        await client.send_document(
            chat_id=chat_id, document=file_path, caption=caption,
            thumb=thumbnail_path, parse_mode=ParseMode.MARKDOWN, progress=_progress,
        )

    elapsed = get_readable_time(int(time() - start_ts))
    await status_msg.edit_text(
        f"✅ **সফলভাবে পাঠানো হয়েছে!**\n\n"
        f"📦 `{get_readable_file_size(file_size)}` | ⏱ `{elapsed}`",
        parse_mode=ParseMode.MARKDOWN,
    )


# ─────────────────────────────────────────────────────────────────────────────
# CORE PIPELINE
# ─────────────────────────────────────────────────────────────────────────────

async def _run_jd_download(
    client: Client,
    message: Message,
    url: str,
    save_path: str,
    status_msg: Message,
    is_premium: bool,
):
    user_id   = message.from_user.id
    chat_id   = message.chat.id
    start_ts  = time()
    last_edit = time()
    pkg_ids   = []

    try:
        # ── Clear linkgrabber & add link ──────────────────────────────────
        await jd.clear_linkgrabber()
        await jd.add_links(url, save_path)

        await status_msg.edit_text(
            "🔍 **Link বিশ্লেষণ করা হচ্ছে...**",
            parse_mode=ParseMode.MARKDOWN,
        )

        # ── Wait for grabber to collect ───────────────────────────────────
        for _ in range(30):
            await asyncio.sleep(1)
            if not await jd.is_collecting():
                break

        # ── Get grabber packages ──────────────────────────────────────────
        packages = await jd.get_grabber_packages()
        if not packages:
            await status_msg.edit_text(
                "❌ **Link থেকে কোনো download item পাওয়া যায়নি।**\n\n"
                "Link টি সঠিক কিনা যাচাই করুন।",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        pkg_ids = [p["uuid"] for p in packages]
        total   = sum(p.get("bytesTotal", 0) for p in packages)

        # ── Size check ────────────────────────────────────────────────────
        max_allowed = MAX_FILE_SIZE if is_premium else FREE_FILE_LIMIT
        if total > max_allowed and total > 0:
            await status_msg.edit_text(
                f"❌ **ফাইল অনেক বড়!**\n\n"
                f"📦 `{get_readable_file_size(total)}`\n"
                f"🚫 সীমা: `{get_readable_file_size(max_allowed)}`\n\n"
                f"{'💎 Premium এ আপগ্রেড: /plans' if not is_premium else ''}",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        name = packages[0].get("name", "JD Download")
        await status_msg.edit_text(
            f"📦 **{name[:60]}**\n\n"
            f"📊 Files: `{sum(p.get('childCount', 0) for p in packages)}`\n"
            f"📦 Size: `{get_readable_file_size(total)}`\n\n"
            "⬇️ Download শুরু হচ্ছে...",
            parse_mode=ParseMode.MARKDOWN,
        )

        # ── Move to download list & start ─────────────────────────────────
        await jd.move_to_downloadlist(pkg_ids)
        await asyncio.sleep(1)
        await jd.start_downloads()

        # ── Monitor download ──────────────────────────────────────────────
        deadline     = time() + MAX_WAIT_SECS
        dl_pkg_ids   = []
        finished_ids = set()

        while time() < deadline:
            if _cancel_flags.get(user_id):
                _cancel_flags.pop(user_id, None)
                if dl_pkg_ids:
                    await jd.remove_packages(dl_pkg_ids)
                try:
                    await status_msg.edit_text(
                        "⛔ **Download বাতিল করা হয়েছে।**",
                        parse_mode=ParseMode.MARKDOWN,
                    )
                except Exception:
                    pass
                return

            dl_packages = await jd.get_download_packages()
            # Filter to our packages by matching save path prefix
            our_packages = [
                p for p in dl_packages
                if p.get("saveTo", "").startswith(save_path)
            ]

            if not dl_pkg_ids and our_packages:
                dl_pkg_ids = [p["uuid"] for p in our_packages]

            if not our_packages and dl_pkg_ids:
                # All finished & removed by JD itself
                break

            total_loaded = sum(p.get("bytesLoaded", 0) for p in our_packages)
            total_size   = sum(p.get("bytesTotal",  0) for p in our_packages)
            speed        = await jd.get_speed()
            eta          = int((total_size - total_loaded) / speed) if speed > 0 else 0
            pct          = (total_loaded / total_size * 100) if total_size > 0 else 0
            elapsed      = time() - start_ts

            # Check if all finished
            all_finished = all(p.get("finished", False) for p in our_packages)
            if all_finished and our_packages:
                break

            if time() - last_edit >= PROGRESS_DELAY:
                bar = _progress_bar(pct)
                status = our_packages[0].get("status", "") if our_packages else ""
                try:
                    await status_msg.edit_text(
                        f"⬇️ **JDownloader Download**\n"
                        f"{f'`{status}`' if status else ''}\n\n"
                        f"`[{bar}]` {pct:.1f}%\n\n"
                        f"📥 `{get_readable_file_size(total_loaded)}` / `{get_readable_file_size(total_size)}`\n"
                        f"⚡ **Speed:** `{get_readable_file_size(speed)}/s`\n"
                        f"⏳ **ETA:** `{get_readable_time(eta) if eta else '...'}`\n"
                        f"⏱ **Elapsed:** `{get_readable_time(int(elapsed))}`",
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("⛔ বাতিল", callback_data=f"jd_cancel_{user_id}")
                        ]]),
                    )
                    last_edit = time()
                except Exception:
                    pass

            await asyncio.sleep(POLL_INTERVAL)

        else:
            if dl_pkg_ids:
                await jd.remove_packages(dl_pkg_ids)
            try:
                await status_msg.edit_text(
                    "⏰ **Timeout!** Download সম্পন্ন হয়নি।",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass
            return

        # ── Locate file ───────────────────────────────────────────────────
        await status_msg.edit_text(
            "✅ **Download সম্পন্ন!**\n\n📤 Upload করা হচ্ছে...",
            parse_mode=ParseMode.MARKDOWN,
        )

        upload_path = _find_downloaded_file(save_path)
        if not upload_path:
            await status_msg.edit_text(
                "❌ Downloaded ফাইল খুঁজে পাওয়া যায়নি।",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        # ── Thumbnail ─────────────────────────────────────────────────────
        thumbnail_path = None
        try:
            user_data = await user_activity_collection.find_one({"user_id": user_id})
            thumbnail_path = user_data.get("thumbnail_path") if user_data else None
            if thumbnail_path and not os.path.exists(thumbnail_path):
                thumbnail_path = None
        except Exception:
            thumbnail_path = None

        # ── Upload ────────────────────────────────────────────────────────
        file_sz = os.path.getsize(upload_path)
        caption = (
            f"📄 **{os.path.basename(upload_path)}**\n"
            f"📦 `{get_readable_file_size(file_sz)}`\n"
            f"🔗 `{url[:60]}`"
        )

        try:
            await _upload_to_telegram(
                client, chat_id, upload_path, caption,
                status_msg, start_ts, thumbnail_path,
            )
            if LOG_GROUP_ID:
                try:
                    await log_file_to_group(
                        bot=client, log_group_id=LOG_GROUP_ID,
                        user=message.from_user, url=url,
                        file_path=upload_path, media_type="document",
                        caption_original=caption, channel_name=None,
                        thumbnail_path=thumbnail_path,
                    )
                except Exception as e:
                    LOGGER.warning(f"[JDDl] Log error: {e}")
        except Exception as upload_err:
            LOGGER.error(f"[JDDl] Upload failed: {upload_err}")

        # ── Cleanup ───────────────────────────────────────────────────────
        if dl_pkg_ids:
            await jd.remove_packages(dl_pkg_ids, delete_files=True)

    except Exception as e:
        LOGGER.error(f"[JDDl] Pipeline error user={user_id}: {e}")
        try:
            await status_msg.edit_text(
                f"❌ **Error:**\n`{str(e)[:300]}`",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass
    finally:
        _cancel_flags.pop(user_id, None)
        try:
            if os.path.isdir(save_path):
                shutil.rmtree(save_path, ignore_errors=True)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# COMMAND HANDLER SETUP
# ─────────────────────────────────────────────────────────────────────────────

def setup_jddl_handler(app: Client):

    @app.on_message(
        filters.command(["jd", "jdownloader"], prefixes=COMMAND_PREFIX)
        & (filters.private | filters.group)
    )
    async def jd_dl_command(client: Client, message: Message):
        user_id    = message.from_user.id
        is_premium = await _is_premium(user_id)

        parts = message.text.split(None, 1)
        url   = parts[1].strip() if len(parts) > 1 else ""

        # Also accept reply text
        if not url and message.reply_to_message:
            url = (message.reply_to_message.text or "").strip()

        if not url:
            await message.reply_text(
                "**🔽 JDownloader Downloader**\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "**ব্যবহার:** `/jd <URL>`\n\n"
                "**Supported:** YouTube, Vimeo, Dailymotion,\n"
                "Uploaded, Rapidgator, Mediafire এবং\n"
                "আরও ১০০০+ সাইট!\n\n"
                "**Example:**\n"
                "`/jd https://www.mediafire.com/file/xxxxx`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        if not (url.startswith("http://") or url.startswith("https://")):
            url = "https://" + url

        LOGGER.info(f"[JDDl] User {user_id} requested: {url[:80]}")

        status_msg = await message.reply_text(
            "🔄 **JDownloader-এ link যোগ করা হচ্ছে...**",
            parse_mode=ParseMode.MARKDOWN,
        )

        user_dir = os.path.join(DOWNLOAD_DIR, str(user_id))
        os.makedirs(user_dir, exist_ok=True)

        asyncio.create_task(
            _run_jd_download(
                client, message, url, user_dir, status_msg, is_premium
            )
        )

    # ── Cancel callback ────────────────────────────────────────────────────
    @app.on_callback_query(filters.regex(r"^jd_cancel_(\d+)$"))
    async def jd_cancel_callback(client, callback_query):
        user_id = int(callback_query.data.split("_")[-1])
        if callback_query.from_user.id != user_id:
            await callback_query.answer("এটা আপনার download নয়!", show_alert=True)
            return
        _cancel_flags[user_id] = True
        try:
            await callback_query.message.edit_text(
                "⛔ **Cancel সংকেত পাঠানো হয়েছে...**",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass
        await callback_query.answer("বাতিলের অনুরোধ পাঠানো হয়েছে!")

    LOGGER.info("[JDDl] /jd command handler registered.")
