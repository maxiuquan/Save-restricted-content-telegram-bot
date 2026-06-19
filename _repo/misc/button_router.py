# Copyright @juktijol
# Channel t.me/juktijol
# UPDATED: New button labels, English UI, fixed italic markdown, new thumb flow

from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode

from misc.keyboards import BUTTON_COMMAND_MAP, get_main_reply_keyboard, get_start_inline
from plugins.pbatch import handle_batch_start
from utils import LOGGER


def setup_button_router(app: Client):
    """Register the catch-all text handler that routes Reply Keyboard presses."""

    _button_labels = set(BUTTON_COMMAND_MAP.keys())

    _hints: dict[str, str] = {
        "autolink": (
            "🔗 **Single Link Download**\n\n"
            "No command needed! ⚡\n\n"
            "Just paste a Telegram link in the chat:\n"
            "• `https://t.me/channelname/123` → public channel\n"
            "• `https://t.me/c/1234567890/123` → private channel "
            "__(need to /login first)__\n\n"
            "The bot will detect the link and download it for you. ✅"
        ),
        "autobatch": (
            "📦 **Batch Download**\n\n"
            "Download many files at once! 🎯\n\n"
            "**Public batch:**\n"
            "`https://t.me/channelname/123`\n\n"
            "**Private batch** __(need to /login first)__:\n"
            "`https://t.me/c/1234567890/123`\n\n"
            "Send the link → the bot will ask how many files you want. 🚀\n\n"
            "__Premium users only. Higher plans = more files per batch.__"
        ),
        "ytdl": (
            "🌐 **Website Video Download**\n\n"
            "**How to use:** `/ytdl <link>`\n\n"
            "**Works with:**\n"
            "• YouTube 🎥\n"
            "• Instagram 📸\n"
            "• TikTok 🎵\n"
            "• Twitter / X 🐦\n"
            "• Facebook 📘\n"
            "• Vimeo, Dailymotion, Twitch\n"
            "• SoundCloud, Reddit, Bilibili\n"
            "• And **1000+** more sites!\n\n"
            "**Example:**\n"
            "`/ytdl https://youtube.com/watch?v=xxxxx`"
        ),
        "setthumb": (
            "📌 **Set Thumbnail**\n\n"
            "Super easy — just 2 steps! 👇\n\n"
            "**Step 1:** Type `/setthumb`\n"
            "**Step 2:** Send a photo when the bot asks\n\n"
            "That's it! ✅\n\n"
            "__Or just send any photo — the bot will ask if you want to set it as a thumbnail!__"
        ),
        "transfer": (
            "🔄 **Transfer Premium**\n\n"
            "Want to give your premium to a friend? 🎁\n\n"
            "**How to use:**\n"
            "`/transfer <user_id>` or `/transfer @username`\n\n"
            "⚠️ This cannot be undone — your premium will be removed."
        ),
    }

    def _autolink_buttons() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_home")],
        ])

    def _plan_buttons() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✨ Plan 1 — 150 ⭐", callback_data="plan_select_plan1"),
                InlineKeyboardButton("🌟 Plan 2 — 500 ⭐", callback_data="plan_select_plan2"),
            ],
            [InlineKeyboardButton("💎 Plan 3 — 1000 ⭐", callback_data="plan_select_plan3")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_home")],
        ])

    @app.on_message(
        filters.text
        & (filters.private | filters.group)
        & filters.create(
            lambda _, __, msg: (
                msg.text is not None
                and msg.text.strip() in _button_labels
            )
        ),
        group=99,
    )
    async def button_router(client: Client, message: Message):
        label   = message.text.strip()
        command = BUTTON_COMMAND_MAP.get(label)

        if not command:
            return

        LOGGER.info(
            f"[ButtonRouter] user={message.from_user.id} "
            f"label='{label}' → command='{command}'"
        )

        # ── autolink / setthumb / transfer / ytdl ──────────────────────────
        if command in ("autolink", "setthumb", "transfer", "ytdl"):
            hint = _hints.get(command, "Send a link to use this feature.")
            if command == "autolink":
                await message.reply_text(hint, parse_mode=ParseMode.MARKDOWN,
                                         reply_markup=_autolink_buttons())
            else:
                await message.reply_text(hint, parse_mode=ParseMode.MARKDOWN)
            return

        # ── referral ────────────────────────────────────────────────────────
        if command == "referral":
            from plugins.referral import get_referral_text
            referral_text = await get_referral_text(client, message.from_user.id)
            await message.reply_text(referral_text, parse_mode=ParseMode.MARKDOWN)
            return

        # ── autobatch ────────────────────────────────────────────────────────
        if command == "autobatch":
            await handle_batch_start(client, message)
            return

        # ── settings ────────────────────────────────────────────────────────
        if command == "settings":
            from plugins.settings import _settings_text, _settings_keyboard
            text = await _settings_text(message.from_user.id)
            await message.reply_text(
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=_settings_keyboard(),
            )
            return

        # ── start / back ────────────────────────────────────────────────────
        if command == "start":
            user_fullname = (
                f"{message.from_user.first_name} "
                f"{message.from_user.last_name or ''}".strip()
            )
            await message.reply_text(
                f"🏠 **Main Menu** — Hey {user_fullname}!\n\nChoose an option below 👇",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=get_start_inline(),
                disable_web_page_preview=True,
            )

        # ── help ─────────────────────────────────────────────────────────────
        elif command == "help":
            await message.reply_text(
                "**❓ Help Menu**\n"
                "━━━━━━━━━━━━━━━━\n"
                "**🔗 Auto Download:** Paste any Telegram link!\n"
                "**📦 Auto Batch:** Send a link → pick how many files to download.\n"
                "**⚙️ Settings:** /settings — set caption, rename, word filter, target chat.\n"
                "━━━━━━━━━━━━━━━━\n"
                "**/plans** — view premium plans\n"
                "**/buy** — get premium\n"
                "**/transfer** — give your premium to a friend\n"
                "**/referral** — share your link & earn rewards\n"
                "**/profile** — your profile & plan info\n"
                "**/refresh** — update your Telegram profile\n"
                "**/getthumb** — see your thumbnail\n"
                "**/setthumb** — set a thumbnail __(just send a photo when asked!)__\n"
                "**/rmthumb** — remove your thumbnail\n"
                "**/settings** — all download settings\n"
                "**/info** — detailed account info\n"
                "**/login** — connect your account\n"
                "**/logout** — remove your session\n"
                "━━━━━━━━━━━━━━━━",
                parse_mode=ParseMode.MARKDOWN,
            )

        # ── plans / buy ───────────────────────────────────────────────────────
        elif command == "plans":
            from plugins.plan import PLAN_OPTIONS_TEXT
            await message.reply_text(
                PLAN_OPTIONS_TEXT,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=_plan_buttons(),
            )

        # ── profile & info ─────────────────────────────────────────────────────
        elif command == "profile_info":
            from core import prem_plan1, prem_plan2, prem_plan3, user_sessions, daily_limit
            from datetime import datetime, timezone, timedelta

            user    = message.from_user
            user_id = user.id
            full_name = (
                f"{user.first_name} {getattr(user, 'last_name', '')}".strip()
                or "Unknown"
            )
            username = f"@{user.username}" if user.username else "@N/A"

            now = datetime.utcnow()
            IST = timezone(timedelta(hours=5, minutes=30))

            plan1 = await prem_plan1.find_one({"user_id": user_id})
            plan2 = await prem_plan2.find_one({"user_id": user_id})
            plan3 = await prem_plan3.find_one({"user_id": user_id})

            membership = "🆓 Free"
            expiry_str = None
            if plan3 and plan3.get("expiry_date", now) > now:
                membership = "💎 Plan 3"
                expiry_str = plan3["expiry_date"].replace(
                    tzinfo=timezone.utc).astimezone(IST).strftime("%d %b %Y, %I:%M %p IST")
            elif plan2 and plan2.get("expiry_date", now) > now:
                membership = "🌟 Plan 2"
                expiry_str = plan2["expiry_date"].replace(
                    tzinfo=timezone.utc).astimezone(IST).strftime("%d %b %Y, %I:%M %p IST")
            elif plan1 and plan1.get("expiry_date", now) > now:
                membership = "✨ Plan 1"
                expiry_str = plan1["expiry_date"].replace(
                    tzinfo=timezone.utc).astimezone(IST).strftime("%d %b %Y, %I:%M %p IST")

            session       = await user_sessions.find_one({"user_id": user_id})
            sessions_list = session.get("sessions", []) if session else []
            if not sessions_list:
                login_status = "Not logged in"
            elif len(sessions_list) == 1:
                login_status = f"Logged in as {sessions_list[0].get('account_name', 'Unknown')}"
            else:
                names = ", ".join(s.get("account_name", "Unknown") for s in sessions_list)
                login_status = f"{len(sessions_list)} accounts: {names}"

            daily_record = await daily_limit.find_one({"user_id": user_id})
            total_dl     = daily_record.get("total_downloads", 0) if daily_record else 0

            total_stars = 0
            if plan1: total_stars += 150
            if plan2: total_stars += 500
            if plan3: total_stars += 1000

            expiry_line = (
                f"\n<b>📅 Plan Expires:</b> <code>{expiry_str}</code>"
                if expiry_str else ""
            )

            await message.reply_text(
                f"<b>━━━━━━━━━━━━━━━━</b>\n"
                f"<b>👤 My Profile</b>\n"
                f"<b>━━━━━━━━━━━━━━━━</b>\n"
                f"<b>🆔 ID:</b> <code>{user_id}</code>\n"
                f"<b>👤 Name:</b> <code>{full_name}</code>\n"
                f"<b>📛 Username:</b> <code>{username}</code>\n"
                f"<b>━━━━━━━━━━━━━━━━</b>\n"
                f"<b>💎 Plan:</b> <code>{membership}</code>"
                f"{expiry_line}\n"
                f"<b>⭐ Stars Spent:</b> <code>{total_stars}</code>\n"
                f"<b>━━━━━━━━━━━━━━━━</b>\n"
                f"<b>🔗 Login:</b> <code>{login_status}</code>\n"
                f"<b>📥 Total Downloads:</b> <code>{total_dl}</code>\n"
                f"<b>━━━━━━━━━━━━━━━━</b>",
                parse_mode=ParseMode.HTML,
            )

        # ── getthumb ───────────────────────────────────────────────────────────
        elif command == "getthumb":
            import os
            from core import user_activity_collection
            user_data  = await user_activity_collection.find_one({"user_id": message.from_user.id})
            thumb_path = user_data.get("thumbnail_path") if user_data else None
            if thumb_path and os.path.exists(thumb_path):
                await client.send_photo(
                    chat_id=message.chat.id,
                    photo=thumb_path,
                    caption=(
                        "🖼 **Your current thumbnail**\n\n"
                        "🗑 Remove it: `/rmthumb`\n"
                        "🔄 Change it: `/setthumb`"
                    ),
                    parse_mode=ParseMode.MARKDOWN,
                )
            else:
                await message.reply_text(
                    "❌ **You don't have a thumbnail set yet.**\n\n"
                    "📌 **How to set one — super easy!**\n"
                    "**Step 1:** Type `/setthumb`\n"
                    "**Step 2:** Send a photo when the bot asks\n\n"
                    "__Or just send any photo — the bot will ask if you want to set it!__",
                    parse_mode=ParseMode.MARKDOWN,
                )

        # ── rmthumb ────────────────────────────────────────────────────────────
        elif command == "rmthumb":
            import os
            from core import user_activity_collection
            user_id   = message.from_user.id
            user_data = await user_activity_collection.find_one({"user_id": user_id})
            if not user_data or "thumbnail_path" not in user_data:
                await message.reply_text(
                    "❌ **You don't have any thumbnail set.**",
                    parse_mode=ParseMode.MARKDOWN,
                )
                return
            thumb_path = user_data["thumbnail_path"]
            if os.path.exists(thumb_path):
                os.remove(thumb_path)
            await user_activity_collection.update_one(
                {"user_id": user_id},
                {"$unset": {"thumbnail_path": "", "thumbnail_file_id": ""}},
            )
            await message.reply_text(
                "✅ **Thumbnail removed!**\n\n"
                "__Downloaded videos will no longer have a custom thumbnail.__",
                parse_mode=ParseMode.MARKDOWN,
            )

        # ── login ──────────────────────────────────────────────────────────────
        elif command == "login":
            await message.reply_text(
                "🔐 **Type `/login` to connect your Telegram account.**\n\n"
                "__You'll need to login to download from private channels.__",
                parse_mode=ParseMode.MARKDOWN,
            )

        # ── logout ─────────────────────────────────────────────────────────────
        elif command == "logout":
            await message.reply_text(
                "🚪 **Type `/logout` to remove your saved session.**",
                parse_mode=ParseMode.MARKDOWN,
            )

        else:
            await message.reply_text(
                f"Please type `/{command}` to use this feature.",
                parse_mode=ParseMode.MARKDOWN,
            )

    @app.on_message(
        filters.text
        & (filters.private | filters.group)
        & filters.regex(r"^(?i:menu)$"),
        group=98,
    )
    async def menu_shortcut(client: Client, message: Message):
        user_fullname = (
            f"{message.from_user.first_name} "
            f"{message.from_user.last_name or ''}".strip()
        )
        await message.reply_text(
            f"🏠 **Main Menu** — Hey {user_fullname}!\n\nChoose an option below 👇",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_start_inline(),
            disable_web_page_preview=True,
        )
        await client.send_message(
            chat_id=message.chat.id,
            text="__Keyboard refreshed.__",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_main_reply_keyboard(),
        )
