# ✅ v14.0 重构：raw MTProto + InputDocument → user_client.download_media()
# ✅ 核心修复：msg.media=None 时，用 raw API 取 document，
# ✅   构造 InputDocument 传给 download_media → 绕开 Pyrofork 解析 bug
# ✅ 然后按 mime type 推断 media_type 上传到 Saved Messages
# ✅ 已修复：公开批量中 processMediaGroup 重复调用 bug

import os
import re
import json
import asyncio
from time import time
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
    safe_edit_progress,
    get_media_info,
    get_video_resolution,
    get_video_thumbnail,
    GLOBAL_DOWNLOAD_SEMAPHORE,
    GLOBAL_UPLOAD_SEMAPHORE,
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
    elapsed = time() - start_ts
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
        start_ts = time()

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

        last_edit = time()
        idx = 0
        _progress_running = True

        async def _bg_update():
            while _progress_running:
                await asyncio.sleep(3)
                if not _progress_running:
                    break
                try:
                    await safe_edit_progress(
                        status_message,
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

                        now = time()
                        if idx % 2 == 0 or idx == 1 or idx == effective_total or (now - last_edit) >= 3:
                            try:
                                await safe_edit_progress(
                                    status_message,
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

                now = time()
                if idx % 3 == 0 or idx == 1 or idx == effective_total or (now - last_edit) >= 3:
                    try:
                        await safe_edit_progress(
                            status_message,
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

        elapsed = int(time() - start_ts)
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

    # v6.0 helper: upload media to Saved Messages by type
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
            LOGGER.info(f"[PrivateBatch] sniffed media_type={media_type} for msg {msg_id} from ext")
        if media_type == MessageMediaType.VIDEO:
            duration, _, _ = await get_media_info(file_path)
            width, height = await get_video_resolution(file_path)
            thumb = await get_video_thumbnail(file_path, duration)
            await user_client.send_video("me", file_path, caption=caption,
                duration=duration or 0, width=width, height=height,
                thumb=thumb, supports_streaming=True)
            LOGGER.info(f"[PrivateBatch] ok video msg {msg_id}")
        elif media_type == MessageMediaType.PHOTO:
            await user_client.send_photo("me", file_path, caption=caption)
            LOGGER.info(f"[PrivateBatch] ok photo msg {msg_id}")
        elif media_type == MessageMediaType.DOCUMENT:
            thumb = None
            ext = os.path.splitext(file_path)[1].lower()
            if ext in ('.mp4','.mkv','.webm','.avi','.mov'):
                try:
                    doc_dur, _, _ = await get_media_info(file_path)
                    thumb = await get_video_thumbnail(file_path, doc_dur or 0)
                except Exception: pass
            await user_client.send_document("me", file_path, caption=caption, thumb=thumb)
            LOGGER.info(f"[PrivateBatch] ok document msg {msg_id}")
        elif media_type == MessageMediaType.AUDIO:
            duration, artist, title = await get_media_info(file_path)
            await user_client.send_audio("me", file_path, caption=caption,
                duration=duration or 0, performer=artist, title=title)
            LOGGER.info(f"[PrivateBatch] ok audio msg {msg_id}")
        elif media_type == MessageMediaType.VIDEO_NOTE:
            duration, _, _ = await get_media_info(file_path)
            await user_client.send_video_note("me", file_path, duration=duration or 0)
            LOGGER.info(f"[PrivateBatch] ok video_note msg {msg_id}")
        elif media_type == MessageMediaType.VOICE:
            await user_client.send_voice("me", file_path, caption=caption)
            LOGGER.info(f"[PrivateBatch] ok voice msg {msg_id}")
        else:
            await user_client.send_document("me", file_path, caption=caption)
            LOGGER.info(f"[PrivateBatch] ok fallback msg {msg_id} type={media_type}")

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
        start_ts   = time()

        LOGGER.info(f"[PrivateBatch] v14.0: raw InputDocument for video download")
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
        missing_count  = 0
        effective_total = count
        processed_groups = set()
        _current_status = ""
        _file_progress = [0, 0]

        try:
            log_user = await bot.get_users(user_id)
        except Exception as e:
            LOGGER.warning(f"[PrivateBatch] Could not fetch user {user_id} for logging: {e}")
            log_user = None

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

        # 按原版：直接用 get_messages 获取消息
        # 但需要先用 raw API 解析 channel peer（避免 Peer id invalid）
        try:
            _raw_channel_id = int(str(pvt_chat_id)[4:])  # -100XXXXXXXXX → XXXXXXXXX
            _r = await user_client.invoke(
                raw.functions.channels.GetChannels(
                    id=[raw.types.InputChannel(channel_id=_raw_channel_id, access_hash=0)]
                )
            )
            if _r.chats and hasattr(_r.chats[0], 'access_hash'):
                _peer = raw.types.InputPeerChannel(
                    channel_id=_raw_channel_id,
                    access_hash=_r.chats[0].access_hash
                )
                # 注入到 peer cache
                if hasattr(user_client, 'peers_by_id'):
                    user_client.peers_by_id[pvt_chat_id] = _peer
                if hasattr(user_client, 'peers_by_username'):
                    pass  # 用户名查找不影响
                LOGGER.info(f"[PrivateBatch] Channel peer resolved and cached")
        except Exception as e:
            LOGGER.warning(f"[PrivateBatch] Could not pre-resolve channel: {e}")

        total_msg_count = count  # 初始值，后面用 get_chat_history 结果更新

        class CancelDownload(Exception):
            pass

        def _file_progress_cb(current, total, *args):
            if cancel_flags.get(chat_id):
                raise CancelDownload()
            _file_progress[0] = current
            _file_progress[1] = total
            Leaves.progress_for_pyrogram(current, total, *args)

        _progress_running = True
        last_edit = time()

        def _update_progress():
            nonlocal last_edit
            now = time()
            if idx % 2 == 0 or idx == 1 or idx == total_msg_count or (now - last_edit) >= 3:
                try:
                    asyncio.ensure_future(
                        safe_edit_progress(
                            status_message,
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
                    await safe_edit_progress(
                        status_message,
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

        try:
            # ── v9.0：get_chat_history + client.download_media((chat, msg_id)) ──

            all_messages = []
            async for msg in user_client.get_chat_history(
                chat_id=pvt_chat_id,
                limit=count + 10,
                offset_id=start_message_id + count,
            ):
                all_messages.append(msg)
            all_messages.reverse()
            all_messages = [m for m in all_messages if m and m.id and m.id >= start_message_id]
            total_msg_count = len(all_messages)
            LOGGER.info(f"[PrivateBatch] v14.0: {total_msg_count} messages")

            if not all_messages:
                try:
                    await status_message.edit_text("**FAIL: No messages.**", parse_mode=ParseMode.MARKDOWN)
                except Exception:
                    pass
                _del_state(chat_id)
                await safe_stop_client(user_client)
                return

            for msg in all_messages:
                if cancel_flags.get(chat_id):
                    break

                idx = all_messages.index(msg) + 1
                msg_id = msg.id

                # media group: skip already processed
                _gid = getattr(msg, 'media_group_id', None)
                if _gid and _gid in processed_groups:
                    continue

                # text-only message
                if msg.text and not msg.media:
                    _current_status = f"text {idx}/{total_msg_count}"
                    try:
                        _parsed = await get_parsed_msg(msg.text, msg.entities or msg.caption_entities)
                        await bot.send_message(chat_id=chat_id, text=_parsed, parse_mode=ParseMode.MARKDOWN)
                        success_count += 1
                    except Exception:
                        fail_count += 1
                    _update_progress()
                    await asyncio.sleep(1)
                    continue

                # 有 media 或有 media_group_id：都尝试 download+upload
                if msg.media or _gid:
                    media_type = msg.media
                    caption_text = msg.caption.markdown if msg.caption else ""
                    file_path = None
                    try:
                        _current_status = f"download {idx}/{total_msg_count}"
                        _update_progress()
                        # v14.0：raw MTProto，but use file_id+download with custom storage
                        # 关键修复：Pyrofork 的 Message.media 为 None（视频），
                        # 但 raw Message 有 document。手动构造 file_id 再调 download_media
                        if not msg.media:
                            # raw fetch + construct synthetic Message
                            try:
                                from pyrogram.raw import functions as raw_funcs, types as raw_types
                                peer = await user_client.resolve_peer(pvt_chat_id)
                                result = await user_client.invoke(
                                    raw_funcs.channels.GetMessages(channel=peer, id=[msg_id])
                                )
                                if not result or not result.messages:
                                    LOGGER.warning(f"[raw] empty result for {msg_id}")
                                    fail_count += 1
                                    continue
                                raw_msg = result.messages[0]
                                if not isinstance(raw_msg, raw_types.Message) or not raw_msg.media:
                                    LOGGER.warning(f"[raw] no media for {msg_id}")
                                    fail_count += 1
                                    continue
                                # Get file_id from raw message
                                if isinstance(raw_msg.media, raw_types.MessageMediaDocument):
                                    doc = raw_msg.media.document
                                    # Build file_id from raw document
                                    file_id = raw_types.InputDocument(
                                        id=doc.id, access_hash=doc.access_hash,
                                        file_reference=doc.file_reference
                                    )
                                    # Try downloading via file_id
                                    file_path = await user_client.download_media(
                                        file_id,
                                        progress=Leaves.progress_for_pyrogram,
                                        progress_args=progressArgs("downloading", status_message, start_ts),
                                    )
                                    if file_path and os.path.exists(file_path):
                                        # Determine media_type by mime
                                        mime = doc.mime_type or ""
                                        if mime.startswith("video/"):
                                            media_type = MessageMediaType.VIDEO
                                        elif mime.startswith("audio/"):
                                            # Check voice vs audio
                                            is_voice = any(isinstance(a, raw_types.DocumentAttributeAudio) and a.voice
                                                          for a in doc.attributes)
                                            media_type = MessageMediaType.VOICE if is_voice else MessageMediaType.AUDIO
                                        else:
                                            media_type = MessageMediaType.DOCUMENT
                                        # Video note?
                                        is_vnote = any(isinstance(a, raw_types.DocumentAttributeVideo) and a.round_message
                                                      for a in doc.attributes)
                                        if is_vnote:
                                            media_type = MessageMediaType.VIDEO_NOTE
                                        LOGGER.info(f"[raw] downloaded {os.path.getsize(file_path)} bytes msg {msg_id} type={media_type}")
                                    else:
                                        LOGGER.warning(f"[raw] download_media returned None for {msg_id}")
                                        fail_count += 1
                                        continue
                                else:
                                    LOGGER.warning(f"[raw] non-document media for {msg_id}")
                                    fail_count += 1
                                    continue
                            except Exception as fe:
                                LOGGER.error(f"[raw] exception msg {msg_id}: {type(fe).__name__}: {fe}")
                                fail_count += 1
                                continue
                        else:
                            file_path = await msg.download(
                                progress=Leaves.progress_for_pyrogram,
                                progress_args=progressArgs("downloading", status_message, start_ts),
                            )
                        if file_path and os.path.exists(file_path):
                            _current_status = f"upload {idx}/{total_msg_count}"
                            _update_progress()
                            await _upload_to_saved(user_client, media_type, file_path, caption_text, thumbnail_path, msg_id)
                            success_count += 1
                            if _gid:
                                processed_groups.add(_gid)
                        else:
                            fail_count += 1
                    except FloodWait as fw:
                        w = fw.value if hasattr(fw, 'value') else 60
                        await asyncio.sleep(w + 2)
                        fail_count += 1
                    except Exception as e:
                        LOGGER.error(f"[PrivateBatch] Failed msg {msg_id}: {type(e).__name__}: {e}")
                        fail_count += 1
                    finally:
                        if file_path and os.path.exists(file_path):
                            try: os.remove(file_path)
                            except Exception: pass
                    _update_progress()
                    await _apply_delay(idx)
                    continue

                # v10.0：删除 v8.0 重复的 group 处理块（已合并到主分支）
                # no media, no group: skip
                LOGGER.warning(f"[PrivateBatch] skip msg {msg_id}: no media, no group")
                fail_count += 1
                _update_progress()
                await asyncio.sleep(1)

        except Exception as e:
            LOGGER.error(f"[PrivateBatch] Unexpected error: {e}")
        finally:
            _cleanup_bg()

        await daily_limit.update_one(
            {"user_id": user_id},
            {"$inc": {"total_downloads": success_count}},
            upsert=True,
        )

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

        elapsed = int(time() - start_ts)

        _missing_line = f"\n**⚠️ 频道已删除：** `{missing_count}` 条" if missing_count > 0 else ""
        completion_msg = await bot.send_message(
            chat_id=chat_id,
            text=(
                f"**✅ 私密批量下载完成！**\n\n"
                f"**📥 请求下载：** `{count}` 条\n"
                f"**✅ 下载成功：** `{success_count}`\n"
                f"**❌ 下载失败：** `{fail_count}`"
                f"{_missing_line}\n"
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
