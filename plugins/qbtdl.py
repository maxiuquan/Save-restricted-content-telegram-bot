#
# plugins/qbtdl.py — qBittorrent 下载插件
#
# 命令：
#   /qbt <磁力链接>        → 通过磁力链接下载
#   /qbt <种子文件 URL>   → 通过种子文件 URL 下载
#   /qbt (回复 .torrent 文件)  → 通过 .torrent 文件下载
#
# 功能：
#   ✅ 实时进度条（下载 + 做种）
#   ✅ 高级用户/免费用户文件大小检查
#   ✅ 下载完成后自动检测文件并上传到 Telegram
#   ✅ 支持取消按钮
#   ✅ 每次操作后完整清理
#   ✅ 安全的 FloodWait 进度更新
#   ✅ 持久的 aiohttp 会话（无内存泄漏）

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
# 配置 — 在 .env 或 config.py 中设置这些值
# ─────────────────────────────────────────────────────────────────────────────

QBT_URL      = os.environ.get("QBT_URL",      "http://localhost:8090")
QBT_USERNAME = os.environ.get("QBT_USERNAME", "mltb")
QBT_PASSWORD = os.environ.get("QBT_PASSWORD")

DOWNLOAD_DIR    = os.path.join(tempfile.gettempdir(), "qbtdl_downloads")
PROGRESS_DELAY  = 4        # 进度消息编辑之间的秒数
POLL_INTERVAL   = 3        # qBittorrent 状态轮询之间的秒数
MAX_WAIT_SECS   = 3600 * 6 # 最长等待时间 6 小时
MAX_FILE_SIZE   = 2  * 1024 ** 3   # 2 GB — 高级用户
FREE_FILE_LIMIT = 500 * 1024 ** 2  # 500 MB — 免费用户

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# 活跃取消标志 — { torrent_hash: True }
_cancel_flags: dict = {}


# ─────────────────────────────────────────────────────────────────────────────
# qBittorrent Web API 客户端
# 使用单个持久化会话来避免内存泄漏。
# 如果 cookie 过期则自动重新登录。
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

    # ── 内部：获取或创建会话 ──────────────────────────────────────────

    async def _get_session(self) -> aiohttp.ClientSession:
        """Return existing session or create a new one."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                cookie_jar=aiohttp.CookieJar(),
                timeout=aiohttp.ClientTimeout(total=30),
            )
            self._logged_in = False
        return self._session

    # ── 登录 ─────────────────────────────────────────────────────────────

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

    # ── 确保已登录 ──────────────────────────────────────────────────────────

    async def _ensure_login(self):
        """Log in only if not already logged in."""
        if not self._logged_in:
            await self.login()

    # ── 通用请求 ───────────────────────────────────────────────────────────

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

            # 如果是 403 禁止访问，尝试重新登录一次
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
                "无法连接到 qBittorrent WebUI！\n"
                "请确保 qBittorrent 正在运行。"
            )

    # ── 关闭会话 ─────────────────────────────────────────────────────────────

    async def close(self):
        """Close the aiohttp session cleanly."""
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None
        self._logged_in = False

    # ── 添加磁力链接 ───────────────────────────────────────────────────────

    async def add_magnet(self, magnet: str, save_path: str) -> str:
        """
        Add a magnet link to qBittorrent.
        Returns the torrent hash (lowercase hex string).
        """
        await self._request(
            "POST", "torrents/add",
            data={"urls": magnet, "savepath": save_path, "autoTMM": "false"},
        )
        # 从磁力链接 URI 中提取哈希
        match = re.search(r"xt=urn:btih:([a-fA-F0-9]{40}|[a-zA-Z2-7]{32})", magnet)
        if match:
            raw = match.group(1)
            if len(raw) == 32:
                # Base32 → 十六进制转换
                raw = base64.b32decode(raw.upper()).hex()
            return raw.lower()
        return ""

    # ── 添加种子文件 ──────────────────────────────────────────────────────────

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

        # 等待片刻，然后获取最新的种子
        await asyncio.sleep(1.5)
        resp = await self._request("GET", "torrents/info", params={"sort": "added_on", "reverse": "true"})
        torrents = await resp.json()
        if torrents:
            return torrents[0]["hash"].lower()
        return ""

    # ── 添加种子 URL ───────────────────────────────────────────────────────────

    async def add_torrent_url(self, url: str, save_path: str) -> str:
        """Add a torrent via a direct URL (treated same as magnet)."""
        return await self.add_magnet(url, save_path)

    # ── 获取种子信息 ──────────────────────────────────────────────────────────

    async def get_torrent_info(self, torrent_hash: str) -> Optional[dict]:
        """Return torrent info dict, or None if not found."""
        resp = await self._request(
            "GET", "torrents/info",
            params={"hashes": torrent_hash},
        )
        data = await resp.json()
        return data[0] if data else None

    # ── 获取种子文件列表 ─────────────────────────────────────────────────────────

    async def get_torrent_files(self, torrent_hash: str) -> list:
        """Return a list of files inside the torrent."""
        resp = await self._request(
            "GET", "torrents/files",
            params={"hash": torrent_hash},
        )
        return await resp.json()

    # ── 移除种子 ────────────────────────────────────────────────────────────

    async def remove_torrent(self, torrent_hash: str, delete_files: bool = True):
        """Remove a torrent from qBittorrent (and optionally delete files)."""
        await self._request(
            "POST", "torrents/delete",
            data={
                "hashes": torrent_hash,
                "deleteFiles": "true" if delete_files else "false",
            },
        )

    # ── 暂停种子 ─────────────────────────────────────────────────────────────

    async def pause_torrent(self, torrent_hash: str):
        """Pause a torrent."""
        await self._request("POST", "torrents/pause", data={"hashes": torrent_hash})


# 全局唯一的客户端实例
qbt = QBittorrentClient()


# ─────────────────────────────────────────────────────────────────────────────
# 辅助函数
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
        "downloading":        "⬇️ **下载中**",
        "stalledDL":          "⏳ **等待中**（等待节点）",
        "metaDL":             "🔍 **获取元数据**",
        "checkingDL":         "🔎 **检查文件**",
        "checkingResumeData": "🔎 **检查恢复数据**",
        "queuedDL":           "📋 **队列中**",
        "pausedDL":           "⏸ **已暂停**",
        "error":              "❌ **错误**",
        "missingFiles":       "❓ **文件缺失**",
        "uploading":          "🌱 **做种中**",
        "stalledUP":          "🌱 **做种中**（已停滞）",
        "forcedDL":           "⬇️ **强制下载**",
        "forcedUP":           "🌱 **强制做种**",
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

    # 回退方案：遍历目录
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
        pass  # 文本内容相同 — 没有问题
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
# 上传到 Telegram
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
        # 每隔 PROGRESS_DELAY 秒更新一次（除非是最后一块）
        if now - last_edit[0] < PROGRESS_DELAY and current < total:
            return
        elapsed = now - upload_start[0]
        speed   = current / elapsed if elapsed > 0 else 0
        eta     = int((total - current) / speed) if speed > 0 else 0
        pct     = (current / total * 100) if total > 0 else 0
        bar     = _progress_bar(pct)

        text = (
            f"📤 **上传到 Telegram...**\n\n"
            f"`[{bar}]` {pct:.1f}%\n\n"
            f"📦 `{get_readable_file_size(current)}` / `{get_readable_file_size(total)}`\n"
            f"⚡ **速度：** `{get_readable_file_size(speed)}/s`\n"
            f"⏳ **预计：** `{get_readable_time(eta)}`\n\n"
            f"📄 `{os.path.basename(file_path)}`"
        )
        await _safe_edit(status_msg, text)
        last_edit[0] = now

    VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv", ".m4v"}
    AUDIO_EXTS = {".mp3", ".flac", ".ogg", ".opus", ".m4a", ".wav", ".aac"}

    try:
        if ext in VIDEO_EXTS:
            # 如果没有提供缩略图，则尝试生成
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

            # 清理自动生成的缩略图
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

        # ── 成功消息 ───────────────────────────────────────────────────
        elapsed = get_readable_time(int(time() - start_ts))
        await _safe_edit(
            status_msg,
            f"✅ **文件发送成功！**\n\n"
            f"📦 **大小：** `{get_readable_file_size(file_size)}`\n"
            f"⏱ **总耗时：** `{elapsed}`",
        )

    except Exception as e:
        LOGGER.error(f"[QBTDl] Upload error: {e}")
        raise


# ─────────────────────────────────────────────────────────────────────────────
# 核心下载 + 上传流程
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
        # ── 第 1 步：等待哈希出现（最多 15 秒）────────────────────
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
                    "❌ **添加种子失败！**\n\n"
                    "请检查 qBittorrent 是否运行后重试。",
                )
                return

        deadline = time() + MAX_WAIT_SECS

        # ── 第 2 步：轮询循环 ─────────────────────────────────────────────────
        while time() < deadline:

            # 检查取消标志
            if _cancel_flags.get(torrent_hash):
                _cancel_flags.pop(torrent_hash, None)
                await qbt.remove_torrent(torrent_hash)
                await _safe_edit(status_msg, "⛔ **下载已取消。**")
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

            # ── 文件大小检查 ───────────────────────────────────────────────
            if size > 0 and size > max_allowed:
                await qbt.remove_torrent(torrent_hash)
                upgrade_hint = "\n\n💎 升级到高级会员：/plans" if not is_premium else ""
                await _safe_edit(
                    status_msg,
                    f"❌ **文件过大！**\n\n"
                    f"📦 **文件大小:** `{get_readable_file_size(size)}`\n"
                    f"🚫 **你的限制:** `{get_readable_file_size(max_allowed)}`"
                    f"{upgrade_hint}",
                )
                return

            # ── 进度更新 ───────────────────────────────────────────────
            if time() - last_edit_ts >= PROGRESS_DELAY:
                bar        = _progress_bar(pct)
                state_text = _state_label(state)
                eta_text   = (
                    get_readable_time(eta_secs)
                    if eta_secs and eta_secs < 8_640_000
                    else "计算中..."
                )
                cancel_btn = InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "⛔ 取消",
                        callback_data=f"qbt_cancel_{torrent_hash}",
                    )
                ]])
                await _safe_edit(
                    status_msg,
                    f"{state_text}\n\n"
                    f"`[{bar}]` {pct:.1f}%\n\n"
                    f"📥 **已下载：** `{get_readable_file_size(dl_bytes)}` / `{get_readable_file_size(size)}`\n"
                    f"⚡ **速度：** `{get_readable_file_size(speed)}/s`\n"
                    f"⏳ **预计：** `{eta_text}`\n"
                    f"⏱ **已用时间：** `{get_readable_time(int(time() - start_ts))}`",
                    markup=cancel_btn,
                )
                last_edit_ts = time()

            # ── 检查下载是否完成 ─────────────────────────────────
            if state in ("uploading", "stalledUP", "forcedUP", "pausedUP", "queuedUP"):
                break  # 下载完成 — 进入上传阶段

            elif state == "error":
                err_msg = info.get("comment", "未知错误")
                await _safe_edit(
                    status_msg,
                    f"❌ **下载失败！**\n\n`{err_msg}`",
                )
                await qbt.remove_torrent(torrent_hash)
                return

            await asyncio.sleep(POLL_INTERVAL)

        else:
            # 循环结束但未 break → 超时
            await qbt.remove_torrent(torrent_hash)
            await _safe_edit(
                status_msg,
                "⏰ **超时！**\n\n下载未及时完成。",
            )
            return

        # ── 第 3 步：查找下载的文件 ─────────────────────────────────────
        await _safe_edit(
            status_msg,
            "✅ **下载完成！**\n\n📤 准备上传...",
        )

        torrent_files = await qbt.get_torrent_files(torrent_hash)
        info          = await qbt.get_torrent_info(torrent_hash)
        actual_save   = info.get("save_path", save_path) if info else save_path

        upload_path = _find_largest_file(actual_save, torrent_files)

        if not upload_path or not os.path.isfile(upload_path):
            await _safe_edit(
                status_msg,
                "❌ **找不到下载的文件。**\n\n请重试。",
            )
            await qbt.remove_torrent(torrent_hash)
            return

        # ── 第 4 步：获取用户缩略图（如果已保存）────────────────────────────────
        thumbnail_path: Optional[str] = None
        try:
            user_data = await user_activity_collection.find_one({"user_id": user_id})
            if user_data:
                tp = user_data.get("thumbnail_path")
                if tp and os.path.isfile(tp):
                    thumbnail_path = tp
        except Exception:
            pass

        # ── 第 5 步：上传前检查最终文件大小 ───────────────────────────
        file_sz = os.path.getsize(upload_path)
        if file_sz > max_allowed:
            upgrade_hint = "\n\n💎 升级到高级会员：/plans" if not is_premium else ""
            await _safe_edit(
                status_msg,
                f"❌ **文件过大无法上传！**\n\n"
                f"📦 `{get_readable_file_size(file_sz)}` > `{get_readable_file_size(max_allowed)}`"
                f"{upgrade_hint}",
            )
            await qbt.remove_torrent(torrent_hash)
            return

# ── 第 6 步：上传到 Telegram ────────────────────────────────────────
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
                f"❌ **上传失败！**\n\n`{str(upload_err)[:300]}`",
            )
            return

# ── 第 7 步：记录到日志群组（如果已配置）─────────────────────────────
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

# ── 移除种子（上传完成前保留文件）──────────────────────────────────
        await qbt.remove_torrent(torrent_hash, delete_files=True)

    except Exception as e:
        LOGGER.error(f"[QBTDl] Pipeline error — user={user_id}: {e}")
        await _safe_edit(
            status_msg,
            f"❌ **出了点问题！**\n\n`{str(e)[:300]}`",
        )
        try:
            await qbt.remove_torrent(torrent_hash)
        except Exception:
            pass

    finally:
        # 始终清理取消标志和临时文件夹
        _cancel_flags.pop(torrent_hash, None)
        user_dir = os.path.join(DOWNLOAD_DIR, str(user_id))
        if os.path.isdir(user_dir):
            shutil.rmtree(user_dir, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────────
# 命令处理器注册
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

        # ── 判断用户发送了什么 ─────────────────────────────────────────
        #
        # 优先级顺序：
        #   1. 回复一个 .torrent 文件附件
        #   2. 回复包含磁力链接/URL 的消息文本
        #   3. 命令后的参数（例如 /qbt magnet:?xt=...）

        if message.reply_to_message:
            doc = message.reply_to_message.document
            replied_text = (message.reply_to_message.text or "").strip()

            if doc and (
                doc.mime_type == "application/x-bittorrent"
                or (doc.file_name or "").endswith(".torrent")
            ):
                # 情况 1：回复了一个 .torrent 文件
                dl_msg = await message.reply_text(
                    "⬇️ **下载 .torrent 文件中...**",
                    parse_mode=ParseMode.MARKDOWN,
                )
                torrent_file_path = await message.reply_to_message.download()
                source_label      = doc.file_name or "torrent file"
                # 删除临时消息 — 我们会在下面复用它
                try:
                    await dl_msg.delete()
                except Exception:
                    pass

            elif replied_text:
# 情况 2：回复了包含磁力链接/URL 的消息文本
                args_text = replied_text

        if not torrent_file_path and not args_text:
            # 情况 3：命令后的参数
            parts = message.text.split(None, 1)
            args_text = parts[1].strip() if len(parts) > 1 else ""

        # ── 如果没有提供输入则显示帮助 ───────────────────────────────────
        if not torrent_file_path and not args_text:
            await message.reply_text(
                "🌊 **qBittorrent 下载**\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "**使用方法：**\n"
                "`/qbt <磁力链接>`\n"
                "`/qbt <种子文件 URL>`\n"
                "回复 `.torrent` 文件并输入 `/qbt`\n\n"
                "**支持输入类型：**\n"
                "• 磁力链接（`magnet:?xt=...`）\n"
                "• 直链种子文件 URL\n"
                "• `.torrent` 文件附件\n\n"
                "__提示：高级用户有 2GB 限制。免费用户有 500MB。__",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        if not source_label and args_text:
            source_label = args_text[:80]

        # ── 状态消息 ────────────────────────────────────────────────────
        status_msg = await message.reply_text(
            "🔄 **正在添加到 qBittorrent...**",
            parse_mode=ParseMode.MARKDOWN,
        )

        # ── 创建用户临时文件夹 ───────────────────────────────────────────
        user_dir = os.path.join(DOWNLOAD_DIR, str(user_id))
        os.makedirs(user_dir, exist_ok=True)

        try:
            # ── 添加到 qBittorrent ────────────────────────────────────────────
            if torrent_file_path:
                torrent_hash = await qbt.add_torrent_file(torrent_file_path, user_dir)
                # 添加后删除本地 .torrent 文件
                try:
                    os.remove(torrent_file_path)
                except Exception:
                    pass

            elif args_text.startswith("magnet:"):
                torrent_hash = await qbt.add_magnet(args_text, user_dir)

            else:
                torrent_hash = await qbt.add_torrent_url(args_text, user_dir)

            # ── 确认种子已添加 ─────────────────────────────────────────
            short_hash = (torrent_hash[:16] + "...") if torrent_hash else "detecting..."
            await _safe_edit(
                status_msg,
                f"✅ **种子已添加！**\n\n"
                f"🔑 **哈希：** `{short_hash}`\n"
                f"📡 **来源：** `{source_label[:60]}`\n\n"
                f"⏳ 开始下载...",
            )

            LOGGER.info(f"[QBTDl] User {user_id} added torrent — hash={torrent_hash[:12] if torrent_hash else 'unknown'}")

            # ── 在后台启动下载流程 ─────────────────────────────
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
                f"❌ **无法添加种子！**\n\n`{str(e)[:300]}`",
            )
            # 如果存在则清理下载的 .torrent 文件
            if torrent_file_path and os.path.exists(torrent_file_path):
                try:
                    os.remove(torrent_file_path)
                except Exception:
                    pass

    # ── 取消按钮回调 ────────────────────────────────────────────────────

    @app.on_callback_query(filters.regex(r"^qbt_cancel_(.+)$"))
    async def qbt_cancel_callback(client: Client, callback_query):
        """Handle the Cancel button press."""
        # 从回调数据中提取种子哈希
        torrent_hash = callback_query.data.split("_", 2)[-1]

        # 设置取消标志 — 轮询循环会检测到
        _cancel_flags[torrent_hash] = True

        await _safe_edit(
            callback_query.message,
            "⛔ **已发送取消请求...**\n\n请稍候。",
        )
        await callback_query.answer("已发送取消请求！")

    LOGGER.info("[QBTDl] /qbt command handler registered successfully.")
