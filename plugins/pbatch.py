# ✅ v21.1 全面优化版
# ✅ 核心：get_chat_history 获取消息 → download_media → 上传到 Saved Messages
# ✅ 新增: 文件名清理、FileReferenceExpired重试、thumbnail优化、详细日志追踪

import os
import re
import json
import asyncio
import time
import random
from datetime import datetime
from pyrogram import Client, filters, raw
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode, ChatType, MessageMediaType
from pyrogram.errors import (
    ChannelInvalid,
    ChannelPrivate,
    PeerIdInvalid,
    FileReferenceExpired,
    AuthKeyUnregistered,
    FloodWait,
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
from utils.helper import (
    create_optimized_user_client,
    safe_stop_client,
    get_media_info,
    get_video_resolution,
    get_video_thumbnail,
)
from core import (
    daily_limit,
    prem_plan1,
    prem_plan2,
    prem_plan3,
    user_sessions,
    user_activity_collection,
)

# ── 状态持久化文件 ───────────────────────────────────────────────────────
BATCH_STATE_FILE = "batch_state.json"

# ── 内存状态 ────────────────────────────────────────────────────────────
batch_data: dict = {}

# ── 活跃下载取消标志 ─────────────────────────────────────────────────
cancel_flags: dict = {}

# ── 链接匹配模式 ──────────────────────────────────────────────────────────
TELEGRAM_LINK_PATTERN = re.compile(
    r"(?:https?://)?(?:t\.me|telegram\.me)/(?:c/)?([a-zA-Z0-9_]+|\d+)/(\d+)(?:/\d+)?"
)


# ═════════════════════════════════════════════════════════════════════════
# 持久化辅助函数
# ═════════════════════════════════════════════════════════════════════════

def _load_state() -> dict:
    if not os.path.exists(BATCH_STATE_FILE):
        return {}
    try:
        with open(BATCH_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {int(k): v for k, v in data.items()}
    except Exception as e:
        LOGGER.error(f"[批量持久化] 加载状态失败: {e}")
        return {}


def _save_state():
    try:
        with open(BATCH_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({str(k): v for k, v in batch_data.items()}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        LOGGER.error(f"[批量持久化] 保存状态失败: {e}")


def _set_state(chat_id: int, data: dict):
    batch_data[chat_id] = data
    _save_state()


def _del_state(chat_id: int):
    batch_data.pop(chat_id, None)
    cancel_flags.pop(chat_id, None)
    _save_state()


def is_private_link(url: str) -> bool:
    return bool(re.search(r"(?:t\.me|telegram\.me)/c/", url))


def _progress_text(done: int, total: int, success: int, fail: int, start_ts: float, is_private: bool, status_line: str = "") -> str:
    elapsed = time.time() - start_ts
    rate = done / elapsed if elapsed > 0 else 0
    eta = int((total - done) / rate) if rate > 0 else 0
    pct = (done / total * 100) if total else 0

    bar_len = 10
    filled = int(bar_len * done / total) if total else 0
    bar = "▓" * filled + "░" * (bar_len - filled)

    label = "🔒 Private" if is_private else "✅ Public"
    eta_str = f"{eta // 60}m {eta % 60}s" if eta >= 60 else f"{eta}s"

    result = (
        f"**{label} 批量下载**\n\n"
        f"`[{bar}]` {pct:.1f}%\n\n"
        f"**📥 进度：** `{done}/{total}`\n"
        f"**✅ 成功：** `{success}`  **❌ 失败：** `{fail}`\n"
        f"**⏱ 耗时：** `{int(elapsed)}s`  **⏳ 预计：** `{eta_str}`"
    )
    if status_line:
        result += f"\n{status_line}"
    result += "\n\n__发送 /stop 取消__"
    return result


# ═════════════════════════════════════════════════════════════════════════
# 套餐检查
# ═════════════════════════════════════════════════════════════════════════

async def is_premium_user(user_id: int) -> bool:
    current_time = datetime.utcnow()
    for col in [prem_plan1, prem_plan2, prem_plan3]:
        doc = await col.find_one({"user_id": user_id})
        if doc and doc.get("expiry_date", current_time) > current_time:
            return True
    return False


# ═════════════════════════════════════════════════════════════════════════
# 共享批量启动
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
# 主配置
# ═════════════════════════════════════════════════════════════════════════

def setup_pbatch_handler(app: Client):

    global batch_data
    batch_data = _load_state()
    if batch_data:
            LOGGER.info(f"[批量持久化] 从磁盘加载了 {len(batch_data)} 个待处理的批量状态。")

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
    # 文本处理器
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
            if not state.get("session_id") and state.get("is_private"):
                await message.reply_text(
                    "**⚠️ 请先选择一个账号！**",
                    parse_mode=ParseMode.MARKDOWN,
                )
                return
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
    # 回调处理器
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
    # 内部：URL 检测与路由
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
                base_state["stage"] = "await_session"
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
    # ── 公开批量下载 ────────────────────────────────────────────────────────────

    async def _run_public_batch(client: Client, status_message: Message, state: dict):
        user_id = state["user_id"]
        chat_id = status_message.chat.id
        url     = state["url"]
        count   = state["count"]
        start_ts = time.time()

        cancel_flags.pop(chat_id, None)

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

        success_count = 0
        fail_count    = 0
        missing_count = 0
        processed_media_groups = set()

        message_ids = list(range(start_message_id, start_message_id + count))
        all_messages = []

        CHUNK = 200
        for i in range(0, len(message_ids), CHUNK):
            chunk_ids = message_ids[i:i + CHUNK]
            try:
                chunk_msgs = await client.get_messages(channel_username, chunk_ids)
                all_messages.extend(chunk_msgs)
            except Exception as e:
                LOGGER.error(f"[PublicBatch] Fetch chunk failed: {e}")
                fail_count += len(chunk_ids)

        total_msg_count = len(all_messages)
        missing_count = count - total_msg_count
        effective_total = total_msg_count

        if missing_count > 0:
            LOGGER.info(f"[PublicBatch] {missing_count}/{count} messages not found in channel (deleted)")

        if not all_messages:
            try:
                await status_message.edit_text(
                    "**❌ 无法获取任何消息。**",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass
            _del_state(chat_id)
            return

        await status_message.edit_text(
            _progress_text(0, effective_total, 0, fail_count, start_ts, False),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⛔ 取消", callback_data=f"batch_cancel_{chat_id}"),
            ]]),
        )

        last_edit = time.time()
        idx = 0
        _progress_running = True

        async def _bg_update():
            while _progress_running:
                await asyncio.sleep(3)
                if not _progress_running:
                    break
                try:
                    await status_message.edit_text(
                        _progress_text(idx, effective_total, success_count, fail_count, start_ts, False),
                    )
                except Exception:
                    pass

        def _cleanup_bg():
            nonlocal _progress_running
            _progress_running = False
            try:
                _bg_task.cancel()
            except Exception:
                pass

        _bg_task = asyncio.create_task(_bg_update())

        try:
            for idx, source_message in enumerate(all_messages, 1):
                if cancel_flags.get(chat_id):
                    try:
                        await status_message.edit_text(
                            f"**⛔ 用户已取消批量下载。**\n\n"
                            f"**✅ 完成：** `{success_count}`  **❌ 失败：** `{fail_count}`\n"
                            f"**📊 已处理：** `{idx - 1}/{effective_total}`",
                            parse_mode=ParseMode.MARKDOWN,
                        )
                    except Exception:
                        pass
                    _cleanup_bg()
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

                        # 从 all_messages 中统计媒体组大小
                        group_size = sum(
                            1 for m in all_messages
                            if m and getattr(m, 'media_group_id', None) == group_id
                        )

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
                        else:
                            fail_count += group_size

                        now = time.time()
                        if idx % 2 == 0 or idx == 1 or idx == effective_total or (now - last_edit) >= 3:
                            try:
                                await status_message.edit_text(
                                    _progress_text(idx, effective_total, success_count, fail_count, start_ts, False),
                                )
                                last_edit = now
                            except Exception:
                                pass

                        await asyncio.sleep(0.5)
                        continue

                    # 原版方式：send_video + copy_message
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
                        video = source_message.video
                        duration = video.duration or 0
                        width = video.width or 1280
                        height = video.height or 720
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

                now = time.time()
                if idx % 3 == 0 or idx == 1 or idx == effective_total or (now - last_edit) >= 3:
                    try:
                        await status_message.edit_text(
                            _progress_text(idx, effective_total, success_count, fail_count, start_ts, False),
                        )
                        last_edit = now
                    except Exception:
                        pass

                await asyncio.sleep(0.5)

        except Exception as e:
            LOGGER.error(f"[PublicBatch] Unexpected error: {e}")
        finally:
            _cleanup_bg()

        await daily_limit.update_one(
            {"user_id": user_id},
            {"$inc": {"total_downloads": success_count}},
            upsert=True,
        )

        elapsed = int(time.time() - start_ts)
        _missing_line = f"\n**⚠️ 频道已删除：** `{missing_count}` 条" if missing_count > 0 else ""
        completion_msg = await client.send_message(
            chat_id=chat_id,
            text=(
                f"**✅ 公开批量下载完成！**\n\n"
                f"**📥 请求下载：** `{count}` 条\n"
                f"**✅ 成功：** `{success_count}`\n"
                f"**❌ 失败：** `{fail_count}`"
                f"{_missing_line}\n"
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
    # 私密批量下载
    # ────────────────────────────────────────────────────────────────────

    # 关键修复: 从消息中获取正确的文件扩展名（避免 PHOTO_EXT_INVALID / VIDEO_EXT_INVALID 错误）
    # 参考 devgaganin/Save-Restricted-Content-Bot-v3 的实现
    def _get_file_ext(msg, media_type):
        """从消息对象中提取正确的文件扩展名"""
        try:
            # 1) 视频：优先从 msg.video.file_name 获取（参考项目的实现）
            if hasattr(msg, 'video') and msg.video:
                fname = getattr(msg.video, 'file_name', None)
                if fname:
                    ext = os.path.splitext(fname)[1].lower()
                    if ext:
                        return ext
                # 从 mime_type 推断
                mime = getattr(msg.video, 'mime_type', None)
                if mime:
                    mime = mime.lower()
                    video_map = {'video/mp4': '.mp4', 'video/webm': '.webm', 'video/quicktime': '.mov'}
                    if mime in video_map:
                        return video_map[mime]
                # 视频默认 .mp4
                return '.mp4'

            # 2) 音频：优先从 msg.audio.file_name 获取
            if hasattr(msg, 'audio') and msg.audio:
                fname = getattr(msg.audio, 'file_name', None)
                if fname:
                    ext = os.path.splitext(fname)[1].lower()
                    if ext:
                        return ext
                mime = getattr(msg.audio, 'mime_type', None)
                if mime:
                    mime = mime.lower()
                    audio_map = {'audio/mpeg': '.mp3', 'audio/mp4': '.m4a', 'audio/x-wav': '.wav', 'audio/flac': '.flac'}
                    if mime in audio_map:
                        return audio_map[mime]
                return '.mp3'

            # 3) 文档：msg.document.file_name
            if hasattr(msg, 'document') and msg.document:
                fname = getattr(msg.document, 'file_name', None)
                if fname:
                    ext = os.path.splitext(fname)[1].lower()
                    if ext:
                        return ext
                # 从 mime_type 推断
                mime = getattr(msg.document, 'mime_type', None)
                if mime:
                    mime = mime.lower()
                    mime_map = {
                        'image/jpeg': '.jpg', 'image/jpg': '.jpg', 'image/png': '.png',
                        'image/webp': '.webp', 'image/bmp': '.bmp',
                        'video/mp4': '.mp4', 'video/webm': '.webm', 'video/quicktime': '.mov',
                        'audio/mpeg': '.mp3', 'audio/mp4': '.m4a', 'audio/ogg': '.ogg',
                        'audio/x-wav': '.wav', 'audio/flac': '.flac',
                    }
                    if mime in mime_map:
                        return mime_map[mime]

            # 4) 语音：msg.voice
            if hasattr(msg, 'voice') and msg.voice:
                mime = getattr(msg.voice, 'mime_type', None)
                if mime and 'opus' in mime.lower():
                    return '.opus'
                return '.ogg'

            # 5) 视频便签：msg.video_note
            if hasattr(msg, 'video_note') and msg.video_note:
                return '.mp4'

            # 6) 动图：msg.animation
            if hasattr(msg, 'animation') and msg.animation:
                mime = getattr(msg.animation, 'mime_type', None)
                if mime and 'webm' in mime.lower():
                    return '.webm'
                return '.mp4'

            # 7) 贴纸：msg.sticker（WEBP 格式）
            if hasattr(msg, 'sticker') and msg.sticker:
                return '.webp'

            # 8) 根据 media_type 推断默认扩展名
            if media_type == MessageMediaType.PHOTO:
                return '.jpg'
            elif media_type == MessageMediaType.VIDEO:
                return '.mp4'
            elif media_type == MessageMediaType.AUDIO:
                return '.mp3'
            elif media_type == MessageMediaType.VOICE:
                return '.ogg'
            elif media_type == MessageMediaType.VIDEO_NOTE:
                return '.mp4'
            elif media_type == MessageMediaType.ANIMATION:
                return '.mp4'
            elif media_type == MessageMediaType.STICKER:
                return '.webp'
        except Exception:
            pass
        return ''

    # 关键修复: 构建带正确扩展名的 file_name（参考 devgaganin 项目）
    def _build_dl_filename(msg, mid, media_type):
        """
        为 download_media 构建 file_name。
        优先使用消息自带的 file_name（包含原始扩展名），
        否则根据媒体类型生成 "dl_{mid}_{ts}{ext}"。
        ✅ 新增: 清理文件名中的非法字符，防止目录穿越攻击
        """
        try:
            # 1) 视频：使用 msg.video.file_name（参考 devgaganin）
            if hasattr(msg, 'video') and msg.video:
                fname = getattr(msg.video, 'file_name', None)
                if fname:
                    return sanitize_filename(fname)
                ext = _get_file_ext(msg, media_type) or '.mp4'
                return f"dl_{mid}_{int(time.time())}{ext}"

            # 2) 音频：使用 msg.audio.file_name
            if hasattr(msg, 'audio') and msg.audio:
                fname = getattr(msg.audio, 'file_name', None)
                if fname:
                    return sanitize_filename(fname)
                ext = _get_file_ext(msg, media_type) or '.mp3'
                return f"dl_{mid}_{int(time.time())}{ext}"

            # 3) 文档：使用 msg.document.file_name
            if hasattr(msg, 'document') and msg.document:
                fname = getattr(msg.document, 'file_name', None)
                if fname:
                    return sanitize_filename(fname)
                # 没有 file_name 时使用时间戳+ext
                ext = _get_file_ext(msg, media_type) or ''
                return f"dl_{mid}_{int(time.time())}{ext}"

            # 4) 语音
            if hasattr(msg, 'voice') and msg.voice:
                ext = _get_file_ext(msg, media_type) or '.ogg'
                return f"dl_{mid}_{int(time.time())}{ext}"

            # 5) 视频便签
            if hasattr(msg, 'video_note') and msg.video_note:
                return f"dl_{mid}_{int(time.time())}.mp4"

            # 6) 动图
            if hasattr(msg, 'animation') and msg.animation:
                fname = getattr(msg.animation, 'file_name', None)
                if fname:
                    return fname
                ext = _get_file_ext(msg, media_type) or '.mp4'
                return f"dl_{mid}_{int(time.time())}{ext}"

            # 7) 贴纸
            if hasattr(msg, 'sticker') and msg.sticker:
                return f"dl_{mid}_{int(time.time())}.webp"

            # 8) 照片 / 其他：使用 ext
            ext = _get_file_ext(msg, media_type) or '.jpg'
            return f"dl_{mid}_{int(time.time())}{ext}"
        except Exception:
            pass
        return f"dl_{mid}_{int(time.time())}"

    # ────────────────────────────────────────────────────────────────────
    # ✅ v21.0 helper: sanitize filename (prevent directory traversal & invalid chars)
    # ────────────────────────────────────────────────────────────────────

    def sanitize_filename(fname: str) -> str:
        """
        Clean filename to prevent:
        - Directory traversal (../, ..\)
        - Invalid chars on Windows/Unix (\, /, :, *, ?, ", <, >, |)
        - Leading/trailing spaces and dots
        """
        if not fname:
            return ""
        
        # Remove directory separators
        fname = fname.replace('\\', '/').split('/')[-1]
        
        # Replace invalid characters with underscore
        invalid_chars = r'[\\/:*?"<>|]'
        import re
        fname = re.sub(invalid_chars, '_', fname)
        
        # Strip leading/trailing spaces and dots
        fname = fname.strip(' .')
        
        # Prevent empty filename
        if not fname:
            fname = f"file_{int(time.time())}"
        
        # Truncate to 128 chars (Telegram limit)
        if len(fname) > 128:
            name, ext = os.path.splitext(fname)
            fname = name[:128-len(ext)] + ext
        
        return fname

    # ────────────────────────────────────────────────────────────────────
    # ✅ v21.0 helper: extract video metadata (width, height, duration)
    # This is CRITICAL to prevent squished/stretch video aspect ratio
    # ────────────────────────────────────────────────────────────────────

    def extract_video_metadata(chat_message) -> dict:
        """
        Extract video metadata from message.
        Without width/height/duration, Telegram displays video with wrong aspect ratio.
        
        Returns:
            dict: width, height, duration keys
        """
        metadata = {
            "width": 0,
            "height": 0,
            "duration": 0,
        }

        video = chat_message.video
        if video:
            metadata["width"]    = getattr(video, "width",    0) or 0
            metadata["height"]   = getattr(video, "height",   0) or 0
            metadata["duration"] = getattr(video, "duration", 0) or 0
        elif chat_message.document:
            doc = chat_message.document
            metadata["width"]    = getattr(doc, "width",    0) or 0
            metadata["height"]   = getattr(doc, "height",   0) or 0
            metadata["duration"] = getattr(doc, "duration", 0) or 0
        elif chat_message.animation:
            anim = chat_message.animation
            metadata["width"]    = getattr(anim, "width",    0) or 0
            metadata["height"]   = getattr(anim, "height",   0) or 0
            metadata["duration"] = getattr(anim, "duration", 0) or 0

        LOGGER.debug(
            f"[VideoMeta] Extracted → "
            f"width={metadata['width']}, "
            f"height={metadata['height']}, "
            f"duration={metadata['duration']}s"
        )
        return metadata

    # ────────────────────────────────────────────────────────────────────
    # v21.0 helper: upload media to Saved Messages by type
    async def _upload_to_saved(user_client, media_type, file_path, caption, thumb_path, msg_id):
        # If media_type is None (Pyrofork couldn't parse), sniff from file ext
        if media_type is None:
            ext = os.path.splitext(file_path)[1].lower()
            if ext in ('.mp4', '.mkv', '.webm', '.avi', '.mov'):
                media_type = MessageMediaType.VIDEO
            elif ext in ('.jpg', '.jpeg', '.png', '.webp', '.bmp'):
                media_type = MessageMediaType.PHOTO
            elif ext in ('.ogg', '.oga'):
                media_type = MessageMediaType.VOICE
            elif ext in ('.mp3', '.m4a', '.wav', '.flac'):
                media_type = MessageMediaType.AUDIO
            else:
                media_type = MessageMediaType.DOCUMENT
            LOGGER.info(f"[v21] sniffed media_type={media_type} for msg {msg_id} from ext")

        # v21.0: 如果文件没有扩展名或扩展名不匹配 media_type，使用 send_document 兜底
        ext = os.path.splitext(file_path)[1].lower()
        if media_type == MessageMediaType.PHOTO and ext not in ('.jpg', '.jpeg', '.png', '.webp', '.bmp'):
            try:
                media_info = await get_media_info(file_path)
                LOGGER.info(f"[v21] msg {msg_id} photo ext invalid, falling back to document")
                media_type = MessageMediaType.DOCUMENT
            except Exception:
                media_type = MessageMediaType.DOCUMENT
        elif media_type == MessageMediaType.VIDEO and ext not in ('.mp4', '.mkv', '.webm', '.avi', '.mov'):
            LOGGER.info(f"[v21] msg {msg_id} video ext invalid, falling back to document")
            media_type = MessageMediaType.DOCUMENT
        elif media_type == MessageMediaType.AUDIO and ext not in ('.mp3', '.m4a', '.wav', '.flac', '.aac'):
            media_type = MessageMediaType.DOCUMENT
        elif media_type == MessageMediaType.VOICE and ext not in ('.ogg', '.oga', '.opus'):
            media_type = MessageMediaType.DOCUMENT

        if media_type == MessageMediaType.VIDEO:
            duration, _, _ = await get_media_info(file_path)
            width, height = await get_video_resolution(file_path)

            # ✅ 优化: 尝试生成缩略图，失败时使用空缩略图
            thumb = None
            try:
                thumb = await get_video_thumbnail(file_path, duration)
                LOGGER.info(f"[v21] thumbnail generated for {msg_id}")
            except Exception as thumb_err:
                LOGGER.warning(f"[v21] thumbnail gen failed for {msg_id}: {thumb_err}, using no thumb")
                thumb = None

            # 第一次尝试：send_video with thumb
            try:
                await user_client.send_video("me", file_path, caption=caption,
                    duration=duration or 0, width=width, height=height,
                    thumb=thumb, supports_streaming=True)
                LOGGER.info(f"[v21] ok video msg {msg_id}")
            except Exception as video_err:
                LOGGER.warning(f"[v21] send_video failed (with thumb) for {msg_id}: {video_err}")
                # 第二次尝试：不带缩略图
                try:
                    await user_client.send_video("me", file_path, caption=caption,
                        duration=duration or 0, width=width, height=height,
                        supports_streaming=True)
                    LOGGER.info(f"[v21] ok video (no thumb) msg {msg_id}")
                except Exception as video_err2:
                    LOGGER.warning(f"[v21] send_video failed (no thumb) for {msg_id}: {video_err2}, fallback to document")
                    # 最终兜底：作为文档发送
                    await user_client.send_document("me", file_path, caption=caption, thumb=thumb)
                    LOGGER.info(f"[v21] ok document (fallback from video) msg {msg_id}")
        elif media_type == MessageMediaType.PHOTO:
            try:
                await user_client.send_photo("me", file_path, caption=caption)
                LOGGER.info(f"[v21] ok photo msg {msg_id}")
            except Exception as photo_err:
                LOGGER.warning(f"[v21] send_photo failed for {msg_id}: {photo_err}, fallback to document")
                await user_client.send_document("me", file_path, caption=caption)
                LOGGER.info(f"[v21] ok document (fallback from photo) msg {msg_id}")
        elif media_type == MessageMediaType.DOCUMENT:
            thumb = None
            ext = os.path.splitext(file_path)[1].lower()
            if ext in ('.mp4','.mkv','.webm','.avi','.mov'):
                try:
                    doc_dur, _, _ = await get_media_info(file_path)
                    thumb = await get_video_thumbnail(file_path, doc_dur or 0)
                except Exception: pass
            await user_client.send_document("me", file_path, caption=caption, thumb=thumb)
            LOGGER.info(f"[v21] ok document msg {msg_id}")
        elif media_type == MessageMediaType.AUDIO:
            duration, artist, title = await get_media_info(file_path)
            await user_client.send_audio("me", file_path, caption=caption,
                duration=duration or 0, performer=artist, title=title)
            LOGGER.info(f"[v21] ok audio msg {msg_id}")
        elif media_type == MessageMediaType.VIDEO_NOTE:
            duration, _, _ = await get_media_info(file_path)
            await user_client.send_video_note("me", file_path, duration=duration or 0)
            LOGGER.info(f"[v21] ok video_note msg {msg_id}")
        elif media_type == MessageMediaType.VOICE:
            await user_client.send_voice("me", file_path, caption=caption)
            LOGGER.info(f"[v21] ok voice msg {msg_id}")
        else:
            await user_client.send_document("me", file_path, caption=caption)
            LOGGER.info(f"[v21] ok fallback msg {msg_id} type={media_type}")

    async def _apply_delay(idx):
        if idx < 25: t = 3
        elif idx < 50: t = 5
        elif idx < 100: t = 8
        else: t = 12
        await asyncio.sleep(t)

    async def _run_private_batch(bot: Client, status_message: Message, state: dict):
        user_id    = state["user_id"]
        chat_id    = status_message.chat.id
        session_id = state["session_id"]
        url        = state["url"]
        count      = state["count"]
        start_ts   = time.time()

        LOGGER.info(f"[PrivateBatch] v21.1: pure Pyrogram with optimizations")
        LOGGER.info(f"[PrivateBatch] user={user_id} chat={chat_id} session={session_id} url={url} count={count}")
        cancel_flags.pop(chat_id, None)

        try:
            await status_message.edit_text(
                "**⏳ 正在登录用户客户端...**",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⛔ 取消", callback_data=f"batch_cancel_{chat_id}"),
                ]]),
            )
        except Exception:
            pass

        user_client = await get_user_client(user_id, session_id)
        if user_client is None:
            await status_message.edit_text(
                "**❌ 初始化用户客户端失败！请重新 /login。**",
                parse_mode=ParseMode.MARKDOWN,
            )
            _del_state(chat_id)
            return

        user_data      = await user_activity_collection.find_one({"user_id": user_id})
        thumbnail_path = user_data.get("thumbnail_path") if user_data else None
        success_count  = 0
        fail_count     = 0
        _current_status = ""
        _file_progress = [0, 0]

        try:
            pvt_chat_id, start_message_id = getChatMsgID(url)
        except ValueError as e:
            await status_message.edit_text(f"**❌ {e}**", parse_mode=ParseMode.MARKDOWN)
            _del_state(chat_id)
            await safe_stop_client(user_client)
            return

        try:
            await status_message.edit_text(
                f"**⏳ 正在获取消息...**\n共 `{count}` 条",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⛔ 取消", callback_data=f"batch_cancel_{chat_id}"),
                ]]),
            )
        except Exception:
            pass

        # ── 进度 UI ──
        _progress_running = True
        last_edit = time.time()
        idx = 1
        total_msg_count = count

        def _update_progress():
            nonlocal last_edit
            now = time.time()
            if idx % 2 == 0 or idx == 1 or idx == total_msg_count or (now - last_edit) >= 3:
                try:
                    asyncio.ensure_future(
                        status_message.edit_text(
                            _progress_text(idx, total_msg_count, success_count, fail_count, start_ts, True, status_line=_current_status),
                        )
                    )
                    last_edit = now
                except Exception:
                    pass

        async def _bg_update():
            while _progress_running:
                await asyncio.sleep(2)
                if not _progress_running:
                    break
                try:
                    _sl = _current_status
                    if _file_progress[1] > 0:
                        _cur = _file_progress[0]
                        _tot = _file_progress[1]
                        _pct = _cur / _tot * 100
                        _bar_len = 8
                        _filled = int(_bar_len * _cur / _tot)
                        _bar = "▓" * _filled + "░" * (_bar_len - _filled)
                        _human_cur = _cur / 1048576
                        _human_tot = _tot / 1048576
                        _sl += f"\n`[{_bar}]` {_pct:.0f}%  `{_human_cur:.1f}MB/{_human_tot:.1f}MB`"
                    await status_message.edit_text(
                        _progress_text(idx, total_msg_count, success_count, fail_count, start_ts, True, status_line=_sl),
                    )
                except Exception:
                    pass

        def _cleanup_bg():
            nonlocal _progress_running
            _progress_running = False
            try:
                _bg_task.cancel()
            except Exception:
                pass

        _bg_task = asyncio.create_task(_bg_update())

        # 跟踪已通过 "media group shell" fallback 处理过的消息 ID，避免主循环重复处理
        _handled_by_grouped_fallback: set = set()

        try:
            # ── v21.0 核心：使用 get_chat_history 获取消息 ──
            LOGGER.info(f"[v21] start={start_message_id} count={count}")

            # 关键步骤：用 raw API 获取 channel access_hash 并注入 peer 缓存
            try:
                _raw_channel_id = int(str(pvt_chat_id)[4:])
                _r = await user_client.invoke(
                    raw.functions.channels.GetChannels(
                        id=[raw.types.InputChannel(channel_id=_raw_channel_id, access_hash=0)]
                    )
                )
                if _r.chats:
                    _p = _r.chats[0]
                    if hasattr(_p, 'access_hash') and _p.access_hash:
                        _peer = raw.types.InputPeerChannel(
                            channel_id=_raw_channel_id,
                            access_hash=_p.access_hash
                        )
                        if hasattr(user_client, 'peers_by_id'):
                            user_client.peers_by_id[pvt_chat_id] = _peer
                        LOGGER.info(f"[v21] raw resolved and cached peer for -100{_raw_channel_id}")
            except Exception as e:
                LOGGER.warning(f"[v21] raw peer resolve failed: {e}")

            target_message_ids = list(range(start_message_id, start_message_id + count))
            LOGGER.info(f"[v21] target message ids: {target_message_ids}")

            # 使用 get_chat_history 获取消息
            all_messages = []
            try:
                _end_id = start_message_id + count - 1
                _collected = []
                LOGGER.info(f"[v21] get_chat_history with offset_id={start_message_id + count}, limit=100")
                async for m in user_client.get_chat_history(
                    chat_id=pvt_chat_id,
                    offset_id=start_message_id + count,
                    limit=100,
                ):
                    if m.empty or not m.id:
                        continue
                    if m.id < start_message_id:
                        continue
                    if m.id > _end_id:
                        continue
                    _collected.append(m)
                    LOGGER.info(f"[v21]   got msg id={m.id} media={bool(m.media)} v={bool(getattr(m,'video',None))} p={bool(getattr(m,'photo',None))} d={bool(getattr(m,'document',None))}")
                    if m.id == start_message_id:
                        break
                all_messages = list(reversed(_collected))
                LOGGER.info(f"[v21] get_chat_history collected {len(all_messages)} msgs in [{start_message_id}, {_end_id}]")
            except Exception as e:
                LOGGER.warning(f"[v21] get_chat_history failed: {type(e).__name__}: {e}")
            if not all_messages:
                LOGGER.info("[v21] get_chat_history empty, falling back to expanded get_messages")
                all_messages = []
                CHUNK = 200
                try:
                    for i in range(0, len(target_message_ids), CHUNK):
                        chunk_ids = target_message_ids[i:i + CHUNK]
                        try:
                            chunk_msgs = await user_client.get_messages(
                                chat_id=pvt_chat_id, message_ids=chunk_ids
                            )
                            if chunk_msgs:
                                if not isinstance(chunk_msgs, list):
                                    chunk_msgs = [chunk_msgs]
                                for m in chunk_msgs:
                                    if m and not getattr(m, 'empty', False) and m.id:
                                        all_messages.append(m)
                        except Exception as e2:
                            LOGGER.warning(f"[v21] chunk fetch failed: {e2}")
                except Exception as e2:
                    LOGGER.warning(f"[v21] get_messages failed: {e2}")

            # 按 ID 升序排序
            all_messages.sort(key=lambda m: m.id)

            if all_messages:
                got_ids = [m.id for m in all_messages]
                LOGGER.info(f"[v21] got message ids: {got_ids}")

            messages = all_messages

            if not messages:
                await status_message.edit_text(
                    f"**❌ 无法获取消息。**\n\n"
                    f"**🔢 请求：** `{count}` 条\n"
                    f"**📌 起始 ID：** `{start_message_id}`",
                    parse_mode=ParseMode.MARKDOWN,
                )
                await safe_stop_client(user_client)
                _del_state(chat_id)
                return

            total_msg_count = len(messages)
            LOGGER.info(f"[v21] got {total_msg_count} msgs")

            # ── 预处理：扩展媒体组消息 ──
            # 对于有 media_group_id 的消息，扫描整个媒体组以确保不漏掉任何媒体
            _expanded_ids = set(m.id for m in messages)
            _expanded_msgs = list(messages)
            _mgid_set = {}
            for m in messages:
                _mgid = getattr(m, 'media_group_id', None)
                if _mgid:
                    _mgid_set.setdefault(_mgid, []).append(m)
            
            for _mgid, _mg_msgs in _mgid_set.items():
                _first_mid = min(m.id for m in _mg_msgs)
                _last_mid = max(m.id for m in _mg_msgs)
                LOGGER.info(f"[v21] expanding media_group {_mgid} from [{_first_mid}, {_last_mid}] ({len(_mg_msgs)} msgs)")
                try:
                    _found_new = 0
                    _search_back = 100
                    _search_forward = 100
                    _min_search_id = max(1, _first_mid - _search_back)
                    _max_search_id = _last_mid + _search_forward
                    
                    _exp_ids_before = set(_expanded_ids)
                    
                    # 向后搜索（消息 ID 更大的方向）
                    async for _exp_msg in user_client.get_chat_history(
                        chat_id=pvt_chat_id,
                        offset_id=_max_search_id + 1,
                        limit=_search_forward + 50,
                    ):
                        if _exp_msg.id < _min_search_id:
                            break
                        if getattr(_exp_msg, 'media_group_id', None) == _mgid:
                            if _exp_msg.id not in _expanded_ids:
                                _expanded_msgs.append(_exp_msg)
                                _expanded_ids.add(_exp_msg.id)
                                _found_new += 1
                                LOGGER.info(f"[v21]   expanded: id={_exp_msg.id} media={bool(getattr(_exp_msg, 'media'))} v={bool(getattr(_exp_msg, 'video'))} p={bool(getattr(_exp_msg, 'photo'))}")
                    
                    # 向前搜索（消息 ID 更小的方向）：通过多次 get_chat_history 拼接
                    # get_chat_history 从 offset_id 往旧消息方向走，所以向前搜索需要更小的 offset_id
                    if _first_mid > 1:
                        _fwd_offset = max(1, _first_mid - 1)
                        _fwd_limit = min(_search_back + 50, _first_mid)
                        async for _exp_msg_prev in user_client.get_chat_history(
                            chat_id=pvt_chat_id,
                            offset_id=_fwd_offset,
                            limit=_fwd_limit,
                        ):
                            if _exp_msg_prev.id < _min_search_id:
                                break
                            if getattr(_exp_msg_prev, 'media_group_id', None) == _mgid:
                                if _exp_msg_prev.id not in _expanded_ids:
                                    _expanded_msgs.append(_exp_msg_prev)
                                    _expanded_ids.add(_exp_msg_prev.id)
                                    _found_new += 1
                                    LOGGER.info(f"[v21]   expanded (prev): id={_exp_msg_prev.id} media={bool(getattr(_exp_msg_prev, 'media'))} v={bool(getattr(_exp_msg_prev, 'video'))} p={bool(getattr(_exp_msg_prev, 'photo'))}")
                    
                    _expanded_msgs.sort(key=lambda m: m.id)
                    if _found_new:
                        LOGGER.info(f"[v21] media_group {_mgid} expanded by {_found_new} msgs (total {len(_expanded_ids) - len(_exp_ids_before)} new)")
                    else:
                        LOGGER.info(f"[v21] media_group {_mgid}: no new msgs found in ±{_search_back} range")
                except Exception as _exp_e:
                    LOGGER.warning(f"[v21] media_group expansion failed for {_mgid}: {_exp_e}")
            
            messages = _expanded_msgs
            total_msg_count = len(messages)
            LOGGER.info(f"[v21] total messages after expansion: {total_msg_count}")

            # ── 预处理：修复媒体组中的 shell message ──
            # 对于有 media_group_id 但没有具体媒体属性的消息，refetch 以获取完整信息
            _fixed_msgs = []
            for m in messages:
                _mgid = getattr(m, 'media_group_id', None)
                if _mgid and not (m.video or m.photo or m.document or m.audio):
                    # 这是媒体组中的 shell message，尝试 refetch
                    try:
                        _refetched = await user_client.get_messages(chat_id=pvt_chat_id, message_ids=m.id)
                        if _refetched and not getattr(_refetched, 'empty', True):
                            LOGGER.info(f"[v21] refetched shell msg {m.id} → {_refetched.id} video={bool(_refetched.video)} photo={bool(_refetched.photo)}")
                            _fixed_msgs.append(_refetched)
                        else:
                            _fixed_msgs.append(m)
                    except Exception as _ref_e:
                        LOGGER.warning(f"[v21] refetch failed for shell msg {m.id}: {_ref_e}")
                        _fixed_msgs.append(m)
                else:
                    _fixed_msgs.append(m)
            messages = _fixed_msgs

            # ── 遍历处理每一条消息 ──
            for j, msg in enumerate(messages, 1):
                if cancel_flags.get(chat_id):
                    break

                idx = j
                mid = msg.id

                # 跳过已通过 grouped fallback 处理过的消息
                if mid in _handled_by_grouped_fallback:
                    LOGGER.info(f"[v21] skip {mid} — already handled by grouped fallback")
                    continue

                try:
                    # 详细诊断
                    _has_media = bool(msg.media)
                    _has_video = bool(getattr(msg, 'video', None))
                    _has_photo = bool(getattr(msg, 'photo', None))
                    _has_doc = bool(getattr(msg, 'document', None))
                    _has_audio = bool(getattr(msg, 'audio', None))
                    _has_vn = bool(getattr(msg, 'video_note', None))
                    _has_voice = bool(getattr(msg, 'voice', None))
                    _has_anim = bool(getattr(msg, 'animation', None))
                    _has_sticker = bool(getattr(msg, 'sticker', None))
                    _grouped_id = getattr(msg, 'grouped_id', None)
                    _media_group_id = getattr(msg, 'media_group_id', None)
                    _is_scheduled = getattr(msg, 'from_scheduled', False)
                    LOGGER.info(
                        f"[v21] {idx}/{total_msg_count} id={mid} "
                        f"media={_has_media} v={_has_video} p={_has_photo} d={_has_doc} "
                        f"a={_has_audio} vn={_has_vn} vo={_has_voice} an={_has_anim} s={_has_sticker} "
                        f"grouped={_grouped_id} mgid={_media_group_id} sched={_is_scheduled}"
                    )

                    # 文字消息 → 直接发送（排除有 media_group_id 的 shell message）
                    if msg.text and not _has_media and not (_has_video or _has_photo or _has_doc or _has_audio or _has_vn or _has_voice) and not _media_group_id:
                        _current_status = f"text {idx}/{total_msg_count}"
                        _update_progress()
                        try:
                            _parsed = await get_parsed_msg(msg.text, msg.entities or msg.caption_entities)
                            await bot.send_message(chat_id=chat_id, text=_parsed, parse_mode=ParseMode.MARKDOWN)
                            success_count += 1
                        except Exception:
                            fail_count += 1
                        await asyncio.sleep(1)
                        continue

                    # 媒体消息：只有真正有媒体属性的才进入此分支
                    # 注意：不再检查 _media_group_id，因为 shell message 也应该有 media_group_id
                    # shell message 将被下面的专门分支处理（使用 get_media_group()）
                    if _has_media or _has_video or _has_photo or _has_doc or _has_audio or _has_vn or _has_voice or _has_anim or _has_sticker:
                        caption_text = msg.caption.markdown if msg.caption else ""
                        
                        # ✅ 修复: 对于 shell message，需要从 msg.media 推断正确的 media_type 枚举
                        if msg.video:
                            media_type = MessageMediaType.VIDEO
                        elif msg.photo:
                            media_type = MessageMediaType.PHOTO
                        elif msg.document:
                            media_type = MessageMediaType.DOCUMENT
                        elif msg.audio:
                            media_type = MessageMediaType.AUDIO
                        elif msg.video_note:
                            media_type = MessageMediaType.VIDEO_NOTE
                        elif msg.voice:
                            media_type = MessageMediaType.VOICE
                        elif msg.animation:
                            media_type = MessageMediaType.ANIMATION
                        elif msg.sticker:
                            media_type = MessageMediaType.STICKER
                        elif _has_media:
                            # Shell message: 从 msg.media 推断
                            _media_class = type(msg.media).__name__ if msg.media else None
                            if _media_class == 'MessageMediaPhoto':
                                media_type = MessageMediaType.PHOTO
                            elif _media_class == 'MessageMediaVideo':
                                media_type = MessageMediaType.VIDEO
                            elif _media_class == 'MessageMediaDocument':
                                media_type = MessageMediaType.DOCUMENT
                            elif _media_class == 'MessageMediaAudio':
                                media_type = MessageMediaType.AUDIO
                            else:
                                media_type = msg.media  # 保持原样，让 _upload_to_saved 处理
                        else:
                            media_type = msg.media

                        _current_status = f"download {idx}/{total_msg_count}"
                        _update_progress()

                        # 使用正确的文件扩展名
                        _dl_name = _build_dl_filename(msg, mid, media_type)
                        
                        # ✅ 新增: download_media 添加 FileReferenceExpired 重试
                        file_path = None
                        for _retry in range(3):
                            try:
                                file_path = await user_client.download_media(
                                    msg,
                                    file_name=_dl_name,
                                    progress=Leaves.progress_for_pyrogram,
                                    progress_args=progressArgs("downloading", status_message, start_ts),
                                )
                                break
                            except FileReferenceExpired:
                                LOGGER.warning(f"[v21] FileReferenceExpired for {mid}, retrying... ({_retry+1}/3)")
                                # 重新获取消息
                                try:
                                    msg = await user_client.get_messages(chat_id=pvt_chat_id, message_ids=mid)
                                    if not msg:
                                        break
                                except Exception:
                                    break
                            except Exception as dl_err:
                                LOGGER.error(f"[v21] download error for {mid}: {dl_err}")
                                break
                        
                        if not file_path or not os.path.exists(file_path):
                            LOGGER.warning(f"[v21] dl failed after retries: {mid}")
                            fail_count += 1
                            continue

                        _current_status = f"upload {idx}/{total_msg_count}"
                        _update_progress()

                        await _upload_to_saved(user_client, media_type, file_path, caption_text, thumbnail_path, mid)
                        success_count += 1

                        # 清理
                        try:
                            if os.path.exists(file_path):
                                os.remove(file_path)
                        except Exception:
                            pass

                        _update_progress()
                        await _apply_delay(idx)
                        continue

                    # 壳消息处理（受限频道返回 media=None）
                    # 有 media_group_id 但没有具体媒体属性 → 使用 get_media_group() 获取完整媒体组
                    # ⚠️ 不再检查 msg.text，因为 shell message 可能带 caption 文本
                    if not _has_media and not (_has_video or _has_photo or _has_doc or _has_audio or _has_vn or _has_voice) and _media_group_id:
                        LOGGER.info(f"[v21] shell msg {mid}, trying forward_messages...")
                        _current_status = f"shell {idx}/{total_msg_count}"
                        _update_progress()

                        _downloaded = False

                        # 诊断: 使用 raw API 获取 shell message 的原始媒体类型
                        # 受限频道可能导致 msg.media 为空，但原始消息可能有媒体
                        _raw_msg_for_download = None
                        try:
                            _raw_peer = await user_client.resolve_peer(pvt_chat_id)
                            _raw_chan_id = int(str(pvt_chat_id)[4:]) if str(pvt_chat_id).startswith('-100') else 0
                            _raw_access_hash = getattr(_raw_peer, 'access_hash', 0) if _raw_peer else 0
                            if _raw_chan_id and _raw_access_hash:
                                _raw_msgs = await user_client.invoke(
                                    raw.functions.channels.GetMessages(
                                        channel=raw.types.InputChannel(
                                            channel_id=_raw_chan_id,
                                            access_hash=_raw_access_hash,
                                        ),
                                        id=[raw.types.InputMessageID(id=mid)],
                                    )
                                )
                                _raw_msg_list = getattr(_raw_msgs, 'messages', [])
                                if _raw_msg_list:
                                    _raw_m = _raw_msg_list[0]
                                    _raw_media = getattr(_raw_m, 'media', None)
                                    _raw_media_type = type(_raw_media).__name__ if _raw_media else 'None'
                                    _raw_mgid = getattr(_raw_m, 'media_group_id', None)
                                    LOGGER.info(f"[v21] DIAG shell {mid}: raw_media={_raw_media_type} mgid={_raw_mgid}")
                                    
                                    _is_unsupported = _raw_media_type == 'MessageMediaUnsupported'
                                    if _is_unsupported:
                                        LOGGER.info(f"[v21] DIAG shell {mid}: This is MessageMediaUnsupported - will try to download raw")
                                        _raw_msg_for_download = _raw_m
                                    
                                    _raw_media_cls = type(_raw_media).__name__ if _raw_media else ''
                                    if 'Photo' in _raw_media_cls:
                                        LOGGER.info(f"[v21] DIAG shell {mid}: raw type contains Photo")
                                    elif 'Video' in _raw_media_cls:
                                        LOGGER.info(f"[v21] DIAG shell {mid}: raw type contains Video")
                                    elif 'Document' in _raw_media_cls:
                                        _doc = getattr(_raw_media, 'document', None)
                                        if _doc:
                                            _mime = getattr(_doc, 'mime_type', '')
                                            LOGGER.info(f"[v21] DIAG shell {mid}: raw type = Document mime={_mime}")
                        except Exception as _diag_err:
                            LOGGER.warning(f"[v21] DIAG failed for {mid}: {_diag_err}")

                        # 方法0: 使用 raw API 直接下载 MessageMediaUnsupported
                        # 如果 raw API 返回 MessageMediaUnsupported，直接用 download_media 尝试下载原始消息对象
                        # 这是处理受限频道 shell message 的最底层方法
                        if not _downloaded and _raw_msg_for_download:
                            LOGGER.info(f"[v21] trying raw API download for MessageMediaUnsupported")
                            try:
                                _raw_path = await user_client.download_media(
                                    _raw_msg_for_download,
                                    file_name=f"raw_{mid}_",
                                    progress=Leaves.progress_for_pyrogram,
                                    progressArgs=progressArgs("downloading", status_message, start_ts),
                                )
                                if _raw_path and os.path.exists(_raw_path):
                                    _raw_ext = os.path.splitext(_raw_path)[1].lower()
                                    _raw_type = (
                                        MessageMediaType.VIDEO if _raw_ext in ('.mp4','.mkv','.webm','.mov','.avi')
                                        else MessageMediaType.PHOTO if _raw_ext in ('.jpg','.jpeg','.png','.webp','.gif')
                                        else MessageMediaType.DOCUMENT
                                    )
                                    _raw_cap = await get_parsed_msg(msg.caption or "", msg.caption_entities or [])
                                    await _upload_to_saved(user_client, _raw_type, _raw_path, _raw_cap, thumbnail_path, mid)
                                    _downloaded = True
                                    success_count += 1
                                    LOGGER.info(f"[v21] raw API download OK: shell {mid} type={_raw_type} path={_raw_path}")
                                    try: os.remove(_raw_path)
                                    except: pass
                                    _handled_by_grouped_fallback.add(mid)
                                else:
                                    LOGGER.info(f"[v21] raw API download returned empty for shell {mid}")
                            except Exception as _raw_dl_err:
                                LOGGER.warning(f"[v21] raw API download failed for shell {mid}: {_raw_dl_err}")

                        # 方法1: 使用 get_media_group() 获取完整媒体组（参考 bisnuray/RestrictedContentDL）
                        # 这是最可靠的方法，因为 Pyrogram 的 get_media_group() 会通过 user client
                        # 获取同一 media_group_id 的所有消息，包括在受限频道中显示为 shell 的消息
                        try:
                            _group_msgs = await msg.get_media_group()
                            if _group_msgs and len(_group_msgs) > 1:
                                LOGGER.info(f"[v21] get_media_group() found {len(_group_msgs)} msgs for shell {mid}")
                                _group_success = 0
                                for _gm in _group_msgs:
                                    _gm_id = _gm.id
                                    _gm_caption = _gm.caption.markdown if _gm.caption else ""
                                    _gm_has_media = bool(_gm.photo or _gm.video or _gm.document or _gm.audio or _gm.voice or _gm.video_note or _gm.animation or _gm.sticker)

                                    if not _gm_has_media and _gm.media:
                                        # 尝试 refetch 以获取完整媒体属性
                                        try:
                                            _refetched = await user_client.get_messages(chat_id=pvt_chat_id, message_ids=_gm_id)
                                            if _refetched and not getattr(_refetched, 'empty', True):
                                                _gm = _refetched
                                                _gm_has_media = bool(_gm.photo or _gm.video or _gm.document or _gm.audio or _gm.voice or _gm.video_note or _gm.animation or _gm.sticker)
                                                LOGGER.info(f"[v21] get_media_group refetched {_gm_id}: media={_gm_has_media}")
                                        except Exception:
                                            pass

                                    if not _gm_has_media:
                                        LOGGER.info(f"[v21] get_media_group msg {_gm_id} has no media, skipping")
                                        continue

                                    try:
                                        _gm_dl_name = _build_dl_filename(_gm, _gm_id, _gm.media)
                                        _gm_path = await _gm.download(
                                            file_name=_gm_dl_name,
                                            progress=Leaves.progress_for_pyrogram,
                                            progress_args=progressArgs("downloading", status_message, start_ts),
                                        )
                                        if _gm_path and os.path.exists(_gm_path):
                                            _gm_ext = os.path.splitext(_gm_path)[1].lower()
                                            _gm_type = (
                                                MessageMediaType.VIDEO if _gm.video or _gm_ext in ('.mp4','.mkv','.webm','.mov','.avi')
                                                else MessageMediaType.PHOTO if _gm.photo or _gm_ext in ('.jpg','.jpeg','.png','.webp','.gif')
                                                else MessageMediaType.AUDIO if _gm.audio or _gm_ext in ('.mp3','.m4a','.wav','.flac')
                                                else MessageMediaType.DOCUMENT
                                            )
                                            _gm_cap = await get_parsed_msg(_gm.caption or "", _gm.caption_entities or [])
                                            await _upload_to_saved(user_client, _gm_type, _gm_path, _gm_cap, thumbnail_path, _gm_id)
                                            _group_success += 1
                                            LOGGER.info(f"[v21] get_media_group OK: msg {_gm_id} type={_gm_type}")
                                            try: os.remove(_gm_path)
                                            except: pass
                                        else:
                                            LOGGER.warning(f"[v21] get_media_group download failed for {_gm_id}")
                                    except Exception as _gm_err:
                                        LOGGER.warning(f"[v21] get_media_group err for {_gm_id}: {_gm_err}")

                                if _group_success > 0:
                                    _downloaded = True
                                    success_count += _group_success
                                    LOGGER.info(f"[v21] get_media_group success: {_group_success} msgs from shell {mid}")
                                    # 标记组内所有消息为已处理
                                    for _gm in _group_msgs:
                                        _handled_by_grouped_fallback.add(_gm.id)
                                else:
                                    LOGGER.info(f"[v21] get_media_group found msgs but none downloaded")
                            else:
                                LOGGER.info(f"[v21] get_media_group() returned empty or single msg for shell {mid}")
                        except Exception as _gmg_err:
                            LOGGER.warning(f"[v21] get_media_group() failed for shell {mid}: {_gmg_err}")

                        # 方法0.5: 直接下载 shell message 本身（如果原始消息有媒体但 Pyrogram 没解析出来）
                        # 受限频道可能导致 msg.media 为空，但 raw API 能获取到真实媒体
                        # 直接对 msg 对象调用 download()，Pyrogram 内部会用 user client 下载
                        if not _downloaded:
                            LOGGER.info(f"[v21] trying direct download on shell msg {mid}")
                            try:
                                _direct_path = await msg.download(
                                    file_name=f"shell_{mid}_",
                                    progress=Leaves.progress_for_pyrogram,
                                    progress_args=progressArgs("downloading", status_message, start_ts),
                                )
                                if _direct_path and os.path.exists(_direct_path):
                                    _direct_ext = os.path.splitext(_direct_path)[1].lower()
                                    _direct_type = (
                                        MessageMediaType.VIDEO if _direct_ext in ('.mp4','.mkv','.webm','.mov','.avi')
                                        else MessageMediaType.PHOTO if _direct_ext in ('.jpg','.jpeg','.png','.webp','.gif')
                                        else MessageMediaType.DOCUMENT
                                    )
                                    _direct_cap = await get_parsed_msg(msg.caption or "", msg.caption_entities or [])
                                    await _upload_to_saved(user_client, _direct_type, _direct_path, _direct_cap, thumbnail_path, mid)
                                    _downloaded = True
                                    success_count += 1
                                    LOGGER.info(f"[v21] direct download OK: shell {mid} type={_direct_type}")
                                    try: os.remove(_direct_path)
                                    except: pass
                                    _handled_by_grouped_fallback.add(mid)
                                else:
                                    LOGGER.info(f"[v21] direct download returned empty for shell {mid}")
                            except Exception as _direct_err:
                                LOGGER.warning(f"[v21] direct download failed for shell {mid}: {_direct_err}")

                        # 方法1: 直接用 raw API forward_messages 转发到 Saved Messages
                        try:
                            _peer = await user_client.resolve_peer(pvt_chat_id)
                            _random_id = random.randint(1, 2**63 - 1)
                            _fwd = await user_client.invoke(
                                raw.functions.messages.ForwardMessages(
                                    from_peer=_peer,
                                    id=[mid],
                                    to_peer=raw.types.InputPeerSelf(),
                                    random_id=[_random_id],
                                )
                            )
                            if _fwd and hasattr(_fwd, 'updates') and _fwd.updates:
                                _downloaded = True
                                LOGGER.info(f"[v21] forward_messages success for shell {mid}")
                                success_count += 1
                        except Exception as fwd_err:
                            LOGGER.warning(f"[v21] forward_messages failed for {mid}: {fwd_err}")

                        # 方法2: forward 失败，尝试 channels.getMessages 检查原始媒体类型
                        if not _downloaded:
                            try:
                                _raw_channel_id = int(str(pvt_chat_id)[4:])
                                _peer2 = await user_client.resolve_peer(pvt_chat_id)
                                _r = await user_client.invoke(
                                    raw.functions.channels.GetMessages(
                                        channel=raw.types.InputChannel(
                                            channel_id=_raw_channel_id,
                                            access_hash=getattr(_peer2, 'access_hash', 0),
                                        ),
                                        id=[raw.types.InputMessageID(id=mid)],
                                    )
                                )
                                _raw_msgs = getattr(_r, 'messages', [])
                                for _rm in _raw_msgs:
                                    _rm_id = getattr(_rm, 'id', None)
                                    _rm_media = getattr(_rm, 'media', None)
                                    if _rm_media and _rm_id == mid:
                                        # 如果不是 Unsupported，尝试正常下载
                                        if not isinstance(_rm_media, raw.types.MessageMediaUnsupported):
                                            try:
                                                _rm_path = await user_client.download_media(
                                                    _rm_media,
                                                    file_name=f"ch_{mid}_",
                                                    progress=Leaves.progress_for_pyrogram,
                                                    progress_args=progressArgs("downloading", status_message, start_ts),
                                                )
                                                if _rm_path and os.path.exists(_rm_path):
                                                    _downloaded = True
                                                    LOGGER.info(f"[v21] channels.getMessages download OK: {_rm_path}")
                                                    _ext = os.path.splitext(_rm_path)[1].lower()
                                                    _rm_type = MessageMediaType.VIDEO if _ext in ('.mp4','.mkv','.webm','.mov','.avi') else (MessageMediaType.PHOTO if _ext in ('.jpg','.jpeg','.png','.webp','.gif') else MessageMediaType.DOCUMENT)
                                                    _rm_cap = await get_parsed_msg(msg.caption or "", msg.caption_entities or [])
                                                    await _upload_to_saved(user_client, _rm_type, _rm_path, _rm_cap, thumbnail_path, mid)
                                                    success_count += 1
                                                    try: os.remove(_rm_path)
                                                    except: pass
                                            except Exception as rde:
                                                LOGGER.warning(f"[v21] channels.getMessages download err: {rde}")
                                        break
                            except Exception as ce:
                                LOGGER.warning(f"[v21] channels.getMessages failed: {ce}")

                        # 方法3: 最后尝试 copy_message
                        if not _downloaded:
                            LOGGER.info(f"[v21] trying copy_message for shell {mid}")
                            try:
                                _copied = await user_client.copy_message(
                                    chat_id="me",
                                    from_chat_id=pvt_chat_id,
                                    message_id=mid,
                                )
                                if _copied:
                                    _downloaded = True
                                    LOGGER.info(f"[v21] copy_message success for shell {mid}")
                                    success_count += 1
                            except Exception as copy_err:
                                LOGGER.warning(f"[v21] copy_message failed for {mid}: {copy_err}")

                        # 方法4: get_messages 重新获取，Pyrogram 可能填充正确的 media
                        if not _downloaded:
                            LOGGER.info(f"[v21] trying get_messages retry for shell {mid}")
                            try:
                                _refreshed = await user_client.get_messages(
                                    chat_id=pvt_chat_id, message_ids=[mid]
                                )
                                if _refreshed and not getattr(_refreshed, 'empty', True):
                                    _ref_media = getattr(_refreshed, 'media', None)
                                    if _ref_media:
                                        _ref_path = await _refreshed.download(
                                            file_name=f"shell_{mid}_",
                                            progress=Leaves.progress_for_pyrogram,
                                            progress_args=progressArgs("downloading", status_message, start_ts),
                                        )
                                        if _ref_path and os.path.exists(_ref_path):
                                            _downloaded = True
                                            LOGGER.info(f"[v21] get_messages download OK: {_ref_path}")
                                            _ext = os.path.splitext(_ref_path)[1].lower()
                                            _rm_type = MessageMediaType.VIDEO if _ext in ('.mp4','.mkv','.webm','.mov','.avi') else (MessageMediaType.PHOTO if _ext in ('.jpg','.jpeg','.png','.webp','.gif') else MessageMediaType.DOCUMENT)
                                            _rm_cap = await get_parsed_msg(msg.caption or "", msg.caption_entities or [])
                                            await _upload_to_saved(user_client, _rm_type, _ref_path, _rm_cap, thumbnail_path, mid)
                                            success_count += 1
                                            try: os.remove(_ref_path)
                                            except: pass
                            except Exception as gm_err:
                                LOGGER.warning(f"[v21] get_messages failed for {mid}: {gm_err}")

                        # 方法5: 获取整个 media_group，下载组内尚未处理的消息
                        if not _downloaded and _media_group_id:
                            LOGGER.info(f"[v21] trying media_group scan for shell {mid} (mgid={_media_group_id})")
                            try:
                                _scan_count = 0
                                _scan_min_id = mid - 50
                                _scan_max_id = mid + 50
                                
                                _group_msgs = []
                                async for _gm_msg in user_client.get_chat_history(
                                    chat_id=pvt_chat_id,
                                    offset_id=mid + 51,
                                    limit=200,
                                ):
                                    if _gm_msg.id < _scan_min_id:
                                        break
                                    if getattr(_gm_msg, 'media_group_id', None) == _media_group_id:
                                        _group_msgs.append(_gm_msg)
                                
                                async for _gm_msg_prev in user_client.get_chat_history(
                                    chat_id=pvt_chat_id,
                                    offset_id=_scan_max_id,
                                    limit=200,
                                ):
                                    if _gm_msg_prev.id < _scan_min_id:
                                        break
                                    if getattr(_gm_msg_prev, 'media_group_id', None) == _media_group_id:
                                        if _gm_msg_prev.id not in [m.id for m in _group_msgs]:
                                            _group_msgs.append(_gm_msg_prev)
                                
                                _group_msgs.sort(key=lambda m: m.id)
                                LOGGER.info(f"[v21] media_group scan found {len(_group_msgs)} msgs for mgid={_media_group_id}")
                                
                                for _gm_msg in _group_msgs:
                                    if _gm_msg.id == mid:
                                        continue
                                    if _gm_msg.id in _handled_by_grouped_fallback:
                                        continue
                                    _scan_count += 1
                                    if _scan_count > 20:
                                        break
                                    _gm_media = getattr(_gm_msg, 'media', None)
                                    if not _gm_media:
                                        continue
                                    try:
                                        _gm_path = await _gm_msg.download(
                                            file_name=f"group_{_gm_msg.id}_",
                                            progress=Leaves.progress_for_pyrogram,
                                            progress_args=progressArgs("downloading", status_message, start_ts),
                                        )
                                        if _gm_path and os.path.exists(_gm_path):
                                            _downloaded = True
                                            LOGGER.info(f"[v21] group download OK: {_gm_path} (msg {_gm_msg.id})")
                                            _ext = os.path.splitext(_gm_path)[1].lower()
                                            _rm_type = MessageMediaType.VIDEO if _ext in ('.mp4','.mkv','.webm','.mov','.avi') else (MessageMediaType.PHOTO if _ext in ('.jpg','.jpeg','.png','.webp','.gif') else MessageMediaType.DOCUMENT)
                                            _gm_cap = await get_parsed_msg(_gm_msg.caption or "", _gm_msg.caption_entities or [])
                                            await _upload_to_saved(user_client, _rm_type, _gm_path, _gm_cap, thumbnail_path, _gm_msg.id)
                                            success_count += 1
                                            try: os.remove(_gm_path)
                                            except: pass
                                            _handled_by_grouped_fallback.add(_gm_msg.id)
                                    except Exception as gme:
                                        LOGGER.warning(f"[v21] group msg {_gm_msg.id} download err: {gme}")
                            except Exception as mg_err:
                                LOGGER.warning(f"[v21] media_group scan failed for {mid}: {mg_err}")

                        # 方法6: 使用 raw API 直接获取消息并下载（最底层方法）
                        if not _downloaded and _media_group_id:
                            LOGGER.info(f"[v21] trying raw API direct download for shell {mid}")
                            try:
                                _raw_channel_id = int(str(pvt_chat_id)[4:])
                                _peer = await user_client.resolve_peer(pvt_chat_id)
                                _access_hash = getattr(_peer, 'access_hash', 0) if _peer else 0
                                
                                _search_start = mid - 10
                                _search_end = mid + 10
                                for _search_id in range(_search_start, _search_end + 1):
                                    if _search_id <= 0:
                                        continue
                                    try:
                                        _raw_result = await user_client.invoke(
                                            raw.functions.channels.GetMessages(
                                                channel=raw.types.InputChannel(
                                                    channel_id=_raw_channel_id,
                                                    access_hash=_access_hash,
                                                ),
                                                id=[raw.types.InputMessageID(id=_search_id)],
                                            )
                                        )
                                        _raw_messages = getattr(_raw_result, 'messages', [])
                                        for _rm in _raw_messages:
                                            if isinstance(_rm, raw.types.Message):
                                                _rm_media = getattr(_rm, 'media', None)
                                                if _rm_media:
                                                    _rm_mgid = getattr(_rm, 'media_group_id', None)
                                                    if _rm_mgid == _media_group_id:
                                                        _rm_has_video = isinstance(_rm_media, raw.types.MessageMediaVideo)
                                                        _rm_has_doc = isinstance(_rm_media, raw.types.MessageMediaDocument)
                                                        if _rm_has_video or _rm_has_doc:
                                                            LOGGER.info(f"[v21] raw API found video/doc in msg {_search_id} for mgid={_media_group_id}")
                                                            try:
                                                                _raw_path = await user_client.download_media(
                                                                    _rm,
                                                                    file_name=f"raw_{_search_id}_",
                                                                    progress=Leaves.progress_for_pyrogram,
                                                                    progress_args=progressArgs("downloading", status_message, start_ts),
                                                                )
                                                                if _raw_path and os.path.exists(_raw_path):
                                                                    _downloaded = True
                                                                    LOGGER.info(f"[v21] raw API download OK: {_raw_path}")
                                                                    _ext = os.path.splitext(_raw_path)[1].lower()
                                                                    _rm_type = MessageMediaType.VIDEO if _ext in ('.mp4','.mkv','.webm','.mov','.avi') else (MessageMediaType.PHOTO if _ext in ('.jpg','.jpeg','.png','.webp','.gif') else MessageMediaType.DOCUMENT)
                                                                    _rm_cap = await get_parsed_msg(msg.caption or "", msg.caption_entities or [])
                                                                    await _upload_to_saved(user_client, _rm_type, _raw_path, _rm_cap, thumbnail_path, _search_id)
                                                                    success_count += 1
                                                                    try: os.remove(_raw_path)
                                                                    except: pass
                                                                    _handled_by_grouped_fallback.add(_search_id)
                                                                    break
                                                            except Exception as raw_dl_err:
                                                                LOGGER.warning(f"[v21] raw API download err for {_search_id}: {raw_dl_err}")
                                                if _downloaded:
                                                    break
                                    except Exception as raw_err:
                                        continue
                                    if _downloaded:
                                        break
                            except Exception as raw_main_err:
                                LOGGER.warning(f"[v21] raw API scan failed for {mid}: {raw_main_err}")

                        if not _downloaded:
                            LOGGER.info(f"[v21] all methods failed for shell {mid}")
                            fail_count += 1
                        _handled_by_grouped_fallback.add(mid)
                        continue

                    # 无媒体无文字且不在媒体组中 → 跳过
                    LOGGER.warning(f"[v21] skip: {mid}")
                    fail_count += 1
                    _update_progress()

                except FloodWait as fw:
                    w = fw.value if hasattr(fw, 'value') else 60
                    LOGGER.warning(f"[v21] FloodWait {w}s at {mid}")
                    await asyncio.sleep(w + 2)
                    fail_count += 1
                except Exception as e:
                    LOGGER.error(f"[v21] err {mid}: {type(e).__name__}: {e}")
                    fail_count += 1

        except Exception as e:
            LOGGER.error(f"[PrivateBatch] Unexpected error: {e}", exc_info=True)
        finally:
            _cleanup_bg()

        # ── 详细统计日志 ──
        elapsed = int(time.time() - start_ts)
        LOGGER.info(
            f"[PrivateBatch] Completed: success={success_count} fail={fail_count} "
            f"total={success_count+fail_count} elapsed={elapsed}s"
        )

        # ── 更新统计 ──
        await daily_limit.update_one(
            {"user_id": user_id},
            {"$inc": {"total_downloads": success_count}},
            upsert=True,
        )

        # ── 完成消息 ──
        if cancel_flags.get(chat_id):
            try:
                await status_message.edit_text(
                    f"**⛔ 用户已取消批量下载。**\n\n"
                    f"**✅ 完成：** `{success_count}`  **❌ 失败：** `{fail_count}`",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass
            _del_state(chat_id)
            await safe_stop_client(user_client)
            return

        elapsed = int(time.time() - start_ts)
        completion_msg = await bot.send_message(
            chat_id=chat_id,
            text=(
                f"**✅ 私密批量下载完成！**\n\n"
                f"**📥 请求：** `{count}` 条\n"
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
        await safe_stop_client(user_client)
