# Copyright @juktijol
# Channel t.me/juktijol
#
# plugins/nzbdl.py — SABnzbd / Usenet NZB Downloader
#
# Handles:
#   • /nzb <NZB URL>           → Download from Usenet via NZB URL
#   • /nzb (reply to .nzb)     → Download .nzb file attachment
#
# ✅ Real-time progress bar
# ✅ Premium / free size check
# ✅ Multi-stage status (Downloading → Verifying → Repairing → Extracting)
# ✅ Auto upload to Telegram after completion
# ✅ Cancel button support
# ✅ Cleanup after operation

import os
import shutil
import asyncio
import tempfile
from time import time
from datetime import datetime

import aiohttp
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode

from config import COMMAND_PREFIX, LOG_GROUP_ID
from utils import LOGGER, log_file_to_group
from utils.helper import get_readable_file_size, get_readable_time, get_video_thumbnail
from core import (
    prem_plan1, prem_plan2, prem_plan3,
    user_activity_collection,
)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

SAB_HOST     = os.environ.get("SAB_HOST",    "http://localhost:8070")
SAB_API_KEY  = os.environ.get("SAB_API_KEY", "mltb")

DOWNLOAD_DIR    = os.path.join(tempfile.gettempdir(), "nzbdl_downloads")
PROGRESS_DELAY  = 4
POLL_INTERVAL   = 4
MAX_WAIT_SECS   = 3600 * 6
MAX_FILE_SIZE   = 2 * 1024 ** 3
FREE_FILE_LIMIT = 500 * 1024 ** 2

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

_cancel_flags: dict = {}


# ─────────────────────────────────────────────────────────────────────────────
# SABnzbd API CLIENT
# ─────────────────────────────────────────────────────────────────────────────

class SABClient:
    """Thin async wrapper around SABnzbd API."""

    def __init__(self, host: str = SAB_HOST, api_key: str = SAB_API_KEY):
        self.base   = host.rstrip("/") + "/sabnzbd/api"
        self.params = {"apikey": api_key, "output": "json"}

    async def _call(self, params: dict) -> dict:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self.base,
                    params={**self.params, **params},
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    data = await resp.json(content_type=None)
            return data
        except aiohttp.ClientConnectorError:
            raise RuntimeError(
                "SABnzbd-এ connect করা যাচ্ছে না!\n"
                "নিশ্চিত করুন SABnzbd চালু আছে।"
            )

    async def add_url(self, nzb_url: str, cat: str = "*", dest: str = "") -> str:
        """Add NZB by URL. Returns nzo_id."""
        params: dict = {
            "mode":  "addurl",
            "name":  nzb_url,
            "cat":   cat,
            "pp":    "3",      # Download + Verify + Repair + Unpack
        }
        if dest:
            params["dir"] = dest
        data = await self._call(params)
        ids = data.get("nzo_ids", [])
        return ids[0] if ids else ""

    async def add_file(self, file_path: str, cat: str = "*", dest: str = "") -> str:
        """Add NZB from local file. Returns nzo_id."""
        params: dict = {
            "mode": "addlocalfile",
            "name": file_path,
            "cat":  cat,
            "pp":   "3",
        }
        if dest:
            params["dir"] = dest
        data = await self._call(params)
        ids = data.get("nzo_ids", [])
        return ids[0] if ids else ""

    async def get_queue(self, nzo_id: str = "") -> dict:
        params: dict = {"mode": "queue"}
        if nzo_id:
            params["nzo_ids"] = nzo_id
        return await self._call(params)

    async def get_history(self, nzo_id: str = "") -> dict:
        params: dict = {"mode": "history", "limit": "5"}
        if nzo_id:
            params["nzo_ids"] = nzo_id
        return await self._call(params)

    async def pause_job(self, nzo_id: str):
        return await self._call({"mode": "queue", "name": "pause", "value": nzo_id})

    async def delete_job(self, nzo_id: str, delete_files: bool = True):
        return await self._call({
            "mode":      "queue",
            "name":      "delete",
            "value":     nzo_id,
            "del_files": "1" if delete_files else "0",
        })

    async def delete_history(self, nzo_id: str, delete_files: bool = True):
        return await self._call({
            "mode":      "history",
            "name":      "delete",
            "value":     nzo_id,
            "del_files": "1" if delete_files else "0",
        })


sab = SABClient()


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


def _stage_emoji(status: str) -> str:
    s = status.lower()
    if "download" in s:  return "⬇️"
    if "verif"    in s:  return "🔎"
    if "repair"   in s:  return "🔧"
    if "extract"  in s:  return "📦"
    if "complet"  in s:  return "✅"
    if "fail"     in s:  return "❌"
    return "🔄"


def _find_completed_file(storage_path: str) -> str | None:
    """Find the largest file in the SABnzbd completed directory."""
    if not storage_path or not os.path.exists(storage_path):
        return None
    all_files = []
    for root, _, files in os.walk(storage_path):
        for f in files:
            fp = os.path.join(root, f)
            if os.path.isfile(fp) and not f.endswith((".nzb",)):
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

async def _run_nzb_download(
    client: Client,
    message: Message,
    nzo_id: str,
    nzb_name: str,
    status_msg: Message,
    is_premium: bool,
    source_url: str = "",
):
    user_id   = message.from_user.id
    chat_id   = message.chat.id
    start_ts  = time()
    last_edit = time()
    storage   = ""

    try:
        if not nzo_id:
            await status_msg.edit_text(
                "❌ **NZB job ID পাওয়া যায়নি।**",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        deadline = time() + MAX_WAIT_SECS

        while time() < deadline:
            if _cancel_flags.get(user_id):
                _cancel_flags.pop(user_id, None)
                await sab.delete_job(nzo_id)
                try:
                    await status_msg.edit_text(
                        "⛔ **Download বাতিল করা হয়েছে।**",
                        parse_mode=ParseMode.MARKDOWN,
                    )
                except Exception:
                    pass
                return

            # ── Check active queue ────────────────────────────────────────
            queue_data = await sab.get_queue(nzo_id)
            slots      = queue_data.get("queue", {}).get("slots", [])

            if slots:
                slot   = slots[0]
                status = slot.get("status", "Downloading")
                mb     = float(slot.get("mb", 0))
                mbleft = float(slot.get("mbleft", 0))
                total_mb = mb
                done_mb  = mb - mbleft
                speed_str = queue_data.get("queue", {}).get("kbpersec", "0")
                speed_kbps = float(speed_str) if speed_str else 0
                pct  = (done_mb / total_mb * 100) if total_mb > 0 else 0
                eta_str = slot.get("timeleft", "")

                # Size check
                total_bytes = total_mb * 1024 * 1024
                max_allowed = MAX_FILE_SIZE if is_premium else FREE_FILE_LIMIT
                if total_bytes > max_allowed and total_bytes > 0:
                    await sab.delete_job(nzo_id)
                    try:
                        await status_msg.edit_text(
                            f"❌ **ফাইল অনেক বড়!**\n\n"
                            f"📦 `{get_readable_file_size(int(total_bytes))}`\n"
                            f"🚫 সীমা: `{get_readable_file_size(max_allowed)}`\n\n"
                            f"{'💎 Premium এ আপগ্রেড: /plans' if not is_premium else ''}",
                            parse_mode=ParseMode.MARKDOWN,
                        )
                    except Exception:
                        pass
                    return

                if time() - last_edit >= PROGRESS_DELAY:
                    bar   = _progress_bar(pct)
                    emoji = _stage_emoji(status)
                    try:
                        await status_msg.edit_text(
                            f"{emoji} **{status}**\n\n"
                            f"`[{bar}]` {pct:.1f}%\n\n"
                            f"📥 `{done_mb:.1f} MB` / `{total_mb:.1f} MB`\n"
                            f"⚡ **Speed:** `{speed_kbps:.0f} KB/s`\n"
                            f"⏳ **ETA:** `{eta_str or '...'}`\n"
                            f"⏱ **Elapsed:** `{get_readable_time(int(time() - start_ts))}`",
                            parse_mode=ParseMode.MARKDOWN,
                            reply_markup=InlineKeyboardMarkup([[
                                InlineKeyboardButton("⛔ বাতিল", callback_data=f"nzb_cancel_{user_id}")
                            ]]),
                        )
                        last_edit = time()
                    except Exception:
                        pass

            else:
                # ── Check history (post-processing) ──────────────────────
                history_data = await sab.get_history(nzo_id)
                hist_slots   = history_data.get("history", {}).get("slots", [])

                if hist_slots:
                    slot    = hist_slots[0]
                    status  = slot.get("status", "")
                    storage = slot.get("storage", "")
                    fail    = slot.get("fail_message", "")
                    action  = slot.get("action_line", "")

                    if status == "Completed":
                        break
                    elif status == "Failed":
                        await status_msg.edit_text(
                            f"❌ **Download ব্যর্থ হয়েছে!**\n\n`{fail or 'Unknown error'}`",
                            parse_mode=ParseMode.MARKDOWN,
                        )
                        await sab.delete_history(nzo_id)
                        return

                    if time() - last_edit >= PROGRESS_DELAY:
                        emoji = _stage_emoji(status)
                        try:
                            await status_msg.edit_text(
                                f"{emoji} **Post-Processing: {status}**\n"
                                + (f"\n`{action}`" if action else ""),
                                parse_mode=ParseMode.MARKDOWN,
                                reply_markup=InlineKeyboardMarkup([[
                                    InlineKeyboardButton("⛔ বাতিল", callback_data=f"nzb_cancel_{user_id}")
                                ]]),
                            )
                            last_edit = time()
                        except Exception:
                            pass
                else:
                    # Both queue and history empty — still starting
                    pass

            await asyncio.sleep(POLL_INTERVAL)

        else:
            await sab.delete_job(nzo_id)
            try:
                await status_msg.edit_text(
                    "⏰ **Timeout!** Download সম্পন্ন হয়নি।",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass
            return

        # ── Locate completed file ─────────────────────────────────────────
        await status_msg.edit_text(
            "✅ **Download সম্পন্ন!**\n\n📤 Upload করা হচ্ছে...",
            parse_mode=ParseMode.MARKDOWN,
        )

        upload_path = _find_completed_file(storage)
        if not upload_path:
            # Fallback: search by name in SABnzbd complete directory
            sab_complete = os.path.join(
                os.path.expanduser("~"), "Downloads", "complete"
            )
            upload_path = _find_completed_file(sab_complete)

        if not upload_path:
            await status_msg.edit_text(
                "❌ Completed ফাইল খুঁজে পাওয়া যায়নি।\n"
                f"Storage path: `{storage}`",
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
            f"📦 `{get_readable_file_size(file_sz)}`"
            + (f"\n🔗 `{source_url[:60]}`" if source_url else "")
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
                        user=message.from_user, url=source_url or nzb_name,
                        file_path=upload_path, media_type="document",
                        caption_original=caption, channel_name=None,
                        thumbnail_path=thumbnail_path,
                    )
                except Exception as e:
                    LOGGER.warning(f"[NZBDl] Log error: {e}")
        except Exception as upload_err:
            LOGGER.error(f"[NZBDl] Upload failed: {upload_err}")

        # ── Cleanup ───────────────────────────────────────────────────────
        await sab.delete_history(nzo_id, delete_files=True)

    except Exception as e:
        LOGGER.error(f"[NZBDl] Pipeline error user={user_id}: {e}")
        try:
            await status_msg.edit_text(
                f"❌ **Error:**\n`{str(e)[:300]}`",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass
        try:
            await sab.delete_job(nzo_id)
        except Exception:
            pass
    finally:
        _cancel_flags.pop(user_id, None)


# ─────────────────────────────────────────────────────────────────────────────
# COMMAND HANDLER SETUP
# ─────────────────────────────────────────────────────────────────────────────

def setup_nzbdl_handler(app: Client):

    @app.on_message(
        filters.command(["nzb", "usenet"], prefixes=COMMAND_PREFIX)
        & (filters.private | filters.group)
    )
    async def nzb_dl_command(client: Client, message: Message):
        user_id    = message.from_user.id
        is_premium = await _is_premium(user_id)

        nzb_file_path = None
        source_url    = ""
        nzb_name      = "NZB Download"

        # ── Input: replied .nzb file ──────────────────────────────────────
        if message.reply_to_message:
            doc = message.reply_to_message.document
            if doc and (
                doc.mime_type in ("application/x-nzb", "text/nzb")
                or (doc.file_name or "").endswith(".nzb")
            ):
                status_msg = await message.reply_text(
                    "⬇️ **.nzb ফাইল download হচ্ছে...**",
                    parse_mode=ParseMode.MARKDOWN,
                )
                nzb_file_path = await message.reply_to_message.download()
                nzb_name      = doc.file_name or "nzb_file"

        # ── Input: URL from args ──────────────────────────────────────────
        if not nzb_file_path:
            parts      = message.text.split(None, 1)
            source_url = parts[1].strip() if len(parts) > 1 else ""
            if not source_url and message.reply_to_message:
                source_url = (message.reply_to_message.text or "").strip()

        if not nzb_file_path and not source_url:
            await message.reply_text(
                "**📰 Usenet / NZB Downloader**\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "**ব্যবহার:**\n"
                "`/nzb <NZB URL>`\n"
                "`.nzb` ফাইলে reply করে `/nzb`\n\n"
                "**Example:**\n"
                "`/nzb https://nzbindex.com/download/xxxxx`\n\n"
                "**Note:** Usenet সার্ভার configured থাকতে হবে।",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        status_msg = await message.reply_text(
            "🔄 **SABnzbd-এ NZB যোগ করা হচ্ছে...**",
            parse_mode=ParseMode.MARKDOWN,
        )

        try:
            # ── Add to SABnzbd ────────────────────────────────────────────
            user_dir = os.path.join(DOWNLOAD_DIR, str(user_id))
            os.makedirs(user_dir, exist_ok=True)

            if nzb_file_path:
                nzo_id = await sab.add_file(nzb_file_path, dest=user_dir)
                os.remove(nzb_file_path)
            else:
                nzo_id = await sab.add_url(source_url, dest=user_dir)

            if not nzo_id:
                await status_msg.edit_text(
                    "❌ **NZB যোগ করা যায়নি।**\n\n"
                    "SABnzbd চালু আছে কিনা এবং Usenet সার্ভার configured কিনা যাচাই করুন।",
                    parse_mode=ParseMode.MARKDOWN,
                )
                return

            await status_msg.edit_text(
                f"✅ **NZB Queue-এ যোগ হয়েছে!**\n\n"
                f"🆔 Job ID: `{nzo_id}`\n\n"
                "⏳ Download শুরু হচ্ছে...",
                parse_mode=ParseMode.MARKDOWN,
            )

            LOGGER.info(f"[NZBDl] User {user_id} added NZB nzo_id={nzo_id}")

            asyncio.create_task(
                _run_nzb_download(
                    client, message, nzo_id, nzb_name,
                    status_msg, is_premium, source_url,
                )
            )

        except Exception as e:
            LOGGER.error(f"[NZBDl] Failed to add NZB for {user_id}: {e}")
            try:
                await status_msg.edit_text(
                    f"❌ **NZB যোগ করা যায়নি:**\n\n`{str(e)[:300]}`",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass
            if nzb_file_path and os.path.exists(nzb_file_path):
                os.remove(nzb_file_path)

    # ── Cancel callback ────────────────────────────────────────────────────
    @app.on_callback_query(filters.regex(r"^nzb_cancel_(\d+)$"))
    async def nzb_cancel_callback(client, callback_query):
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

    LOGGER.info("[NZBDl] /nzb command handler registered.")
