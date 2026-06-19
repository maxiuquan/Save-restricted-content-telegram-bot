# Copyright @juktijol
# Channel t.me/juktijol
#
# plugins/aria2dl.py — Aria2c Downloader (Azure Safe — Pyrogram MTProto Upload)
#
# Commands:
#   /dl  <URL / magnet link>       → Direct link or magnet download
#   /dl  (reply to .torrent file)  → Torrent file download
#   /mirror <same as /dl>          → Alias command
#
# ✅ NO localhost/RPC dependency — Azure safe (subprocess mode)
# ✅ Pyrogram MTProto used for uploading (supports up to 2GB)
# ✅ Real-time progress via aria2c stdout parsing
# ✅ Multi-file torrent → uploads separately one by one (NO ZIP)
# ✅ Strict 2 GB Telegram upload limit per file
# ✅ Files >2GB are skipped, user is notified (download skipped entirely)
# ✅ HTTP links: pre-check size before downloading
# ✅ FloodWait + MessageNotModified safe edits
# ✅ Premium / Free user restriction + 5-min cooldown
# ✅ Safe cancel via asyncio.Event (no race condition)
# ✅ Python 3.9+ compatible

import asyncio
import os
import re
import shutil
import tempfile
from datetime import datetime
from time import time
from typing import Dict, List, Optional, Tuple

import aiohttp
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.errors import FloodWait, MessageNotModified
from pyrogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import COMMAND_PREFIX, LOG_GROUP_ID
from core import (
    daily_limit,
    prem_plan1,
    prem_plan2,
    prem_plan3,
    user_activity_collection,
)
from utils import LOGGER
from utils.helper import (
    get_readable_file_size,
    get_readable_time,
    get_video_thumbnail,
)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

DOWNLOAD_DIR    = os.path.join(tempfile.gettempdir(), "aria2dl_downloads")
PROGRESS_DELAY  = 3                   # Seconds between progress edits
MAX_WAIT_SECS   = 3600 * 6           # Max 6 hours per download
TELEGRAM_MAX    = 2 * 1024 ** 3      # 2 GB — Telegram strict limit (Pyrogram MTProto)
FREE_FILE_LIMIT = 500 * 1024 ** 2    # 500 MB — Free users
FREE_COOLDOWN   = 300                 # 5 minutes cooldown for free users
DB_TIMEOUT      = 5.0                 # Max seconds for DB response

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Cancel events — { message_id: asyncio.Event }
_cancel_events: Dict[int, asyncio.Event] = {}

# ─────────────────────────────────────────────────────────────────────────────
# FILE TYPE CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv", ".m4v"}
AUDIO_EXTS = {".mp3", ".flac", ".ogg", ".opus", ".m4a", ".wav", ".aac"}
SKIP_EXTS  = {".aria2", ".torrent"}

# ─────────────────────────────────────────────────────────────────────────────
# ARIA2C STDOUT PARSER
# ─────────────────────────────────────────────────────────────────────────────

# Matches: [#a1b2c3 10MiB/100MiB(10%) CN:16 DL:5.0MiB ETA:18s]
_PROGRESS_RE = re.compile(
    r"\[.*?"
    r"(?P<done>[0-9.]+[KMGT]?i?B)/"
    r"(?P<total>[0-9.]+[KMGT]?i?B)"
    r"\((?P<pct>\d+)%\)"
    r".*?DL:(?P<speed>[0-9.]+[KMGT]?i?B)/s"
    r"(?:.*?ETA:(?P<eta>[^\]\s]+))?"
    r".*?\]",
    re.IGNORECASE,
)


def _parse_progress(line: str) -> Optional[dict]:
    m = _PROGRESS_RE.search(line)
    if not m:
        return None
    return {
        "done":  m.group("done"),
        "total": m.group("total"),
        "pct":   int(m.group("pct")),
        "speed": m.group("speed"),
        "eta":   m.group("eta") or "...",
    }


def _size_to_bytes(size_str: str) -> int:
    """Convert aria2 size string (e.g., '1.2GiB', '500MiB') to bytes."""
    s = size_str.upper().strip()
    # Remove 'IB' suffix first, then 'B'
    s = s.replace("IB", "").replace("B", "").strip()
    try:
        if "K" in s: return int(float(s.replace("K", "")) * 1024)
        if "M" in s: return int(float(s.replace("M", "")) * 1024 ** 2)
        if "G" in s: return int(float(s.replace("G", "")) * 1024 ** 3)
        if "T" in s: return int(float(s.replace("T", "")) * 1024 ** 4)
        return int(float(s))
    except (ValueError, TypeError):
        return 0

# ─────────────────────────────────────────────────────────────────────────────
# PREMIUM / COOLDOWN / LOG HELPERS
# ─────────────────────────────────────────────────────────────────────────────

async def _is_premium(user_id: int) -> bool:
    now = datetime.utcnow()
    for col in [prem_plan1, prem_plan2, prem_plan3]:
        try:
            doc = await asyncio.wait_for(
                col.find_one({"user_id": user_id}), timeout=DB_TIMEOUT
            )
            if doc and doc.get("expiry_date", now) > now:
                return True
        except Exception:
            pass
    return False


async def _check_and_set_cooldown(user_id: int) -> Optional[str]:
    """Returns cooldown message if user must wait, else sets timestamp & returns None."""
    now = time()
    try:
        doc = await asyncio.wait_for(
            daily_limit.find_one({"user_id": user_id}), timeout=DB_TIMEOUT
        )
        if doc:
            last_dl  = doc.get("last_aria2dl_download", 0)
            elapsed  = now - last_dl
            if elapsed < FREE_COOLDOWN:
                remaining = int(FREE_COOLDOWN - elapsed)
                mins, secs = divmod(remaining, 60)
                return (
                    f"⏳ Please wait **{mins}m {secs}s** before your next download.\n\n"
                    f"💎 Upgrade for no limits: /plans"
                )
        await asyncio.wait_for(
            daily_limit.update_one(
                {"user_id": user_id},
                {"$set": {"last_aria2dl_download": now}},
                upsert=True,
            ),
            timeout=DB_TIMEOUT,
        )
    except Exception:
        pass
    return None


async def _increment_download_count(user_id: int):
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


async def _log_to_group(
    client: Client,
    user,
    source_url: str,
    file_name: str,
    file_size: int,
    is_premium: bool,
    success: bool,
    error_msg: str = "",
):
    if not LOG_GROUP_ID:
        return
    try:
        full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
        username  = f"@{user.username}" if user.username else "N/A"
        plan      = "💎 Premium" if is_premium else "🆓 Free"
        status    = "✅ Success" if success else "❌ Failed"
        size_str  = get_readable_file_size(file_size) if file_size > 0 else "N/A"

        text = (
            f"📥 **Aria2DL Log**\n{'─' * 30}\n"
            f"👤 **User:** [{full_name}](tg://user?id={user.id}) | `{user.id}`\n"
            f"📋 **Plan:** {plan} | {username}\n"
            f"📄 **File:** `{file_name}` | `{size_str}`\n"
            f"📊 **Status:** {status}\n"
        )
        if error_msg:
            text += f"❌ **Error:** `{error_msg[:200]}`\n"

        markup = None
        if source_url and source_url.startswith(("http", "magnet:")):
            btn_url = source_url if source_url.startswith("http") else "https://t.me"
            markup  = InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔗 Source", url=btn_url)]]
            )

        await client.send_message(
            LOG_GROUP_ID,
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    except Exception as e:
        LOGGER.warning(f"[Aria2DL] Log failed: {e}")

# ─────────────────────────────────────────────────────────────────────────────
# UI HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _progress_bar(pct: float, length: int = 20) -> str:
    filled = int(length * pct / 100)
    return "▓" * filled + "░" * (length - filled)


async def _safe_edit(msg: Message, text: str, markup=None):
    """Edit message safely — ignores MessageNotModified, handles FloodWait."""
    try:
        await msg.edit_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    except MessageNotModified:
        pass
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
# PRE-CHECK HTTP SIZE — Avoid downloading >limit files
# ─────────────────────────────────────────────────────────────────────────────

async def _check_http_size(url: str, max_allowed: int) -> Optional[str]:
    """
    Returns an error message string if the remote file exceeds max_allowed,
    otherwise returns None (OK to proceed).
    """
    if not url.startswith("http"):
        return None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.head(
                url,
                allow_redirects=True,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    length = int(resp.headers.get("Content-Length", 0))
                    if length > 0 and length > max_allowed:
                        return (
                            f"❌ **File too large!**\n\n"
                            f"📦 **Remote size:** `{get_readable_file_size(length)}`\n"
                            f"🚫 **Telegram limit:** `{get_readable_file_size(TELEGRAM_MAX)}`\n\n"
                            f"Telegram supports a maximum of **2 GB** per file upload.\n"
                            f"This file cannot be downloaded or uploaded."
                        )
    except Exception:
        pass
    return None

# ─────────────────────────────────────────────────────────────────────────────
# TELEGRAM UPLOAD — Pyrogram MTProto (supports full 2 GB)
# ─────────────────────────────────────────────────────────────────────────────

async def _upload_file(
    client: Client,
    chat_id: int,
    file_path: str,
    caption: str,
    status_msg: Message,
    file_num: int,
    total_files: int,
) -> int:
    """
    Upload a single file using Pyrogram MTProto.
    Returns the file size in bytes on success.

    Why Pyrogram MTProto?
    ─────────────────────
    • Bot API has a 50 MB upload limit (local server raises it, but
      localhost:6800 / local Bot API server is unavailable on Azure).
    • Pyrogram uses MTProto directly → supports up to 2 GB natively.
    • No external server required — Azure safe.
    """
    file_size = os.path.getsize(file_path)
    ext       = os.path.splitext(file_path)[1].lower()
    last_edit = [0.0]
    start_ts  = [time()]

    # ── Upload progress callback ─────────────────────────────────────────
    async def _on_progress(current: int, total: int):
        now = time()
        # Throttle edits — only update every PROGRESS_DELAY seconds
        if now - last_edit[0] < PROGRESS_DELAY and current < total:
            return

        pct      = (current / total * 100) if total > 0 else 0
        elapsed  = now - start_ts[0]
        speed    = current / elapsed if elapsed > 0 else 0
        eta      = int((total - current) / speed) if speed > 0 else 0
        bar      = _progress_bar(pct)

        header = (
            f"📤 **Uploading ({file_num}/{total_files})...**\n\n"
            if total_files > 1
            else "📤 **Uploading...**\n\n"
        )

        await _safe_edit(
            status_msg,
            f"{header}"
            f"`[{bar}]` {pct:.1f}%\n\n"
            f"📦 `{get_readable_file_size(current)}` / `{get_readable_file_size(total)}`\n"
            f"⚡ **Speed:** `{get_readable_file_size(speed)}/s`\n"
            f"⏳ **ETA:** `{get_readable_time(eta)}`\n"
            f"📡 **Method:** `Pyrogram MTProto`",
        )
        last_edit[0] = now

    # ── Determine media type and send ────────────────────────────────────
    thumb = None

    try:
        if ext in VIDEO_EXTS:
            # Try to generate thumbnail for videos
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

        elif ext in AUDIO_EXTS:
            await client.send_audio(
                chat_id=chat_id,
                audio=file_path,
                caption=caption,
                parse_mode=ParseMode.MARKDOWN,
                progress=_on_progress,
            )

        else:
            # Everything else as a document
            await client.send_document(
                chat_id=chat_id,
                document=file_path,
                caption=caption,
                parse_mode=ParseMode.MARKDOWN,
                progress=_on_progress,
            )

    finally:
        # Clean up temporary thumbnail
        if thumb and os.path.exists(thumb):
            try:
                os.remove(thumb)
            except Exception:
                pass

    return file_size

# ─────────────────────────────────────────────────────────────────────────────
# CORE DOWNLOAD + UPLOAD PIPELINE
# ─────────────────────────────────────────────────────────────────────────────

async def _run_download(
    client: Client,
    message: Message,
    source_url: str,
    torrent_path: Optional[str],
    status_msg: Message,
    is_premium: bool,
    cancel_event: asyncio.Event,
):
    user_id     = message.from_user.id
    chat_id     = message.chat.id
    start_ts    = time()
    max_allowed = TELEGRAM_MAX if is_premium else FREE_FILE_LIMIT

    # Unique temp directory per user + timestamp
    user_dir = os.path.join(DOWNLOAD_DIR, str(user_id), str(int(time())))
    os.makedirs(user_dir, exist_ok=True)

    final_name = ""
    final_size = 0
    success    = False
    error      = ""

    try:
        # ══════════════════════════════════════════════════════════════════
        # STEP 1 — Build aria2c subprocess command (NO --enable-rpc)
        # ══════════════════════════════════════════════════════════════════
        cmd = [
            "aria2c",
            "--enable-rpc=false",           # ← No RPC server (Azure safe)
            "--show-console-readout=true",
            "--console-log-level=notice",
            "--summary-interval=1",
            "--max-connection-per-server=16",
            "--split=16",
            "--min-split-size=5M",
            "--max-tries=5",
            "--retry-wait=5",
            "--continue=true",
            "--bt-enable-lpd=true",
            "--enable-dht=true",
            "--bt-save-metadata=true",
            f"--dir={user_dir}",
        ]

        if torrent_path:
            # aria2c detects .torrent files automatically
            cmd.append(torrent_path)
        else:
            cmd.append(source_url)

        # ══════════════════════════════════════════════════════════════════
        # STEP 2 — Launch subprocess and stream stdout for progress
        # ══════════════════════════════════════════════════════════════════
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        last_edit_ts = 0.0
        error_lines: List[str] = []

        cancel_btn = InlineKeyboardMarkup(
            [[InlineKeyboardButton(
                "⛔ Cancel",
                callback_data=f"aria2_cancel_{status_msg.id}"
            )]]
        )

        await _safe_edit(status_msg, "⬇️ **Downloading...**\n\n`Connecting...`", markup=cancel_btn)

        # ── Read stdout line by line ──────────────────────────────────────
        while True:
            # Check cancel
            if cancel_event.is_set():
                try:
                    process.kill()
                except Exception:
                    pass
                await _safe_edit(status_msg, "⛔ **Download cancelled by user.**")
                error = "Cancelled by user"
                return

            # Check timeout
            if time() - start_ts > MAX_WAIT_SECS:
                try:
                    process.kill()
                except Exception:
                    pass
                await _safe_edit(status_msg, "❌ **Download timed out (6 hours limit).**")
                error = "Timeout"
                return

            # Read one line with a short timeout to allow cancel checks
            try:
                raw = await asyncio.wait_for(process.stdout.readline(), timeout=1.0)
            except asyncio.TimeoutError:
                # No new output — check if process ended
                if process.returncode is not None:
                    break
                continue

            # Empty bytes = process stdout closed
            if not raw:
                if process.returncode is not None:
                    break
                continue

            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            LOGGER.debug(f"[Aria2DL] stdout: {line}")

            # Collect error lines for reporting
            if "FAILED" in line.upper() or " error" in line.lower():
                error_lines.append(line)

            # ── Parse and display progress ────────────────────────────────
            prog = _parse_progress(line)
            if prog:
                # Real-time size limit enforcement
                total_bytes = _size_to_bytes(prog["total"])
                if total_bytes > 0 and total_bytes > TELEGRAM_MAX:
                    # File exceeds Telegram 2 GB hard limit — kill immediately
                    try:
                        process.kill()
                    except Exception:
                        pass
                    error = (
                        f"Single file exceeds Telegram 2 GB limit: "
                        f"{prog['total']}"
                    )
                    await _safe_edit(
                        status_msg,
                        f"❌ **Cannot download this file!**\n\n"
                        f"📦 **File size:** `{prog['total']}`\n"
                        f"🚫 **Telegram limit:** `2 GB`\n\n"
                        f"Telegram supports a maximum of **2 GB** per file.\n"
                        f"This file cannot be uploaded to Telegram.",
                    )
                    return

                # Free user limit check
                if not is_premium and total_bytes > 0 and total_bytes > FREE_FILE_LIMIT:
                    try:
                        process.kill()
                    except Exception:
                        pass
                    error = f"File exceeds free limit: {prog['total']}"
                    await _safe_edit(
                        status_msg,
                        f"❌ **File too large for Free plan!**\n\n"
                        f"📦 **Size:** `{prog['total']}`\n"
                        f"🆓 **Free limit:** `{get_readable_file_size(FREE_FILE_LIMIT)}`\n"
                        f"💎 **Telegram max:** `2 GB`\n\n"
                        f"Upgrade to Premium for up to 2 GB: /plans",
                    )
                    return

                # Throttle UI updates
                now = time()
                if now - last_edit_ts >= PROGRESS_DELAY:
                    bar     = _progress_bar(prog["pct"])
                    elapsed = int(now - start_ts)
                    await _safe_edit(
                        status_msg,
                        f"⬇️ **Downloading...**\n\n"
                        f"`[{bar}]` {prog['pct']}%\n\n"
                        f"📥 `{prog['done']}` / `{prog['total']}`\n"
                        f"⚡ **Speed:** `{prog['speed']}/s`\n"
                        f"⏳ **ETA:** `{prog['eta']}`\n"
                        f"⏱ **Elapsed:** `{get_readable_time(elapsed)}`",
                        markup=cancel_btn,
                    )
                    last_edit_ts = now

        # ── Wait for process to fully exit ────────────────────────────────
        await process.wait()

        # ══════════════════════════════════════════════════════════════════
        # STEP 3 — Check exit code
        # ══════════════════════════════════════════════════════════════════
        if process.returncode != 0:
            err_detail = (
                " | ".join(error_lines[-3:])
                if error_lines
                else f"aria2c exited with code {process.returncode}"
            )
            await _safe_edit(
                status_msg,
                f"❌ **Download failed!**\n\n"
                f"`{err_detail[:400]}`",
            )
            error = err_detail
            return

        # ══════════════════════════════════════════════════════════════════
        # STEP 4 — Scan downloaded files
        # ══════════════════════════════════════════════════════════════════
        await _safe_edit(status_msg, "✅ **Download complete!**\n\n🔍 Scanning files...")

        # files_to_upload  → (filepath, size_bytes)
        # skipped_too_big  → (filename, size_bytes) — exceed Telegram 2 GB
        files_to_upload: List[Tuple[str, int]] = []
        skipped_too_big: List[Tuple[str, int]] = []

        for root, _, fnames in os.walk(user_dir):
            for fname in sorted(fnames):
                ext_check = os.path.splitext(fname)[1].lower()
                if ext_check in SKIP_EXTS:
                    continue
                fp  = os.path.join(root, fname)
                sz  = os.path.getsize(fp)

                if sz > TELEGRAM_MAX:
                    # Hard Telegram limit — cannot upload regardless of plan
                    skipped_too_big.append((fname, sz))
                    LOGGER.warning(
                        f"[Aria2DL] Skipping '{fname}' — "
                        f"{get_readable_file_size(sz)} exceeds 2 GB Telegram limit"
                    )
                elif not is_premium and sz > FREE_FILE_LIMIT:
                    # Free user limit — skip with note
                    skipped_too_big.append((fname, sz))
                else:
                    files_to_upload.append((fp, sz))

        # Nothing to upload
        if not files_to_upload and not skipped_too_big:
            await _safe_edit(status_msg, "❌ **No files found after download.**")
            error = "No files found"
            return

        # All files are too big
        if not files_to_upload and skipped_too_big:
            skip_text = "\n".join(
                f"• `{n}` — `{get_readable_file_size(s)}`"
                for n, s in skipped_too_big
            )
            await _safe_edit(
                status_msg,
                f"❌ **Cannot upload — all files exceed Telegram's 2 GB limit!**\n\n"
                f"**Skipped files:**\n{skip_text}\n\n"
                f"Telegram supports a maximum of **2 GB** per file upload.",
            )
            error = "All files exceed 2 GB limit"
            return

        # ══════════════════════════════════════════════════════════════════
        # STEP 5 — Upload files one by one via Pyrogram MTProto
        #
        # Key design:
        #   • Process one file → upload it → process next file
        #   • This avoids loading all files into memory simultaneously
        #   • Each file gets its own progress display
        #   • Cancel is checked between files
        # ══════════════════════════════════════════════════════════════════
        total_valid         = len(files_to_upload)
        total_uploaded_size = 0
        upload_errors: List[str] = []

        for idx, (fp, sz) in enumerate(
            sorted(files_to_upload, key=lambda x: os.path.basename(x[0])), start=1
        ):
            # Check cancel between uploads
            if cancel_event.is_set():
                error = "Cancelled during upload"
                await _safe_edit(status_msg, "⛔ **Upload cancelled by user.**")
                return

            name = os.path.basename(fp)
            caption = (
                f"📄 **{name}**\n"
                f"📦 `{get_readable_file_size(sz)}`"
                + (f"\n📎 File `{idx}/{total_valid}`" if total_valid > 1 else "")
                + f"\n\n📡 `Pyrogram MTProto`"
            )

            LOGGER.info(
                f"[Aria2DL] Uploading [{idx}/{total_valid}]: "
                f"'{name}' ({get_readable_file_size(sz)})"
            )

            try:
                uploaded_size = await _upload_file(
                    client, chat_id, fp, caption, status_msg, idx, total_valid
                )
                total_uploaded_size += uploaded_size
                success = True

            except FloodWait as fw:
                LOGGER.warning(f"[Aria2DL] FloodWait {fw.value}s during upload")
                await asyncio.sleep(fw.value + 2)
                # Retry once
                try:
                    uploaded_size = await _upload_file(
                        client, chat_id, fp, caption, status_msg, idx, total_valid
                    )
                    total_uploaded_size += uploaded_size
                    success = True
                except Exception as retry_err:
                    LOGGER.error(f"[Aria2DL] Upload retry failed: {retry_err}")
                    upload_errors.append(f"{name}: {str(retry_err)[:100]}")

            except Exception as upload_err:
                LOGGER.error(f"[Aria2DL] Upload error for '{name}': {upload_err}")
                upload_errors.append(f"{name}: {str(upload_err)[:100]}")

        # ══════════════════════════════════════════════════════════════════
        # STEP 6 — Final report
        # ══════════════════════════════════════════════════════════════════
        elapsed   = get_readable_time(int(time() - start_ts))
        report    = f"✅ **All done!** ⏱ `{elapsed}`\n\n"

        if total_valid > 0:
            report += (
                f"📤 **Uploaded:** `{total_valid}` file(s)\n"
                f"📦 **Total size:** `{get_readable_file_size(total_uploaded_size)}`\n"
                f"📡 **Method:** `Pyrogram MTProto`\n"
            )

        if skipped_too_big:
            report += f"\n⚠️ **Skipped (exceed Telegram 2 GB limit):**\n"
            for sf_name, sf_sz in skipped_too_big:
                report += f"• `{sf_name}` — `{get_readable_file_size(sf_sz)}`\n"

        if upload_errors:
            report += f"\n❌ **Upload errors:**\n"
            for ue in upload_errors:
                report += f"• `{ue}`\n"
            error = "; ".join(upload_errors)

        await _safe_edit(status_msg, report)

        # Increment stats only if at least one file was uploaded
        if success:
            asyncio.create_task(_increment_download_count(user_id))

        final_name = ", ".join(os.path.basename(fp) for fp, _ in files_to_upload)
        final_size = total_uploaded_size

    except Exception as pipeline_err:
        LOGGER.error(f"[Aria2DL] Pipeline error: {pipeline_err}", exc_info=True)
        error = str(pipeline_err)
        await _safe_edit(
            status_msg,
            f"❌ **Unexpected error!**\n\n`{str(pipeline_err)[:400]}`",
        )

    finally:
        # ── Cleanup ──────────────────────────────────────────────────────
        # Log to group
        asyncio.create_task(
            _log_to_group(
                client,
                message.from_user,
                source_url,
                final_name,
                final_size,
                is_premium,
                success,
                error,
            )
        )

        # Remove cancel event
        _cancel_events.pop(status_msg.id, None)

        # Delete temp download directory
        if os.path.isdir(user_dir):
            shutil.rmtree(user_dir, ignore_errors=True)
            LOGGER.info(f"[Aria2DL] Cleaned up: {user_dir}")

        # Delete downloaded torrent file
        if torrent_path and os.path.exists(torrent_path):
            try:
                os.remove(torrent_path)
            except Exception:
                pass

# ─────────────────────────────────────────────────────────────────────────────
# HANDLER REGISTRATION
# ─────────────────────────────────────────────────────────────────────────────

def setup_aria2dl_handler(app: Client):
    """Register /dl and /mirror command handlers + cancel callback."""

    @app.on_message(
        filters.command(["dl", "mirror"], prefixes=COMMAND_PREFIX)
        & (filters.private | filters.group)
    )
    async def aria2_dl_command(client: Client, message: Message):
        user_id = message.from_user.id

        # ── Verify aria2c is installed ────────────────────────────────────
        if not shutil.which("aria2c"):
            await message.reply_text(
                "❌ **aria2c is not installed on this server!**\n\n"
                "Ask admin to run: `sudo apt install aria2`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        # ── Plan + cooldown check ─────────────────────────────────────────
        is_premium  = await _is_premium(user_id)
        max_allowed = TELEGRAM_MAX if is_premium else FREE_FILE_LIMIT

        if not is_premium:
            cd_msg = await _check_and_set_cooldown(user_id)
            if cd_msg:
                await message.reply_text(cd_msg, parse_mode=ParseMode.MARKDOWN)
                return

        # ── Parse input ───────────────────────────────────────────────────
        torrent_path = None
        source_url   = ""
        args_text    = ""

        if message.reply_to_message:
            doc          = message.reply_to_message.document
            replied_text = (message.reply_to_message.text or "").strip()

            if doc and (
                doc.mime_type == "application/x-bittorrent"
                or (doc.file_name or "").endswith(".torrent")
            ):
                # Download the .torrent file locally
                init_msg = await message.reply_text(
                    "⬇️ **Fetching .torrent file...**",
                    parse_mode=ParseMode.MARKDOWN,
                )
                torrent_path = await message.reply_to_message.download()
                source_url   = doc.file_name or "torrent"
                try:
                    await init_msg.delete()
                except Exception:
                    pass

            elif replied_text:
                args_text = replied_text

        # Fall back to command arguments
        if not torrent_path and not args_text:
            parts     = message.text.split(None, 1)
            args_text = parts[1].strip() if len(parts) > 1 else ""

        if not torrent_path and not args_text:
            # Show usage help
            plan_info = (
                f"💎 **Premium:** up to `{get_readable_file_size(TELEGRAM_MAX)}` per file"
                if is_premium
                else (
                    f"🆓 **Free:** up to `{get_readable_file_size(FREE_FILE_LIMIT)}` per file "
                    f"| 5 min cooldown"
                )
            )
            await message.reply_text(
                "🌊 **Aria2 Downloader**\n"
                "━━━━━━━━━━━━━━━━━━━━━\n\n"
                "**Usage:**\n"
                "`/dl <URL>` — HTTP / HTTPS / FTP\n"
                "`/dl <magnet:...>` — Magnet link\n"
                "Reply to a `.torrent` file with `/dl`\n\n"
                f"📊 **Your plan:** {plan_info}\n\n"
                "📡 **Upload method:** Pyrogram MTProto\n"
                "🚫 **Hard limit:** 2 GB per file (Telegram)\n\n"
                "__Multi-file torrents are uploaded separately.__\n"
                "__Files >2 GB are skipped (Telegram limit).__",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        if not source_url and args_text:
            source_url = args_text

        # ── Pre-check remote HTTP file size ───────────────────────────────
        if not torrent_path and source_url.startswith("http"):
            size_err = await _check_http_size(source_url, max_allowed)
            if size_err:
                await message.reply_text(size_err, parse_mode=ParseMode.MARKDOWN)
                return

        # ── Start download task ───────────────────────────────────────────
        status_msg = await message.reply_text(
            "🔄 **Starting download...**",
            parse_mode=ParseMode.MARKDOWN,
        )

        cancel_event                       = asyncio.Event()
        _cancel_events[status_msg.id]      = cancel_event

        asyncio.create_task(
            _run_download(
                client,
                message,
                source_url,
                torrent_path,
                status_msg,
                is_premium,
                cancel_event,
            )
        )

    # ── Cancel button callback ────────────────────────────────────────────────

    @app.on_callback_query(filters.regex(r"^aria2_cancel_(\d+)$"))
    async def aria2_cancel_callback(client: Client, callback_query):
        msg_id = int(callback_query.data.split("_")[-1])
        event  = _cancel_events.get(msg_id)

        if event:
            event.set()
            await callback_query.answer("⛔ Cancelling download...")
            await _safe_edit(
                callback_query.message,
                "⛔ **Cancelling... please wait.**",
            )
        else:
            await callback_query.answer(
                "⚠️ No active download found for this session.",
                show_alert=True,
            )

    LOGGER.info(
        "[Aria2DL] Handlers registered: /dl /mirror | "
        "Upload: Pyrogram MTProto | Azure Safe | 2 GB limit enforced"
    )
