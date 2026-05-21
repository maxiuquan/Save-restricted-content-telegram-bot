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
        ("💎 套餐3", prem_plan3),
        ("🌟 套餐2", prem_plan2),
        ("✨ 套餐1", prem_plan1),
    ]
    for label, collection in plan_checks:
        doc = await collection.find_one({"user_id": user_id})
        if doc:
            expiry = doc.get("expiry_date")
            if expiry and expiry > now:
                expiry_ist = expiry.replace(tzinfo=timezone.utc).astimezone(IST)
                expiry_str = expiry_ist.strftime("%d %b %Y, %I:%M %p IST")
                return label, expiry_str
    return "免费", None


async def _get_login_status(user_id: int):
    """
    Motor async — session গণনা।
    Returns (account_count, account_names_list)
    """
    session_doc = await user_sessions.find_one({"user_id": user_id})
    if not session_doc:
        return 0, []
    sessions = session_doc.get("sessions", [])
    names = [s.get("account_name", "未知") for s in sessions]
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
            login_status = "未登录"
        elif account_count == 1:
            login_status = f"已登录：{account_names[0]}"
        else:
            login_status = f"{account_count} 个账户：" + "、".join(account_names)

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
            expiry_line = f"\n<b>📅 到期时间：</b> <code>{expiry_str}</code>"
        else:
            expiry_line = ""

        info_text = (
            f"<b>━━━━━━━━━━━━━━━━</b>\n"
            f"<b>👤 用户信息</b>\n"
            f"<b>━━━━━━━━━━━━━━━━</b>\n"
            f"<b>🆔 ID：</b> <code>{user_id}</code>\n"
            f"<b>👤 姓名：</b> <code>{full_name}</code>\n"
            f"<b>📛 用户名：</b> <code>{username}</code>\n"
            f"<b>━━━━━━━━━━━━━━━━</b>\n"
            f"<b>💎 会员：</b> <code>{plan_label}</code>"
            f"{expiry_line}\n"
            f"<b>⭐ 已消费星星：</b> <code>{total_stars}</code>\n"
            f"<b>━━━━━━━━━━━━━━━━</b>\n"
            f"<b>🔗 登录状态：</b> <code>{login_status}</code>\n"
            f"<b>📥 总下载数：</b> <code>{total_downloads}</code>\n"
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
            login_status = "未登录"
        elif account_count == 1:
            login_status = f"已登录：{account_names[0]}"
        else:
            login_status = f"{account_count} 个账户：" + "、".join(account_names)

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
            expiry_line = f"\n<b>📅 到期时间：</b> <code>{expiry_str}</code>"
        else:
            expiry_line = ""

        profile_text = (
            f"<b>━━━━━━━━━━━━━━━━</b>\n"
            f"<b>👤 个人资料</b>\n"
            f"<b>━━━━━━━━━━━━━━━━</b>\n"
            f"<b>🆔 ID：</b> <code>{user_id}</code>\n"
            f"<b>👤 姓名：</b> <code>{full_name}</code>\n"
            f"<b>📛 用户名：</b> <code>{username}</code>\n"
            f"<b>━━━━━━━━━━━━━━━━</b>\n"
            f"<b>💎 会员：</b> <code>{plan_label}</code>"
            f"{expiry_line}\n"
            f"<b>⭐ 已消费星星：</b> <code>{total_stars}</code>\n"
            f"<b>━━━━━━━━━━━━━━━━</b>\n"
            f"<b>🔗 登录状态：</b> <code>{login_status}</code>\n"
            f"<b>📥 总下载数：</b> <code>{total_downloads}</code>\n"
            f"<b>━━━━━━━━━━━━━━━━</b>"
        )

        await message.reply_text(profile_text, parse_mode=ParseMode.HTML)
        LOGGER.info(f"Profile command triggered by user {user_id}")

    async def help_command(client: Client, message: Message):
        help_text = (
            "<b>💥 帮助菜单</b>\n"
            "<b>━━━━━━━━━━━━━━━━</b>\n"
            "<b>🔗 自动下载</b>\n"
            "直接粘贴 Telegram 链接 — 无需命令！\n\n"
            "<b>📦 自动批量</b>\n"
            "发送链接，机器人会询问要下载多少文件。\n\n"
            "<b>━━━━━━━━━━━━━━━━</b>\n"
            "<b>命令列表</b>\n"
            "<b>/plans</b> — 查看高级套餐\n"
            "<b>/buy</b> — 购买高级套餐\n"
            "<b>/transfer</b> — 转让高级会员给其他用户\n"
            "<b>/profile</b> — 查看个人资料\n"
            "<b>/info</b> — 详细账户信息\n"
            "<b>/login</b> — 连接账户（高级用户专属）\n"
            "<b>/logout</b> — 移除会话\n"
            "<b>/getthumb</b> — 查看缩略图\n"
            "<b>/setthumb</b> — 设置缩略图（回复照片）\n"
            "<b>/rmthumb</b> — 删除缩略图\n"
            "<b>/settings</b> — 设置标题、重命名、关键词过滤、目标对话\n"
            "<b>/refresh</b> — 同步最新 Telegram 资料到数据库\n"
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
