# 更新：新按钮标签、英文界面、修复斜体markdown、新缩略图流程

from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode

from misc.keyboards import BUTTON_COMMAND_MAP, get_main_reply_keyboard, get_start_inline
from plugins.pbatch import handle_batch_start
from utils import LOGGER


def setup_button_router(app: Client):
    """注册捕获所有文本处理函数，用于路由回复键盘按钮的点击。"""

    _button_labels = set(BUTTON_COMMAND_MAP.keys())

    _hints: dict[str, str] = {
        "autolink": (
            "🔗 **单链接下载**\n\n"
            "无需命令！⚡\n\n"
            "只需在聊天中粘贴 Telegram 链接：\n"
            "• `https://t.me/channelname/123` → 公开频道\n"
            "• `https://t.me/c/1234567890/123` → 私有频道 "
            "__(需先 /login)__\n\n"
            "机器人会自动检测链接并下载。✅"
        ),
        "autobatch": (
            "📦 **批量下载**\n\n"
            "一次性下载多个文件！🎯\n\n"
            "**公开批量：**\n"
            "`https://t.me/channelname/123`\n\n"
            "**私有批量** __(需先 /login)__：\n"
            "`https://t.me/c/1234567890/123`\n\n"
            "发送链接 → 机器人会询问你要下载多少文件。🚀\n\n"
            "__仅限高级用户。更高级套餐 = 每批更多文件。__"
        ),
        "ytdl": (
            "🌐 **网站视频下载**\n\n"
            "**使用方法：** `/ytdl <链接>`\n\n"
            "**支持的网站：**\n"
            "• YouTube 🎥\n"
            "• Instagram 📸\n"
            "• TikTok 🎵\n"
            "• Twitter / X 🐦\n"
            "• Facebook 📘\n"
            "• Vimeo、Dailymotion、Twitch\n"
            "• SoundCloud、Reddit、Bilibili\n"
            "• 以及 **1000+** 更多网站！\n\n"
            "**示例：**\n"
            "`/ytdl https://youtube.com/watch?v=xxxxx`"
        ),
        "setthumb": (
            "📌 **设置缩略图**\n\n"
            "非常简单 — 只需 2 步！👇\n\n"
            "**第一步：** 输入 `/setthumb`\n"
            "**第二步：** 机器人询问时发送一张图片\n\n"
            "就完成了！✅\n\n"
            "__或者直接发送任意图片 — 机器人会询问是否设为缩略图！__"
        ),
        "transfer": (
            "🔄 **转让高级会员**\n\n"
            "想把你的高级会员转给朋友？🎁\n\n"
            "**使用方法：**\n"
            "`/transfer <用户ID>` 或 `/transfer @用户名`\n\n"
            "⚠️ 操作不可撤销 — 你的高级会员将被移除。"
        ),
    }

    def _autolink_buttons() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 主菜单", callback_data="menu_home")],
        ])

    def _plan_buttons() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✨ 套餐1 — 150 ⭐", callback_data="plan_select_plan1"),
                InlineKeyboardButton("🌟 套餐2 — 500 ⭐", callback_data="plan_select_plan2"),
            ],
            [InlineKeyboardButton("💎 套餐3 — 1000 ⭐", callback_data="plan_select_plan3")],
            [InlineKeyboardButton("🏠 主菜单", callback_data="menu_home")],
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

        # ── 单链接下载 / 设置缩略图 / 转让 / 网站视频下载 ──────────────────
        if command in ("autolink", "setthumb", "transfer", "ytdl"):
            hint = _hints.get(command, "Send a link to use this feature.")
            if command == "autolink":
                await message.reply_text(hint, parse_mode=ParseMode.MARKDOWN,
                                         reply_markup=_autolink_buttons())
            else:
                await message.reply_text(hint, parse_mode=ParseMode.MARKDOWN)
            return

        # ── 推荐 ────────────────────────────────────────────────────────────
        if command == "referral":
            from plugins.referral import get_referral_text
            referral_text = await get_referral_text(client, message.from_user.id)
            await message.reply_text(referral_text, parse_mode=ParseMode.MARKDOWN)
            return

        # ── 批量下载 ────────────────────────────────────────────────────────
        if command == "autobatch":
            await handle_batch_start(client, message)
            return

        # ── 设置 ────────────────────────────────────────────────────────────
        if command == "settings":
            from plugins.settings import _settings_text, _settings_keyboard
            text = await _settings_text(message.from_user.id)
            await message.reply_text(
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=_settings_keyboard(),
            )
            return

        # ── 开始 / 返回 ──────────────────────────────────────────────────────
        if command == "start":
            user_fullname = (
                f"{message.from_user.first_name} "
                f"{message.from_user.last_name or ''}".strip()
            )
            await message.reply_text(
                f"🏠 **主菜单** — {user_fullname}，你好！\n\n请在下方选择 👇",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=get_start_inline(),
                disable_web_page_preview=True,
            )

        # ── 帮助 ─────────────────────────────────────────────────────────────
        elif command == "help":
            await message.reply_text(
                "**❓ 帮助菜单**\n"
                "━━━━━━━━━━━━━━━━\n"
                "**🔗 自动下载：** 粘贴任意 Telegram 链接即可！\n"
                "**📦 自动批量：** 发送链接 → 选择下载文件数量。\n"
                "**⚙️ 设置：** /settings — 设置标题、重命名、关键词过滤、目标对话。\n"
                "━━━━━━━━━━━━━━━━\n"
                "**/plans** — 查看高级套餐\n"
                "**/buy** — 购买高级会员\n"
                "**/transfer** — 转让高级会员给朋友\n"
                "**/referral** — 分享推荐链接赚取奖励\n"
                "**/profile** — 个人主页和套餐信息\n"
                "**/refresh** — 更新 Telegram 个人资料\n"
                "**/getthumb** — 查看缩略图\n"
                "**/setthumb** — 设置缩略图 __（询问时发送图片即可！）__\n"
                "**/rmthumb** — 删除缩略图\n"
                "**/settings** — 所有下载设置\n"
                "**/info** — 详细账户信息\n"
                "**/login** — 连接账户\n"
                "**/logout** — 移除会话\n"
                "━━━━━━━━━━━━━━━━",
                parse_mode=ParseMode.MARKDOWN,
            )

        # ── 套餐 / 购买 ───────────────────────────────────────────────────────
        elif command == "plans":
            from plugins.plan import PLAN_OPTIONS_TEXT
            await message.reply_text(
                PLAN_OPTIONS_TEXT,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=_plan_buttons(),
            )

        # ── 个人中心 & 信息 ───────────────────────────────────────────────────
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

            membership = "🆓 免费"
            expiry_str = None
            if plan3 and plan3.get("expiry_date", now) > now:
                membership = "💎 套餐3"
                expiry_str = plan3["expiry_date"].replace(
                    tzinfo=timezone.utc).astimezone(IST).strftime("%Y年%m月%d日 %H:%M")
            elif plan2 and plan2.get("expiry_date", now) > now:
                membership = "🌟 套餐2"
                expiry_str = plan2["expiry_date"].replace(
                    tzinfo=timezone.utc).astimezone(IST).strftime("%Y年%m月%d日 %H:%M")
            elif plan1 and plan1.get("expiry_date", now) > now:
                membership = "✨ 套餐1"
                expiry_str = plan1["expiry_date"].replace(
                    tzinfo=timezone.utc).astimezone(IST).strftime("%Y年%m月%d日 %H:%M")

            session       = await user_sessions.find_one({"user_id": user_id})
            sessions_list = session.get("sessions", []) if session else []
            if not sessions_list:
                login_status = "未登录"
            elif len(sessions_list) == 1:
                login_status = f"已登录：{sessions_list[0].get('account_name', '未知')}"
            else:
                names = "、".join(s.get("account_name", "未知") for s in sessions_list)
                login_status = f"{len(sessions_list)} 个账户：{names}"

            daily_record = await daily_limit.find_one({"user_id": user_id})
            total_dl     = daily_record.get("total_downloads", 0) if daily_record else 0

            total_stars = 0
            if plan1: total_stars += 150
            if plan2: total_stars += 500
            if plan3: total_stars += 1000

            expiry_line = (
                f"\n<b>📅 到期时间：</b> <code>{expiry_str}</code>"
                if expiry_str else ""
            )

            await message.reply_text(
                f"<b>━━━━━━━━━━━━━━━━</b>\n"
                f"<b>👤 个人中心</b>\n"
                f"<b>━━━━━━━━━━━━━━━━</b>\n"
                f"<b>🆔 ID：</b> <code>{user_id}</code>\n"
                f"<b>👤 姓名：</b> <code>{full_name}</code>\n"
                f"<b>📛 用户名：</b> <code>{username}</code>\n"
                f"<b>━━━━━━━━━━━━━━━━</b>\n"
                f"<b>💎 套餐：</b> <code>{membership}</code>"
                f"{expiry_line}\n"
                f"<b>⭐ 已消费星星：</b> <code>{total_stars}</code>\n"
                f"<b>━━━━━━━━━━━━━━━━</b>\n"
                f"<b>🔗 登录状态：</b> <code>{login_status}</code>\n"
                f"<b>📥 总下载数：</b> <code>{total_dl}</code>\n"
                f"<b>━━━━━━━━━━━━━━━━</b>",
                parse_mode=ParseMode.HTML,
            )

        # ── 查看缩略图 ────────────────────────────────────────────────────────
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
                        "🖼 **你当前的缩略图**\n\n"
                        "🗑 删除：`/rmthumb`\n"
                        "🔄 更换：`/setthumb`"
                    ),
                    parse_mode=ParseMode.MARKDOWN,
                )
            else:
                await message.reply_text(
                    "❌ **你还没有设置缩略图。**\n\n"
                    "📌 **如何设置 — 非常简单！**\n"
                    "**第一步：** 输入 `/setthumb`\n"
                    "**第二步：** 机器人询问时发送图片\n\n"
                    "__或者直接发送任意图片 — 机器人会询问是否设为缩略图！__",
                    parse_mode=ParseMode.MARKDOWN,
                )

        # ── 删除缩略图 ────────────────────────────────────────────────────────
        elif command == "rmthumb":
            import os
            from core import user_activity_collection
            user_id   = message.from_user.id
            user_data = await user_activity_collection.find_one({"user_id": user_id})
            if not user_data or "thumbnail_path" not in user_data:
                await message.reply_text(
                    "❌ **你没有任何缩略图。**",
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
                "✅ **缩略图已删除！**\n\n"
                "__下载的视频将不再使用自定义缩略图。__",
                parse_mode=ParseMode.MARKDOWN,
            )

        # ── 登录 ──────────────────────────────────────────────────────────────
        elif command == "login":
            await message.reply_text(
                "🔐 **请输入 `/login` 连接你的 Telegram 账户。**\n\n"
                "__需要登录才能从私有频道下载。__",
                parse_mode=ParseMode.MARKDOWN,
            )

        # ── 退出登录 ───────────────────────────────────────────────────────────
        elif command == "logout":
            await message.reply_text(
                "🚪 **请输入 `/logout` 移除已保存的会话。**",
                parse_mode=ParseMode.MARKDOWN,
            )

        else:
            await message.reply_text(
                f"请输入 `/{command}` 来使用此功能。",
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
            f"🏠 **主菜单** — {user_fullname}，你好！\n\n请在下方选择 👇",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_start_inline(),
            disable_web_page_preview=True,
        )
        await client.send_message(
            chat_id=message.chat.id,
            text="__键盘已刷新。__",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_main_reply_keyboard(),
        )
