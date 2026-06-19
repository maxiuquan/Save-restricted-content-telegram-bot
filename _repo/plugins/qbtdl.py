# Copyright @juktijol
# Channel t.me/juktijol
#
# plugins/qbtdl.py — qBittorrent Downloader Plugin
#
# Commands:
#   /qbt <magnet link>        → Download via magnet link
#   /qbt <torrent file URL>   → Download via torrent URL
#   /qbt (reply to .torrent)  → Download via .torrent file
#
# Features:
#   ✅ Real-time progress bar (download + seeding)
#   ✅ Premium / free user file size check
#   ✅ Auto file detection & Telegram upload after download
#   ✅ Cancel button support
#   ✅ Full cleanup after every operation
#   ✅ FloodWait safe progress updates
#   ✅ Persistent aiohttp session (no memory leaks)

import asyncio
import base64
import os
import re
import shutil
import tempfile
from datetime import datetime
from time import time
from typing import Optional

import aiohttp
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.errors import FloodWait, MessageNotModified
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import COMMAND_PREFIX, LOG_GROUP_ID
from core import (
    daily_limit,
    prem_plan1,
    prem_plan2,
    prem_plan3,
    user_activity_collection,
)
from utils import LOGGER, log_file_to_group
from utils.helper import get_readable_file_size, get_readable_time, get_video_thumbnail

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG — Set these in your .env or config.py
# ─────────────────────────────────────────────────────────────────────────────

QBT_URL      = os.environ.get("QBT_URL",      "http://localhost:8090")
QBT_USERNAME = os.environ.get("QBT_USERNAME", "mltb")
QBT_PASSWORD = os.environ.get("QBT_PASSWORD", "mltbmltb")

DOWNLOAD_DIR    = os.path.join(tempfile.gettempdir(), "qbtdl_downloads")
PROGRESS_DELAY  = 4        # Seconds between progress message edits
POLL_INTERVAL   = 3        # Seconds between qBittorrent status polls
MAX_WAIT_SECS   = 3600 * 6 # Max 6 hours wait time
MAX_FILE_SIZE   = 2  * 1024 ** 3   # 2 GB  — Premium users
FREE_FILE_LIMIT = 500 * 1024 ** 2  # 500 MB — Free users

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Active cancel flags — { torrent_hash: True }
_cancel_flags: dict = {}


# ─────────────────────────────────────────────────────────────────────────────
# qBittorrent Web API CLIENT
# Uses a single persistent session to avoid memory leaks.
# Auto re-login if cookie expires.
# ─────────────────────────────────────────────────────────────────────────────

class QBittorrentClient:
    """
    Simple async wrapper for qBittorrent WebUI API v2.

    Key improvements over the original:
    - One shared aiohttp.ClientSession (no new session per call).
    - Auto re-login when cookie is missing or expired.
    - Clean close() method to free resources.
    """

    def __init__(
        self,
        url: str      = QBT_URL,
        username: str = QBT_USERNAME,
        password: str = QBT_PASSWORD,
    ):
        self.url      = url.rstrip("/")
        self.username = username
        self.password = password
        self._session: Optional[aiohttp.ClientSession] = None
        self._logged_in = False

    # ── Internal: get or create session ──────────────────────────────────────

    async def _get_session(self) -> aiohttp.ClientSession:
        """Return existing session or create a new one."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                cookie_jar=aiohttp.CookieJar(),
                timeout=aiohttp.ClientTimeout(total=30),
            )
            self._logged_in = False
        return self._session

    # ── Login ─────────────────────────────────────────────────────────────────

    async def login(self) -> bool:
        """Log in to qBittorrent WebUI. Returns True on success."""
        session = await self._get_session()
        try:
            resp = await session.post(
                f"{self.url}/api/v2/auth/login",
                data={"username": self.username, "password": self.password},
            )
            text = await resp.text()
            if text.strip() == "Ok.":
                self._logged_in = True
                return True
            LOGGER.warning(f"[QBTDl] Login failed — response: {text.strip()}")
            return False
        except Exception as e:
            raise RuntimeError(f"Cannot connect to qBittorrent: {e}") from e

    # ── Ensure logged in ──────────────────────────────────────────────────────

    async def _ensure_login(self):
        """Log in only if not already logged in."""
        if not self._logged_in:
            await self.login()

    # ── Generic request ───────────────────────────────────────────────────────

    async def _request(self, method: str, endpoint: str, **kwargs) -> aiohttp.ClientResponse:
        """
        Make a GET or POST request to the qBittorrent API.
        Auto-retries login once if session seems expired.
        """
        await self._ensure_login()
        session = await self._get_session()
        url = f"{self.url}/api/v2/{endpoint}"
        try:
            if method == "POST":
                resp = await session.post(url, **kwargs)
            else:
                resp = await session.get(url, **kwargs)

            # If forbidden, try re-login once
            if resp.status == 403:
                self._logged_in = False
                await self.login()
                session = await self._get_session()
                if method == "POST":
                    resp = await session.post(url, **kwargs)
                else:
                    resp = await session.get(url, **kwargs)

            return resp

        except aiohttp.ClientConnectorError:
            raise RuntimeError(
                "Cannot connect to qBittorrent WebUI!\n"
                "Please make sure qBittorrent is running."
            )

    # ── Close session ─────────────────────────────────────────────────────────

    async def close(self):
        """Close the aiohttp session cleanly."""
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None
        self._logged_in = False

    # ── Add magnet link ───────────────────────────────────────────────────────

    async def add_magnet(self, magnet: str, save_path: str) -> str:
        """
        Add a magnet link to qBittorrent.
        Returns the torrent hash (lowercase hex string).
        """
        await self._request(
            "POST", "torrents/add",
            data={"urls": magnet, "savepath": save_path, "autoTMM": "false"},
        )
        # Extract hash from magnet URI
        match = re.search(r"xt=urn:btih:([a-fA-F0-9]{40}|[a-zA-Z2-7]{32})", magnet)
        if match:
            raw = match.group(1)
            if len(raw) == 32:
                # Base32 → hex conversion
                raw = base64.b32decode(raw.upper()).hex()
            return raw.lower()
        return ""

    # ── Add torrent file ──────────────────────────────────────────────────────

    async def add_torrent_file(self, file_path: str, save_path: str) -> str:
        """
        Upload a .torrent file to qBittorrent.
        Returns the hash of the newly added torrent.
        """
        with open(file_path, "rb") as f:
            torrent_data = f.read()

        form = aiohttp.FormData()
        form.add_field(
            "torrents", torrent_data,
            filename="file.torrent",
            content_type="application/x-bittorrent",
        )
        form.add_field("savepath", save_path)
        form.add_field("autoTMM", "false")

        await self._request("POST", "torrents/add", data=form)

        # Wait a moment then fetch the newest torrent
        await asyncio.sleep(1.5)
        resp = await self._request("GET", "torrents/info", params={"sort": "added_on", "reverse": "true"})
        torrents = await resp.json()
        if torrents:
            return torrents[0]["hash"].lower()
        return ""

    # ── Add torrent URL ───────────────────────────────────────────────────────

    async def add_torrent_url(self, url: str, save_path: str) -> str:
        """Add a torrent via a direct URL (treated same as magnet)."""
        return await self.add_magnet(url, save_path)

    # ── Get torrent info ──────────────────────────────────────────────────────

    async def get_torrent_info(self, torrent_hash: str) -> Optional[dict]:
        """Return torrent info dict, or None if not found."""
        resp = await self._request(
            "GET", "torrents/info",
            params={"hashes": torrent_hash},
        )
        data = await resp.json()
        return data[0] if data else None

    # ── Get torrent files ─────────────────────────────────────────────────────

    async def get_torrent_files(self, torrent_hash: str) -> list:
        """Return a list of files inside the torrent."""
        resp = await self._request(
            "GET", "torrents/files",
            params={"hash": torrent_hash},
        )
        return await resp.json()

    # ── Remove torrent ────────────────────────────────────────────────────────

    async def remove_torrent(self, torrent_hash: str, delete_files: bool = True):
        """Remove a torrent from qBittorrent (and optionally delete files)."""
        await self._request(
            "POST", "torrents/delete",
            data={
                "hashes": torrent_hash,
                "deleteFiles": "true" if delete_files else "false",
            },
        )

    # ── Pause torrent ─────────────────────────────────────────────────────────

    async def pause_torrent(self, torrent_hash: str):
        """Pause a torrent."""
        await self._request("POST", "torrents/pause", data={"hashes": torrent_hash})


# Single global client instance
qbt = QBittorrentClient()


# ─────────────────────────────────────────────────────────────────────────────
# HELPER FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

async def _is_premium(user_id: int) -> bool:
    """Check if a user has an active premium plan."""
    now = datetime.utcnow()
    for col in [prem_plan1, prem_plan2, prem_plan3]:
        doc = await col.find_one({"user_id": user_id})
        if doc and doc.get("expiry_date", now) > now:
            return True
    return False


def _progress_bar(pct: float, length: int = 20) -> str:
    """Return a text progress bar string. Example: ▓▓▓▓▓░░░░░"""
    filled = int(length * pct / 100)
    return "▓" * filled + "░" * (length - filled)


def _state_label(state: str) -> str:
    """Convert qBittorrent state string to a friendly label."""
    labels = {
        "downloading":        "⬇️ **Downloading**",
        "stalledDL":          "⏳ **Stalled** (waiting for peers)",
        "metaDL":             "🔍 **Fetching Metadata**",
        "checkingDL":         "🔎 **Checking Files**",
        "checkingResumeData": "🔎 **Checking Resume Data**",
        "queuedDL":           "📋 **Queued**",
        "pausedDL":           "⏸ **Paused**",
        "error":              "❌ **Error**",
        "missingFiles":       "❓ **Missing Files**",
        "uploading":          "🌱 **Seeding**",
        "stalledUP":          "🌱 **Seeding** (stalled)",
        "forcedDL":           "⬇️ **Forced Download**",
        "forcedUP":           "🌱 **Forced Seeding**",
    }
    return labels.get(state, f"🔄 **{state.capitalize()}**")


def _find_largest_file(save_path: str, files_info: list) -> Optional[str]:
    """
    Find and return the full path of the largest file in the torrent.
    Falls back to scanning the save_path directory if file list is empty.
    """
    best_path: Optional[str] = None
    best_size = 0

    for f in files_info:
        rel = f.get("name", "")
        full = os.path.join(save_path, rel)
        if os.path.isfile(full):
            sz = os.path.getsize(full)
            if sz > best_size:
                best_size = sz
                best_path = full

    if best_path:
        return best_path

    # Fallback: walk the directory
    all_files = []
    for root, _, fnames in os.walk(save_path):
        for fname in fnames:
            all_files.append(os.path.join(root, fname))

    return max(all_files, key=os.path.getsize) if all_files else None


async def _safe_edit(msg: Message, text: str, markup=None):
    """
    Safely edit a message text.
    - Ignores MessageNotModified errors (text didn't change).
    - Waits and retries on FloodWait.
    """
    try:
        await msg.edit_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    except MessageNotModified:
        pass  # Text was already the same — no problem
    except FloodWait as fw:
        await asyncio.sleep(fw.value + 1)
        try:
            await msg.edit_text(
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=markup,
                disable_web_page_preview=True,
            )
        except Exception:
            pass
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# UPLOAD TO TELEGRAM
# ─────────────────────────────────────────────────────────────────────────────

async def _upload_to_telegram(
    client: Client,
    chat_id: int,
    file_path: str,
    caption: str,
    status_msg: Message,
    start_ts: float,
    thumbnail_path: Optional[str] = None,
):
    """
    Upload a file to Telegram with a live progress bar.
    Automatically chooses: video / audio / document based on file extension.
    """
    file_size = os.path.getsize(file_path)
    ext       = os.path.splitext(file_path)[1].lower()
    last_edit = [0.0]
    upload_start = [time()]

    async def _progress(current: int, total: int):
        now = time()
        # Only update every PROGRESS_DELAY seconds (unless it's the final chunk)
        if now - last_edit[0] < PROGRESS_DELAY and current < total:
            return
        elapsed = now - upload_start[0]
        speed   = current / elapsed if elapsed > 0 else 0
        eta     = int((total - current) / speed) if speed > 0 else 0
        pct     = (current / total * 100) if total > 0 else 0
        bar     = _progress_bar(pct)

        text = (
            f"📤 **Uploading to Telegram...**\n\n"
            f"`[{bar}]` {pct:.1f}%\n\n"
            f"📦 `{get_readable_file_size(current)}` / `{get_readable_file_size(total)}`\n"
            f"⚡ **Speed:** `{get_readable_file_size(speed)}/s`\n"
            f"⏳ **ETA:** `{get_readable_time(eta)}`\n\n"
            f"📄 `{os.path.basename(file_path)}`"
        )
        await _safe_edit(status_msg, text)
        last_edit[0] = now

    VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv", ".m4v"}
    AUDIO_EXTS = {".mp3", ".flac", ".ogg", ".opus", ".m4a", ".wav", ".aac"}

    try:
        if ext in VIDEO_EXTS:
            # Try to generate thumbnail if not provided
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

            # Clean up auto-generated thumbnail
            if thumb and thumb != thumbnail_path and os.path.exists(thumb):
                os.remove(thumb)

        elif ext in AUDIO_EXTS:
            await client.send_audio(
                chat_id=chat_id,
                audio=file_path,
                caption=caption,
                thumb=thumbnail_path,
                parse_mode=ParseMode.MARKDOWN,
                progress=_progress,
            )
        else:
            await client.send_document(
                chat_id=chat_id,
                document=file_path,
                caption=caption,
                thumb=thumbnail_path,
                parse_mode=ParseMode.MARKDOWN,
                progress=_progress,
            )

        # ── Success message ───────────────────────────────────────────────
        elapsed = get_readable_time(int(time() - start_ts))
        await _safe_edit(
            status_msg,
            f"✅ **File sent successfully!**\n\n"
            f"📦 **Size:** `{get_readable_file_size(file_size)}`\n"
            f"⏱ **Total time:** `{elapsed}`",
        )

    except Exception as e:
        LOGGER.error(f"[QBTDl] Upload error: {e}")
        raise


# ─────────────────────────────────────────────────────────────────────────────
# CORE DOWNLOAD + UPLOAD PIPELINE
# ─────────────────────────────────────────────────────────────────────────────

async def _run_qbt_download(
    client: Client,
    message: Message,
    torrent_hash: str,
    save_path: str,
    status_msg: Message,
    is_premium: bool,
    source_label: str = "",
):
    """
    Main pipeline:
    1. Wait for torrent hash to appear in qBittorrent.
    2. Poll status and show live progress.
    3. On completion, find the largest file and upload it.
    4. Clean up temp files and remove torrent from qBittorrent.
    """
    user_id  = message.from_user.id
    chat_id  = message.chat.id
    start_ts = time()
    last_edit_ts = time()

    max_allowed = MAX_FILE_SIZE if is_premium else FREE_FILE_LIMIT

    try:
        # ── Step 1: Wait for hash to appear (max 15 seconds) ─────────────────
        if not torrent_hash:
            for _ in range(15):
                await asyncio.sleep(1)
                resp = await qbt._request(
                    "GET", "torrents/info",
                    params={"sort": "added_on", "reverse": "true"},
                )
                torrents = await resp.json()
                if torrents:
                    torrent_hash = torrents[0]["hash"].lower()
                    break
            else:
                await _safe_edit(
                    status_msg,
                    "❌ **Failed to add torrent!**\n\n"
                    "Please check if qBittorrent is running and try again.",
                )
                return

        deadline = time() + MAX_WAIT_SECS

        # ── Step 2: Poll loop ─────────────────────────────────────────────────
        while time() < deadline:

            # Check cancel flag
            if _cancel_flags.get(torrent_hash):
                _cancel_flags.pop(torrent_hash, None)
                await qbt.remove_torrent(torrent_hash)
                await _safe_edit(status_msg, "⛔ **Download cancelled.**")
                return

            info = await qbt.get_torrent_info(torrent_hash)
            if not info:
                await asyncio.sleep(POLL_INTERVAL)
                continue

            state    = info.get("state", "")
            size     = info.get("size", 0)
            dl_bytes = info.get("completed", 0)
            speed    = info.get("dlspeed", 0)
            progress = info.get("progress", 0.0)
            eta_secs = info.get("eta", 0)
            pct      = progress * 100

            # ── File size check ───────────────────────────────────────────
            if size > 0 and size > max_allowed:
                await qbt.remove_torrent(torrent_hash)
                upgrade_hint = "\n\n💎 Upgrade to Premium: /plans" if not is_premium else ""
                await _safe_edit(
                    status_msg,
                    f"❌ **File is too large!**\n\n"
                    f"📦 **File size:** `{get_readable_file_size(size)}`\n"
                    f"🚫 **Your limit:** `{get_readable_file_size(max_allowed)}`"
                    f"{upgrade_hint}",
                )
                return

            # ── Progress update ───────────────────────────────────────────
            if time() - last_edit_ts >= PROGRESS_DELAY:
                bar        = _progress_bar(pct)
                state_text = _state_label(state)
                eta_text   = (
                    get_readable_time(eta_secs)
                    if eta_secs and eta_secs < 8_640_000
                    else "Calculating..."
                )
                cancel_btn = InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "⛔ Cancel",
                        callback_data=f"qbt_cancel_{torrent_hash}",
                    )
                ]])
                await _safe_edit(
                    status_msg,
                    f"{state_text}\n\n"
                    f"`[{bar}]` {pct:.1f}%\n\n"
                    f"📥 **Downloaded:** `{get_readable_file_size(dl_bytes)}` / `{get_readable_file_size(size)}`\n"
                    f"⚡ **Speed:** `{get_readable_file_size(speed)}/s`\n"
                    f"⏳ **ETA:** `{eta_text}`\n"
                    f"⏱ **Elapsed:** `{get_readable_time(int(time() - start_ts))}`",
                    markup=cancel_btn,
                )
                last_edit_ts = time()

            # ── Check if download is complete ─────────────────────────────
            if state in ("uploading", "stalledUP", "forcedUP", "pausedUP", "queuedUP"):
                break  # Download done — move to upload step

            elif state == "error":
                err_msg = info.get("comment", "Unknown error")
                await _safe_edit(
                    status_msg,
                    f"❌ **Download failed!**\n\n`{err_msg}`",
                )
                await qbt.remove_torrent(torrent_hash)
                return

            await asyncio.sleep(POLL_INTERVAL)

        else:
            # Loop ended without break → timeout
            await qbt.remove_torrent(torrent_hash)
            await _safe_edit(
                status_msg,
                "⏰ **Timeout!**\n\nDownload did not complete in time.",
            )
            return

        # ── Step 3: Find the downloaded file ─────────────────────────────────
        await _safe_edit(
            status_msg,
            "✅ **Download complete!**\n\n📤 Preparing to upload...",
        )

        torrent_files = await qbt.get_torrent_files(torrent_hash)
        info          = await qbt.get_torrent_info(torrent_hash)
        actual_save   = info.get("save_path", save_path) if info else save_path

        upload_path = _find_largest_file(actual_save, torrent_files)

        if not upload_path or not os.path.isfile(upload_path):
            await _safe_edit(
                status_msg,
                "❌ **Could not find downloaded file.**\n\nPlease try again.",
            )
            await qbt.remove_torrent(torrent_hash)
            return

        # ── Step 4: Get user thumbnail (if saved) ─────────────────────────────
        thumbnail_path: Optional[str] = None
        try:
            user_data = await user_activity_collection.find_one({"user_id": user_id})
            if user_data:
                tp = user_data.get("thumbnail_path")
                if tp and os.path.isfile(tp):
                    thumbnail_path = tp
        except Exception:
            pass

        # ── Step 5: Check final file size before upload ───────────────────────
        file_sz = os.path.getsize(upload_path)
        if file_sz > max_allowed:
            upgrade_hint = "\n\n💎 Upgrade to Premium: /plans" if not is_premium else ""
            await _safe_edit(
                status_msg,
                f"❌ **File too large to upload!**\n\n"
                f"📦 `{get_readable_file_size(file_sz)}` > `{get_readable_file_size(max_allowed)}`"
                f"{upgrade_hint}",
            )
            await qbt.remove_torrent(torrent_hash)
            return

        # ── Step 6: Upload to Telegram ────────────────────────────────────────
        name    = os.path.basename(upload_path)
        caption = (
            f"📄 **{name}**\n"
            f"📦 `{get_readable_file_size(file_sz)}`"
            + (f"\n🔗 `{source_label}`" if source_label else "")
        )

        try:
            await _upload_to_telegram(
                client, chat_id, upload_path, caption,
                status_msg, start_ts, thumbnail_path,
            )
        except Exception as upload_err:
            LOGGER.error(f"[QBTDl] Upload failed for user {user_id}: {upload_err}")
            await _safe_edit(
                status_msg,
                f"❌ **Upload failed!**\n\n`{str(upload_err)[:300]}`",
            )
            return

        # ── Step 7: Log to group (if configured) ─────────────────────────────
        if LOG_GROUP_ID:
            try:
                await log_file_to_group(
                    bot=client,
                    log_group_id=LOG_GROUP_ID,
                    user=message.from_user,
                    url=source_label,
                    file_path=upload_path,
                    media_type="document",
                    caption_original=caption,
                    channel_name=None,
                    thumbnail_path=thumbnail_path,
                )
            except Exception as log_err:
                LOGGER.warning(f"[QBTDl] Log to group failed: {log_err}")

        # ── Remove torrent (keep files until upload is done) ──────────────────
        await qbt.remove_torrent(torrent_hash, delete_files=True)

    except Exception as e:
        LOGGER.error(f"[QBTDl] Pipeline error — user={user_id}: {e}")
        await _safe_edit(
            status_msg,
            f"❌ **Something went wrong!**\n\n`{str(e)[:300]}`",
        )
        try:
            await qbt.remove_torrent(torrent_hash)
        except Exception:
            pass

    finally:
        # Always clean up cancel flag and temp folder
        _cancel_flags.pop(torrent_hash, None)
        user_dir = os.path.join(DOWNLOAD_DIR, str(user_id))
        if os.path.isdir(user_dir):
            shutil.rmtree(user_dir, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────────
# COMMAND HANDLER REGISTRATION
# ─────────────────────────────────────────────────────────────────────────────

def setup_qbtdl_handler(app: Client):
    """Register all /qbt command and callback handlers."""

    @app.on_message(
        filters.command(["qbt", "qbittorrent"], prefixes=COMMAND_PREFIX)
        & (filters.private | filters.group)
    )
    async def qbt_dl_command(client: Client, message: Message):
        user_id    = message.from_user.id
        is_premium = await _is_premium(user_id)

        torrent_file_path: Optional[str] = None
        source_label = ""
        args_text    = ""

        # ── Figure out what the user sent ─────────────────────────────────────
        #
        # Priority order:
        #   1. Reply to a .torrent file attachment
        #   2. Reply to a message that contains a magnet/URL text
        #   3. Arguments after the command (e.g. /qbt magnet:?xt=...)

        if message.reply_to_message:
            doc = message.reply_to_message.document
            replied_text = (message.reply_to_message.text or "").strip()

            if doc and (
                doc.mime_type == "application/x-bittorrent"
                or (doc.file_name or "").endswith(".torrent")
            ):
                # Case 1: replied to a .torrent file
                dl_msg = await message.reply_text(
                    "⬇️ **Downloading .torrent file...**",
                    parse_mode=ParseMode.MARKDOWN,
                )
                torrent_file_path = await message.reply_to_message.download()
                source_label      = doc.file_name or "torrent file"
                # Delete the interim message — we'll reuse it below
                try:
                    await dl_msg.delete()
                except Exception:
                    pass

            elif replied_text:
                # Case 2: replied to a message with magnet/URL text
                args_text = replied_text

        if not torrent_file_path and not args_text:
            # Case 3: arguments after the command
            parts = message.text.split(None, 1)
            args_text = parts[1].strip() if len(parts) > 1 else ""

        # ── Show help if no input was given ───────────────────────────────────
        if not torrent_file_path and not args_text:
            await message.reply_text(
                "🌊 **qBittorrent Downloader**\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "**How to use:**\n"
                "`/qbt <magnet link>`\n"
                "`/qbt <torrent file URL>`\n"
                "Reply to a `.torrent` file with `/qbt`\n\n"
                "**Supported input types:**\n"
                "• Magnet links (`magnet:?xt=...`)\n"
                "• Direct torrent file URLs\n"
                "• `.torrent` file attachments\n\n"
                "__Tip: Premium users get a 2 GB limit. Free users get 500 MB.__",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        if not source_label and args_text:
            source_label = args_text[:80]

        # ── Status message ────────────────────────────────────────────────────
        status_msg = await message.reply_text(
            "🔄 **Adding torrent to qBittorrent...**",
            parse_mode=ParseMode.MARKDOWN,
        )

        # ── Create user temp folder ───────────────────────────────────────────
        user_dir = os.path.join(DOWNLOAD_DIR, str(user_id))
        os.makedirs(user_dir, exist_ok=True)

        try:
            # ── Add to qBittorrent ────────────────────────────────────────────
            if torrent_file_path:
                torrent_hash = await qbt.add_torrent_file(torrent_file_path, user_dir)
                # Remove the local .torrent file after adding
                try:
                    os.remove(torrent_file_path)
                except Exception:
                    pass

            elif args_text.startswith("magnet:"):
                torrent_hash = await qbt.add_magnet(args_text, user_dir)

            else:
                torrent_hash = await qbt.add_torrent_url(args_text, user_dir)

            # ── Confirm torrent was added ─────────────────────────────────────
            short_hash = (torrent_hash[:16] + "...") if torrent_hash else "detecting..."
            await _safe_edit(
                status_msg,
                f"✅ **Torrent added!**\n\n"
                f"🔑 **Hash:** `{short_hash}`\n"
                f"📡 **Source:** `{source_label[:60]}`\n\n"
                f"⏳ Starting download...",
            )

            LOGGER.info(f"[QBTDl] User {user_id} added torrent — hash={torrent_hash[:12] if torrent_hash else 'unknown'}")

            # ── Start download pipeline in background ─────────────────────────
            asyncio.create_task(
                _run_qbt_download(
                    client, message, torrent_hash,
                    user_dir, status_msg, is_premium, source_label,
                )
            )

        except Exception as e:
            LOGGER.error(f"[QBTDl] Failed to add torrent for user {user_id}: {e}")
            await _safe_edit(
                status_msg,
                f"❌ **Could not add torrent!**\n\n`{str(e)[:300]}`",
            )
            # Clean up downloaded .torrent file if it exists
            if torrent_file_path and os.path.exists(torrent_file_path):
                try:
                    os.remove(torrent_file_path)
                except Exception:
                    pass

    # ── Cancel button callback ────────────────────────────────────────────────

    @app.on_callback_query(filters.regex(r"^qbt_cancel_(.+)$"))
    async def qbt_cancel_callback(client: Client, callback_query):
        """Handle the Cancel button press."""
        # Extract torrent hash from callback data
        torrent_hash = callback_query.data.split("_", 2)[-1]

        # Set the cancel flag — the poll loop will pick it up
        _cancel_flags[torrent_hash] = True

        await _safe_edit(
            callback_query.message,
            "⛔ **Cancel request sent...**\n\nPlease wait a moment.",
        )
        await callback_query.answer("Cancel request sent!")

    LOGGER.info("[QBTDl] /qbt command handler registered successfully.")
