# Copyright @juktijol
# Channel t.me/juktijol
from datetime import datetime, timezone, timedelta
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import ParseMode
from pyrogram.handlers import MessageHandler
from config import COMMAND_PREFIX
from utils import LOGGER
from core import prem_plan1, prem_plan2, prem_plan3, user_sessions, daily_limit

IST = timezone(timedelta(hours=5, minutes=30))

async def _get_active_plan(user_id: int):
    """
    Motor async — user-এর সর্বোচ্চ active plan খোঁজে।
    Returns (plan_label, expiry_date_ist_str) or ("Free", None)
    """
    now = datetime.utcnow()
    plan_checks = [
        ("💎 Plan 3", prem_plan3),
        ("🌟 Plan 2", prem_plan2),
        ("✨ Plan 1", prem_plan1),
    ]
    for label, collection in plan_checks:
        doc = await collection.find_one({"user_id": user_id})
        if doc:
            expiry = doc.get("expiry_date")
            if expiry and expiry > now:
                expiry_ist = expiry.replace(tzinfo=timezone.utc).astimezone(IST)
                expiry_str = expiry_ist.strftime("%d %b %Y, %I:%M %p IST")
                return label, expiry_str
    return "Free", None


async def _get_login_status(user_id: int):
    """
    Motor async — session গণনা।
    Returns (account_count, account_names_list)
    """
    session_doc = await user_sessions.find_one({"user_id": user_id})
    if not session_doc:
        return 0, []
    sessions = session_doc.get("sessions", [])
    names = [s.get("account_name", "Unknown") for s in sessions]
    return len(sessions), names


def setup_info_handler(app: Client):

    async def info_command(client: Client, message: Message):
        user_id = message.from_user.id
        user = message.from_user
        full_name = f"{user.first_name} {getattr(user, 'last_name', '') or ''}".strip() or "Unknown"
        username = f"@{user.username}" if user.username else "N/A"

        plan_label, expiry_str = await _get_active_plan(user_id)
        account_count, account_names = await _get_login_status(user_id)

        if account_count == 0:
            login_status = "Not logged in"
        elif account_count == 1:
            login_status = f"Logged in as {account_names[0]}"
        else:
            login_status = f"{account_count} accounts: " + ", ".join(account_names)

        daily_record = await daily_limit.find_one({"user_id": user_id})
        total_downloads = daily_record.get("total_downloads", 0) if daily_record else 0

        total_stars = 0
        if await prem_plan1.find_one({"user_id": user_id}):
            total_stars += 150
        if await prem_plan2.find_one({"user_id": user_id}):
            total_stars += 500
        if await prem_plan3.find_one({"user_id": user_id}):
            total_stars += 1000

        if expiry_str:
            expiry_line = f"\n<b>📅 Plan Expiry:</b> <code>{expiry_str}</code>"
        else:
            expiry_line = ""

        info_text = (
            f"<b>━━━━━━━━━━━━━━━━</b>\n"
            f"<b>👤 User Info</b>\n"
            f"<b>━━━━━━━━━━━━━━━━</b>\n"
            f"<b>🆔 ID:</b> <code>{user_id}</code>\n"
            f"<b>👤 Name:</b> <code>{full_name}</code>\n"
            f"<b>📛 Username:</b> <code>{username}</code>\n"
            f"<b>━━━━━━━━━━━━━━━━</b>\n"
            f"<b>💎 Membership:</b> <code>{plan_label}</code>"
            f"{expiry_line}\n"
            f"<b>⭐ Stars Spent:</b> <code>{total_stars}</code>\n"
            f"<b>━━━━━━━━━━━━━━━━</b>\n"
            f"<b>🔗 Login Status:</b> <code>{login_status}</code>\n"
            f"<b>📥 Total Downloads:</b> <code>{total_downloads}</code>\n"
            f"<b>━━━━━━━━━━━━━━━━</b>"
        )

        await message.reply_text(info_text, parse_mode=ParseMode.HTML)
        LOGGER.info(f"Info command triggered by user {user_id}")

    async def profile_command(client: Client, message: Message):
        user_id = message.from_user.id
        user = message.from_user
        full_name = f"{user.first_name} {getattr(user, 'last_name', '') or ''}".strip() or "Unknown"
        username = f"@{user.username}" if user.username else "N/A"

        plan_label, expiry_str = await _get_active_plan(user_id)
        account_count, account_names = await _get_login_status(user_id)

        if account_count == 0:
            login_status = "Not logged in"
        elif account_count == 1:
            login_status = f"Logged in as {account_names[0]}"
        else:
            login_status = f"{account_count} accounts: " + ", ".join(account_names)

        daily_record = await daily_limit.find_one({"user_id": user_id})
        total_downloads = daily_record.get("total_downloads", 0) if daily_record else 0

        total_stars = 0
        if await prem_plan1.find_one({"user_id": user_id}):
            total_stars += 150
        if await prem_plan2.find_one({"user_id": user_id}):
            total_stars += 500
        if await prem_plan3.find_one({"user_id": user_id}):
            total_stars += 1000

        if expiry_str:
            expiry_line = f"\n<b>📅 Plan Expiry:</b> <code>{expiry_str}</code>"
        else:
            expiry_line = ""

        profile_text = (
            f"<b>━━━━━━━━━━━━━━━━</b>\n"
            f"<b>👤 Profile</b>\n"
            f"<b>━━━━━━━━━━━━━━━━</b>\n"
            f"<b>🆔 ID:</b> <code>{user_id}</code>\n"
            f"<b>👤 Name:</b> <code>{full_name}</code>\n"
            f"<b>📛 Username:</b> <code>{username}</code>\n"
            f"<b>━━━━━━━━━━━━━━━━</b>\n"
            f"<b>💎 Membership:</b> <code>{plan_label}</code>"
            f"{expiry_line}\n"
            f"<b>⭐ Stars Spent:</b> <code>{total_stars}</code>\n"
            f"<b>━━━━━━━━━━━━━━━━</b>\n"
            f"<b>🔗 Login Status:</b> <code>{login_status}</code>\n"
            f"<b>📥 Total Downloads:</b> <code>{total_downloads}</code>\n"
            f"<b>━━━━━━━━━━━━━━━━</b>"
        )

        await message.reply_text(profile_text, parse_mode=ParseMode.HTML)
        LOGGER.info(f"Profile command triggered by user {user_id}")

    async def help_command(client: Client, message: Message):
        help_text = (
            "<b>💥 Help Menu</b>\n"
            "<b>━━━━━━━━━━━━━━━━</b>\n"
            "<b>🔗 Auto Download</b>\n"
            "Paste any Telegram link directly — no command needed!\n\n"
            "<b>📦 Auto Batch</b>\n"
            "Send a link and the bot will ask how many files to download.\n\n"
            "<b>━━━━━━━━━━━━━━━━</b>\n"
            "<b>Commands</b>\n"
            "<b>/plans</b> — View premium plans\n"
            "<b>/buy</b> — Purchase a premium plan\n"
            "<b>/transfer</b> — Transfer premium to another user\n"
            "<b>/profile</b> — View your profile\n"
            "<b>/info</b> — Detailed account info\n"
            "<b>/login</b> — Connect your account (premium only)\n"
            "<b>/logout</b> — Remove your session\n"
            "<b>/getthumb</b> — Show your thumbnail\n"
            "<b>/setthumb</b> — Set a thumbnail (reply to a photo)\n"
            "<b>/rmthumb</b> — Remove your thumbnail\n"
            "<b>/settings</b> — Configure caption, rename, word filter, target chat\n"
            "<b>/refresh</b> — Sync your latest Telegram profile to the database\n"
            "<b>━━━━━━━━━━━━━━━━</b>"
        )
        await message.reply_text(help_text, parse_mode=ParseMode.HTML)
        LOGGER.info(f"Help command triggered by user {message.from_user.id}")

    app.add_handler(
        MessageHandler(
            info_command,
            filters=filters.command("info", prefixes=COMMAND_PREFIX) & (filters.private | filters.group)
        ),
        group=1
    )
    app.add_handler(
        MessageHandler(
            profile_command,
            filters=filters.command("profile", prefixes=COMMAND_PREFIX) & (filters.private | filters.group)
        ),
        group=1
    )
    app.add_handler(
        MessageHandler(
            help_command,
            filters=filters.command("help", prefixes=COMMAND_PREFIX) & (filters.private | filters.group)
        ),
        group=1
    )
