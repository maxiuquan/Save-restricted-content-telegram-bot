"""
高级设置插件 v3.0 — 功能完整的交互式设置面板。

功能：
  - 上传类型（DOCUMENT / MEDIA）
  - 自定义标题及实时占位符预览
  - 重命名标签（前缀 / 后缀 / 两者）
  - 删除词（标题过滤）
  - 替换词（标题替换）
  - 自定义转发对话 ID（支持话题）
  - 剧透动画开关
  - 公开频道克隆开关
  - 自动转发模式开关
  - 缩略图模式（保留原图 / 自定义 / 无）
  - 文件名模板
  - 下载质量偏好
  - 标题位置（顶部 / 底部 / 禁用）
  - 重置单个设置或全部重置
  - 完整中文 UI，不使用斜体 markdown
  - 基于对话的文本输入，带自动过期
  - 通过 Motor 异步持久化 MongoDB 存储
"""

import asyncio
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from pyrogram.enums import ParseMode

from config import COMMAND_PREFIX
from core import user_activity_collection, user_sessions
from utils import LOGGER

# ═════════════════════════════════════════════════════════════════
# 常量
# ═════════════════════════════════════════════════════════════════

CONV_TIMEOUT = 180   # 文本输入会话 3 分钟超时
DB_TIMEOUT   = 5.0   # MongoDB 操作超时

# 活跃文本输入会话：{ user_id: state_dict }
_conv: dict = {}


# ═════════════════════════════════════════════════════════════════
# 切换设置 — 循环遍历预设值
# ═════════════════════════════════════════════════════════════════

TOGGLE_META: dict = {
    "upload_type": {
        "label":   "上传类型",
        "icon":    "📤",
        "values":  ["DOCUMENT", "MEDIA"],
        "default": "DOCUMENT",
        "help": (
            "DOCUMENT — 以文件形式发送（保持原画质，无压缩）。\n"
            "MEDIA — 以照片/视频形式发送（Telegram 会进行压缩）。"
        ),
    },
    "caption_position": {
        "label":   "标题位置",
        "icon":    "📝",
        "values":  ["BOTTOM", "TOP", "DISABLED"],
        "default": "BOTTOM",
        "help": (
            "BOTTOM — 标题显示在文件下方。\n"
            "TOP — 标题显示在文件上方（前置）。\n"
            "DISABLED — 不添加标题。"
        ),
    },
    "spoiler_animation": {
        "label":   "剧透动画",
        "icon":    "🎭",
        "values":  ["OFF", "ON"],
        "default": "OFF",
        "help": "以 MEDIA 类型发送时为媒体添加剧透模糊效果。",
    },
    "public_channel_clone": {
        "label":   "公开频道克隆",
        "icon":    "📢",
        "values":  ["OFF", "ON"],
        "default": "OFF",
        "help": (
            "ON — 机器人重新上传文件而非直接转发，\n"
            "从而移除来源频道标识。"
        ),
    },
    "auto_forward": {
        "label":   "自动转发模式",
        "icon":    "🔀",
        "values":  ["OFF", "ON"],
        "default": "OFF",
        "help": (
            "ON — 自动将每个下载的文件转发到\n"
            "你的自定义对话 ID（需单独设置）。"
        ),
    },
    "thumbnail_mode": {
        "label":   "缩略图模式",
        "icon":    "🖼",
        "values":  ["CUSTOM", "AUTO", "NONE"],
        "default": "AUTO",
        "help": (
            "CUSTOM — 使用你保存的自定义缩略图。\n"
            "AUTO — 从视频帧自动生成缩略图。\n"
            "NONE — 发送时不带任何缩略图。"
        ),
    },
    "download_quality": {
        "label":   "下载质量",
        "icon":    "🎬",
        "values":  ["BEST", "1080P", "720P", "480P", "360P", "AUDIO_ONLY"],
        "default": "BEST",
        "help": (
            "设置 /ytdl 下载的首选质量。\n"
            "BEST — 最高可用质量。\n"
            "AUDIO_ONLY — 仅提取 MP3 音频。"
        ),
    },
    "rename_style": {
        "label":   "重命名样式",
        "icon":    "✏️",
        "values":  ["PREFIX", "SUFFIX", "BOTH", "REPLACE"],
        "default": "PREFIX",
        "help": (
            "PREFIX — 标签加在文件名前。\n"
            "SUFFIX — 标签加在扩展名前。\n"
            "BOTH — 标签同时加在前后。\n"
            "REPLACE — 用标签完全替换文件名。"
        ),
    },
}


# ═════════════════════════════════════════════════════════════════
# 文本输入设置 — 需要用户输入值
# ═════════════════════════════════════════════════════════════════

SETTINGS_META: dict = {
    "caption": {
        "label": "自定义标题",
        "icon":  "📝",
        "description": (
            "设置自定义标题模板，附加到每个下载的文件。\n\n"
            "支持的占位符：\n"
            "{filename} — 原始文件名\n"
            "{size}     — 文件大小（如 12.4 MB）\n"
            "{caption}  — 来源的原始标题\n"
            "{url}      — 来源链接\n"
            "{date}     — 今天的日期\n\n"
            "在下方发送你的标题模板，或发送 off 以禁用。"
        ),
        "example": "{caption}",
    },
    "rename_tag": {
        "label": "重命名标签",
        "icon":  "✏️",
        "description": (
            "设置用于重命名下载文件的标签。\n\n"
            "重命名样式（需单独设置）控制标签的放置位置：\n"
            "PREFIX  — [标签] 原始文件名.mp4\n"
            "SUFFIX  — 原始文件名 [标签].mp4\n"
            "BOTH    — [标签] 原始文件名 [标签].mp4\n"
            "REPLACE — 标签.mp4\n\n"
            "发送你的标签，或发送 off 以禁用。"
        ),
        "example": "[MyChannel]",
    },
    "word_delete": {
        "label": "删除词列表",
        "icon":  "🗑",
        "description": (
            "将从标题中自动删除的词语。\n\n"
            "格式：空格分隔或逗号分隔的列表。\n\n"
            "示例：spam ads promo\n"
            "或：spam, ads, promo\n\n"
            "发送你的词语列表，或发送 off 以清空。"
        ),
        "example": "spam, ads, promo, subscribe",
    },
    "word_replace": {
        "label": "替换词规则",
        "icon":  "🔄",
        "description": (
            "自动替换标题中的特定词语。\n\n"
            "格式：old->new 对，逗号分隔。\n\n"
            "示例：hello->hi, world->earth\n\n"
            "发送你的替换规则，或发送 off 以清空。"
        ),
        "example": "channel_name->MyChannel, @olduser->@newuser",
    },
    "custom_chat_id": {
        "label": "自定义转发对话",
        "icon":  "📤",
        "description": (
            "将下载内容转发到指定的对话而非当前对话。\n\n"
            "接受格式：\n"
            "@username          — 公开频道或群组\n"
            "-100xxxxxxxxxx     — 私有频道或超级群组\n"
            "-100xxxxxxxxxx/5   — 特定论坛话题线程\n\n"
            "注意：机器人必须在目标对话中是管理员并有发送权限。\n\n"
            "发送对话 ID 或用户名，或发送 off 以禁用。"
        ),
        "example": "@mychannel 或 -1001234567890",
    },
    "file_name_template": {
        "label": "文件名模板",
        "icon":  "📄",
        "description": (
            "设置下载文件的自定义文件名模板。\n\n"
            "支持的占位符：\n"
            "{title}    — 视频/文件标题\n"
            "{date}     — 今天的日期 (YYYY-MM-DD)\n"
            "{quality}  — 视频质量（如 1080p）\n"
            "{ext}      — 文件扩展名（如 mp4）\n\n"
            "示例：{title} [{quality}] {date}.{ext}\n\n"
            "发送你的模板，或发送 off 以重置为默认。"
        ),
        "example": "{title} [{quality}].{ext}",
    },
    "blocked_extensions": {
        "label": "阻止的文件扩展名",
        "icon":  "🚫",
        "description": (
            "跳过下载具有特定扩展名的文件。\n\n"
            "格式：逗号分隔的扩展名列表（不带点）。\n\n"
            "示例：exe, zip, apk, bat\n\n"
            "发送你的扩展名列表，或发送 off 以允许所有类型。"
        ),
        "example": "exe, zip, apk",
    },
    "max_file_size_mb": {
        "label": "最大文件大小 (MB)",
        "icon":  "⚖️",
        "description": (
            "设置以 MB 为单位的最大文件大小限制。\n"
            "超过此大小的文件将被自动跳过。\n\n"
            "免费用户：最大 500 MB\n"
            "高级用户：最大 2000 MB\n\n"
            "发送数字（如 200），或发送 off 使用默认限制。"
        ),
        "example": "500",
    },
}


# ═════════════════════════════════════════════════════════════════
# 异步数据库辅助函数
# ═════════════════════════════════════════════════════════════════

async def _get_settings(user_id: int) -> dict:
    try:
        doc = await asyncio.wait_for(
            user_activity_collection.find_one({"user_id": user_id}),
            timeout=DB_TIMEOUT,
        )
        return (doc or {}).get("settings", {})
    except asyncio.TimeoutError:
        LOGGER.warning(f"[Settings] DB timeout getting settings for {user_id}")
        return {}
    except Exception as e:
        LOGGER.error(f"[Settings] Error getting settings: {e}")
        return {}


async def _save_setting(user_id: int, key: str, value) -> bool:
    try:
        await asyncio.wait_for(
            user_activity_collection.update_one(
                {"user_id": user_id},
                {"$set": {f"settings.{key}": value}},
                upsert=True,
            ),
            timeout=DB_TIMEOUT,
        )
        return True
    except asyncio.TimeoutError:
        LOGGER.warning(f"[Settings] DB timeout saving {key} for {user_id}")
        return False
    except Exception as e:
        LOGGER.error(f"[Settings] Error saving setting: {e}")
        return False


async def _clear_setting(user_id: int, key: str) -> bool:
    try:
        await asyncio.wait_for(
            user_activity_collection.update_one(
                {"user_id": user_id},
                {"$unset": {f"settings.{key}": ""}},
                upsert=True,
            ),
            timeout=DB_TIMEOUT,
        )
        return True
    except asyncio.TimeoutError:
        LOGGER.warning(f"[Settings] DB timeout clearing {key} for {user_id}")
        return False
    except Exception as e:
        LOGGER.error(f"[Settings] Error clearing setting: {e}")
        return False


async def _reset_all_settings(user_id: int) -> bool:
    try:
        await asyncio.wait_for(
            user_activity_collection.update_one(
                {"user_id": user_id},
                {"$unset": {"settings": ""}},
                upsert=True,
            ),
            timeout=DB_TIMEOUT,
        )
        return True
    except asyncio.TimeoutError:
        LOGGER.warning(f"[Settings] DB timeout resetting all for {user_id}")
        return False
    except Exception as e:
        LOGGER.error(f"[Settings] Error resetting settings: {e}")
        return False


# ═════════════════════════════════════════════════════════════════
# 值格式化函数
# ═════════════════════════════════════════════════════════════════

def _fmt(val) -> str:
    """格式化设置值用于显示"""
    if val is None or val == "":
        return "未设置"
    if isinstance(val, dict):
        pairs = ", ".join(f"{k} -> {v}" for k, v in val.items())
        return pairs or "空"
    if isinstance(val, list):
        return ", ".join(str(w) for w in val) if val else "空"
    return str(val)


def _toggle_display(settings: dict, key: str) -> str:
    meta = TOGGLE_META[key]
    val = settings.get(key, meta["default"])
    return str(val)


# ═════════════════════════════════════════════════════════════════
# 状态辅助函数
# ═════════════════════════════════════════════════════════════════

async def _get_session_status(user_id: int) -> str:
    try:
        doc = await asyncio.wait_for(
            user_sessions.find_one({"user_id": user_id}),
            timeout=DB_TIMEOUT,
        )
        if doc and doc.get("sessions"):
            sessions = doc["sessions"]
            names = ", ".join(s.get("account_name", "未知") for s in sessions)
            count = len(sessions)
            return f"已开启 ({count} 个账户：{names})"
        return "已关闭"
    except Exception:
        return "未知"


async def _get_thumbnail_status(user_id: int) -> str:
    try:
        doc = await asyncio.wait_for(
            user_activity_collection.find_one({"user_id": user_id}),
            timeout=DB_TIMEOUT,
        )
        if doc and doc.get("thumbnail_path"):
            return "已开启（已设置自定义缩略图）"
        return "已关闭"
    except Exception:
        return "未知"


# ═════════════════════════════════════════════════════════════════
# 设置面板文本生成器
# ═════════════════════════════════════════════════════════════════

async def _settings_text(user_id: int) -> str:
    s = await _get_settings(user_id)
    session_status = await _get_session_status(user_id)
    thumb_status   = await _get_thumbnail_status(user_id)

    chat_fwd_val = s.get("custom_chat_id")
    if chat_fwd_val:
        cid = chat_fwd_val.get("chat_id", "")
        tid = chat_fwd_val.get("topic_id")
        chat_fwd = f"已开启 ({cid}" + (f" / 话题 {tid})" if tid else ")")
    else:
        chat_fwd = "已关闭"

    lines = [
        "设置面板",
        "=" * 30,
        "",
        "[ 开关设置 ]",
    ]

    for key, meta in TOGGLE_META.items():
        val = _toggle_display(s, key)
        lines.append(f"{meta['icon']} {meta['label']}: {val}")

    lines += [
        "",
        "[ 状态 ]",
        f"🔐 用户会话登录：{session_status}",
        f"🖼 自定义缩略图：  {thumb_status}",
        f"📤 自定义转发对话：{chat_fwd}",
        "",
        "[ 文本设置 ]",
    ]

    for key, meta in SETTINGS_META.items():
        val = s.get(key)
        display = _fmt(val)
        # 截断过长的值以适应面板显示
        if len(display) > 60:
            display = display[:57] + "..."
        lines.append(f"{meta['icon']} {meta['label']}: {display}")

    lines += [
        "",
        "=" * 30,
        "点击下方按钮更改设置。",
    ]

    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════
# 键盘生成器（全部在模块级别 — 完全可导入）
# ═════════════════════════════════════════════════════════════════

def _main_keyboard() -> InlineKeyboardMarkup:
    rows = []

    # 节标题按钮（不可点击的分割线）
    rows.append([InlineKeyboardButton(
        "— 开关设置 —", callback_data="cfg_noop"
    )])

    # 切换按钮 — 每行2个
    toggle_keys = list(TOGGLE_META.keys())
    for i in range(0, len(toggle_keys), 2):
        row = []
        for key in toggle_keys[i:i + 2]:
            meta = TOGGLE_META[key]
            row.append(InlineKeyboardButton(
                f"{meta['icon']} {meta['label']}",
                callback_data=f"cfg_toggle_{key}",
            ))
        rows.append(row)

    rows.append([InlineKeyboardButton(
        "— 文本设置 —", callback_data="cfg_noop"
    )])

    # 文本输入设置 — 每行2个
    text_keys = list(SETTINGS_META.keys())
    for i in range(0, len(text_keys), 2):
        row = []
        for key in text_keys[i:i + 2]:
            meta = SETTINGS_META[key]
            row.append(InlineKeyboardButton(
                f"{meta['icon']} {meta['label']}",
                callback_data=f"cfg_{key}",
            ))
        rows.append(row)

    rows.append([InlineKeyboardButton(
        "— 操作 —", callback_data="cfg_noop"
    )])

    # 操作按钮
    rows.append([
        InlineKeyboardButton("📋 导出设置", callback_data="cfg_export"),
        InlineKeyboardButton("📥 导入设置", callback_data="cfg_import"),
    ])
    rows.append([
        InlineKeyboardButton("🔄 重置所有设置", callback_data="cfg_reset_all"),
        InlineKeyboardButton("❓ 帮助", callback_data="cfg_help"),
    ])
    rows.append([InlineKeyboardButton("❌ 关闭", callback_data="cfg_close")])

    return InlineKeyboardMarkup(rows)


def _settings_keyboard() -> InlineKeyboardMarkup:
    """
    _main_keyboard 的模块级别别名。
    可通过以下方式由 button_router.py 和 misc/callback.py 导入：
        from plugins.settings import _settings_text, _settings_keyboard
    """
    return _main_keyboard()


def _field_keyboard(key: str, has_value: bool = True) -> InlineKeyboardMarkup:
    rows = []
    if has_value:
        rows.append([
            InlineKeyboardButton("🗑 清除此设置", callback_data=f"cfg_clear_{key}"),
        ])
    rows.append([
        InlineKeyboardButton("🔙 返回设置", callback_data="cfg_back"),
        InlineKeyboardButton("❌ 取消输入", callback_data="cfg_cancel_input"),
    ])
    return InlineKeyboardMarkup(rows)


def _toggle_detail_keyboard(key: str) -> InlineKeyboardMarkup:
    meta = TOGGLE_META[key]
    rows = []
    # 显示所有可能的值作为快捷设置按钮
    for val in meta["values"]:
        rows.append([InlineKeyboardButton(
            f"设置为: {val}",
            callback_data=f"cfg_set_{key}_{val}",
        )])
    rows.append([InlineKeyboardButton("🔙 返回设置", callback_data="cfg_back")])
    return InlineKeyboardMarkup(rows)


def _reset_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("是，重置所有", callback_data="cfg_reset_confirm"),
            InlineKeyboardButton("不，取消", callback_data="cfg_back"),
        ]
    ])


# ═════════════════════════════════════════════════════════════════
# 解析器
# ═════════════════════════════════════════════════════════════════

def _parse_chat_id(raw: str):
    """
    解析自定义转发对话输入。
    支持：@username, -100xxxxxxxxxx, -100xxxxxxxxxx/topic_id
    返回 (chat_id, topic_id | None)，失败时返回 (None, None)。
    """
    raw = raw.strip()
    if "/" in raw and not raw.startswith("@"):
        parts = raw.split("/", 1)
        try:
            chat_id = int(parts[0].strip())
        except ValueError:
            return None, None
        try:
            topic_id = int(parts[1].strip())
        except ValueError:
            return None, None
        return chat_id, topic_id
    try:
        return int(raw), None
    except ValueError:
        if raw.startswith("@") and len(raw) > 1:
            return raw, None
        return None, None


def _parse_word_replace(raw: str) -> dict:
    """将 'old->new, old2->new2' 解析为字典"""
    result = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if "->" in pair:
            parts = pair.split("->", 1)
            old = parts[0].strip()
            new = parts[1].strip()
            if old:
                result[old] = new
    return result


def _parse_word_delete(raw: str) -> list:
    """接受逗号分隔或空格分隔的词列表"""
    if "," in raw:
        return [w.strip() for w in raw.split(",") if w.strip()]
    return [w.strip() for w in raw.split() if w.strip()]


def _parse_max_size(raw: str, is_premium: bool) -> int | None:
    """解析并验证以 MB 为单位的最大文件大小值"""
    try:
        val = int(raw.strip())
        if val <= 0:
            return None
        limit = 2000 if is_premium else 500
        return min(val, limit)
    except ValueError:
        return None


def _parse_blocked_extensions(raw: str) -> list:
    """解析逗号分隔的扩展名列表，去除点号"""
    return [
        ext.strip().lstrip(".").lower()
        for ext in raw.split(",")
        if ext.strip()
    ]


# ═════════════════════════════════════════════════════════════════
# 公共 API — 被 autolink、pbatch、ytdl 等模块使用
# ═════════════════════════════════════════════════════════════════

async def apply_caption(
    user_id: int,
    original_caption: str,
    filename: str = "",
    size: str = "",
    url: str = "",
) -> str:
    """
    对标题字符串应用标题模板和词语过滤器。
    返回处理后的标题。
    """
    s = await _get_settings(user_id)
    caption = original_caption or ""

    # 1. 词语删除
    for word in (s.get("word_delete") or []):
        caption = caption.replace(word, "")

    # 2. 词语替换
    for old, new in (s.get("word_replace") or {}).items():
        caption = caption.replace(old, new)

    caption = caption.strip()

    # 3. 标题位置检查
    position = s.get("caption_position", "BOTTOM")
    if position == "DISABLED":
        return ""

    # 4. 自定义标题模板
    template = s.get("caption")
    if template:
        from datetime import date
        caption = template.format(
            filename=filename,
            size=size,
            caption=caption,
            url=url,
            date=date.today().isoformat(),
        )

    return caption


async def apply_rename(user_id: int, filename: str) -> str:
    """
    Apply rename tag according to the rename_style setting.
    Returns the new filename.
    """
    s = await _get_settings(user_id)
    tag = s.get("rename_tag")
    if not tag:
        return filename

    style = s.get("rename_style", "PREFIX")
    name, _, ext = filename.rpartition(".")
    name = name or filename
    ext_part = f".{ext}" if ext else ""

    if style == "PREFIX":
        return f"{tag} {name}{ext_part}"
    elif style == "SUFFIX":
        return f"{name} {tag}{ext_part}"
    elif style == "BOTH":
        return f"{tag} {name} {tag}{ext_part}"
    elif style == "REPLACE":
        return f"{tag}{ext_part}"
    return filename


async def get_target_chat(user_id: int, fallback_chat_id):
    """
    Returns (chat_id, topic_id | None) for forwarding.
    Falls back to the provided chat_id if no custom chat is set.
    """
    s = await _get_settings(user_id)
    ccd = s.get("custom_chat_id")
    if ccd:
        return ccd.get("chat_id", fallback_chat_id), ccd.get("topic_id")
    return fallback_chat_id, None


async def get_upload_type(user_id: int) -> str:
    s = await _get_settings(user_id)
    return s.get("upload_type", TOGGLE_META["upload_type"]["default"])


async def get_spoiler_animation(user_id: int) -> bool:
    s = await _get_settings(user_id)
    return s.get("spoiler_animation", "OFF") == "ON"


async def get_public_channel_clone(user_id: int) -> bool:
    s = await _get_settings(user_id)
    return s.get("public_channel_clone", "OFF") == "ON"


async def get_auto_forward(user_id: int) -> bool:
    s = await _get_settings(user_id)
    return s.get("auto_forward", "OFF") == "ON"


async def get_thumbnail_mode(user_id: int) -> str:
    s = await _get_settings(user_id)
    return s.get("thumbnail_mode", TOGGLE_META["thumbnail_mode"]["default"])


async def get_download_quality(user_id: int) -> str:
    s = await _get_settings(user_id)
    return s.get("download_quality", TOGGLE_META["download_quality"]["default"])


async def get_max_file_size_bytes(user_id: int, is_premium: bool) -> int:
    s = await _get_settings(user_id)
    custom_mb = s.get("max_file_size_mb")
    if custom_mb:
        try:
            return int(custom_mb) * 1024 * 1024
        except (ValueError, TypeError):
            pass
    # 默认限制
    return (2 * 1024 * 1024 * 1024) if is_premium else (500 * 1024 * 1024)


async def should_skip_extension(user_id: int, filename: str) -> bool:
    """Returns True if the file extension is in the user's blocked list."""
    s = await _get_settings(user_id)
    blocked = s.get("blocked_extensions") or []
    if not blocked:
        return False
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext in blocked


async def apply_file_name_template(user_id: int, title: str, quality: str, ext: str) -> str:
    """Apply custom file name template if set."""
    s = await _get_settings(user_id)
    template = s.get("file_name_template")
    if not template:
        return f"{title}.{ext}"
    from datetime import date
    try:
        return template.format(
            title=title,
            date=date.today().isoformat(),
            quality=quality,
            ext=ext,
        )
    except Exception:
        return f"{title}.{ext}"


async def export_settings(user_id: int) -> str:
    """Export all settings as a formatted text block."""
    s = await _get_settings(user_id)
    if not s:
        return "尚未配置任何设置。"

    lines = [
        "设置导出",
        "=" * 30,
        f"用户 ID：{user_id}",
        f"导出时间：{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "[ 开关设置 ]",
    ]
    for key, meta in TOGGLE_META.items():
        val = s.get(key, meta["default"])
        lines.append(f"{meta['label']}: {val}")

    lines += ["", "[ 文本设置 ]"]
    for key, meta in SETTINGS_META.items():
        val = s.get(key)
        lines.append(f"{meta['label']}: {_fmt(val)}")

    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════
# 高级会员检查（轻量级，避免循环导入）
# ═════════════════════════════════════════════════════════════════

async def _is_premium(user_id: int) -> bool:
    from datetime import datetime
    from core import prem_plan1, prem_plan2, prem_plan3
    now = datetime.utcnow()
    for col in [prem_plan1, prem_plan2, prem_plan3]:
        try:
            doc = await asyncio.wait_for(
                col.find_one({"user_id": user_id}),
                timeout=DB_TIMEOUT,
            )
            if doc and doc.get("expiry_date", now) > now:
                return True
        except Exception:
            pass
    return False


# ═════════════════════════════════════════════════════════════════
# 注册处理函数
# ═════════════════════════════════════════════════════════════════

def setup_settings_handler(app: Client):

    # ── /settings command ───────────────────────────────────────────────

    @app.on_message(
        filters.command("settings", prefixes=COMMAND_PREFIX)
        & (filters.private | filters.group)
    )
    async def settings_command(client: Client, message: Message):
        user_id = message.from_user.id
        LOGGER.info(f"[Settings] /settings opened by user {user_id}")
        loading = await message.reply_text(
            "正在加载你的设置...",
            parse_mode=ParseMode.DISABLED,
        )
        text = await _settings_text(user_id)
        await loading.edit_text(
            text,
            parse_mode=ParseMode.DISABLED,
            reply_markup=_main_keyboard(),
        )

    # ── 空操作按钮 ─────────────────────────────────────────────────────

    @app.on_callback_query(filters.regex(r"^cfg_noop$"))
    async def cfg_noop(client: Client, cq: CallbackQuery):
        await cq.answer()

    # ── 关闭面板 ──────────────────────────────────────────────────────

    @app.on_callback_query(filters.regex(r"^cfg_close$"))
    async def cfg_close(client: Client, cq: CallbackQuery):
        _conv.pop(cq.from_user.id, None)
        try:
            await cq.message.delete()
        except Exception:
            pass
        await cq.answer("设置已关闭。")

    # ── 返回主面板 ───────────────────────────────────────────────────────────────

    @app.on_callback_query(filters.regex(r"^cfg_back$"))
    async def cfg_back(client: Client, cq: CallbackQuery):
        user_id = cq.from_user.id
        _conv.pop(user_id, None)
        text = await _settings_text(user_id)
        try:
            await cq.message.edit_text(
                text,
                parse_mode=ParseMode.DISABLED,
                reply_markup=_main_keyboard(),
            )
        except Exception:
            pass
        await cq.answer()

    # ── 取消当前文本输入 ─────────────────────────────────────────

    @app.on_callback_query(filters.regex(r"^cfg_cancel_input$"))
    async def cfg_cancel_input(client: Client, cq: CallbackQuery):
        user_id = cq.from_user.id
        _conv.pop(user_id, None)
        text = await _settings_text(user_id)
        try:
            await cq.message.edit_text(
                text,
                parse_mode=ParseMode.DISABLED,
                reply_markup=_main_keyboard(),
            )
        except Exception:
            pass
        await cq.answer("输入已取消。")

    # ── 切换：循环值 ──────────────────────────────────────────────

    @app.on_callback_query(filters.regex(r"^cfg_toggle_([a-z_]+)$"))
    async def cfg_toggle(client: Client, cq: CallbackQuery):
        key = cq.data[len("cfg_toggle_"):]
        if key not in TOGGLE_META:
            return await cq.answer("未知开关。", show_alert=True)

        meta    = TOGGLE_META[key]
        user_id = cq.from_user.id
        s       = await _get_settings(user_id)
        current = s.get(key, meta["default"])
        values  = meta["values"]
        idx     = values.index(current) if current in values else 0
        new_val = values[(idx + 1) % len(values)]

        success = await _save_setting(user_id, key, new_val)
        if not success:
            await cq.answer("数据库错误 — 请重试。", show_alert=True)
            return

        text = await _settings_text(user_id)
        try:
            await cq.message.edit_text(
                text,
                parse_mode=ParseMode.DISABLED,
                reply_markup=_main_keyboard(),
            )
        except Exception:
            pass
        await cq.answer(f"{meta['icon']} {meta['label']} 已设置为：{new_val}")
        LOGGER.info(f"[Settings] user={user_id} toggled {key} -> {new_val}")

    # ── 切换：直接设置值 ─────────────────────────────────────────

    @app.on_callback_query(filters.regex(r"^cfg_set_([a-z_]+)_(.+)$"))
    async def cfg_set_value(client: Client, cq: CallbackQuery):
        raw    = cq.data[len("cfg_set_"):]
        matched_key = None
        matched_val = None
        for k in TOGGLE_META:
            if raw.startswith(k + "_"):
                matched_key = k
                matched_val = raw[len(k) + 1:]
                break
        if not matched_key:
            return await cq.answer("未知设置。", show_alert=True)

        meta = TOGGLE_META[matched_key]
        if matched_val not in meta["values"]:
            return await cq.answer("无效值。", show_alert=True)

        success = await _save_setting(cq.from_user.id, matched_key, matched_val)
        if not success:
            await cq.answer("数据库错误。", show_alert=True)
            return

        text = await _settings_text(cq.from_user.id)
        try:
            await cq.message.edit_text(
                text,
                parse_mode=ParseMode.DISABLED,
                reply_markup=_main_keyboard(),
            )
        except Exception:
            pass
        await cq.answer(f"{meta['icon']} {meta['label']} 已设置为：{matched_val}")

    # ── 打开文本输入设置 ──────────────────────────────────────────

    @app.on_callback_query(filters.regex(r"^cfg_([a-z_]+)$"))
    async def cfg_open_field(client: Client, cq: CallbackQuery):
        key = cq.data[4:]

        # 将保留的键路由到各自的处理函数
        if key in ("noop", "close", "back", "cancel_input", "export",
                   "import", "reset_all", "reset_confirm", "help"):
            return

        if key not in SETTINGS_META:
            return await cq.answer("未知设置。", show_alert=True)

        meta    = SETTINGS_META[key]
        user_id = cq.from_user.id
        s       = await _get_settings(user_id)
        current = s.get(key)
        has_val = current is not None and current != ""

        display_current = _fmt(current)
        if len(display_current) > 200:
            display_current = display_current[:197] + "..."

        panel_text = (
            f"{meta['icon']} {meta['label']}\n"
            f"{'=' * 30}\n\n"
            f"{meta['description']}\n\n"
            f"{'=' * 30}\n"
            f"当前值：{display_current}\n\n"
            f"示例：{meta.get('example', '无')}\n\n"
            f"在下方输入新值，或使用按钮操作。"
        )

        try:
            await cq.message.edit_text(
                panel_text,
                parse_mode=ParseMode.DISABLED,
                reply_markup=_field_keyboard(key, has_val),
            )
        except Exception as e:
            LOGGER.warning(f"[Settings] edit_text failed: {e}")

        await cq.answer()

        # 开始对话
        _conv[user_id] = {
            "stage":        key,
            "chat_id":      cq.message.chat.id,
            "panel_msg_id": cq.message.id,
        }

        async def _expire_conv():
            await asyncio.sleep(CONV_TIMEOUT)
            state = _conv.get(user_id, {})
            if state.get("stage") == key:
                _conv.pop(user_id, None)
                try:
                    timeout_text = await _settings_text(user_id)
                    await client.edit_message_text(
                        chat_id=state["chat_id"],
                        message_id=state["panel_msg_id"],
                        text=f"输入会话已过期（{CONV_TIMEOUT}秒无响应）。\n\n" + timeout_text,
                        parse_mode=ParseMode.DISABLED,
                        reply_markup=_main_keyboard(),
                    )
                except Exception:
                    pass

        asyncio.create_task(_expire_conv())

    # ── 清除单个设置 ─────────────────────────────────────────

    @app.on_callback_query(filters.regex(r"^cfg_clear_([a-z_]+)$"))
    async def cfg_clear(client: Client, cq: CallbackQuery):
        key = cq.data[len("cfg_clear_"):]
        if key not in SETTINGS_META:
            return await cq.answer("未知设置。", show_alert=True)

        user_id = cq.from_user.id
        _conv.pop(user_id, None)
        success = await _clear_setting(user_id, key)

        if not success:
            await cq.answer("数据库错误 — 请重试。", show_alert=True)
            return

        text = await _settings_text(user_id)
        try:
            await cq.message.edit_text(
                text,
                parse_mode=ParseMode.DISABLED,
                reply_markup=_main_keyboard(),
            )
        except Exception:
            pass
        await cq.answer(f"{SETTINGS_META[key]['icon']} {SETTINGS_META[key]['label']} 已清除。")
        LOGGER.info(f"[Settings] user={user_id} cleared {key}")

    # ── 全部重置 — 确认步骤 ────────────────────────────────────

    @app.on_callback_query(filters.regex(r"^cfg_reset_all$"))
    async def cfg_reset_all(client: Client, cq: CallbackQuery):
        try:
            await cq.message.edit_text(
                "重置所有设置\n"
                "=" * 30 + "\n\n"
                "你确定要重置**所有**设置为默认值吗？\n\n"
                "此操作无法撤销。",
                parse_mode=ParseMode.DISABLED,
                reply_markup=_reset_confirm_keyboard(),
            )
        except Exception:
            pass
        await cq.answer()

    @app.on_callback_query(filters.regex(r"^cfg_reset_confirm$"))
    async def cfg_reset_confirm(client: Client, cq: CallbackQuery):
        user_id = cq.from_user.id
        _conv.pop(user_id, None)
        success = await _reset_all_settings(user_id)

        if not success:
            await cq.answer("数据库错误 — 请重试。", show_alert=True)
            return

        text = await _settings_text(user_id)
        try:
            await cq.message.edit_text(
                "所有设置已重置为默认值。\n\n" + text,
                parse_mode=ParseMode.DISABLED,
                reply_markup=_main_keyboard(),
            )
        except Exception:
            pass
        await cq.answer("所有设置已重置。")
        LOGGER.info(f"[Settings] user={user_id} reset all settings")

    # ── 导出设置 ──────────────────────────────────────────────────

    @app.on_callback_query(filters.regex(r"^cfg_export$"))
    async def cfg_export(client: Client, cq: CallbackQuery):
        user_id = cq.from_user.id
        export_text = await export_settings(user_id)
        try:
            await cq.message.reply_text(
                f"设置导出\n{'=' * 30}\n\n{export_text}",
                parse_mode=ParseMode.DISABLED,
            )
        except Exception as e:
            LOGGER.error(f"[Settings] Export failed: {e}")
        await cq.answer("设置已导出。")

    # ── 导入设置（占位 — 引导用户）──────────────────────

    @app.on_callback_query(filters.regex(r"^cfg_import$"))
    async def cfg_import(client: Client, cq: CallbackQuery):
        try:
            await cq.message.reply_text(
                "导入设置\n"
                "=" * 30 + "\n\n"
                "要导入设置，请使用设置面板按钮逐一配置。\n\n"
                "完整的 JSON 导入功能将在未来更新中提供。",
                parse_mode=ParseMode.DISABLED,
            )
        except Exception:
            pass
        await cq.answer("导入指南已发送。")

    # ── 帮助：解释所有设置 ──────────────────────────────────────

    @app.on_callback_query(filters.regex(r"^cfg_help$"))
    async def cfg_help(client: Client, cq: CallbackQuery):
        lines = [
            "设置帮助",
            "=" * 30,
            "",
            "[ 开关设置 ]",
        ]
        for meta in TOGGLE_META.values():
            values_str = " / ".join(meta["values"])
            lines.append(f"{meta['icon']} {meta['label']} ({values_str})")
            lines.append(f"   {meta['help']}")
            lines.append("")

        lines += ["[ 文本设置 ]", ""]
        for meta in SETTINGS_META.values():
            lines.append(f"{meta['icon']} {meta['label']}")
            lines.append(f"   示例：{meta.get('example', '无')}")
            lines.append("")

        lines += [
            "=" * 30,
            "提示：在输入文本设置时发送 'off' 可禁用它。",
        ]

        try:
            await cq.message.reply_text(
                "\n".join(lines),
                parse_mode=ParseMode.DISABLED,
            )
        except Exception:
            pass
        await cq.answer("帮助已发送。")

    # ── 文本输入处理 ───────────────────────────────────────────────

    @app.on_message(
        filters.text
        & (filters.private | filters.group)
        & filters.create(
            lambda _, __, msg: (
                msg.from_user is not None
                and msg.from_user.id in _conv
                and _conv[msg.from_user.id].get("chat_id") == msg.chat.id
            )
        ),
        group=50,
    )
    async def cfg_text_input(client: Client, message: Message):
        user_id = message.from_user.id
        state   = _conv.get(user_id)
        if not state:
            return

        key     = state.get("stage")
        raw     = (message.text or "").strip()

        if key not in SETTINGS_META:
            _conv.pop(user_id, None)
            return

        meta = SETTINGS_META[key]

        # ── "off" 禁用该设置 ───────────────────────────────────
        if raw.lower() == "off":
            await _clear_setting(user_id, key)
            _conv.pop(user_id, None)
            await message.reply_text(
                f"{meta['icon']} {meta['label']} 已禁用。",
                parse_mode=ParseMode.DISABLED,
            )
            await _refresh_panel(client, state, user_id)
            return

        # ── 逐键验证并保存 ────────────────────────────────
        reply = ""
        save_ok = False

        if key == "caption":
            save_ok = await _save_setting(user_id, "caption", raw)
            reply = (
                f"{meta['icon']} 标题模板已保存。\n\n"
                f"预览：\n{raw}"
            )

        elif key == "rename_tag":
            save_ok = await _save_setting(user_id, "rename_tag", raw)
            style = (await _get_settings(user_id)).get("rename_style", "PREFIX")
            sample = f"[{raw}] example_file.mp4"
            reply = (
                f"{meta['icon']} 重命名标签已保存。\n\n"
                f"样式：{style}\n"
                f"示例结果：{sample}"
            )

        elif key == "word_delete":
            words = _parse_word_delete(raw)
            if not words:
                await message.reply_text(
                    "未找到有效词语。请使用空格或逗号分隔格式。\n"
                    "示例：spam, ads, promo",
                    parse_mode=ParseMode.DISABLED,
                )
                return
            save_ok = await _save_setting(user_id, "word_delete", words)
            reply = (
                f"{meta['icon']} 删除词列表已保存。\n\n"
                f"将被删除的词语：{', '.join(words)}"
            )

        elif key == "word_replace":
            pairs = _parse_word_replace(raw)
            if not pairs:
                await message.reply_text(
                    "无法解析任何替换规则。\n"
                    "请使用格式：old->new, old2->new2",
                    parse_mode=ParseMode.DISABLED,
                )
                return
            save_ok = await _save_setting(user_id, "word_replace", pairs)
            formatted = "\n".join(f"{o} -> {n}" for o, n in pairs.items())
            reply = f"{meta['icon']} 替换词规则已保存。\n\n{formatted}"

        elif key == "custom_chat_id":
            chat_id_val, topic_id = _parse_chat_id(raw)
            if chat_id_val is None:
                await message.reply_text(
                    "无效的对话 ID 格式。\n\n"
                    "请使用：@username, -100xxxxxxxxxx, 或 -100xxxxxxxxxx/话题ID",
                    parse_mode=ParseMode.DISABLED,
                )
                return
            # Verify bot access
            try:
                chat_obj = await asyncio.wait_for(
                    client.get_chat(chat_id_val),
                    timeout=10.0,
                )
                chat_name = chat_obj.title or str(chat_id_val)
            except asyncio.TimeoutError:
                await message.reply_text(
                    "验证对话超时，请重试。",
                    parse_mode=ParseMode.DISABLED,
                )
                return
            except Exception as e:
                await message.reply_text(
                    f"无法访问该对话：{chat_id_val}\n"
                    f"请确保机器人是该对话的成员/管理员。\n\n"
                    f"错误：{str(e)[:100]}",
                    parse_mode=ParseMode.DISABLED,
                )
                return

            value = {"chat_id": chat_id_val}
            if topic_id is not None:
                value["topic_id"] = topic_id

            save_ok = await _save_setting(user_id, "custom_chat_id", value)
            topic_str = f"，话题 {topic_id}" if topic_id else ""
            reply = (
                f"{meta['icon']} 自定义转发对话已保存。\n\n"
                f"对话：{chat_name}{topic_str}\n"
                f"所有下载内容将被转发到那里。"
            )

        elif key == "file_name_template":
            if "{ext}" not in raw:
                await message.reply_text(
                    "你的模板必须包含 {ext} 以保留文件扩展名。\n\n"
                    f"示例：{meta.get('example')}",
                    parse_mode=ParseMode.DISABLED,
                )
                return
            save_ok = await _save_setting(user_id, "file_name_template", raw)
            reply = (
                f"{meta['icon']} 文件名模板已保存。\n\n"
                f"模板：{raw}"
            )

        elif key == "blocked_extensions":
            exts = _parse_blocked_extensions(raw)
            if not exts:
                await message.reply_text(
                    "未找到有效扩展名。\n"
                    "请使用逗号分隔列表：exe, zip, apk",
                    parse_mode=ParseMode.DISABLED,
                )
                return
            save_ok = await _save_setting(user_id, "blocked_extensions", exts)
            reply = (
                f"{meta['icon']} 阻止的扩展名已保存。\n\n"
                f"以下类型的文件将被跳过：{', '.join(exts)}"
            )

        elif key == "max_file_size_mb":
            is_premium = await _is_premium(user_id)
            val = _parse_max_size(raw, is_premium)
            if val is None:
                limit = 2000 if is_premium else 500
                await message.reply_text(
                    f"无效大小。请输入 1 到 {limit} (MB) 之间的数字。\n"
                    f"示例：200",
                    parse_mode=ParseMode.DISABLED,
                )
                return
            save_ok = await _save_setting(user_id, "max_file_size_mb", val)
            reply = (
                f"{meta['icon']} 最大文件大小已设置为 {val} MB。\n"
                f"超过此大小的文件将被跳过。"
            )

        else:
            _conv.pop(user_id, None)
            return

        if not save_ok:
            await message.reply_text(
                "保存时数据库错误，请重试。",
                parse_mode=ParseMode.DISABLED,
            )
            return

        _conv.pop(user_id, None)
        await message.reply_text(reply, parse_mode=ParseMode.DISABLED)
        await _refresh_panel(client, state, user_id)

        LOGGER.info(f"[Settings] user={user_id} updated {key}")

    # ── 刷新浮动面板消息 ───────────────────────────────

    async def _refresh_panel(client: Client, state: dict, user_id: int):
        panel_msg_id = state.get("panel_msg_id")
        chat_id      = state.get("chat_id")
        if panel_msg_id and chat_id:
            try:
                text = await _settings_text(user_id)
                await client.edit_message_text(
                    chat_id=chat_id,
                    message_id=panel_msg_id,
                    text=text,
                    parse_mode=ParseMode.DISABLED,
                    reply_markup=_main_keyboard(),
                )
            except Exception as e:
                LOGGER.warning(f"[Settings] Could not refresh panel: {e}")
