# Copyright @juktijol
# Channel t.me/juktijol
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
)

# ══════════════════════════════════════════════════
# MAIN REPLY KEYBOARD — Always visible at the bottom
# ══════════════════════════════════════════════════

def get_main_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("🚀 Start"),                  KeyboardButton("❓ Help")],
            [KeyboardButton("🔗 Single Link Download"),   KeyboardButton("📦 Batch Download")],
            [KeyboardButton("🌐 Website Video Download")],
            [KeyboardButton("💎 Plans & Buy"),            KeyboardButton("👤 My Profile")],
            [KeyboardButton("📌 Set Thumbnail"),          KeyboardButton("👁 View Thumbnail")],
            [KeyboardButton("🗑 Remove Thumbnail"),       KeyboardButton("🔐 Login")],
            [KeyboardButton("🚪 Logout"),                 KeyboardButton("⚙️ Settings")],
            [KeyboardButton("🔄 Transfer"),               KeyboardButton("🔗 Referral")],
            [KeyboardButton("🏠 Back")],
        ],
        resize_keyboard=True,
    )


# ══════════════════════════════════════════════════
# Mapping: button label → command/action key
# ══════════════════════════════════════════════════

BUTTON_COMMAND_MAP: dict[str, str] = {
    "🚀 Start":                    "start",
    "❓ Help":                     "help",
    "🏠 Back":                     "start",
    "🔗 Single Link Download":     "autolink",
    "📦 Batch Download":           "autobatch",
    "🌐 Website Video Download":   "ytdl",
    "💎 Plans & Buy":              "plans",
    "👤 My Profile":               "profile_info",
    "📌 Set Thumbnail":            "setthumb",
    "👁 View Thumbnail":           "getthumb",
    "🗑 Remove Thumbnail":         "rmthumb",
    "🔐 Login":                    "login",
    "🚪 Logout":                   "logout",
    "⚙️ Settings":                 "settings",
    "🔄 Transfer":                 "transfer",
    "🔗 Referral":                 "referral",
}


# ══════════════════════════════════════════════════
# START / HOME — Inline Keyboard
# ══════════════════════════════════════════════════

def get_start_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔗 Single Link DL",  callback_data="menu_autolink"),
            InlineKeyboardButton("📦 Batch Link DL",   callback_data="menu_autobatch"),
        ],
        [
            InlineKeyboardButton("💎 Plans & Buy",     callback_data="menu_plans"),
            InlineKeyboardButton("👤 My Profile",      callback_data="menu_profile"),
        ],
        [
            InlineKeyboardButton("🖼 Thumbnail",       callback_data="menu_thumb"),
            InlineKeyboardButton("🔐 Login",           callback_data="menu_login"),
        ],
        [
            InlineKeyboardButton("⚙️ Settings",        callback_data="menu_settings"),
            InlineKeyboardButton("❓ Help",            callback_data="menu_help"),
        ],
        [
            InlineKeyboardButton("🔄 Transfer",        callback_data="menu_transfer"),
            InlineKeyboardButton("🔗 Referral",        callback_data="menu_referral"),
        ],
    ])


# ══════════════════════════════════════════════════
# THUMBNAIL MENU
# ══════════════════════════════════════════════════

def get_thumb_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📌 Set Thumbnail",    callback_data="guide_setthumb"),
            InlineKeyboardButton("👁 View Thumbnail",   callback_data="action_getthumb"),
        ],
        [
            InlineKeyboardButton("🗑 Remove Thumbnail", callback_data="action_rmthumb"),
        ],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_home")],
    ])


# ══════════════════════════════════════════════════
# LOGIN MENU
# ══════════════════════════════════════════════════

def get_login_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔐 Login",   callback_data="action_login"),
            InlineKeyboardButton("🚪 Logout",  callback_data="action_logout"),
        ],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_home")],
    ])


# ══════════════════════════════════════════════════
# BACK helpers
# ══════════════════════════════════════════════════

def back_to_home() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_home")],
    ])


# Legacy aliases
def get_download_menu() -> InlineKeyboardMarkup:
    return get_start_inline()

def back_to_download() -> InlineKeyboardMarkup:
    return back_to_home()
