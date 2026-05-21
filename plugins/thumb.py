# UPDATED: Conversation flow + Auto photo detection for /setthumb

import os
import asyncio
from pyrogram import Client, filters
from pyrogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from pyrogram.enums import ParseMode
from pyrogram.handlers import MessageHandler, CallbackQueryHandler
from config import COMMAND_PREFIX
from utils import LOGGER
from core import user_activity_collection

# ── In-memory state: users waiting for a photo after /setthumb ──────
# { user_id: {"chat_id": int, "expires_at": float} }
_waiting_for_photo: dict = {}

# Session expiry: 5 minutes
SESSION_EXPIRY = 300


def _is_waiting(user_id: int) -> bool:
    state = _waiting_for_photo.get(user_id)
    if not state:
        return False
    import time
    if time.time() > state["expires_at"]:
        _waiting_for_photo.pop(user_id, None)
        return False
    return True


def _set_waiting(user_id: int, chat_id: int):
    import time
    _waiting_for_photo[user_id] = {
        "chat_id":    chat_id,
        "expires_at": time.time() + SESSION_EXPIRY,
    }


def _clear_waiting(user_id: int):
    _waiting_for_photo.pop(user_id, None)


async def _save_thumbnail(client: Client, message: Message, photo, user_id: int) -> bool:
    """Download photo and save it to DB. Return True on success."""
    os.makedirs("Assets", exist_ok=True)
    thumb_path = f"Assets/{user_id}_thumb.jpg"
    try:
        await client.download_media(photo.file_id, file_name=thumb_path)
        await user_activity_collection.update_one(
            {"user_id": user_id},
            {
                "$set": {
                    "thumbnail_path":    thumb_path,
                    "thumbnail_file_id": photo.file_id,
                }
            },
            upsert=True,
        )
        LOGGER.info(f"Thumbnail saved for user {user_id} → {thumb_path}")
        return True
    except Exception as e:
        LOGGER.error(f"Error saving thumbnail for user {user_id}: {e}")
        return False


def setup_thumb_handler(app: Client):

    # ════════════════════════════════════════════════════════════════
    # /setthumb — two paths:
    #   Path A: reply-to-photo (old way, still supported)
    #   Path B: conversation mode — start waiting for a photo
    # ════════════════════════════════════════════════════════════════

    async def setthumb_command(client: Client, message: Message):
        user_id = message.from_user.id

        # ── Path A: user replied with /setthumb ──────────────────────
        if message.reply_to_message and message.reply_to_message.photo:
            photo = message.reply_to_message.photo
            success = await _save_thumbnail(client, message, photo, user_id)
            _clear_waiting(user_id)  # close active session if there was one

            if success:
                await message.reply_text(
                    "⚡ **缩略图已设置！**\n\n"
                    "此缩略图将用于你下载的视频。\n\n"
                    "⚡ 使用 `/setthumb` 更改。\n"
                    "🗑 使用 `/rmthumb` 删除。",
                    parse_mode=ParseMode.MARKDOWN,
                )
            else:
                await message.reply_text(
                    "❌ **无法设置缩略图。**\n"
                    "请重试。",
                    parse_mode=ParseMode.MARKDOWN,
                )
            return

        # ── Path B: start conversation mode ──────────────────────
        _set_waiting(user_id, message.chat.id)

        await message.reply_text(
            "⚡ **设置缩略图**\n"
            "━━━━━━━━━━━━━━━━\n\n"
            "**第1步：** 发送或转发一张照片。\n"
            "**第2步：** 我会自动设置 ✅\n\n"
            "⚡ 如果5分钟内未发送照片，会话将自动关闭。",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ 取消", callback_data="thumb_cancel"),
            ]]),
        )

    # ════════════════════════════════════════════════════════════════
    # Photo listener — does two things:
    #   1. If conversation mode is active -> set directly
    #   2. For any photo -> show "Set as thumbnail?" buttons
    # ════════════════════════════════════════════════════════════════

    @app.on_message(
        filters.photo & (filters.private | filters.group),
        group=5,
    )
    async def photo_listener(client: Client, message: Message):
        if not message.from_user:
            return

        user_id = message.from_user.id
        photo   = message.photo

        # ── Mode 1: conversation active — set directly ──────────────
        if _is_waiting(user_id):
            _clear_waiting(user_id)
            processing = await message.reply_text(
                "⚡ **正在设置缩略图...**",
                parse_mode=ParseMode.MARKDOWN,
            )
            success = await _save_thumbnail(client, message, photo, user_id)
            if success:
                await processing.edit_text(
                    "⚡ **缩略图设置成功！**\n\n"
                    "此缩略图将用于你下载的视频。\n\n"
                    "🗑 使用 `/rmthumb` 删除。",
                    parse_mode=ParseMode.MARKDOWN,
                )
            else:
                await processing.edit_text(
                    "❌ **无法设置缩略图。** 请重试。",
                    parse_mode=ParseMode.MARKDOWN,
                )
            return

        # ── Mode 2: any photo — show "Set as thumbnail?" prompt ──────
        # (private chat only, to avoid group spam)
        if message.chat.type.name == "PRIVATE":
            await message.reply_text(
                "⚡ **将此照片设为缩略图？**\n\n"
                "此缩略图将用于你下载的视频。",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "✅ 是，设为缩略图",
                            callback_data=f"thumb_set_{photo.file_id}",
                        ),
                        InlineKeyboardButton(
                            "❌ 不",
                            callback_data="thumb_skip",
                        ),
                    ]
                ]),
            )

    # ════════════════════════════════════════════════════════════════
    # Callback handler
    # ════════════════════════════════════════════════════════════════

    @app.on_callback_query(
        filters.regex(r"^thumb_(set_.+|skip|cancel)$"),
    )
    async def thumb_callback(client: Client, cq: CallbackQuery):
        data    = cq.data
        user_id = cq.from_user.id

        # ── cancel ────────────────────────────────────────────────────
        if data in ("thumb_cancel", "thumb_skip"):
            _clear_waiting(user_id)
            try:
                await cq.message.delete()
            except Exception:
                pass
            await cq.answer(
                "已取消。" if data == "thumb_cancel" else "好的，已跳过。"
            )
            return

        # ── set photo ──────────────────────────────────────────────
        if data.startswith("thumb_set_"):
            file_id = data[len("thumb_set_"):]

            await cq.answer("⚡ 正在设置...")

            os.makedirs("Assets", exist_ok=True)
            thumb_path = f"Assets/{user_id}_thumb.jpg"
            try:
                await client.download_media(file_id, file_name=thumb_path)
                await user_activity_collection.update_one(
                    {"user_id": user_id},
                    {
                        "$set": {
                            "thumbnail_path":    thumb_path,
                            "thumbnail_file_id": file_id,
                        }
                    },
                    upsert=True,
                )
                LOGGER.info(f"[Callback] Thumbnail saved for user {user_id}")
                try:
                    await cq.message.edit_text(
                        "⚡ **缩略图设置成功！**\n\n"
                        "此缩略图将用于你下载的视频。\n\n"
                        "🗑 使用 `/rmthumb` 删除。",
                        parse_mode=ParseMode.MARKDOWN,
                    )
                except Exception:
                    pass
            except Exception as e:
                LOGGER.error(f"[Callback] Thumbnail save error for user {user_id}: {e}")
                try:
                    await cq.message.edit_text(
                        "❌ **无法设置缩略图。** 请重试。",
                        parse_mode=ParseMode.MARKDOWN,
                    )
                except Exception:
                    pass

    # ════════════════════════════════════════════════════════════════
    # /rmthumb
    # ════════════════════════════════════════════════════════════════

    async def rmthumb_command(client: Client, message: Message):
        user_id   = message.from_user.id
        user_data = await user_activity_collection.find_one({"user_id": user_id})

        if not user_data or "thumbnail_path" not in user_data:
            await message.reply_text(
                "❌ **未设置缩略图。**\n\n"
                "使用 `/setthumb` 添加一个。",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        thumb_path = user_data["thumbnail_path"]
        try:
            if os.path.exists(thumb_path):
                os.remove(thumb_path)
            await user_activity_collection.update_one(
                {"user_id": user_id},
                {"$unset": {"thumbnail_path": "", "thumbnail_file_id": ""}},
            )
            await message.reply_text(
                "⚡ **缩略图已删除。**\n\n"
                "使用 `/setthumb` 设置新的。",
                parse_mode=ParseMode.MARKDOWN,
            )
            LOGGER.info(f"Thumbnail removed for user {user_id}")
        except Exception as e:
            await message.reply_text(
                "❌ **无法删除缩略图。**",
                parse_mode=ParseMode.MARKDOWN,
            )
            LOGGER.error(f"Error removing thumbnail for user {user_id}: {e}")

    # ════════════════════════════════════════════════════════════════
    # /getthumb
    # ════════════════════════════════════════════════════════════════

    async def getthumb_command(client: Client, message: Message):
        user_id   = message.from_user.id
        user_data = await user_activity_collection.find_one({"user_id": user_id})

        if not user_data or "thumbnail_path" not in user_data:
            await message.reply_text(
                "❌ **未设置缩略图。**\n\n"
                "⚡ 要设置缩略图：\n"
                "1. 发送 `/setthumb`\n"
                "2. 按提示发送一张照片\n\n"
                "或者直接发送任何照片，我会询问你是否设为缩略图。",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        thumb_path = user_data["thumbnail_path"]
        if os.path.exists(thumb_path):
            try:
                await client.send_photo(
                    chat_id=message.chat.id,
                    photo=thumb_path,
                    caption=(
                        "⚡ **你当前的缩略图**\n\n"
                        "🗑 删除：`/rmthumb`\n"
                        "🔄 更改：`/setthumb`"
                    ),
                    parse_mode=ParseMode.MARKDOWN,
                )
                LOGGER.info(f"Thumbnail retrieved for user {user_id}")
            except Exception as e:
                await message.reply_text(
                    "❌ **无法显示缩略图。**",
                    parse_mode=ParseMode.MARKDOWN,
                )
                LOGGER.error(f"Error retrieving thumbnail for user {user_id}: {e}")
        else:
            # File missing — clean DB entry
            await user_activity_collection.update_one(
                {"user_id": user_id},
                {"$unset": {"thumbnail_path": "", "thumbnail_file_id": ""}},
            )
            await message.reply_text(
                "❌ **未找到缩略图文件。**\n"
                "请使用 `/setthumb` 重新设置。",
                parse_mode=ParseMode.MARKDOWN,
            )
            LOGGER.warning(f"Thumbnail file missing for user {user_id} at {thumb_path}")

    # ════════════════════════════════════════════════════════════════
    # Register handlers
    # ════════════════════════════════════════════════════════════════

    app.add_handler(
        MessageHandler(
            setthumb_command,
            filters=filters.command("setthumb", prefixes=COMMAND_PREFIX)
                    & (filters.private | filters.group),
        ),
        group=1,
    )
    app.add_handler(
        MessageHandler(
            rmthumb_command,
            filters=filters.command("rmthumb", prefixes=COMMAND_PREFIX)
                    & (filters.private | filters.group),
        ),
        group=1,
    )
    app.add_handler(
        MessageHandler(
            getthumb_command,
            filters=filters.command("getthumb", prefixes=COMMAND_PREFIX)
                    & (filters.private | filters.group),
        ),
        group=1,
    )
