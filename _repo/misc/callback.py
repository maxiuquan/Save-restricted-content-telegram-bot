# Copyright @juktijol
# Channel t.me/juktijol

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
# MESSAGE TEMPLATES
# ══════════════════════════════════════════════════

HOME_TEXT = """🚀 **RestrictedContentDL Bot**

📌 Download content from any Telegram channel or group — even restricted ones!

Just paste a link below 👇"""

AUTOLINK_GUIDE_TEXT = """🔗 **Single Link Download**

No command needed — just paste a link! ⚡

• `https://t.me/channelname/123` → public channel
• `https://t.me/c/1234567890/123` → private channel __(need to /login first)__

The bot will find and send the file to you. ✅

⏱ Free users: wait 5 minutes between downloads.
💎 Premium users: instant & unlimited!"""

AUTOBATCH_GUIDE_TEXT = """📦 **Batch Download**

Want to download many files at once? Easy! 🎯

Just send a Telegram link:
`https://t.me/channelname/123`

The bot will ask how many files you want. Done! 🚀

**Plan limits:**
• Plan 1 — up to 1,000 files
• Plan 2 — up to 2,000 files
• Plan 3 — unlimited ♾️

__Batch download is for premium users only.__"""

GUIDE_SETTHUMB_TEXT = """📌 **How to Set a Thumbnail**

Super easy — just 2 steps! 👇

**Step 1:** Type `/setthumb`
**Step 2:** Send a photo when the bot asks

That's it! ✅ The bot will use that photo as the thumbnail for all your downloaded videos.

__Or just send any photo — the bot will ask if you want to set it as a thumbnail!__"""

THUMB_MENU_TEXT = """🖼 **Thumbnail Settings**

A thumbnail is the small preview image shown on videos. 🎬

• **📌 Set Thumbnail** — pick a new photo to use
• **👁 View Thumbnail** — see your current thumbnail
• **🗑 Remove Thumbnail** — go back to no thumbnail"""

LOGIN_MENU_TEXT = """🔐 **Login / Logout**

**Why login?** To download from private channels! 🔒

**Login:** Connect your Telegram account — safe & easy.
**Logout:** Remove your saved session anytime.

__All users (free & premium) can login.__"""

HELP_TEXT = """❓ **Help & Commands**

**🔗 Auto Download**
Just paste any Telegram link — no command needed!

**📦 Auto Batch**
Send a link → bot asks how many files → done!

**⚙️ Settings**
• /settings — set caption, rename files, filter words, target chat

**Your Account**
• /login — connect your account
• /logout — remove your session
• /profile — see your plan & info
• /refresh — update your profile info

**Thumbnail**
• /setthumb — set a thumbnail __(just send a photo when asked!)__
• /getthumb — view your current thumbnail
• /rmthumb — remove your thumbnail

**Plans**
• /plans — see all premium plans
• /buy — get premium
• /transfer — give your premium to a friend
• /referral — share your link & earn rewards"""

PROFILE_TEXT = """👤 **My Profile**

See your plan, downloads, and account info.

• /profile — quick overview
• /info — full details"""

ACTION_LOGIN_TEXT = """🔐 **How to Login**

Just type `/login` and follow the steps! 👇

1. Send your phone number __(with country code, e.g. +880...)__
2. Enter the code Telegram sends you
3. Done! ✅

__Your session is stored safely. Use /logout to remove it anytime.__"""

ACTION_LOGOUT_TEXT = """🚪 **How to Logout**

Just type `/logout` — the bot will remove your saved session right away. ✅"""

ACTION_GETTHUMB_TEXT = """👁 **View Your Thumbnail**

Type `/getthumb` to see the thumbnail you have saved. 🖼"""

ACTION_RMTHUMB_TEXT = """🗑 **Remove Your Thumbnail**

Type `/rmthumb` to delete your saved thumbnail. ✅

__After this, downloaded videos will have no custom thumbnail.__"""

TRANSFER_TEXT = """🔄 **Transfer Premium**

Want to give your premium plan to a friend? 🎁

**How to use:**
`/transfer <user_id>` or `/transfer @username`

__The remaining days of your plan will move to them.__
⚠️ This cannot be undone — your premium will be removed."""


# ══════════════════════════════════════════════════
# MAIN CALLBACK HANDLER
# ══════════════════════════════════════════════════

async def handle_callback_query(client: Client, callback_query: CallbackQuery):
    user_id    = callback_query.from_user.id
    chat_id    = callback_query.message.chat.id
    message_id = callback_query.message.id
    data       = callback_query.data

    LOGGER.info(f"Callback: {data} from user {user_id}")

    # ── FORCE SUBSCRIBE CHECK ────────────────────
    from utils.force_sub import check_force_sub, CHECK_SUB_CALLBACK_DATA
    if data == CHECK_SUB_CALLBACK_DATA:
        is_member = await check_force_sub(client, user_id, refresh=True)
        if is_member:
            await callback_query.answer(
                "✅ Welcome! You can now use the bot.",
                show_alert=True,
            )
            try:
                await callback_query.message.delete()
            except Exception as e:
                LOGGER.error(f"Failed to delete force sub message: {e}")
        else:
            await callback_query.answer(
                "❌ You haven't joined yet! Please join first.",
                show_alert=True,
            )
        return

    # ── HOME ──────────────────────────────────────
    if data in ("menu_home", "main_menu", "menu_back"):
        await _edit(client, chat_id, message_id, HOME_TEXT, get_start_inline())
        return await callback_query.answer("🏠 Main menu")

    # ── AUTO LINK GUIDE ───────────────────────────
    if data in ("menu_autolink", "menu_dl"):
        await _edit(client, chat_id, message_id, AUTOLINK_GUIDE_TEXT, back_to_home())
        return await callback_query.answer("🔗 Single Link DL")

    # ── AUTO BATCH GUIDE ──────────────────────────
    if data in ("menu_autobatch", "menu_batch"):
        await _edit(client, chat_id, message_id, AUTOBATCH_GUIDE_TEXT, back_to_home())
        return await callback_query.answer("📦 Batch DL")

    # ── PLANS ─────────────────────────────────────
    if data == "menu_plans":
        from plugins.plan import PLAN_OPTIONS_TEXT
        plan_buttons = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✨ Plan 1 — 150 ⭐", callback_data="plan_select_plan1"),
                InlineKeyboardButton("🌟 Plan 2 — 500 ⭐", callback_data="plan_select_plan2"),
            ],
            [InlineKeyboardButton("💎 Plan 3 — 1000 ⭐", callback_data="plan_select_plan3")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_home")],
        ])
        await _edit(client, chat_id, message_id, PLAN_OPTIONS_TEXT, plan_buttons)
        return await callback_query.answer("⭐ Plans")

    # ── PROFILE ───────────────────────────────────
    if data == "menu_profile":
        await _edit(client, chat_id, message_id, PROFILE_TEXT, back_to_home())
        return await callback_query.answer("👤 Profile")

    # ── THUMBNAIL MENU ────────────────────────────
    if data == "menu_thumb":
        await _edit(client, chat_id, message_id, THUMB_MENU_TEXT, get_thumb_menu())
        return await callback_query.answer("🖼 Thumbnail")

    if data == "guide_setthumb":
        await _edit(client, chat_id, message_id, GUIDE_SETTHUMB_TEXT, back_to_home())
        return await callback_query.answer("📌 Set Thumbnail")

    if data == "action_getthumb":
        await _edit(client, chat_id, message_id, ACTION_GETTHUMB_TEXT, back_to_home())
        return await callback_query.answer("👁 View Thumbnail")

    if data == "action_rmthumb":
        await _edit(client, chat_id, message_id, ACTION_RMTHUMB_TEXT, back_to_home())
        return await callback_query.answer("🗑 Remove Thumbnail")

    # ── LOGIN MENU ────────────────────────────────
    if data == "menu_login":
        await _edit(client, chat_id, message_id, LOGIN_MENU_TEXT, get_login_menu())
        return await callback_query.answer("🔐 Login / Logout")

    if data == "action_login":
        await _edit(client, chat_id, message_id, ACTION_LOGIN_TEXT, back_to_home())
        return await callback_query.answer("🔐 Login")

    if data == "action_logout":
        await _edit(client, chat_id, message_id, ACTION_LOGOUT_TEXT, back_to_home())
        return await callback_query.answer("🚪 Logout")

    # ── TRANSFER ─────────────────────────────────
    if data == "menu_transfer":
        await _edit(client, chat_id, message_id, TRANSFER_TEXT, back_to_home())
        return await callback_query.answer("🔄 Transfer Premium")

    # ── REFERRAL ─────────────────────────────────
    if data == "menu_referral":
        from plugins.referral import get_referral_text
        referral_text = await get_referral_text(client, user_id)
        await _edit(client, chat_id, message_id, referral_text, back_to_home())
        return await callback_query.answer("🔗 Referral")

    # ── SETTINGS ──────────────────────────────────
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
        return await callback_query.answer("⚙️ Settings")

    # ── HELP ──────────────────────────────────────
    if data == "menu_help":
        await _edit(
            client, chat_id, message_id, HELP_TEXT, back_to_home(),
            parse_mode=ParseMode.MARKDOWN
        )
        return await callback_query.answer("❓ Help")

    # ── CLOSE ─────────────────────────────────────
    if data in ("menu_close", "close_doc$", "close_logs$"):
        await callback_query.message.delete()
        return await callback_query.answer("✅ Closed")

    return await callback_query.answer("✅")


# ══════════════════════════════════════════════════
# HELPER: edit message safely
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
