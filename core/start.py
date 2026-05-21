# core/start.py — UPDATED: Uses new process_referral() from plugins/referral.py

from datetime import datetime
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode
from utils import LOGGER

from misc.keyboards import get_main_reply_keyboard, get_start_inline
from core.database import total_users, referrals


def setup_start_handler(app: Client):

    @app.on_message(filters.command("start"))
    async def start(client: Client, message: Message):
        user = message.from_user
        user_fullname = (
            f"{user.first_name} "
            f"{user.last_name or ''}".strip()
        )

        # ── MongoDB-তে ইউজার সেভ/আপডেট করো ─────────────────────────────────
        try:
            await total_users.update_one(
                {"user_id": user.id},
                {
                    "$set": {
                        "user_id":    user.id,
                        "first_name": user.first_name or "",
                        "last_name":  user.last_name or "",
                        "name":       user_fullname,
                        "username":   user.username or "",
                        "last_active": datetime.utcnow(),
                    }
                },
                upsert=True,
            )
            LOGGER.info(f"User saved/updated in DB: {user.id} ({user_fullname})")
        except Exception as e:
            LOGGER.error(f"Failed to save user {user.id} to DB: {e}")

        # ── Referral tracking: /start <referrer_id> ───────────────────────
        if len(message.command) > 1:
            referrer_arg = message.command[1]
            try:
                referrer_id = int(referrer_arg)
                # Import here to avoid circular imports
                from plugins.referral import process_referral
                # process_referral handles anti-cheat + reward automatically
                success = await process_referral(client, user.id, referrer_id)
                if success:
                    LOGGER.info(f"Referral processed: {user.id} referred by {referrer_id}")
            except (ValueError, TypeError):
                pass  # Not a referral deep link
            except Exception as e:
                LOGGER.error(f"Referral tracking error for {user.id}: {e}")

        start_message = f"""{user_fullname}，你好！👋 欢迎！

━━━━━━━━━━━━━━━━━━━━━━━

🤔 **这个机器人能做什么？**
本机器人可以绕过限制，轻松下载或转发来自公开频道、私有频道和群组的内容，即使保存和转发功能被禁用也能下载。

📖 **如何使用：**
• **自动下载：** 直接在聊天中粘贴任意 Telegram 链接 — 无需命令！
• **自动批量：** 粘贴链接后，机器人会询问你一次要下载多少条消息。
• **私有内容：** 安全登录后，可从你已加入的私有频道下载文件。文件会直接发送到你的收藏夹。

💎 **免费 vs 高级：**
免费用户每次下载间隔 5 分钟。高级用户可即时、无限制下载，并支持批量下载！

📌 **只需粘贴任意 Telegram 链接即可开始！**

━━━━━━━━━━━━━━━━━━━━━━━
"""

        await message.reply_text(
            start_message,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_start_inline(),
            disable_web_page_preview=True,
        )

        await client.send_message(
            chat_id=message.chat.id,
            text="⌨️ __使用下方按钮可快速访问所有功能：__",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_main_reply_keyboard(),
        )

        LOGGER.info(f"Start command triggered by {user.id}")
