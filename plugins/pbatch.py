# Fixed: JSON persistence, proper cancel, improved progress tracking
# Fixed: All DB calls now use Motor async (await)
# ✅ FIXED: in_memory=True + no_updates=True → sqlite3 + TCPTransport error fix
# ✅ FIXED: AuthKeyUnregistered → session auto-remove + user notify
# ✅ FIXED: safe_stop_client → OSError ignore

import os
import re
import json
import asyncio
import shutil
import traceback
from time import time
from datetime import datetime
from pyrogram import Client, filters, raw
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode, ChatType
from pyrogram.errors import (
    ChannelInvalid,
    ChannelPrivate,
    PeerIdInvalid,
    FileReferenceExpired,
    AuthKeyUnregistered,
    FloodWait,
    ChatForwardsRestricted,
)
from pyleaves import Leaves
from config import COMMAND_PREFIX, LOG_GROUP_ID
from utils import (
    LOGGER,
    getChatMsgID,
    processMediaGroup,
    get_parsed_msg,
    fileSizeLimit,
    progressArgs,
    send_media_to_saved,
    log_file_to_group,
)
from utils.helper import create_optimized_user_client, safe_stop_client  # ✅ safe_stop_client added
from core import (
    daily_limit,
    prem_plan1,
    prem_plan2,
    prem_plan3,
    user_sessions,
    user_activity_collection,
)

# ── Persistence file ──────────────────────────────────────────────────────
BATCH_STATE_FILE = "batch_state.json"

# ── In-memory state ───────────────────────────────────────────────────────
batch_data: dict = {}

# ── Active download cancel flags ─────────────────────────────────────────
cancel_flags: dict = {}

# ── Link pattern ──────────────────────────────────────────────────────────
TELEGRAM_LINK_PATTERN = re.compile(
    r"(?:https?://)?(?:t\.me|telegram\.me)/(?:c/)?([a-zA-Z0-9_]+|\d+)/(\d+)(?:/\d+)?"
)


# ═════════════════════════════════════════════════════════════════════════
# PERSISTENCE HELPERS
# ═════════════════════════════════════════════════════════════════════════

def _load_state() -> dict:
    if not os.path.exists(BATCH_STATE_FILE):
        return {}
    try:
        with open(BATCH_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {int(k): v for k, v in data.items()}
    except Exception as e:
        LOGGER.error(f"[BatchPersist] Failed to load state: {e}")
        return {}


def _save_state():
    try:
        with open(BATCH_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({str(k): v for k, v in batch_data.items()}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        LOGGER.error(f"[BatchPersist] Failed to save state: {e}")


def _set_state(chat_id: int, data: dict):
    batch_data[chat_id] = data
    _save_state()


def _del_state(chat_id: int):
    batch_data.pop(chat_id, None)
    cancel_flags.pop(chat_id, None)
    _save_state()


def _get_free_disk_gb(path: str = ".") -> float:
    try:
        usage = shutil.disk_usage(path)
        return usage.free / (1024 ** 3)
    except Exception as e:
        LOGGER.warning(f"[DiskCheck] Failed to check disk space: {e}")
        return -1


def _cleanup_stale_downloads(base_dir: str = ".") -> float:
    freed_bytes = 0
    media_exts = {
        ".mp4", ".mkv", ".webm", ".mov", ".avi",
        ".jpg", ".jpeg", ".png", ".gif", ".webp",
        ".mp3", ".ogg", ".wav", ".flac",
        ".pdf", ".zip", ".rar", ".7z",
    }
    dirs_to_scan = [base_dir, os.path.join(base_dir, "downloads")]
    cleaned = 0

    for scan_dir in dirs_to_scan:
        if not os.path.isdir(scan_dir):
            continue
        try:
            for entry in os.scandir(scan_dir):
                if not entry.is_file(follow_symlinks=False):
                    continue
                ext = os.path.splitext(entry.name)[1].lower()
                if ext not in media_exts:
                    continue
                try:
                    file_size = entry.stat().st_size
                    os.remove(entry.path)
                    freed_bytes += file_size
                    cleaned += 1
                except OSError:
                    pass
        except OSError:
            pass

    if cleaned > 0:
        freed_gb = freed_bytes / (1024 ** 3)
        LOGGER.info(f"[DiskCheck] Cleaned {cleaned} stale downloads, freed {freed_gb:.2f}GB")
    return freed_bytes / (1024 ** 3)


async def _ensure_disk_space(status_message, chat_id, success_count, fail_count, idx, count) -> bool:
    free_gb = _get_free_disk_gb()
    if free_gb < 0:
        return True

    if free_gb < 1.0:
        LOGGER.warning(f"[DiskCheck] Low disk: {free_gb:.2f}GB, running cleanup...")
        freed = _cleanup_stale_downloads()
        free_gb = _get_free_disk_gb()
        LOGGER.warning(f"[DiskCheck] After cleanup: {free_gb:.2f}GB (freed {freed:.2f}GB)")

    if 0 < free_gb < 0.5:
        await status_message.edit_text(
            f"**⚠️ 磁盘空间不足！剩余 `{free_gb:.1f}GB`。**\n\n"
            f"**✅ 成功：** `{success_count}`  **❌ 失败：** `{fail_count}`\n"
            f"**📊 已处理：** `{idx - 1}/{count}`",
            parse_mode=ParseMode.MARKDOWN,
        )
        LOGGER.error(f"[DiskCheck] Still low after cleanup: {free_gb:.2f}GB, stopping")
        return False

    return True


# ═════════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════════

def is_private_link(url: str) -> bool:
    return bool(re.search(r"(?:t\.me|telegram\.me)/c/", url))


def _progress_text(done: int, total: int, success: int, fail: int, start_ts: float, is_private: bool) -> str:
    elapsed = time() - start_ts
    rate = done / elapsed if elapsed > 0 else 0
    eta = int((total - done) / rate) if rate > 0 else 0
    pct = (done / total * 100) if total else 0

    bar_len = 10
    filled = int(bar_len * done / total) if total else 0
    bar = "▓" * filled + "░" * (bar_len - filled)

    label = "🔒 Private" if is_private else "✅ Public"
    eta_str = f"{eta // 60}m {eta % 60}s" if eta >= 60 else f"{eta}s"

    return (
        f"**{label} 批量下载**\n\n"
        f"`[{bar}]` {pct:.1f}%\n\n"
        f"**📥 进度：** `{done}/{total}`\n"
        f"**✅ 成功：** `{success}`  **❌ 失败：** `{fail}`\n"
        f"**⏱ 耗时：** `{int(elapsed)}s`  **⏳ 预计：** `{eta_str}`\n\n"
        f"__发送 /stop 取消__"
    )


# ═════════════════════════════════════════════════════════════════════════
# PLAN CHECK
# ═════════════════════════════════════════════════════════════════════════

async def is_premium_user(user_id: int) -> bool:
    current_time = datetime.utcnow()
    for col in [prem_plan1, prem_plan2, prem_plan3]:
        doc = await col.find_one({"user_id": user_id})
        if doc and doc.get("expiry_date", current_time) > current_time:
            return True
    return False


# ═════════════════════════════════════════════════════════════════════════
# SHARED BATCH START
# ═════════════════════════════════════════════════════════════════════════

async def handle_batch_start(client: Client, message: Message):
    user_id = message.from_user.id
    chat_id = message.chat.id

    if not await is_premium_user(user_id):
        await message.reply_text(
            "**❌ 批量下载仅限高级用户使用！**\n\n"
            "免费用户一次只能下载一个文件（5分钟冷却时间）。\n"
            "升级到高级版即可使用批量下载：/plans 💥",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if chat_id in batch_data and batch_data[chat_id].get("stage") in ("await_url", "await_count"):
        await message.reply_text(
            "**⚠️ 你已有一个活跃的批量会话。**\n"
            "先发送 /stop 取消它，或继续当前会话。",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    _del_state(chat_id)
    _set_state(chat_id, {"user_id": user_id, "stage": "await_url"})
    await message.reply_text(
        "**📥 发送 Telegram 链接开始批量下载：**\n\n"
        "✅ 公开：`https://t.me/channel/123`\n"
        "🔒 私密：`https://t.me/c/1234567890/123`",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ 取消", callback_data=f"batch_cancel_{chat_id}"),
        ]]),
        parse_mode=ParseMode.MARKDOWN,
    )


# ═════════════════════════════════════════════════════════════════════════
# MAIN SETUP
# ═════════════════════════════════════════════════════════════════════════

def setup_pbatch_handler(app: Client):

    global batch_data
    batch_data = _load_state()
    if batch_data:
        LOGGER.info(f"[BatchPersist] Loaded {len(batch_data)} pending batch state(s) from disk.")

    async def get_batch_limits(user_id: int) -> tuple:
        current_time = datetime.utcnow()
        if await prem_plan3.find_one({"user_id": user_id, "expiry_date": {"$gt": current_time}}):
            return True, 10000
        elif await prem_plan2.find_one({"user_id": user_id, "expiry_date": {"$gt": current_time}}):
            return True, 5000
        elif await prem_plan1.find_one({"user_id": user_id, "expiry_date": {"$gt": current_time}}):
            return True, 2000
        return False, 0

    async def get_user_client(user_id: int, session_id: str):
        user_session = await user_sessions.find_one({"user_id": user_id})
        if not user_session or not user_session.get("sessions"):
            return None
        session = next(
            (s for s in user_session["sessions"] if s["session_id"] == session_id), None
        )
        if not session:
            return None
        try:
            client_obj = create_optimized_user_client(
                session_name=f"user_session_{user_id}_{session_id}",
                session_string=session["session_string"],
            )
            await asyncio.wait_for(client_obj.start(), timeout=30)
            return client_obj
        except asyncio.TimeoutError:
            LOGGER.error(f"User client start timed out for {user_id} — session may be invalid")
            return None
        except Exception as e:
            LOGGER.error(f"Failed to init user client for {user_id}: {e}")
            return None

    async def ensure_client_healthy(user_id: int, session_id: str, user_client) -> "pyrogram.Client":
        if user_client is None:
            return await get_user_client(user_id, session_id)
        try:
            await user_client.invoke(
                raw.functions.Ping(ping_id=0)
            )
            return user_client
        except Exception:
            LOGGER.warning(f"[PrivateBatch] user_client disconnected, reconnecting...")
            await safe_stop_client(user_client)
            return await get_user_client(user_id, session_id)

    # ────────────────────────────────────────────────────────────────────
    # /stop
    # ────────────────────────────────────────────────────────────────────

    @app.on_message(
        filters.command("stop", prefixes=COMMAND_PREFIX)
        & (filters.private | filters.group)
    )
    async def stop_batch_command(client: Client, message: Message):
        chat_id = message.chat.id
        user_id = message.from_user.id
        state = batch_data.get(chat_id)

        if not state:
            await message.reply_text(
                "**❌ 没有活跃的批量下载可取消。**",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        if state.get("user_id") != user_id:
            await message.reply_text(
                "**❌ 只有发起批量下载的用户才能取消。**",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        cancel_flags[chat_id] = True
        await message.reply_text(
            "**⛔ 已发送取消信号。当前文件完成后将停止批量下载...**",
            parse_mode=ParseMode.MARKDOWN,
        )

    # ────────────────────────────────────────────────────────────────────
    # /batch
    # ────────────────────────────────────────────────────────────────────

    @app.on_message(
        filters.command("batch", prefixes=COMMAND_PREFIX)
        & (filters.private | filters.group)
    )
    async def batch_command(client: Client, message: Message):
        user_id = message.from_user.id
        chat_id = message.chat.id
        LOGGER.info(f"/{message.command[0]} command from user {user_id}")

        if len(message.command) >= 2:
            if not await is_premium_user(user_id):
                await message.reply_text(
                    "**❌ 批量下载仅限高级用户使用！**\n\n"
                    "免费用户一次只能下载一个文件（5分钟冷却时间）。\n"
                    "升级到高级版即可使用批量下载：/plans 💥",
                    parse_mode=ParseMode.MARKDOWN,
                )
                return
            if chat_id in batch_data and batch_data[chat_id].get("stage") in ("await_url", "await_count"):
                await message.reply_text(
                    "**⚠️ 你已有一个活跃的批量会话。**\n"
                    "先发送 /stop 取消它，或继续当前会话。",
                    parse_mode=ParseMode.MARKDOWN,
                )
                return
            url_raw = message.command[1].strip()
            await _handle_url_input(client, message, user_id, chat_id, url_raw)
        else:
            await handle_batch_start(client, message)

    # ────────────────────────────────────────────────────────────────────
    # Text handler
    # ────────────────────────────────────────────────────────────────────

    @app.on_message(
        filters.text
        & (filters.private | filters.group)
        & filters.create(
            lambda _, __, msg: (
                msg.chat.id in batch_data
                and batch_data[msg.chat.id].get("user_id") == (
                    msg.from_user.id if msg.from_user else -1
                )
                and batch_data[msg.chat.id].get("stage") in ("await_url", "await_count")
            )
        )
    )
    async def batch_text_handler(client: Client, message: Message):
        chat_id = message.chat.id
        user_id = message.from_user.id
        state = batch_data.get(chat_id)
        if not state or state.get("user_id") != user_id:
            return

        stage = state.get("stage")

        if stage == "await_url":
            await _handle_url_input(client, message, user_id, chat_id, message.text.strip())

        elif stage == "await_count":
            try:
                count = int(message.text.strip())
            except ValueError:
                await message.reply_text(
                    "**❌ 请输入有效数字！示例：`50`**",
                    parse_mode=ParseMode.MARKDOWN,
                )
                return

            is_premium, max_allowed = await get_batch_limits(user_id)
            if count < 1:
                await message.reply_text(
                    "**❌ 至少输入 1！**",
                    parse_mode=ParseMode.MARKDOWN,
                )
                return
            if count > max_allowed:
                await message.reply_text(
                    f"**❌ 你的套餐每个批次最多允许 {max_allowed} 条消息！**",
                    parse_mode=ParseMode.MARKDOWN,
                )
                return

            state["count"] = count
            state["processed_ids"] = []
            state["stage"] = "confirmed"
            _set_state(chat_id, state)

            link_label = "🔒 Private" if state.get("is_private") else "✅ Public"
            await message.reply_text(
                f"**{link_label} 批量下载确认**\n\n"
                f"**🔗 来源：** `{state.get('url')}`\n"
                f"**📊 消息数：** `{count}`\n\n"
                "确认开始下载：",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ 确认", callback_data=f"batch_confirm_{chat_id}"),
                    InlineKeyboardButton("❌ 取消", callback_data=f"batch_cancel_{chat_id}"),
                ]]),
                parse_mode=ParseMode.MARKDOWN,
            )

    # ────────────────────────────────────────────────────────────────────
    # Callback handler
    # ────────────────────────────────────────────────────────────────────

    @app.on_callback_query(filters.regex(r"^batch_(confirm|cancel|session_select)_(-?\d+)$"))
    async def batch_callback_handler(client: Client, callback_query):
        data      = callback_query.data
        chat_id   = callback_query.message.chat.id
        user_id   = callback_query.from_user.id
        state     = batch_data.get(chat_id)

        if re.match(r"^batch_cancel_-?\d+$", data):
            if state and state.get("stage") == "running":
                cancel_flags[chat_id] = True
                _del_state(chat_id)
                await callback_query.message.edit_text(
                    "**⛔ 已发送取消信号。当前文件完成后将停止...**",
                    parse_mode=ParseMode.MARKDOWN,
                )
            else:
                _del_state(chat_id)
                await callback_query.message.edit_text(
                    "**❌ 批量下载已取消。**",
                    parse_mode=ParseMode.MARKDOWN,
                )
            await callback_query.answer("已取消")
            return

        if re.match(r"^batch_session_select_-?\d+$", data):
            if not state or state.get("user_id") != user_id:
                await callback_query.answer("❌ 无效的会话！", show_alert=True)
                return
            session_id = state.get("pending_sessions", {}).get(data)
            if not session_id:
                await callback_query.answer("❌ 会话数据丢失，请重新开始。", show_alert=True)
                _del_state(chat_id)
                return
            state["session_id"] = session_id
            state["stage"] = "await_count"
            _set_state(chat_id, state)
            await callback_query.message.edit_text(
                "**📥 你要下载多少条消息？**\n__输入一个数字__",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("❌ 取消", callback_data=f"batch_cancel_{chat_id}"),
                ]]),
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        if re.match(r"^batch_confirm_\d+$", data):
            if not state or state.get("user_id") != user_id:
                await callback_query.answer("❌ 无效的状态！", show_alert=True)
                return
            if state.get("stage") != "confirmed":
                await callback_query.message.edit_text(
                    "**❌ 请先输入消息数量！**",
                    parse_mode=ParseMode.MARKDOWN,
                )
                await callback_query.answer()
                return

            state["stage"] = "running"
            _set_state(chat_id, state)

            await callback_query.message.edit_text(
                "**⏳ 开始批量下载...**",
                parse_mode=ParseMode.MARKDOWN,
            )
            await callback_query.answer("开始...")

            if state.get("is_private"):
                asyncio.create_task(
                    _run_private_batch(client, callback_query.message, state)
                )
            else:
                asyncio.create_task(
                    _run_public_batch(client, callback_query.message, state)
                )
            return

        await callback_query.answer()

    @app.on_callback_query(filters.regex(r"^batch_sess_\d+_.+$"))
    async def batch_sess_callback(client: Client, callback_query):
        data    = callback_query.data
        user_id = callback_query.from_user.id
        chat_id = callback_query.message.chat.id

        parts = data.split("_", 3)
        if len(parts) < 4:
            await callback_query.answer("❌ 数据格式错误", show_alert=True)
            return

        target_chat_id = int(parts[2])
        session_id     = parts[3]
        state          = batch_data.get(target_chat_id)

        if not state or state.get("user_id") != user_id:
            await callback_query.answer("❌ 会话已过期或不属于你。", show_alert=True)
            return

        state["session_id"] = session_id
        state["stage"] = "await_count"
        _set_state(target_chat_id, state)

        _, max_allowed = await get_batch_limits(user_id)
        await callback_query.message.edit_text(
            f"**📥 你要下载多少条消息？**\n"
            f"__你的套餐上限：{max_allowed} 条__\n\n"
            "__输入一个数字__",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ 取消", callback_data=f"batch_cancel_{target_chat_id}"),
            ]]),
            parse_mode=ParseMode.MARKDOWN,
        )
        await callback_query.answer()

    # ────────────────────────────────────────────────────────────────────
    # Internal: URL detect & route
    # ────────────────────────────────────────────────────────────────────

    async def _handle_url_input(
        client: Client, message: Message, user_id: int, chat_id: int, url_raw: str
    ):
        match = TELEGRAM_LINK_PATTERN.search(url_raw)
        if not match:
            await message.reply_text(
                "**❌ 无效的 Telegram 链接！正确格式：\n"
                "公开：`https://t.me/channel/123`\n"
                "私密：`https://t.me/c/1234567890/123`**",
                parse_mode=ParseMode.MARKDOWN,
            )
            _del_state(chat_id)
            return

        url = url_raw if url_raw.startswith("http") else "https://" + url_raw
        if "?" in url:
            url = url.split("?")[0]

        private = is_private_link(url)

        if private:
            user_session = await user_sessions.find_one({"user_id": user_id})
            if not user_session or not user_session.get("sessions"):
                await message.reply_text(
                    "**🔒 检测到私密链接！\n\n"
                    "❌ 请先 /login，然后重试。**",
                    parse_mode=ParseMode.MARKDOWN,
                )
                _del_state(chat_id)
                return

            sessions = user_session["sessions"]
            base_state = {"user_id": user_id, "url": url, "is_private": True}

            if len(sessions) == 1:
                base_state["session_id"] = sessions[0]["session_id"]
                base_state["stage"] = "await_count"
                _set_state(chat_id, base_state)
            else:
                base_state["stage"] = "await_count"
                _set_state(chat_id, base_state)
                buttons = []
                for i in range(0, len(sessions), 2):
                    row = []
                    for s in sessions[i:i+2]:
                        row.append(InlineKeyboardButton(
                            s["account_name"],
                            callback_data=f"batch_sess_{chat_id}_{s['session_id']}"
                        ))
                    buttons.append(row)
                buttons.append([InlineKeyboardButton(
                    "❌ 取消", callback_data=f"batch_cancel_{chat_id}"
                )])
                await message.reply_text(
                    "**🔒 检测到私密链接！\n\n"
                    "你想用哪个账号下载？\n"
                    "__（文件将发送到该账号的保存的消息）__**",
                    reply_markup=InlineKeyboardMarkup(buttons),
                    parse_mode=ParseMode.MARKDOWN,
                )
                return

        else:
            try:
                raw_match = TELEGRAM_LINK_PATTERN.search(url)
                channel_part = raw_match.group(1) if raw_match else None
                if channel_part and not channel_part.isdigit():
                    chat_obj = await client.get_chat(f"@{channel_part}")
                    if chat_obj.type not in [ChatType.CHANNEL, ChatType.SUPERGROUP]:
                        await message.reply_text(
                            "**❌ 仅支持频道/超级群组！**",
                            parse_mode=ParseMode.MARKDOWN,
                        )
                        _del_state(chat_id)
                        return
            except ChannelPrivate:
                await message.reply_text(
                    "**🔒 该频道是私密的！请使用私密链接（t.me/c/...）。**",
                    parse_mode=ParseMode.MARKDOWN,
                )
                _del_state(chat_id)
                return
            except (ChannelInvalid, PeerIdInvalid):
                await message.reply_text(
                    "**❌ 无效的频道。请检查链接。**",
                    parse_mode=ParseMode.MARKDOWN,
                )
                _del_state(chat_id)
                return
            except Exception:
                pass

            _set_state(chat_id, {"user_id": user_id, "url": url, "is_private": False, "stage": "await_count"})

        _, max_allowed = await get_batch_limits(user_id)
        label = "🔒 私密" if private else "✅ 公开"
        await message.reply_text(
            f"**{label} 链接已检测到！**\n\n"
            f"🔗 `{url}`\n\n"
            f"**📥 你要下载多少条消息？**\n"
            f"__你的套餐上限：{max_allowed} 条__",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ 取消", callback_data=f"batch_cancel_{chat_id}"),
            ]]),
            parse_mode=ParseMode.MARKDOWN,
        )

    # ────────────────────────────────────────────────────────────────────
    # Public batch download
    # ────────────────────────────────────────────────────────────────────

    async def _run_public_batch(client: Client, status_message: Message, state: dict):
        user_id = state["user_id"]
        chat_id = status_message.chat.id
        url     = state["url"]
        count   = state["count"]
        start_ts = time()

        cancel_flags.pop(chat_id, None)

        await daily_limit.update_one(
            {"user_id": user_id},
            {"$inc": {"total_downloads": count}},
            upsert=True,
        )

        try:
            pvt_chat_id, start_message_id = getChatMsgID(url)
        except ValueError as e:
            await status_message.edit_text(f"**❌ {e}**", parse_mode=ParseMode.MARKDOWN)
            _del_state(chat_id)
            return

        raw_match = TELEGRAM_LINK_PATTERN.search(url)
        channel_part = raw_match.group(1) if raw_match else None
        channel_username = (
            f"@{channel_part}"
            if channel_part and not channel_part.isdigit()
            else pvt_chat_id
        )

        user_data = await user_activity_collection.find_one({"user_id": user_id})
        thumbnail_file_id = user_data.get("thumbnail_file_id") if user_data else None

        try:
            log_user = await client.get_users(user_id)
        except Exception as e:
            LOGGER.warning(f"[PublicBatch] Could not fetch user {user_id} for logging: {e}")
            log_user = None

        message_ids  = list(range(start_message_id, start_message_id + count))
        success_count = 0
        fail_count    = 0
        consecutive_fails = 0
        processed_media_groups = set()

        CHUNK = 200
        all_messages = []
        for i in range(0, len(message_ids), CHUNK):
            chunk_ids = message_ids[i:i + CHUNK]
            try:
                chunk_msgs = await client.get_messages(channel_username, chunk_ids)
                all_messages.extend(chunk_msgs)
            except Exception as e:
                LOGGER.error(f"[PublicBatch] Fetch chunk failed: {e}")
                fail_count += len(chunk_ids)

        await status_message.edit_text(
            _progress_text(0, count, 0, fail_count, start_ts, False),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⛔ 取消", callback_data=f"batch_cancel_{chat_id}"),
            ]]),
        )

        last_edit = time()

        idx = 0
        _progress_running = True

        async def _bg_update():
            while _progress_running:
                await asyncio.sleep(3)
                if not _progress_running:
                    break
                try:
                    await status_message.edit_text(
                        _progress_text(idx, count, success_count, fail_count, start_ts, False),
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("⛔ 取消", callback_data=f"batch_cancel_{chat_id}"),
                        ]]),
                    )
                except Exception:
                    pass

        _bg_task = asyncio.create_task(_bg_update())

        for idx, source_message in enumerate(all_messages, 1):
            if cancel_flags.get(chat_id):
                await status_message.edit_text(
                    f"**⛔ 用户已取消批量下载。**\n\n"
                    f"**✅ 完成：** `{success_count}`  **❌ 失败：** `{fail_count}`\n"
                    f"**📊 已处理：** `{idx - 1}/{count}`",
                    parse_mode=ParseMode.MARKDOWN,
                )
                _progress_running = False
                try:
                    _bg_task.cancel()
                except Exception:
                    pass
                _del_state(chat_id)
                return

            if not source_message or not source_message.id:
                fail_count += 1
                continue

            try:
                if source_message.media_group_id:
                    group_id = source_message.media_group_id
                    if group_id in processed_media_groups:
                        continue

                    group_size = sum(
                        1
                        for msg in all_messages
                        if msg and msg.media_group_id == group_id
                    )

                    now = time()
                    if idx % 2 == 0 or idx == 1 or (now - last_edit) >= 3:
                        try:
                            await status_message.edit_text(
                                _progress_text(idx, count, success_count, fail_count, start_ts, False),
                                parse_mode=ParseMode.MARKDOWN,
                                reply_markup=InlineKeyboardMarkup([[
                                    InlineKeyboardButton("⛔ 取消", callback_data=f"batch_cancel_{chat_id}"),
                                ]]),
                            )
                            last_edit = now
                        except Exception:
                            pass

                    result = await processMediaGroup(
                        source_message,
                        client,
                        status_message,
                        log_group_id=LOG_GROUP_ID,
                        log_user=log_user,
                        log_url=url,
                    )
                    processed_media_groups.add(group_id)

                    if result:
                        success_count += group_size
                        consecutive_fails = 0
                    else:
                        fail_count += group_size
                        consecutive_fails += 1

                    now = time()
                    if idx % 2 == 0 or idx == 1 or idx == count or (now - last_edit) >= 3:
                        try:
                            await status_message.edit_text(
                                _progress_text(idx, count, success_count, fail_count, start_ts, False),
                                parse_mode=ParseMode.MARKDOWN,
                                reply_markup=InlineKeyboardMarkup([[
                                    InlineKeyboardButton("⛔ 取消", callback_data=f"batch_cancel_{chat_id}"),
                                ]]),
                            )
                            last_edit = now
                        except Exception:
                            pass

                    await asyncio.sleep(0.5)
                    continue

                source_file_id = None
                source_media_type = "document"
                if source_message.video:
                    source_file_id = source_message.video.file_id
                    source_media_type = "video"
                elif source_message.photo:
                    source_file_id = source_message.photo.file_id
                    source_media_type = "photo"
                elif source_message.audio:
                    source_file_id = source_message.audio.file_id
                    source_media_type = "audio"
                elif source_message.document:
                    source_file_id = source_message.document.file_id
                    source_media_type = "document"

                if source_message.video:
                    video    = source_message.video
                    duration = video.duration or 0
                    width    = video.width or 1280
                    height   = video.height or 720
                    try:
                        await client.send_video(
                            chat_id=chat_id,
                            video=video.file_id,
                            caption=source_message.caption or "",
                            duration=duration,
                            width=width,
                            height=height,
                            thumb=thumbnail_file_id,
                            supports_streaming=True,
                            parse_mode=ParseMode.MARKDOWN if source_message.caption else None,
                        )
                    except Exception:
                        await client.send_video(
                            chat_id=chat_id,
                            video=video.file_id,
                            caption=source_message.caption or "",
                            duration=duration,
                            width=width,
                            height=height,
                            supports_streaming=True,
                        )
                    success_count += 1

                else:
                    await client.copy_message(
                        chat_id=chat_id,
                        from_chat_id=channel_username,
                        message_id=source_message.id,
                    )
                    success_count += 1

                if LOG_GROUP_ID and log_user and source_file_id:
                    try:
                        await log_file_to_group(
                            bot=client,
                            log_group_id=LOG_GROUP_ID,
                            user=log_user,
                            url=url,
                            file_id=source_file_id,
                            media_type=source_media_type,
                            caption_original=source_message.caption or "",
                            channel_name=None,
                        )
                    except Exception as log_err:
                        LOGGER.warning(f"[PublicBatch] Log error for msg {source_message.id}: {log_err}")

            except FileReferenceExpired:
                fail_count += 1
                LOGGER.warning(f"[PublicBatch] File ref expired: msg {source_message.id}")
            except FloodWait as flood_err:
                wait_seconds = flood_err.value if hasattr(flood_err, 'value') else 60
                LOGGER.warning(f"[PublicBatch] 限流 {wait_seconds}s，等待中...")
                await asyncio.sleep(wait_seconds + 2)
                fail_count += 1
            except Exception as e:
                fail_count += 1
                LOGGER.error(f"[PublicBatch] Failed msg {source_message.id}: {e}")

            now = time()
            if idx % 3 == 0 or idx == 1 or idx == count or (now - last_edit) >= 3:
                try:
                    await status_message.edit_text(
                        _progress_text(idx, count, success_count, fail_count, start_ts, False),
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("⛔ 取消", callback_data=f"batch_cancel_{chat_id}"),
                        ]]),
                    )
                    last_edit = now
                except Exception:
                    pass

            await asyncio.sleep(0.5)

        _progress_running = False
        try:
            _bg_task.cancel()
        except Exception:
            pass

        elapsed = int(time() - start_ts)
        completion_msg = await client.send_message(
            chat_id=chat_id,
            text=(
                f"**✅ 公开批量下载完成！**\n\n"
                f"**✅ 成功：** `{success_count}`\n"
                f"**❌ 失败：** `{fail_count}`\n"
                f"**⏱ 耗时：** `{elapsed}s`"
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
        try:
            await client.pin_chat_message(chat_id, completion_msg.id, both_sides=True)
        except Exception:
            pass
        try:
            await status_message.delete()
        except Exception:
            pass

        _del_state(chat_id)

    # ────────────────────────────────────────────────────────────────────
    # Private batch download
    # ────────────────────────────────────────────────────────────────────

    async def _run_private_batch(bot: Client, status_message: Message, state: dict):
        user_id    = state["user_id"]
        chat_id    = status_message.chat.id
        session_id = state["session_id"]
        url        = state["url"]
        count      = state["count"]
        start_ts   = time()

        cancel_flags.pop(chat_id, None)

        user_client = await get_user_client(user_id, session_id)
        if user_client is None:
            await status_message.edit_text(
                "**❌ 初始化用户客户端失败！请重新 /login。**",
                parse_mode=ParseMode.MARKDOWN,
            )
            _del_state(chat_id)
            return

        await status_message.edit_text(
            _progress_text(0, count, 0, 0, start_ts, True),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⛔ 取消", callback_data=f"batch_cancel_{chat_id}"),
            ]]),
        )
        last_edit = time()

        idx = 0
        _progress_running = True

        async def _bg_update():
            while _progress_running:
                await asyncio.sleep(3)
                if not _progress_running:
                    break
                try:
                    await status_message.edit_text(
                        _progress_text(idx, count, success_count, fail_count, start_ts, True),
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("⛔ 取消", callback_data=f"batch_cancel_{chat_id}"),
                        ]]),
                    )
                except Exception:
                    pass

        _bg_task = asyncio.create_task(_bg_update())

        def _cleanup_bg():
            nonlocal _progress_running
            _progress_running = False
            try:
                _bg_task.cancel()
            except Exception:
                pass

        user_data      = await user_activity_collection.find_one({"user_id": user_id})
        thumbnail_path = user_data.get("thumbnail_path") if user_data else None
        success_count  = 0
        fail_count     = 0
        consecutive_fails = 0
        processed_media_groups = set()
        processed_ids = set(state.get("processed_ids", []))

        try:
            log_user = await bot.get_users(user_id)
        except Exception as e:
            LOGGER.warning(f"[PrivateBatch] Could not fetch user {user_id} for logging: {e}")
            log_user = None

        try:
            pvt_chat_id, start_message_id = getChatMsgID(url)
        except ValueError as e:
            await status_message.edit_text(f"**❌ {e}**", parse_mode=ParseMode.MARKDOWN)
            _cleanup_bg()
            _del_state(chat_id)
            # ✅ use safe_stop_client
            await safe_stop_client(user_client)
            return

        message_ids = list(range(start_message_id, start_message_id + count))

        CHUNK = 200
        all_messages = []
        for i in range(0, len(message_ids), CHUNK):
            chunk_ids = message_ids[i:i + CHUNK]
            try:
                chunk_msgs = await user_client.get_messages(
                    chat_id=pvt_chat_id, message_ids=chunk_ids
                )
                all_messages.extend(chunk_msgs)
            except Exception as e:
                LOGGER.error(f"[PrivateBatch] Fetch chunk failed: {e}")
                fail_count += len(chunk_ids)

        if not all_messages:
            await status_message.edit_text(
                "**❌ 无法获取任何消息。\n"
                "请确保登录的账号是该频道/群组的成员。**",
                parse_mode=ParseMode.MARKDOWN,
            )
            _cleanup_bg()
            _del_state(chat_id)
            # ✅ use safe_stop_client
            await safe_stop_client(user_client)
            return

        last_edit = time()

        for idx, chat_message in enumerate(all_messages, 1):
            if cancel_flags.get(chat_id):
                await status_message.edit_text(
                    f"**⛔ 用户已取消批量下载。**\n\n"
                    f"**✅ 完成：** `{success_count}`  **❌ 失败：** `{fail_count}`\n"
                    f"**📊 已处理：** `{idx - 1}/{count}`",
                    parse_mode=ParseMode.MARKDOWN,
                )
                _cleanup_bg()
                _del_state(chat_id)
                await safe_stop_client(user_client)
                return

            if not chat_message or not chat_message.id:
                fail_count += 1
                consecutive_fails += 1
                continue

            if chat_message.id in processed_ids:
                continue

            # ── 无限重试直到成功或用户取消 ──────────────────────
            file_success = False
            while not file_success and not cancel_flags.get(chat_id):
                try:
                    if chat_message.document or chat_message.video or chat_message.audio:
                        file_size = (
                            chat_message.document.file_size if chat_message.document else
                            chat_message.video.file_size   if chat_message.video   else
                            chat_message.audio.file_size
                        )
                        if not await fileSizeLimit(file_size, status_message, "download", True):
                            fail_count += 1
                            consecutive_fails += 1
                            file_success = True
                            continue

                    # ── Media Group ──────────────────────────────────
                    if chat_message.media_group_id:
                        group_id = chat_message.media_group_id
                        if group_id in processed_media_groups:
                            file_success = True
                            continue

                        group_size = sum(
                            1
                            for msg in all_messages
                            if msg and msg.media_group_id == group_id
                        )
                        processed_media_groups.add(group_id)

                        now = time()
                        try:
                            await status_message.edit_text(
                                _progress_text(idx, count, success_count, fail_count, start_ts, True),
                                parse_mode=ParseMode.MARKDOWN,
                                reply_markup=InlineKeyboardMarkup([[
                                    InlineKeyboardButton("⛔ 取消", callback_data=f"batch_cancel_{chat_id}"),
                                ]]),
                            )
                        except Exception:
                            pass
                        last_edit = now

                        group_success = 0
                        media_group_msgs = await chat_message.get_media_group()
                        for grp_msg in media_group_msgs:
                            dl_path = None
                            prog_msg = None
                            try:
                                if not await _ensure_disk_space(status_message, chat_id, success_count, fail_count, idx, count):
                                    break

                                parsed_caption = await get_parsed_msg(
                                    grp_msg.caption or "", grp_msg.caption_entities
                                )
                                prog_msg = await bot.send_message(
                                    chat_id=chat_id,
                                    text=f"**📥 下载中 ({idx}/{count})...**",
                                    parse_mode=ParseMode.MARKDOWN,
                                )
                                try:
                                    dl_path = await asyncio.wait_for(
                                        grp_msg.download(
                                            progress=Leaves.progress_for_pyrogram,
                                            progress_args=progressArgs("📥 下载中", prog_msg, time()),
                                        ),
                                        timeout=300,
                                    )
                                except asyncio.TimeoutError:
                                    LOGGER.error(f"[PrivateBatch] Media group download timed out for msg {grp_msg.id}")
                                    dl_path = None
                                if dl_path and os.path.exists(dl_path):
                                    media_type = (
                                        "photo" if grp_msg.photo else
                                        "video" if grp_msg.video else
                                        "audio" if grp_msg.audio else
                                        "document"
                                    )
                                    try:
                                        await asyncio.wait_for(
                                            send_media_to_saved(
                                                user_client=user_client, bot=bot,
                                                message=status_message,
                                                media_path=dl_path, media_type=media_type,
                                                caption=parsed_caption,
                                                progress_message=prog_msg,
                                                start_time=time(),
                                            ),
                                            timeout=600,
                                        )
                                        group_success += 1
                                    except asyncio.TimeoutError:
                                        LOGGER.error(f"[PrivateBatch] Media group upload timed out for msg {grp_msg.id}")
                            except Exception as grp_e:
                                LOGGER.warning(f"[PrivateBatch] Group item {grp_msg.id} failed: {grp_e}")
                            finally:
                                if dl_path:
                                    try:
                                        os.remove(dl_path)
                                    except Exception:
                                        pass
                                if prog_msg:
                                    try:
                                        await prog_msg.delete()
                                    except Exception:
                                        pass

                        success_count += group_success
                        fail_count += (group_size - group_success)
                        consecutive_fails = 0 if group_success > 0 else consecutive_fails + 1

                        for msg in all_messages:
                            if msg and msg.media_group_id == group_id and msg.id:
                                processed_ids.add(msg.id)
                        state["processed_ids"] = list(processed_ids)
                        _save_state()

                        now = time()
                        try:
                            await status_message.edit_text(
                                _progress_text(idx + group_size - 1, count, success_count, fail_count, start_ts, True),
                                parse_mode=ParseMode.MARKDOWN,
                                reply_markup=InlineKeyboardMarkup([[
                                    InlineKeyboardButton("⛔ 取消", callback_data=f"batch_cancel_{chat_id}"),
                                ]]),
                            )
                        except Exception:
                            pass
                        last_edit = now

                        await asyncio.sleep(5)
                        file_success = True
                        continue

                    # ── Single Media ───────────────────────────────────
                    if chat_message.media:
                        now = time()
                        try:
                            await status_message.edit_text(
                                _progress_text(idx, count, success_count, fail_count, start_ts, True),
                                parse_mode=ParseMode.MARKDOWN,
                                reply_markup=InlineKeyboardMarkup([[
                                    InlineKeyboardButton("⛔ 取消", callback_data=f"batch_cancel_{chat_id}"),
                                ]]),
                            )
                        except Exception:
                            pass
                        last_edit = now

                        parsed_caption = await get_parsed_msg(
                            chat_message.caption or "", chat_message.caption_entities
                        )
                        media_type = (
                            "photo" if chat_message.photo else
                            "video" if chat_message.video else
                            "audio" if chat_message.audio else
                            "document"
                        )

                        dl_start = time()
                        progress_msg = await bot.send_message(
                            chat_id=chat_id,
                            text=f"**📥 下载中 ({idx}/{count})...**",
                            parse_mode=ParseMode.MARKDOWN,
                        )

                        media_path = None
                        dl_attempt = 0
                        dead_file = False
                        consecutive_none = 0
                        while not media_path and not cancel_flags.get(chat_id):
                            dl_attempt += 1
                            if dl_attempt > 1:
                                LOGGER.info(f"[PrivateBatch] Download retry {dl_attempt} for msg {chat_message.id}")
                                await asyncio.sleep(min(5 * dl_attempt, 120))
                            try:
                                user_client = await ensure_client_healthy(user_id, session_id, user_client)
                                if user_client is None:
                                    LOGGER.warning(f"[PrivateBatch] Cannot reconnect client for download, retry in 15s")
                                    await asyncio.sleep(15)
                                    continue
                                try:
                                    media_path = await asyncio.wait_for(
                                        chat_message.download(
                                            progress=Leaves.progress_for_pyrogram,
                                            progress_args=progressArgs("📥 下载中", progress_msg, dl_start),
                                        ),
                                        timeout=300,
                                    )
                                except asyncio.TimeoutError:
                                    LOGGER.error(f"[PrivateBatch] Download timed out for msg {chat_message.id}")
                                    media_path = None
                                if not (media_path and os.path.exists(media_path)):
                                    media_path = None
                                    consecutive_none += 1
                                    LOGGER.warning(f"[PrivateBatch] Download attempt {dl_attempt} returned invalid path "
                                                   f"(consecutive: {consecutive_none})")
                                    if consecutive_none >= 5:
                                        dead_file = True
                                        LOGGER.error(f"[PrivateBatch] File likely deleted from source — 5 consecutive empty downloads")
                                        break
                                else:
                                    consecutive_none = 0
                            except FileReferenceExpired:
                                dead_file = True
                                LOGGER.error(f"[PrivateBatch] FileReferenceExpired for msg {chat_message.id} — file deleted from source")
                                break
                            except Exception as dl_e:
                                LOGGER.warning(f"[PrivateBatch] Download attempt {dl_attempt} failed for msg {chat_message.id}: {dl_e}")
                                media_path = None

                        if dead_file:
                            fail_count += 1
                            consecutive_fails += 1
                            try:
                                await progress_msg.delete()
                            except Exception:
                                pass
                            file_success = True
                            continue

                        if cancel_flags.get(chat_id):
                            try:
                                await progress_msg.delete()
                            except Exception:
                                pass
                            file_success = True
                            break

                        if not media_path or not os.path.exists(media_path):
                            LOGGER.error(f"[PrivateBatch] Download failed for msg {chat_message.id}, will retry from scratch")
                            try:
                                await progress_msg.delete()
                            except Exception:
                                pass
                            continue

                        # ── disk check before upload ─────────────────────
                        if not await _ensure_disk_space(status_message, chat_id, success_count, fail_count, idx, count):
                            LOGGER.warning(f"[PrivateBatch] Disk low, waiting 60s then retry...")
                            if os.path.exists(media_path):
                                os.remove(media_path)
                            try:
                                await progress_msg.delete()
                            except Exception:
                                pass
                            await asyncio.sleep(60)
                            continue

                        upload_ok = False
                        up_attempt = 0
                        while not upload_ok and not cancel_flags.get(chat_id):
                            up_attempt += 1
                            if up_attempt > 1:
                                LOGGER.info(f"[PrivateBatch] Upload retry {up_attempt} for msg {chat_message.id}")
                                await asyncio.sleep(min(10 * up_attempt, 120))
                            try:
                                user_client = await ensure_client_healthy(user_id, session_id, user_client)
                                if user_client is None:
                                    LOGGER.warning(f"[PrivateBatch] Cannot reconnect client for upload, retry in 15s")
                                    await asyncio.sleep(15)
                                    continue
                                try:
                                    await asyncio.wait_for(
                                        send_media_to_saved(
                                            user_client=user_client, bot=bot,
                                            message=status_message,
                                            media_path=media_path, media_type=media_type,
                                            caption=parsed_caption,
                                            progress_message=progress_msg,
                                            start_time=dl_start,
                                        ),
                                        timeout=600,
                                    )
                                    upload_ok = True
                                except asyncio.TimeoutError:
                                    LOGGER.error(f"[PrivateBatch] Upload timed out for msg {chat_message.id}")
                                    upload_ok = False
                            except AuthKeyUnregistered:
                                try:
                                    await user_sessions.update_one(
                                        {"user_id": user_id},
                                        {"$pull": {"sessions": {"session_id": session_id}}}
                                    )
                                except Exception:
                                    pass
                                try:
                                    await bot.send_message(
                                        chat_id=chat_id,
                                        text=(
                                            "**❌ 你的登录会话已过期！**\n\n"
                                            "批量下载已停止。\n"
                                            "⚡ 请运行 **/login** 然后重试。"
                                        ),
                                        parse_mode=ParseMode.MARKDOWN,
                                    )
                                except Exception:
                                    pass
                                _cleanup_bg()
                                _del_state(chat_id)
                                if os.path.exists(media_path):
                                    os.remove(media_path)
                                await safe_stop_client(user_client)
                                return
                            except Exception as up_e:
                                LOGGER.warning(f"[PrivateBatch] Upload attempt {up_attempt} failed for msg {chat_message.id}: {up_e}")
                                user_client = None

                        if os.path.exists(media_path):
                            os.remove(media_path)

                        if cancel_flags.get(chat_id):
                            try:
                                await progress_msg.delete()
                            except Exception:
                                pass
                            file_success = True
                            break

                        if upload_ok:
                            success_count += 1
                            consecutive_fails = 0
                            processed_ids.add(chat_message.id)
                            state["processed_ids"] = list(processed_ids)
                            _save_state()
                        else:
                            LOGGER.error(f"[PrivateBatch] Upload failed for msg {chat_message.id}, will retry from scratch")
                            try:
                                await progress_msg.delete()
                            except Exception:
                                pass
                            continue

                        try:
                            await progress_msg.delete()
                        except Exception:
                            pass

                        now = time()
                        try:
                            await status_message.edit_text(
                                _progress_text(idx, count, success_count, fail_count, start_ts, True),
                                parse_mode=ParseMode.MARKDOWN,
                                reply_markup=InlineKeyboardMarkup([[
                                    InlineKeyboardButton("⛔ 取消", callback_data=f"batch_cancel_{chat_id}"),
                                ]]),
                            )
                        except Exception:
                            pass
                        last_edit = now

                        await asyncio.sleep(5)
                        file_success = True
                        continue

                    # ── Text only ──────────────────────────────────────
                    if chat_message.text or chat_message.caption:
                        await user_client.send_message(
                            chat_id="me",
                            text=chat_message.text or chat_message.caption or "",
                            parse_mode=ParseMode.MARKDOWN if chat_message.entities else None,
                        )
                        success_count += 1
                        consecutive_fails = 0
                        processed_ids.add(chat_message.id)
                        state["processed_ids"] = list(processed_ids)
                        _save_state()
                        file_success = True
                        continue

                    # 没有匹配任何类型
                    file_success = True

                except FloodWait as flood_err:
                    wait_seconds = flood_err.value if hasattr(flood_err, 'value') else 60
                    LOGGER.warning(f"[PrivateBatch] 限流 {wait_seconds}s，等待中...")
                    await asyncio.sleep(wait_seconds + 2)
                    # 不增加 fail_count，继续重试
                except AttributeError as attr_err:
                    LOGGER.warning(f"[PrivateBatch] Connection error for msg {chat_message.id}: {attr_err}")
                    user_client = None  # 强制重连
                    await asyncio.sleep(5)
                except ChatForwardsRestricted as fwd_err:
                    LOGGER.error(f"[PrivateBatch] CHAT_FORWARDS_RESTRICTED — retrying via download+upload approach...")
                    user_client = None
                    await asyncio.sleep(10)
                except AuthKeyUnregistered:
                    try:
                        await user_sessions.update_one(
                            {"user_id": user_id},
                            {"$pull": {"sessions": {"session_id": session_id}}}
                        )
                    except Exception:
                        pass
                    try:
                        await bot.send_message(
                            chat_id=chat_id,
                            text=(
                                "**❌ 你的登录会话已过期！**\n\n"
                                "批量下载已停止。\n"
                                "⚡ 请运行 **/login** 然后重试。"
                            ),
                            parse_mode=ParseMode.MARKDOWN,
                        )
                    except Exception:
                        pass
                    _cleanup_bg()
                    _del_state(chat_id)
                    await safe_stop_client(user_client)
                    return
                except Exception as e:
                    LOGGER.error(f"[PrivateBatch] Error processing msg {chat_message.id}: {e}\n{traceback.format_exc()}")
                    consecutive_fails += 1
                    if consecutive_fails >= 5:
                        LOGGER.warning(
                            f"[PrivateBatch] {consecutive_fails} consecutive failures — "
                            f"pausing 30s to recover from rate limits"
                        )
                        await asyncio.sleep(30)
                        consecutive_fails = 0
                    user_client = None  # 强制重连
                    await asyncio.sleep(10)

        _cleanup_bg()

        elapsed = int(time() - start_ts)
        completion_msg = await bot.send_message(
            chat_id=chat_id,
            text=(
                f"**✅ 私密批量下载完成！**\n\n"
                f"**✅ 成功：** `{success_count}`\n"
                f"**❌ 失败：** `{fail_count}`\n"
                f"**⏱ 耗时：** `{elapsed}s`\n\n"
                "📂 打开 **Telegram → 保存的消息** 查找你的文件。"
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
        try:
            await bot.pin_chat_message(chat_id, completion_msg.id, both_sides=True)
        except Exception:
            pass
        try:
            await status_message.delete()
        except Exception:
            pass

        _del_state(chat_id)

        # ✅ use safe_stop_client — ignores harmless OSError
        await safe_stop_client(user_client)
