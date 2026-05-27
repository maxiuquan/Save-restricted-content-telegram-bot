# Copyright @juktijol
# Channel t.me/juktijol
# Fixed: JSON persistence, proper cancel, improved progress tracking
# Fixed: All DB calls now use Motor async (await)
# ✅ FIXED: in_memory=True + no_updates=True → sqlite3 + TCPTransport error fix
# ✅ FIXED: AuthKeyUnregistered → session auto-remove + user notify
# ✅ FIXED: safe_stop_client → OSError ignore

import os
import re
import json
import asyncio
from time import time
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode, ChatType
from pyrogram.errors import (
    ChannelInvalid,
    ChannelPrivate,
    PeerIdInvalid,
    FileReferenceExpired,
    AuthKeyUnregistered,  # ✅ new import
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
        f"**{label} Batch Download**\n\n"
        f"`[{bar}]` {pct:.1f}%\n\n"
        f"**📥 Progress:** `{done}/{total}`\n"
        f"**✅ Success:** `{success}`  **❌ Failed:** `{fail}`\n"
        f"**⏱ Elapsed:** `{int(elapsed)}s`  **⏳ ETA:** `{eta_str}`\n\n"
        f"__Send /stop to cancel__"
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
            "**❌ Batch download is available for premium users only!**\n\n"
            "Free users can download one file at a time (5-minute cooldown).\n"
            "Upgrade to premium for batch downloads: /plans 💥",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if chat_id in batch_data and batch_data[chat_id].get("stage") in ("await_url", "await_count"):
        await message.reply_text(
            "**⚠️ You already have an active batch session.**\n"
            "Send /stop to cancel it first, or continue where you left off.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    _set_state(chat_id, {"user_id": user_id, "stage": "await_url"})
    await message.reply_text(
        "**📥 Send a Telegram link to start batch download:**\n\n"
        "✅ Public: `https://t.me/channel/123`\n"
        "🔒 Private: `https://t.me/c/1234567890/123`",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Cancel", callback_data=f"batch_cancel_{chat_id}"),
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
        """
        ✅ FIXED: Uses create_optimized_user_client
        with in_memory=True + no_updates=True.
        - sqlite3 ProgrammingError: Cannot operate on a closed database — fix
        - OSError: TCPTransport closed — fix
        """
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
            await client_obj.start()
            return client_obj
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
                "**❌ No active batch download to cancel.**",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        if state.get("user_id") != user_id:
            await message.reply_text(
                "**❌ Only the user who started the batch can stop it.**",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        cancel_flags[chat_id] = True
        await message.reply_text(
            "**⛔ Cancel signal sent. The batch will stop after the current file finishes...**",
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
                    "**❌ Batch download is available for premium users only!**\n\n"
                    "Free users can download one file at a time (5-minute cooldown).\n"
                    "Upgrade to premium for batch downloads: /plans 💥",
                    parse_mode=ParseMode.MARKDOWN,
                )
                return
            if chat_id in batch_data and batch_data[chat_id].get("stage") in ("await_url", "await_count"):
                await message.reply_text(
                    "**⚠️ You already have an active batch session.**\n"
                    "Send /stop to cancel it first, or continue where you left off.",
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
                    "**❌ Please enter a valid number! Example: `50`**",
                    parse_mode=ParseMode.MARKDOWN,
                )
                return

            is_premium, max_allowed = await get_batch_limits(user_id)
            if count < 1:
                await message.reply_text(
                    "**❌ Please enter at least 1!**",
                    parse_mode=ParseMode.MARKDOWN,
                )
                return
            if count > max_allowed:
                await message.reply_text(
                    f"**❌ Your plan allows a maximum of {max_allowed} messages per batch!**",
                    parse_mode=ParseMode.MARKDOWN,
                )
                return

            state["count"] = count
            state["stage"] = "confirmed"
            _set_state(chat_id, state)

            link_label = "🔒 Private" if state.get("is_private") else "✅ Public"
            await message.reply_text(
                f"**{link_label} Batch Download Confirmation**\n\n"
                f"**🔗 Source:** `{state.get('url')}`\n"
                f"**📊 Messages:** `{count}`\n\n"
                "Confirm to start downloading:",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Confirm", callback_data=f"batch_confirm_{chat_id}"),
                    InlineKeyboardButton("❌ Cancel",  callback_data=f"batch_cancel_{chat_id}"),
                ]]),
                parse_mode=ParseMode.MARKDOWN,
            )

    # ────────────────────────────────────────────────────────────────────
    # Callback handler
    # ────────────────────────────────────────────────────────────────────

    @app.on_callback_query(filters.regex(r"^batch_(confirm|cancel|session_select)_(\d+)$"))
    async def batch_callback_handler(client: Client, callback_query):
        data      = callback_query.data
        chat_id   = callback_query.message.chat.id
        user_id   = callback_query.from_user.id
        state     = batch_data.get(chat_id)

        if re.match(r"^batch_cancel_\d+$", data):
            if state and state.get("stage") == "running":
                cancel_flags[chat_id] = True
                await callback_query.message.edit_text(
                    "**⛔ Cancel signal sent. Stopping after current file...**",
                    parse_mode=ParseMode.MARKDOWN,
                )
            else:
                _del_state(chat_id)
                await callback_query.message.edit_text(
                    "**❌ Batch download cancelled.**",
                    parse_mode=ParseMode.MARKDOWN,
                )
            await callback_query.answer("Cancelled")
            return

        if re.match(r"^batch_session_select_\d+$", data):
            if not state or state.get("user_id") != user_id:
                await callback_query.answer("❌ Invalid session!", show_alert=True)
                return
            session_id = state.get("pending_sessions", {}).get(data)
            if not session_id:
                await callback_query.answer("❌ Session data lost, please restart.", show_alert=True)
                _del_state(chat_id)
                return
            state["session_id"] = session_id
            state["stage"] = "await_count"
            _set_state(chat_id, state)
            await callback_query.message.edit_text(
                "**📥 How many messages do you want to download?**\n__Type a number__",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("❌ Cancel", callback_data=f"batch_cancel_{chat_id}"),
                ]]),
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        if re.match(r"^batch_confirm_\d+$", data):
            if not state or state.get("user_id") != user_id:
                await callback_query.answer("❌ Invalid state!", show_alert=True)
                return
            if state.get("stage") != "confirmed":
                await callback_query.message.edit_text(
                    "**❌ Please enter the number of messages first!**",
                    parse_mode=ParseMode.MARKDOWN,
                )
                await callback_query.answer()
                return

            state["stage"] = "running"
            _set_state(chat_id, state)

            await callback_query.message.edit_text(
                "**⏳ Starting batch download...**",
                parse_mode=ParseMode.MARKDOWN,
            )
            await callback_query.answer("Starting...")

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
            await callback_query.answer("❌ Malformed data", show_alert=True)
            return

        target_chat_id = int(parts[2])
        session_id     = parts[3]
        state          = batch_data.get(target_chat_id)

        if not state or state.get("user_id") != user_id:
            await callback_query.answer("❌ Session expired or not yours.", show_alert=True)
            return

        state["session_id"] = session_id
        state["stage"] = "await_count"
        _set_state(target_chat_id, state)

        _, max_allowed = await get_batch_limits(user_id)
        await callback_query.message.edit_text(
            f"**📥 How many messages do you want to download?**\n"
            f"__max: {max_allowed} for your plan__\n\n"
            "__Type a number__",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data=f"batch_cancel_{target_chat_id}"),
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
                "**❌ Invalid Telegram link! Correct formats:\n"
                "Public: `https://t.me/channel/123`\n"
                "Private: `https://t.me/c/1234567890/123`**",
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
                    "**🔒 Private link detected!\n\n"
                    "❌ Please /login first and try again.**",
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
                    "❌ Cancel", callback_data=f"batch_cancel_{chat_id}"
                )])
                await message.reply_text(
                    "**🔒 Private link detected!\n\n"
                    "Which account do you want to download with?\n"
                    "__(Files will be sent to that account's Saved Messages)__**",
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
                            "**❌ Only channels/supergroups are supported!**",
                            parse_mode=ParseMode.MARKDOWN,
                        )
                        _del_state(chat_id)
                        return
            except ChannelPrivate:
                await message.reply_text(
                    "**🔒 This channel is private! Use a private link (t.me/c/...).**",
                    parse_mode=ParseMode.MARKDOWN,
                )
                _del_state(chat_id)
                return
            except (ChannelInvalid, PeerIdInvalid):
                await message.reply_text(
                    "**❌ Invalid channel. Please check the URL.**",
                    parse_mode=ParseMode.MARKDOWN,
                )
                _del_state(chat_id)
                return
            except Exception:
                pass

            _set_state(chat_id, {"user_id": user_id, "url": url, "is_private": False, "stage": "await_count"})

        _, max_allowed = await get_batch_limits(user_id)
        label = "🔒 Private" if private else "✅ Public"
        await message.reply_text(
            f"**{label} link detected!**\n\n"
            f"🔗 `{url}`\n\n"
            f"**📥 How many messages do you want to download?**\n"
            f"__max: {max_allowed} for your plan__",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data=f"batch_cancel_{chat_id}"),
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
                InlineKeyboardButton("⛔ Cancel", callback_data=f"batch_cancel_{chat_id}"),
            ]]),
        )

        last_edit = time()

        for idx, source_message in enumerate(all_messages, 1):
            if cancel_flags.get(chat_id):
                await status_message.edit_text(
                    f"**⛔ Batch cancelled by user.**\n\n"
                    f"**✅ Done:** `{success_count}`  **❌ Failed:** `{fail_count}`\n"
                    f"**📊 Processed:** `{idx - 1}/{count}`",
                    parse_mode=ParseMode.MARKDOWN,
                )
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
                    await asyncio.sleep(0.3)
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
            except Exception as e:
                fail_count += 1
                LOGGER.error(f"[PublicBatch] Failed msg {source_message.id}: {e}")

            now = time()
            if idx % 5 == 0 or idx == count or (now - last_edit) >= 3:
                try:
                    await status_message.edit_text(
                        _progress_text(idx, count, success_count, fail_count, start_ts, False),
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("⛔ Cancel", callback_data=f"batch_cancel_{chat_id}"),
                        ]]),
                    )
                    last_edit = now
                except Exception:
                    pass

            await asyncio.sleep(0.5)

        elapsed = int(time() - start_ts)
        completion_msg = await client.send_message(
            chat_id=chat_id,
            text=(
                f"**✅ Public Batch Download Complete!**\n\n"
                f"**✅ Successful:** `{success_count}`\n"
                f"**❌ Failed:** `{fail_count}`\n"
                f"**⏱ Time taken:** `{elapsed}s`"
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
                "**❌ Failed to initialize user client! Please /login again.**",
                parse_mode=ParseMode.MARKDOWN,
            )
            _del_state(chat_id)
            return

        await status_message.edit_text(
            _progress_text(0, count, 0, 0, start_ts, True),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⛔ Cancel", callback_data=f"batch_cancel_{chat_id}"),
            ]]),
        )

        user_data      = await user_activity_collection.find_one({"user_id": user_id})
        thumbnail_path = user_data.get("thumbnail_path") if user_data else None
        success_count  = 0
        fail_count     = 0

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
                "**❌ Could not fetch any messages.\n"
                "Make sure the logged-in account is a member of that channel/group.**",
                parse_mode=ParseMode.MARKDOWN,
            )
            _del_state(chat_id)
            # ✅ use safe_stop_client
            await safe_stop_client(user_client)
            return

        last_edit = time()

        for idx, chat_message in enumerate(all_messages, 1):
            if cancel_flags.get(chat_id):
                await status_message.edit_text(
                    f"**⛔ Batch cancelled by user.**\n\n"
                    f"**✅ Done:** `{success_count}`  **❌ Failed:** `{fail_count}`\n"
                    f"**📊 Processed:** `{idx - 1}/{count}`",
                    parse_mode=ParseMode.MARKDOWN,
                )
                _del_state(chat_id)
                # ✅ use safe_stop_client
                await safe_stop_client(user_client)
                return

            if not chat_message or not chat_message.id:
                fail_count += 1
                continue

            try:
                if chat_message.document or chat_message.video or chat_message.audio:
                    file_size = (
                        chat_message.document.file_size if chat_message.document else
                        chat_message.video.file_size   if chat_message.video   else
                        chat_message.audio.file_size
                    )
                    if not await fileSizeLimit(file_size, status_message, "download", True):
                        fail_count += 1
                        continue

                parsed_caption = await get_parsed_msg(
                    chat_message.caption or "", chat_message.caption_entities
                )
                parsed_text = await get_parsed_msg(
                    chat_message.text or "", chat_message.entities
                )

                if chat_message.media_group_id:
                    result = await processMediaGroup(
                        chat_message, bot, status_message, user_client=user_client
                    )
                    if result:
                        success_count += 1
                    else:
                        fail_count += 1
                    await asyncio.sleep(0.3)
                    continue

                if chat_message.media:
                    dl_start = time()
                    progress_msg = await bot.send_message(
                        chat_id=chat_id,
                        text=f"**📥 Downloading ({idx}/{count})...**",
                        parse_mode=ParseMode.MARKDOWN,
                    )

                    media_path = await chat_message.download(
                        progress=Leaves.progress_for_pyrogram,
                        progress_args=progressArgs("📥 Downloading", progress_msg, dl_start),
                    )

                    if not media_path or not os.path.exists(media_path):
                        fail_count += 1
                        try:
                            await progress_msg.delete()
                        except Exception:
                            pass
                        continue

                    media_type = (
                        "photo"    if chat_message.photo    else
                        "video"    if chat_message.video    else
                        "audio"    if chat_message.audio    else
                        "document"
                    )

                    try:
                        await send_media_to_saved(
                            user_client=user_client,
                            bot=bot,
                            message=status_message,
                            media_path=media_path,
                            media_type=media_type,
                            caption=parsed_caption,
                            progress_message=progress_msg,
                            start_time=dl_start,
                            thumbnail_path=thumbnail_path,
                        )
                        success_count += 1
                        if LOG_GROUP_ID and log_user and os.path.exists(media_path):
                            try:
                                await log_file_to_group(
                                    bot=bot,
                                    log_group_id=LOG_GROUP_ID,
                                    user=log_user,
                                    url=url,
                                    file_path=media_path,
                                    media_type=media_type,
                                    caption_original=parsed_caption,
                                    channel_name=None,
                                    thumbnail_path=thumbnail_path,
                                )
                            except Exception as log_err:
                                LOGGER.warning(f"[PrivateBatch] Log error for msg {chat_message.id}: {log_err}")

                    except AuthKeyUnregistered:
                        # ✅ Session expired — remove from MongoDB, notify user, stop batch
                        try:
                            await user_sessions.update_one(
                                {"user_id": user_id},
                                {"$pull": {"sessions": {"session_id": session_id}}}
                            )
                            LOGGER.warning(
                                f"[AuthKey] Batch session {session_id} removed for user {user_id}"
                            )
                        except Exception:
                            pass
                        try:
                            await bot.send_message(
                                chat_id=chat_id,
                                text=(
                                    "**❌ Your login session has expired!**\n\n"
                                    "Batch download has stopped.\n"
                                    "⚡ Please run **/login** and try again."
                                ),
                                parse_mode=ParseMode.MARKDOWN,
                            )
                        except Exception:
                            pass
                        _del_state(chat_id)
                        # ✅ use safe_stop_client
                        await safe_stop_client(user_client)
                        return

                    except Exception as upload_err:
                        LOGGER.error(f"[PrivateBatch] Upload failed for msg {chat_message.id}: {upload_err}")
                        fail_count += 1
                        try:
                            await progress_msg.delete()
                        except Exception:
                            pass
                    finally:
                        if os.path.exists(media_path):
                            os.remove(media_path)

                elif chat_message.text or chat_message.caption:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=parsed_text or parsed_caption,
                        parse_mode=ParseMode.MARKDOWN,
                    )
                    success_count += 1
                    if LOG_GROUP_ID and log_user:
                        try:
                            await log_file_to_group(
                                bot=bot,
                                log_group_id=LOG_GROUP_ID,
                                user=log_user,
                                url=url,
                                caption_original=parsed_text or parsed_caption,
                                channel_name=None,
                            )
                        except Exception as log_err:
                            LOGGER.warning(f"[PrivateBatch] Log error for msg {chat_message.id}: {log_err}")

            except Exception as e:
                LOGGER.error(f"[PrivateBatch] Error processing msg {chat_message.id}: {e}")
                fail_count += 1

            now = time()
            if idx % 5 == 0 or idx == count or (now - last_edit) >= 3:
                try:
                    await status_message.edit_text(
                        _progress_text(idx, count, success_count, fail_count, start_ts, True),
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("⛔ Cancel", callback_data=f"batch_cancel_{chat_id}"),
                        ]]),
                    )
                    last_edit = now
                except Exception:
                    pass

            await asyncio.sleep(0.3)

        elapsed = int(time() - start_ts)
        completion_msg = await bot.send_message(
            chat_id=chat_id,
            text=(
                f"**✅ Private Batch Download Complete!**\n\n"
                f"**✅ Successful:** `{success_count}`\n"
                f"**❌ Failed:** `{fail_count}`\n"
                f"**⏱ Time taken:** `{elapsed}s`\n\n"
                "📂 Open **Telegram → Saved Messages** to find your files."
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
