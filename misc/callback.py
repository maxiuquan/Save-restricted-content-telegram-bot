
from pyrogram import Client
from pyrogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode
from utils import LOGGER
from .keyboards import (
    get_start_inline,
    get_thumb_menu,
    get_login_menu,
    back_to_home,
)

# ══════════════════════════════════════════════════
# 消息模板
# ══════════════════════════════════════════════════

HOME_TEXT = """🚀 **RestrictedContentDL 机器人**

📌 从任意 Telegram 频道或群组下载内容 — 即使受限也能下载！

请在下方粘贴链接 👇"""

AUTOLINK_GUIDE_TEXT = """🔗 **单链接下载**

无需命令 — 直接粘贴链接！⚡

• `https://t.me/channelname/123` → 公开频道
• `https://t.me/c/1234567890/123` → 私有频道 __(需先 /login)__

机器人会自动找到并发送文件给你。✅

⏱ 免费用户：每次下载间隔 5 分钟。
💎 高级用户：即时下载，无限制！"""

AUTOBATCH_GUIDE_TEXT = """📦 **批量下载**

想一次下载多个文件？简单！🎯

只需发送 Telegram 链接：
`https://t.me/channelname/123`

机器人会询问你要下载多少文件。搞定！🚀

**套餐限制：**
• 套餐1 — 最多 1,000 个文件
• 套餐2 — 最多 2,000 个文件
• 套餐3 — 无限制 ♾️

__批量下载仅限高级用户。__"""

GUIDE_SETTHUMB_TEXT = """📌 **如何设置缩略图**

非常简单 — 只需 2 步！👇

**第一步：** 输入 `/setthumb`
**第二步：** 机器人询问时发送图片

就完成了！✅ 机器人会将该图片设为所有下载视频的缩略图。

__或者直接发送任意图片 — 机器人会询问是否设为缩略图！__"""

THUMB_MENU_TEXT = """🖼 **缩略图设置**

缩略图是视频上显示的小预览图。🎬

• **📌 设置缩略图** — 选择新图片作为缩略图
• **👁 查看缩略图** — 查看当前缩略图
• **🗑 删除缩略图** — 恢复无缩略图状态"""

LOGIN_MENU_TEXT = """🔐 **登录 / 退出**

**为何登录？** 从私有频道下载内容！🔒

**登录：** 连接你的 Telegram 账户 — 安全便捷。
**退出：** 随时移除已保存的会话。

__所有用户（免费和高级）均可登录。__"""

HELP_TEXT = """❓ **帮助与命令**

**🔗 自动下载**
直接粘贴任意 Telegram 链接 — 无需命令！

**📦 自动批量**
发送链接 → 机器人询问下载数量 → 完成！

**⚙️ 设置**
• /settings — 设置标题、重命名、关键词过滤、目标对话

**你的账户**
• /login — 连接账户
• /logout — 移除会话
• /profile — 查看套餐和信息
• /refresh — 更新个人资料

**缩略图**
• /setthumb — 设置缩略图 __（询问时发送图片即可！）__
• /getthumb — 查看当前缩略图
• /rmthumb — 删除缩略图

**套餐**
• /plans — 查看所有高级套餐
• /buy — 购买高级会员
• /transfer — 转让高级会员给朋友
• /referral — 分享推荐链接赚取奖励"""

PROFILE_TEXT = """👤 **个人中心**

查看你的套餐、下载和账户信息。

• /profile — 快速概览
• /info — 详细信息"""

ACTION_LOGIN_TEXT = """🔐 **如何登录**

只需输入 `/login` 并按步骤操作！👇

1. 发送你的手机号 __（含国家代码，如 +86...）__
2. 输入 Telegram 发送的验证码
3. 完成！✅

__你的会话已安全存储。随时使用 /logout 移除。__"""

ACTION_LOGOUT_TEXT = """🚪 **如何退出**

只需输入 `/logout` — 机器人会立即移除已保存的会话。✅"""

ACTION_GETTHUMB_TEXT = """👁 **查看你的缩略图**

输入 `/getthumb` 查看已保存的缩略图。🖼"""

ACTION_RMTHUMB_TEXT = """🗑 **删除你的缩略图**

输入 `/rmthumb` 删除已保存的缩略图。✅

__之后下载的视频将不再使用自定义缩略图。__"""

TRANSFER_TEXT = """🔄 **转让高级会员**

想将你的高级套餐转让给朋友？🎁

**使用方法：**
`/transfer <用户ID>` 或 `/transfer @用户名`

__你套餐的剩余天数将转给对方。__
⚠️ 操作不可撤销 — 你的高级会员将被移除。"""


# ══════════════════════════════════════════════════
# 主回调处理器
# ══════════════════════════════════════════════════

async def handle_callback_query(client: Client, callback_query: CallbackQuery):
    user_id    = callback_query.from_user.id
    chat_id    = callback_query.message.chat.id
    message_id = callback_query.message.id
    data       = callback_query.data

    LOGGER.info(f"Callback: {data} from user {user_id}")

    # ── 强制订阅检查 ────────────────────────────
    from utils.force_sub import check_force_sub, CHECK_SUB_CALLBACK_DATA
    if data == CHECK_SUB_CALLBACK_DATA:
        is_member = await check_force_sub(client, user_id, refresh=True)
        if is_member:
            await callback_query.answer(
                "✅ 欢迎！你现在可以使用机器人了。",
                show_alert=True,
            )
            try:
                await callback_query.message.delete()
            except Exception as e:
                LOGGER.error(f"Failed to delete force sub message: {e}")
        else:
            await callback_query.answer(
                "❌ 你尚未加入！请先加入。",
                show_alert=True,
            )
        return

    # ── 主页 ──────────────────────────────────────
    if data in ("menu_home", "main_menu", "menu_back"):
        await _edit(client, chat_id, message_id, HOME_TEXT, get_start_inline())
        return await callback_query.answer("🏠 主菜单")

    # ── 单链接下载指南 ────────────────────────────
    if data in ("menu_autolink", "menu_dl"):
        await _edit(client, chat_id, message_id, AUTOLINK_GUIDE_TEXT, back_to_home())
        return await callback_query.answer("🔗 单链接下载")

    # ── 批量下载指南 ─────────────────────────────
    if data in ("menu_autobatch", "menu_batch"):
        await _edit(client, chat_id, message_id, AUTOBATCH_GUIDE_TEXT, back_to_home())
        return await callback_query.answer("📦 批量下载")

    # ── 套餐 ──────────────────────────────────────
    if data == "menu_plans":
        from plugins.plan import PLAN_OPTIONS_TEXT
        plan_buttons = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✨ 套餐1 — 150 ⭐", callback_data="plan_select_plan1"),
                InlineKeyboardButton("🌟 套餐2 — 500 ⭐", callback_data="plan_select_plan2"),
            ],
            [InlineKeyboardButton("💎 套餐3 — 1000 ⭐", callback_data="plan_select_plan3")],
            [InlineKeyboardButton("🏠 主菜单", callback_data="menu_home")],
        ])
        await _edit(client, chat_id, message_id, PLAN_OPTIONS_TEXT, plan_buttons)
        return await callback_query.answer("⭐ 套餐")

    # ── 个人中心 ──────────────────────────────────
    if data == "menu_profile":
        await _edit(client, chat_id, message_id, PROFILE_TEXT, back_to_home())
        return await callback_query.answer("👤 个人中心")

    # ── 缩略图菜单 ────────────────────────────────
    if data == "menu_thumb":
        await _edit(client, chat_id, message_id, THUMB_MENU_TEXT, get_thumb_menu())
        return await callback_query.answer("🖼 缩略图")

    if data == "guide_setthumb":
        await _edit(client, chat_id, message_id, GUIDE_SETTHUMB_TEXT, back_to_home())
        return await callback_query.answer("📌 设置缩略图")

    if data == "action_getthumb":
        await _edit(client, chat_id, message_id, ACTION_GETTHUMB_TEXT, back_to_home())
        return await callback_query.answer("👁 查看缩略图")

    if data == "action_rmthumb":
        await _edit(client, chat_id, message_id, ACTION_RMTHUMB_TEXT, back_to_home())
        return await callback_query.answer("🗑 删除缩略图")

    # ── 登录菜单 ──────────────────────────────────
    if data == "menu_login":
        await _edit(client, chat_id, message_id, LOGIN_MENU_TEXT, get_login_menu())
        return await callback_query.answer("🔐 登录 / 退出")

    if data == "action_login":
        await _edit(client, chat_id, message_id, ACTION_LOGIN_TEXT, back_to_home())
        return await callback_query.answer("🔐 登录")

    if data == "action_logout":
        await _edit(client, chat_id, message_id, ACTION_LOGOUT_TEXT, back_to_home())
        return await callback_query.answer("🚪 退出登录")

    # ── 转让 ─────────────────────────────────────
    if data == "menu_transfer":
        await _edit(client, chat_id, message_id, TRANSFER_TEXT, back_to_home())
        return await callback_query.answer("🔄 转让高级会员")

    # ── 推荐 ─────────────────────────────────────
    if data == "menu_referral":
        from plugins.referral import get_referral_text
        referral_text = await get_referral_text(client, user_id)
        await _edit(client, chat_id, message_id, referral_text, back_to_home())
        return await callback_query.answer("🔗 推荐")

    # ── 设置 ──────────────────────────────────────
    if data == "menu_settings":
        from plugins.settings import _settings_text, _settings_keyboard
        try:
            text = await _settings_text(user_id)
            await client.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=_settings_keyboard(),
                disable_web_page_preview=True,
            )
        except Exception as e:
            LOGGER.error(f"menu_settings edit error: {e}")
        return await callback_query.answer("⚙️ 设置")

    # ── 帮助 ──────────────────────────────────────
    if data == "menu_help":
        await _edit(
            client, chat_id, message_id, HELP_TEXT, back_to_home(),
            parse_mode=ParseMode.MARKDOWN
        )
        return await callback_query.answer("❓ 帮助")

    # ── 关闭 ─────────────────────────────────────
    if data in ("menu_close", "close_doc$", "close_logs$"):
        await callback_query.message.delete()
        return await callback_query.answer("✅ 已关闭")

    return await callback_query.answer("✅")


# ══════════════════════════════════════════════════
# 辅助函数：安全地编辑消息
# ══════════════════════════════════════════════════

async def _edit(client, chat_id, message_id, text, markup, parse_mode=ParseMode.MARKDOWN):
    try:
        await client.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    except Exception as e:
        LOGGER.error(f"_edit error: {e}")
