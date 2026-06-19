# Copyright @juktijol
# Channel t.me/juktijol
#
# plugins/rclonedl.py — Rclone Cloud Downloader
#
# Handles:
#   • /rclone <remote:path>  — download from any rclone-supported cloud
#
# ✅ Rclone config auto-detection (owner + per-user mrcc:)
# ✅ Real-time progress bar via rclone --progress parsing
# ✅ Single file or entire folder (zip)
# ✅ Premium / free size check
# ✅ Upload to Telegram after download

import os
import re
import asyncio
import zipfile
import shutil
import tempfile
from time import time
from datetime import datetime

from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import ParseMode
from pyrogram.handlers import MessageHandler

from config import COMMAND_PREFIX, LOG_GROUP_ID
from utils import LOGGER, log_file_to_group
from utils.helper import get_readable_file_size, get_readable_time, get_video_thumbnail
from core import prem_plan1, prem_plan2, prem_plan3, user_activity_collection

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

OWNER_RCLONE_CONF = "rclone.conf"
DOWNLOAD_DIR      = os.path.join(tempfile.gettempdir(), "rclonedl_downloads")
MAX_FILE_SIZE     = 2 * 1024 ** 3
FREE_FILE_LIMIT   = 500 * 1024 ** 2
PROGRESS_DELAY    = 3

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


def _get_config_path(user_id: int, path: str) -> tuple[str, str]:
    """
    Returns (config_path, cleaned_path).
    Supports `mrcc:remote:path` for user-specific rclone configs.
    """
    if path.startswith("mrcc:"):
        path        = path[5:]
        config_path = f"rclone/{user_id}.conf"
    else:
        config_path = OWNER_RCLONE_CONF
    return config_path, path


def _progress_bar(pct: float, length: int = 20) -> str:
    filled = int(length * pct / 100)
    return "▓" * filled + "░" * (length - filled)


def _parse_rclone_progress(line: str) -> dict | None:
    """
    Parse a rclone --progress output line.
    Returns dict with transferred, total, speed, eta or None.
    """
    pattern = (
        r"Transferred:\s+([\d.]+\s*\w+)\s*/\s*([\d.]+\s*\w+),\s+"
        r"([\d.]+%),\s+([\d.]+\s*\w+/s),\s+ETA\s+([\w]+)"
    )
    m = re.search(pattern, line)
    if m:
        return {
            "transferred": m.group(1).strip(),
            "total":       m.group(2).strip(),
            "percent":     m.group(3).strip(),
            "speed":       m.group(4).strip(),
            "eta":         m.group(5).strip(),
        }
    return None


async def _get_rclone_size(config_path: str, remote_path: str) -> int:
    """Get total size of remote path in bytes using `rclone size --json`."""
    import json as _json
    cmd = [
        "rclone", "size",
        "--fast-list",
        "--json",
        "--config", config_path,
        remote_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    try:
        data = _json.loads(stdout.decode())
        return data.get("bytes", 0)
    except Exception:
        return 0


async def _is_remote_dir(config_path: str, remote_path: str) -> bool:
    """Check if the remote path is a directory."""
    import json as _json
    cmd = [
        "rclone", "lsjson",
        "--fast-list", "--stat",
        "--no-mimetype", "--no-modtime",
        "--config", config_path,
        remote_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    try:
        data = _json.loads(stdout.decode())
        return data.get("IsDir", False)
    except Exception:
        return False


def _zip_directory(folder: str, zip_path: str):
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(folder):
            for f in files:
                fp  = os.path.join(root, f)
                arc = os.path.relpath(fp, os.path.dirname(folder))
                zf.write(fp, arc)


async def _upload_to_tg(
    client: Client,
    chat_id: int,
    file_path: str,
    caption: str,
    status_msg: Message,
    start_ts: float,
    thumbnail_path: str | None = None,
):
    """Upload a file to Telegram with progress."""
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
            chat_id, video=file_path, caption=caption,
            thumb=thumb, supports_streaming=True,
            parse_mode=ParseMode.MARKDOWN, progress=_progress,
        )
        if thumb and thumb != thumbnail_path and os.path.exists(thumb):
            os.remove(thumb)
    elif ext in audio_exts:
        await client.send_audio(
            chat_id, audio=file_path, caption=caption,
            thumb=thumbnail_path, parse_mode=ParseMode.MARKDOWN, progress=_progress,
        )
    else:
        await client.send_document(
            chat_id, document=file_path, caption=caption,
            thumb=thumbnail_path, parse_mode=ParseMode.MARKDOWN, progress=_progress,
        )

    elapsed = get_readable_time(int(time() - start_ts))
    await status_msg.edit_text(
        f"✅ **সফল!** `{os.path.basename(file_path)}`\n"
        f"📦 `{get_readable_file_size(file_size)}` | ⏱ `{elapsed}`",
        parse_mode=ParseMode.MARKDOWN,
    )


# ─────────────────────────────────────────────────────────────────────────────
# CORE PIPELINE
# ─────────────────────────────────────────────────────────────────────────────

async def _run_rclone_download(
    client: Client,
    message: Message,
    remote_path: str,
    config_path: str,
    status_msg: Message,
    is_premium: bool,
):
    user_id = message.from_user.id
    chat_id = message.chat.id

    # ── Validate config ───────────────────────────────────────────────────────
    if not os.path.exists(config_path):
        await status_msg.edit_text(
            f"❌ **Rclone config পাওয়া যায়নি:** `{config_path}`\n\n"
            "নিজের config ব্যবহার করতে `mrcc:remote:path` prefix দিন।",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # ── Get remote size ───────────────────────────────────────────────────────
    await status_msg.edit_text(
        "🔍 **Remote path যাচাই করা হচ্ছে...**",
        parse_mode=ParseMode.MARKDOWN,
    )

    total_size = await _get_rclone_size(config_path, remote_path)
    max_size   = MAX_FILE_SIZE if is_premium else FREE_FILE_LIMIT

    if total_size > max_size and total_size > 0:
        await status_msg.edit_text(
            f"❌ **ফাইল অনেক বড়!**\n\n"
            f"📦 `{get_readable_file_size(total_size)}` > `{get_readable_file_size(max_size)}`\n\n"
            + ("💎 Premium এ আপগ্রেড: /plans" if not is_premium else ""),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    is_dir = await _is_remote_dir(config_path, remote_path)

    # ── Prepare local destination ─────────────────────────────────────────────
    name    = remote_path.rstrip("/").rsplit("/", 1)[-1] or "rclone_download"
    dest    = os.path.join(DOWNLOAD_DIR, str(user_id), name)
    os.makedirs(os.path.dirname(dest), exist_ok=True)

    # ── Build rclone command ──────────────────────────────────────────────────
    cmd = [
        "rclone", "copy" if is_dir else "copyto",
        "--config",   config_path,
        "--progress",
        "--stats",    "2s",
        "--no-check-certificate",
        remote_path,
        dest if is_dir else dest,
    ]

    # ── Run rclone subprocess ─────────────────────────────────────────────────
    start_ts    = time()
    last_edit   = 0.0
    last_info   = {}

    await status_msg.edit_text(
        f"⬇️ **Rclone download শুরু হয়েছে...**\n\n"
        f"📡 `{remote_path}`\n"
        f"📦 মোট: `{get_readable_file_size(total_size) if total_size else 'অজানা'}`",
        parse_mode=ParseMode.MARKDOWN,
    )

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def _read_stderr():
        nonlocal last_edit, last_info
        async for raw in proc.stderr:
            line = raw.decode(errors="ignore").strip()
            info = _parse_rclone_progress(line)
            if info:
                last_info = info
            now = time()
            if info and now - last_edit >= PROGRESS_DELAY:
                try:
                    pct_str = info["percent"].rstrip("%")
                    pct     = float(pct_str) if pct_str.replace(".", "").isdigit() else 0
                    bar     = _progress_bar(pct)
                    await status_msg.edit_text(
                        f"⬇️ **Download হচ্ছে...**\n\n"
                        f"`[{bar}]` {info['percent']}\n\n"
                        f"📥 `{info['transferred']}` / `{info['total']}`\n"
                        f"⚡ **Speed:** `{info['speed']}`\n"
                        f"⏳ **ETA:** `{info['eta']}`",
                        parse_mode=ParseMode.MARKDOWN,
                    )
                    last_edit = now
                except Exception:
                    pass

    asyncio.create_task(_read_stderr())
    await proc.wait()

    if proc.returncode != 0:
        stderr = (await proc.stderr.read()).decode(errors="ignore")
        await status_msg.edit_text(
            f"❌ **Rclone download ব্যর্থ!**\n\n`{stderr[:400]}`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # ── Resolve final path ────────────────────────────────────────────────────
    if is_dir and os.path.isdir(dest):
        zip_path = dest.rstrip("/") + ".zip"
        await status_msg.edit_text(
            "📦 **Folder zip করা হচ্ছে...**",
            parse_mode=ParseMode.MARKDOWN,
        )
        await asyncio.get_event_loop().run_in_executor(None, _zip_directory, dest, zip_path)
        upload_path = zip_path
    elif os.path.isfile(dest):
        upload_path = dest
    else:
        # Might be single file copied to parent dir
        parent = os.path.dirname(dest)
        files  = [f for f in os.listdir(parent) if os.path.isfile(os.path.join(parent, f))]
        if files:
            upload_path = os.path.join(parent, files[0])
        else:
            await status_msg.edit_text(
                "❌ Download সম্পন্ন হয়েছে কিন্তু ফাইল খুঁজে পাওয়া যায়নি।",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

    # ── Upload ────────────────────────────────────────────────────────────────
    await status_msg.edit_text(
        "✅ **Download সম্পন্ন!**\n📤 Upload করা হচ্ছে...",
        parse_mode=ParseMode.MARKDOWN,
    )

    thumbnail_path = None
    try:
        user_data = await user_activity_collection.find_one({"user_id": user_id})
        thumbnail_path = user_data.get("thumbnail_path") if user_data else None
        if thumbnail_path and not os.path.exists(thumbnail_path):
            thumbnail_path = None
    except Exception:
        thumbnail_path = None

    file_sz  = os.path.getsize(upload_path)
    caption  = (
        f"📄 **{os.path.basename(upload_path)}**\n"
        f"📦 `{get_readable_file_size(file_sz)}`\n"
        f"📡 `{remote_path}`"
    )

    try:
        await _upload_to_tg(
            client, chat_id, upload_path, caption, status_msg, start_ts, thumbnail_path
        )
    finally:
        for p in [upload_path, dest]:
            try:
                if os.path.isfile(p):
                    os.remove(p)
                elif os.path.isdir(p):
                    shutil.rmtree(p, ignore_errors=True)
            except Exception:
                pass

    LOGGER.info(f"[RcloneDL] User {user_id} downloaded: {remote_path}")


# ─────────────────────────────────────────────────────────────────────────────
# SETUP
# ─────────────────────────────────────────────────────────────────────────────

def setup_rclonedl_handler(app: Client):

    @app.on_message(
        filters.command("rclone", prefixes=COMMAND_PREFIX)
        & (filters.private | filters.group)
    )
    async def rclone_dl_command(client: Client, message: Message):
        user_id = message.from_user.id

        if len(message.command) < 2:
            await message.reply_text(
                "**☁️ Rclone Cloud Downloader**\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "**ব্যবহার:** `/rclone <remote:path>`\n\n"
                "**Examples:**\n"
                "`/rclone gdrive:Movies/film.mkv`\n"
                "`/rclone dropbox:Backups/folder`\n"
                "`/rclone mrcc:mygdrive:path` _(আপনার নিজের config)_\n\n"
                "**Note:** Owner এর config-এ থাকা যেকোনো remote ব্যবহার করা যাবে।\n"
                "নিজের config যোগ করতে `/settings` দেখুন।",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        remote_path = message.command[1].strip()
        is_premium  = await _is_premium(user_id)

        # Determine config path
        config_path, cleaned_path = _get_config_path(user_id, remote_path)

        status_msg = await message.reply_text(
            f"🔄 **Rclone download শুরু হচ্ছে...**\n\n"
            f"📡 `{cleaned_path}`",
            parse_mode=ParseMode.MARKDOWN,
        )

        asyncio.create_task(
            _run_rclone_download(
                client, message, cleaned_path,
                config_path, status_msg, is_premium
            )
        )

    LOGGER.info("[RcloneDL] /rclone command handler registered.")
