# Copyright @juktijol
# Channel t.me/juktijol
#
# plugins/refresh.py — /refresh command handler.
#
# Fetches the caller's latest profile from Telegram API and upserts it
# into the database, then replies with a summary of the updated fields.

from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import ParseMode
from pyrogram.handlers import MessageHandler

from config import COMMAND_PREFIX
from utils import LOGGER
from db.users import upsert_user


def setup_refresh_handler(app: Client):

    async def refresh_command(client: Client, message: Message):
        user_id = message.from_user.id

        # ── Fetch fresh user object from Telegram API ──────────────────────
        try:
            user = await client.get_users(user_id)
        except Exception as exc:
            LOGGER.warning(f"[/refresh] get_users failed for {user_id}: {exc}")
            await message.reply_text(
                "❌ <b>Could not fetch your profile from Telegram.</b>\n"
                "You may be blocked or your account is inaccessible.\n\n"
                f"<i>Error: {exc}</i>",
                parse_mode=ParseMode.HTML,
            )
            return

        # ── Upsert into MongoDB ────────────────────────────────────────────
        try:
            doc = await upsert_user(user)
        except Exception as exc:
            LOGGER.error(f"[/refresh] DB upsert failed for {user_id}: {exc}")
            await message.reply_text(
                "❌ <b>Database error while saving your profile.</b>\n"
                "Please try again later.",
                parse_mode=ParseMode.HTML,
            )
            return

        # ── Build reply summary ────────────────────────────────────────────
        username_display = f"@{doc['username']}" if doc["username"] else "(no username)"
        premium_icon     = "✅" if doc["is_premium"]  else "❌"
        verified_icon    = "✅" if doc["is_verified"] else "❌"
        scam_icon        = "⚠️" if doc["is_scam"]    else "✅"
        fake_icon        = "⚠️" if doc["is_fake"]    else "✅"

        dc_line       = f"\n<b>📡 DC:</b> <code>{doc['dc_id']}</code>" if doc["dc_id"] else ""
        lang_line     = (
            f"\n<b>🌐 Language:</b> <code>{doc['language_code']}</code>"
            if doc["language_code"] else ""
        )
        refreshed_str = doc["refreshed_at"].strftime("%Y-%m-%d %H:%M:%S %Z")

        reply = (
            f"✅ <b>Profile Refreshed!</b>\n"
            f"<b>━━━━━━━━━━━━━━━━</b>\n"
            f"<b>🆔 ID:</b> <code>{doc['user_id']}</code>\n"
            f"<b>👤 Name:</b> <code>{doc['full_name']}</code>\n"
            f"<b>📛 Username:</b> <code>{username_display}</code>\n"
            f"<b>━━━━━━━━━━━━━━━━</b>\n"
            f"<b>💎 Premium:</b> {premium_icon}\n"
            f"<b>✔️ Verified:</b> {verified_icon}\n"
            f"<b>🚫 Scam:</b> {scam_icon}\n"
            f"<b>🎭 Fake:</b> {fake_icon}"
            f"{dc_line}"
            f"{lang_line}\n"
            f"<b>━━━━━━━━━━━━━━━━</b>\n"
            f"<b>🕒 Refreshed at:</b> <code>{refreshed_str}</code>"
        )

        await message.reply_text(reply, parse_mode=ParseMode.HTML)
        LOGGER.info(f"[/refresh] Profile refreshed for user_id={user_id}")

    app.add_handler(
        MessageHandler(
            refresh_command,
            filters=filters.command("refresh", prefixes=COMMAND_PREFIX)
            & (filters.private | filters.group),
        ),
        group=1,
    )
