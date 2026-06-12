from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
)

# ══════════════════════════════════════════════════
# 主回复键盘 — 始终显示在底部
# ══════════════════════════════════════════════════

def get_main_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("🚀 开始"),                  KeyboardButton("❓ 帮助")],
            [KeyboardButton("🔗 单链接下载"),            KeyboardButton("📦 批量下载")],
            [KeyboardButton("🌐 网站视频下载")],
            [KeyboardButton("💎 套餐购买"),              KeyboardButton("👤 个人中心")],
            [KeyboardButton("📌 设置缩略图"),            KeyboardButton("👁 查看缩略图")],
            [KeyboardButton("🗑 删除缩略图"),            KeyboardButton("🔐 登录")],
            [KeyboardButton("🚪 退出登录"),              KeyboardButton("⚙️ 设置")],
            [KeyboardButton("🔄 转让"),                  KeyboardButton("🔗 推荐")],
            [KeyboardButton("🏠 返回")],
        ],
        resize_keyboard=True,
    )


# ══════════════════════════════════════════════════
# 映射：按钮标签 → 命令/操作键
# ══════════════════════════════════════════════════

BUTTON_COMMAND_MAP: dict[str, str] = {
    "🚀 开始":                    "start",
    "❓ 帮助":                     "help",
    "🏠 返回":                     "start",
    "🔗 单链接下载":               "autolink",
    "📦 批量下载":                 "autobatch",
    "🌐 网站视频下载":             "ytdl",
    "💎 套餐购买":                 "plans",
    "👤 个人中心":                 "profile_info",
    "📌 设置缩略图":               "setthumb",
    "👁 查看缩略图":               "getthumb",
    "🗑 删除缩略图":               "rmthumb",
    "🔐 登录":                     "login",
    "🚪 退出登录":                 "logout",
    "⚙️ 设置":                     "settings",
    "🔄 转让":                     "transfer",
    "🔗 推荐":                     "referral",
}


# ══════════════════════════════════════════════════
# 开始 / 主页 — 内联键盘
# ══════════════════════════════════════════════════

def get_start_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔗 单链接下载",  callback_data="menu_autolink"),
            InlineKeyboardButton("📦 批量下载",    callback_data="menu_autobatch"),
        ],
        [
            InlineKeyboardButton("💎 套餐购买",    callback_data="menu_plans"),
            InlineKeyboardButton("👤 个人中心",    callback_data="menu_profile"),
        ],
        [
            InlineKeyboardButton("🖼 缩略图",      callback_data="menu_thumb"),
            InlineKeyboardButton("🔐 登录",        callback_data="menu_login"),
        ],
        [
            InlineKeyboardButton("⚙️ 设置",        callback_data="menu_settings"),
            InlineKeyboardButton("❓ 帮助",        callback_data="menu_help"),
        ],
        [
            InlineKeyboardButton("🔄 转让",        callback_data="menu_transfer"),
            InlineKeyboardButton("🔗 推荐",        callback_data="menu_referral"),
        ],
    ])


# ══════════════════════════════════════════════════
# 缩略图菜单
# ══════════════════════════════════════════════════

def get_thumb_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📌 设置缩略图",    callback_data="guide_setthumb"),
            InlineKeyboardButton("👁 查看缩略图",    callback_data="action_getthumb"),
        ],
        [
            InlineKeyboardButton("🗑 删除缩略图",    callback_data="action_rmthumb"),
        ],
        [InlineKeyboardButton("🏠 主菜单", callback_data="menu_home")],
    ])


# ══════════════════════════════════════════════════
# 登录菜单
# ══════════════════════════════════════════════════

def get_login_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔐 登录",      callback_data="action_login"),
            InlineKeyboardButton("🚪 退出登录",  callback_data="action_logout"),
        ],
        [InlineKeyboardButton("🏠 主菜单", callback_data="menu_home")],
    ])


# ══════════════════════════════════════════════════
# 返回辅助函数
# ══════════════════════════════════════════════════

def back_to_home() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 主菜单", callback_data="menu_home")],
    ])


# 旧版别名
def get_download_menu() -> InlineKeyboardMarkup:
    return get_start_inline()

def back_to_download() -> InlineKeyboardMarkup:
    return back_to_home()
