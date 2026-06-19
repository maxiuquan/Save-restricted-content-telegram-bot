# Copyright @juktijol
# Channel t.me/juktijol
"""
Advanced Settings Plugin v3.0 — Full-featured interactive settings panel.

Features:
  - Upload Type (DOCUMENT / MEDIA)
  - Custom Caption with live placeholders preview
  - Rename Tag (prefix / suffix / both)
  - Word Delete (caption filter)
  - Word Replace (caption substitution)
  - Custom Forward Chat ID (with topic support)
  - Spoiler Animation toggle
  - Public Channel Clone toggle
  - Auto-Forward Mode toggle
  - Thumbnail Mode (keep original / custom / none)
  - File Naming Template
  - Download Quality Preference
  - Caption Position (top / bottom / disabled)
  - Reset individual settings or all at once
  - Full English UI, no italic markdown
  - Conversation-based text input with auto-expiry
  - Persistent MongoDB storage via Motor async
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
# CONSTANTS
# ═════════════════════════════════════════════════════════════════

CONV_TIMEOUT = 180   # 3 minutes for text input sessions
DB_TIMEOUT   = 5.0   # MongoDB operation timeout

# Active text-input conversations: { user_id: state_dict }
_conv: dict = {}


# ═════════════════════════════════════════════════════════════════
# TOGGLE SETTINGS — cycle through predefined values
# ═════════════════════════════════════════════════════════════════

TOGGLE_META: dict = {
    "upload_type": {
        "label":   "Upload Type",
        "icon":    "📤",
        "values":  ["DOCUMENT", "MEDIA"],
        "default": "DOCUMENT",
        "help": (
            "DOCUMENT — sends files as documents (preserves quality, no compression).\n"
            "MEDIA — sends files as photos/videos (Telegram compresses them)."
        ),
    },
    "caption_position": {
        "label":   "Caption Position",
        "icon":    "📝",
        "values":  ["BOTTOM", "TOP", "DISABLED"],
        "default": "BOTTOM",
        "help": (
            "BOTTOM — caption appears below the file.\n"
            "TOP — caption appears above the file (prepended).\n"
            "DISABLED — no caption is added."
        ),
    },
    "spoiler_animation": {
        "label":   "Spoiler Animation",
        "icon":    "🎭",
        "values":  ["OFF", "ON"],
        "default": "OFF",
        "help": "Wraps media in a spoiler blur effect when sending as MEDIA type.",
    },
    "public_channel_clone": {
        "label":   "Public Channel Clone",
        "icon":    "📢",
        "values":  ["OFF", "ON"],
        "default": "OFF",
        "help": (
            "ON — bot re-uploads the file instead of forwarding it,\n"
            "removing the source channel tag."
        ),
    },
    "auto_forward": {
        "label":   "Auto Forward Mode",
        "icon":    "🔀",
        "values":  ["OFF", "ON"],
        "default": "OFF",
        "help": (
            "ON — automatically forwards every downloaded file to\n"
            "your Custom Chat ID (must be set separately)."
        ),
    },
    "thumbnail_mode": {
        "label":   "Thumbnail Mode",
        "icon":    "🖼",
        "values":  ["CUSTOM", "AUTO", "NONE"],
        "default": "AUTO",
        "help": (
            "CUSTOM — uses your saved custom thumbnail.\n"
            "AUTO — auto-generates thumbnail from video frames.\n"
            "NONE — sends without any thumbnail."
        ),
    },
    "download_quality": {
        "label":   "Download Quality",
        "icon":    "🎬",
        "values":  ["BEST", "1080P", "720P", "480P", "360P", "AUDIO_ONLY"],
        "default": "BEST",
        "help": (
            "Sets the preferred quality for /ytdl downloads.\n"
            "BEST — highest available quality.\n"
            "AUDIO_ONLY — extracts MP3 audio only."
        ),
    },
    "rename_style": {
        "label":   "Rename Style",
        "icon":    "✏️",
        "values":  ["PREFIX", "SUFFIX", "BOTH", "REPLACE"],
        "default": "PREFIX",
        "help": (
            "PREFIX — tag is prepended to the filename.\n"
            "SUFFIX — tag is appended before the extension.\n"
            "BOTH — tag is both prepended and appended.\n"
            "REPLACE — full filename is replaced with the tag."
        ),
    },
}


# ═════════════════════════════════════════════════════════════════
# TEXT-INPUT SETTINGS — require user to type a value
# ═════════════════════════════════════════════════════════════════

SETTINGS_META: dict = {
    "caption": {
        "label": "Custom Caption",
        "icon":  "📝",
        "description": (
            "Set a custom caption template appended to every downloaded file.\n\n"
            "Supported placeholders:\n"
            "{filename} — original filename\n"
            "{size}     — file size (e.g. 12.4 MB)\n"
            "{caption}  — original caption from source\n"
            "{url}      — source link\n"
            "{date}     — today's date\n\n"
            "Send your caption template below, or send off to disable."
        ),
        "example": "{caption}\n\nDownloaded by @juktijol Bot",
    },
    "rename_tag": {
        "label": "Rename Tag",
        "icon":  "✏️",
        "description": (
            "Set a tag used when renaming downloaded files.\n\n"
            "Rename Style (set separately) controls where the tag is placed:\n"
            "PREFIX  — [Tag] original_name.mp4\n"
            "SUFFIX  — original_name [Tag].mp4\n"
            "BOTH    — [Tag] original_name [Tag].mp4\n"
            "REPLACE — Tag.mp4\n\n"
            "Send your tag, or send off to disable."
        ),
        "example": "[MyChannel]",
    },
    "word_delete": {
        "label": "Word Delete List",
        "icon":  "🗑",
        "description": (
            "Words that will be automatically removed from captions.\n\n"
            "Format: space-separated or comma-separated list.\n\n"
            "Example: spam ads promo\n"
            "or: spam, ads, promo\n\n"
            "Send your word list, or send off to clear."
        ),
        "example": "spam, ads, promo, subscribe",
    },
    "word_replace": {
        "label": "Word Replace Rules",
        "icon":  "🔄",
        "description": (
            "Replace specific words in captions automatically.\n\n"
            "Format: old->new pairs, comma-separated.\n\n"
            "Example: hello->hi, world->earth\n\n"
            "Send your replacement rules, or send off to clear."
        ),
        "example": "channel_name->MyChannel, @olduser->@newuser",
    },
    "custom_chat_id": {
        "label": "Custom Forward Chat",
        "icon":  "📤",
        "description": (
            "Forward all downloads to a specific chat instead of the current chat.\n\n"
            "Accepted formats:\n"
            "@username          — public channel or group\n"
            "-100xxxxxxxxxx     — private channel or supergroup\n"
            "-100xxxxxxxxxx/5   — specific forum topic thread\n\n"
            "Note: The bot must be an admin with send permission in the target chat.\n\n"
            "Send the chat ID or username, or send off to disable."
        ),
        "example": "@mychannel  or  -1001234567890",
    },
    "file_name_template": {
        "label": "File Name Template",
        "icon":  "📄",
        "description": (
            "Set a custom filename template for downloaded files.\n\n"
            "Supported placeholders:\n"
            "{title}    — video/file title\n"
            "{date}     — today's date (YYYY-MM-DD)\n"
            "{quality}  — video quality (e.g. 1080p)\n"
            "{ext}      — file extension (e.g. mp4)\n\n"
            "Example: {title} [{quality}] {date}.{ext}\n\n"
            "Send your template, or send off to reset to default."
        ),
        "example": "{title} [{quality}].{ext}",
    },
    "blocked_extensions": {
        "label": "Blocked File Extensions",
        "icon":  "🚫",
        "description": (
            "Skip downloading files with specific extensions.\n\n"
            "Format: comma-separated list of extensions (without dot).\n\n"
            "Example: exe, zip, apk, bat\n\n"
            "Send your extension list, or send off to allow all types."
        ),
        "example": "exe, zip, apk",
    },
    "max_file_size_mb": {
        "label": "Max File Size (MB)",
        "icon":  "⚖️",
        "description": (
            "Set a maximum file size limit in megabytes.\n"
            "Files larger than this will be skipped automatically.\n\n"
            "Free users: max 500 MB\n"
            "Premium users: max 2000 MB\n\n"
            "Send a number in MB (e.g. 200), or send off to use the plan default."
        ),
        "example": "500",
    },
}


# ═════════════════════════════════════════════════════════════════
# ASYNC DATABASE HELPERS
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
# VALUE FORMATTERS
# ═════════════════════════════════════════════════════════════════

def _fmt(val) -> str:
    """Format a settings value for display."""
    if val is None or val == "":
        return "not set"
    if isinstance(val, dict):
        pairs = ", ".join(f"{k} -> {v}" for k, v in val.items())
        return pairs or "empty"
    if isinstance(val, list):
        return ", ".join(str(w) for w in val) if val else "empty"
    return str(val)


def _toggle_display(settings: dict, key: str) -> str:
    meta = TOGGLE_META[key]
    val = settings.get(key, meta["default"])
    return str(val)


# ═════════════════════════════════════════════════════════════════
# STATUS HELPERS
# ═════════════════════════════════════════════════════════════════

async def _get_session_status(user_id: int) -> str:
    try:
        doc = await asyncio.wait_for(
            user_sessions.find_one({"user_id": user_id}),
            timeout=DB_TIMEOUT,
        )
        if doc and doc.get("sessions"):
            sessions = doc["sessions"]
            names = ", ".join(s.get("account_name", "Unknown") for s in sessions)
            count = len(sessions)
            return f"ON ({count} account{'s' if count > 1 else ''}: {names})"
        return "OFF"
    except Exception:
        return "Unknown"


async def _get_thumbnail_status(user_id: int) -> str:
    try:
        doc = await asyncio.wait_for(
            user_activity_collection.find_one({"user_id": user_id}),
            timeout=DB_TIMEOUT,
        )
        if doc and doc.get("thumbnail_path"):
            return "ON (custom thumbnail set)"
        return "OFF"
    except Exception:
        return "Unknown"


# ═════════════════════════════════════════════════════════════════
# SETTINGS PANEL TEXT BUILDER
# ═════════════════════════════════════════════════════════════════

async def _settings_text(user_id: int) -> str:
    s = await _get_settings(user_id)
    session_status = await _get_session_status(user_id)
    thumb_status   = await _get_thumbnail_status(user_id)

    chat_fwd_val = s.get("custom_chat_id")
    if chat_fwd_val:
        cid = chat_fwd_val.get("chat_id", "")
        tid = chat_fwd_val.get("topic_id")
        chat_fwd = f"ON ({cid}" + (f" / topic {tid})" if tid else ")")
    else:
        chat_fwd = "OFF"

    lines = [
        "Settings Panel",
        "=" * 30,
        "",
        "[ Toggle Settings ]",
    ]

    for key, meta in TOGGLE_META.items():
        val = _toggle_display(s, key)
        lines.append(f"{meta['icon']} {meta['label']}: {val}")

    lines += [
        "",
        "[ Status ]",
        f"🔐 User Session Login: {session_status}",
        f"🖼 Custom Thumbnail:   {thumb_status}",
        f"📤 Custom Forward Chat: {chat_fwd}",
        "",
        "[ Text Settings ]",
    ]

    for key, meta in SETTINGS_META.items():
        val = s.get(key)
        display = _fmt(val)
        # Truncate long values for panel display
        if len(display) > 60:
            display = display[:57] + "..."
        lines.append(f"{meta['icon']} {meta['label']}: {display}")

    lines += [
        "",
        "=" * 30,
        "Tap a button below to change a setting.",
    ]

    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════
# KEYBOARD BUILDERS  (all at module level — fully importable)
# ═════════════════════════════════════════════════════════════════

def _main_keyboard() -> InlineKeyboardMarkup:
    rows = []

    # Section header button (non-clickable separator)
    rows.append([InlineKeyboardButton(
        "— Toggle Settings —", callback_data="cfg_noop"
    )])

    # Toggle buttons — 2 per row
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
        "— Text Settings —", callback_data="cfg_noop"
    )])

    # Text-input settings — 2 per row
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
        "— Actions —", callback_data="cfg_noop"
    )])

    # Action buttons
    rows.append([
        InlineKeyboardButton("📋 Export Settings", callback_data="cfg_export"),
        InlineKeyboardButton("📥 Import Settings", callback_data="cfg_import"),
    ])
    rows.append([
        InlineKeyboardButton("🔄 Reset All Settings", callback_data="cfg_reset_all"),
        InlineKeyboardButton("❓ Help", callback_data="cfg_help"),
    ])
    rows.append([InlineKeyboardButton("❌ Close", callback_data="cfg_close")])

    return InlineKeyboardMarkup(rows)


def _settings_keyboard() -> InlineKeyboardMarkup:
    """
    Module-level alias for _main_keyboard.
    Importable by button_router.py and misc/callback.py via:
        from plugins.settings import _settings_text, _settings_keyboard
    """
    return _main_keyboard()


def _field_keyboard(key: str, has_value: bool = True) -> InlineKeyboardMarkup:
    rows = []
    if has_value:
        rows.append([
            InlineKeyboardButton("🗑 Clear This Setting", callback_data=f"cfg_clear_{key}"),
        ])
    rows.append([
        InlineKeyboardButton("🔙 Back to Settings", callback_data="cfg_back"),
        InlineKeyboardButton("❌ Cancel Input", callback_data="cfg_cancel_input"),
    ])
    return InlineKeyboardMarkup(rows)


def _toggle_detail_keyboard(key: str) -> InlineKeyboardMarkup:
    meta = TOGGLE_META[key]
    rows = []
    # Show all possible values as quick-set buttons
    for val in meta["values"]:
        rows.append([InlineKeyboardButton(
            f"Set: {val}",
            callback_data=f"cfg_set_{key}_{val}",
        )])
    rows.append([InlineKeyboardButton("🔙 Back to Settings", callback_data="cfg_back")])
    return InlineKeyboardMarkup(rows)


def _reset_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("YES, Reset Everything", callback_data="cfg_reset_confirm"),
            InlineKeyboardButton("No, Cancel", callback_data="cfg_back"),
        ]
    ])


# ═════════════════════════════════════════════════════════════════
# PARSERS
# ═════════════════════════════════════════════════════════════════

def _parse_chat_id(raw: str):
    """
    Parse custom forward chat input.
    Supports: @username, -100xxxxxxxxxx, -100xxxxxxxxxx/topic_id
    Returns (chat_id, topic_id | None) or (None, None) on failure.
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
    """Parse 'old->new, old2->new2' into a dict."""
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
    """Accept comma-separated or space-separated word lists."""
    if "," in raw:
        return [w.strip() for w in raw.split(",") if w.strip()]
    return [w.strip() for w in raw.split() if w.strip()]


def _parse_max_size(raw: str, is_premium: bool) -> int | None:
    """Parse and validate a max file size value in MB."""
    try:
        val = int(raw.strip())
        if val <= 0:
            return None
        limit = 2000 if is_premium else 500
        return min(val, limit)
    except ValueError:
        return None


def _parse_blocked_extensions(raw: str) -> list:
    """Parse comma-separated extension list, strip dots."""
    return [
        ext.strip().lstrip(".").lower()
        for ext in raw.split(",")
        if ext.strip()
    ]


# ═════════════════════════════════════════════════════════════════
# PUBLIC API — used by autolink, pbatch, ytdl, etc.
# ═════════════════════════════════════════════════════════════════

async def apply_caption(
    user_id: int,
    original_caption: str,
    filename: str = "",
    size: str = "",
    url: str = "",
) -> str:
    """
    Apply caption template and word filters to a caption string.
    Returns the processed caption.
    """
    s = await _get_settings(user_id)
    caption = original_caption or ""

    # 1. Word delete
    for word in (s.get("word_delete") or []):
        caption = caption.replace(word, "")

    # 2. Word replace
    for old, new in (s.get("word_replace") or {}).items():
        caption = caption.replace(old, new)

    caption = caption.strip()

    # 3. Caption position check
    position = s.get("caption_position", "BOTTOM")
    if position == "DISABLED":
        return ""

    # 4. Custom caption template
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
    # Default limits
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
        return "No settings have been configured yet."

    lines = [
        "Settings Export",
        "=" * 30,
        f"User ID: {user_id}",
        f"Exported: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "[ Toggle Settings ]",
    ]
    for key, meta in TOGGLE_META.items():
        val = s.get(key, meta["default"])
        lines.append(f"{meta['label']}: {val}")

    lines += ["", "[ Text Settings ]"]
    for key, meta in SETTINGS_META.items():
        val = s.get(key)
        lines.append(f"{meta['label']}: {_fmt(val)}")

    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════
# PREMIUM CHECK (lightweight, avoids circular imports)
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
# SETUP
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
            "Loading your settings...",
            parse_mode=ParseMode.DISABLED,
        )
        text = await _settings_text(user_id)
        await loading.edit_text(
            text,
            parse_mode=ParseMode.DISABLED,
            reply_markup=_main_keyboard(),
        )

    # ── No-op button ─────────────────────────────────────────────────────

    @app.on_callback_query(filters.regex(r"^cfg_noop$"))
    async def cfg_noop(client: Client, cq: CallbackQuery):
        await cq.answer()

    # ── Close panel ──────────────────────────────────────────────────────

    @app.on_callback_query(filters.regex(r"^cfg_close$"))
    async def cfg_close(client: Client, cq: CallbackQuery):
        _conv.pop(cq.from_user.id, None)
        try:
            await cq.message.delete()
        except Exception:
            pass
        await cq.answer("Settings closed.")

    # ── Back to main panel ───────────────────────────────────────────────

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

    # ── Cancel active text input ─────────────────────────────────────────

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
        await cq.answer("Input cancelled.")

    # ── Toggle: cycle value ──────────────────────────────────────────────

    @app.on_callback_query(filters.regex(r"^cfg_toggle_([a-z_]+)$"))
    async def cfg_toggle(client: Client, cq: CallbackQuery):
        key = cq.data[len("cfg_toggle_"):]
        if key not in TOGGLE_META:
            return await cq.answer("Unknown toggle.", show_alert=True)

        meta    = TOGGLE_META[key]
        user_id = cq.from_user.id
        s       = await _get_settings(user_id)
        current = s.get(key, meta["default"])
        values  = meta["values"]
        idx     = values.index(current) if current in values else 0
        new_val = values[(idx + 1) % len(values)]

        success = await _save_setting(user_id, key, new_val)
        if not success:
            await cq.answer("DB error — please try again.", show_alert=True)
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
        await cq.answer(f"{meta['icon']} {meta['label']} set to: {new_val}")
        LOGGER.info(f"[Settings] user={user_id} toggled {key} -> {new_val}")

    # ── Toggle: direct value set ─────────────────────────────────────────

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
            return await cq.answer("Unknown setting.", show_alert=True)

        meta = TOGGLE_META[matched_key]
        if matched_val not in meta["values"]:
            return await cq.answer("Invalid value.", show_alert=True)

        success = await _save_setting(cq.from_user.id, matched_key, matched_val)
        if not success:
            await cq.answer("DB error.", show_alert=True)
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
        await cq.answer(f"{meta['icon']} {meta['label']} set to: {matched_val}")

    # ── Open text-input setting ──────────────────────────────────────────

    @app.on_callback_query(filters.regex(r"^cfg_([a-z_]+)$"))
    async def cfg_open_field(client: Client, cq: CallbackQuery):
        key = cq.data[4:]

        # Route reserved keys to their own handlers
        if key in ("noop", "close", "back", "cancel_input", "export",
                   "import", "reset_all", "reset_confirm", "help"):
            return

        if key not in SETTINGS_META:
            return await cq.answer("Unknown setting.", show_alert=True)

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
            f"Current value: {display_current}\n\n"
            f"Example: {meta.get('example', 'N/A')}\n\n"
            f"Type your new value in the chat below, or use the buttons."
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

        # Start conversation
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
                        text=f"Input session expired (no response in {CONV_TIMEOUT}s).\n\n" + timeout_text,
                        parse_mode=ParseMode.DISABLED,
                        reply_markup=_main_keyboard(),
                    )
                except Exception:
                    pass

        asyncio.create_task(_expire_conv())

    # ── Clear individual setting ─────────────────────────────────────────

    @app.on_callback_query(filters.regex(r"^cfg_clear_([a-z_]+)$"))
    async def cfg_clear(client: Client, cq: CallbackQuery):
        key = cq.data[len("cfg_clear_"):]
        if key not in SETTINGS_META:
            return await cq.answer("Unknown setting.", show_alert=True)

        user_id = cq.from_user.id
        _conv.pop(user_id, None)
        success = await _clear_setting(user_id, key)

        if not success:
            await cq.answer("DB error — please try again.", show_alert=True)
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
        await cq.answer(f"{SETTINGS_META[key]['icon']} {SETTINGS_META[key]['label']} cleared.")
        LOGGER.info(f"[Settings] user={user_id} cleared {key}")

    # ── Reset all — confirmation step ────────────────────────────────────

    @app.on_callback_query(filters.regex(r"^cfg_reset_all$"))
    async def cfg_reset_all(client: Client, cq: CallbackQuery):
        try:
            await cq.message.edit_text(
                "Reset All Settings\n"
                "=" * 30 + "\n\n"
                "Are you sure you want to reset ALL settings to their defaults?\n\n"
                "This cannot be undone.",
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
            await cq.answer("DB error — please try again.", show_alert=True)
            return

        text = await _settings_text(user_id)
        try:
            await cq.message.edit_text(
                "All settings have been reset to defaults.\n\n" + text,
                parse_mode=ParseMode.DISABLED,
                reply_markup=_main_keyboard(),
            )
        except Exception:
            pass
        await cq.answer("All settings reset.")
        LOGGER.info(f"[Settings] user={user_id} reset all settings")

    # ── Export settings ──────────────────────────────────────────────────

    @app.on_callback_query(filters.regex(r"^cfg_export$"))
    async def cfg_export(client: Client, cq: CallbackQuery):
        user_id = cq.from_user.id
        export_text = await export_settings(user_id)
        try:
            await cq.message.reply_text(
                f"Settings Export\n{'=' * 30}\n\n{export_text}",
                parse_mode=ParseMode.DISABLED,
            )
        except Exception as e:
            LOGGER.error(f"[Settings] Export failed: {e}")
        await cq.answer("Settings exported above.")

    # ── Import settings (placeholder — guides user) ──────────────────────

    @app.on_callback_query(filters.regex(r"^cfg_import$"))
    async def cfg_import(client: Client, cq: CallbackQuery):
        try:
            await cq.message.reply_text(
                "Settings Import\n"
                "=" * 30 + "\n\n"
                "To import settings, configure each one manually using the\n"
                "settings panel buttons.\n\n"
                "Full JSON import will be available in a future update.",
                parse_mode=ParseMode.DISABLED,
            )
        except Exception:
            pass
        await cq.answer("Import guide sent.")

    # ── Help: explains all settings ──────────────────────────────────────

    @app.on_callback_query(filters.regex(r"^cfg_help$"))
    async def cfg_help(client: Client, cq: CallbackQuery):
        lines = [
            "Settings Help",
            "=" * 30,
            "",
            "[ Toggle Settings ]",
        ]
        for meta in TOGGLE_META.values():
            values_str = " / ".join(meta["values"])
            lines.append(f"{meta['icon']} {meta['label']} ({values_str})")
            lines.append(f"   {meta['help']}")
            lines.append("")

        lines += ["[ Text Settings ]", ""]
        for meta in SETTINGS_META.values():
            lines.append(f"{meta['icon']} {meta['label']}")
            lines.append(f"   Example: {meta.get('example', 'N/A')}")
            lines.append("")

        lines += [
            "=" * 30,
            "Tip: Send 'off' when entering a text setting to disable it.",
        ]

        try:
            await cq.message.reply_text(
                "\n".join(lines),
                parse_mode=ParseMode.DISABLED,
            )
        except Exception:
            pass
        await cq.answer("Help sent above.")

    # ── Text input handler ───────────────────────────────────────────────

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

        # ── "off" disables the setting ───────────────────────────────────
        if raw.lower() == "off":
            await _clear_setting(user_id, key)
            _conv.pop(user_id, None)
            await message.reply_text(
                f"{meta['icon']} {meta['label']} has been disabled.",
                parse_mode=ParseMode.DISABLED,
            )
            await _refresh_panel(client, state, user_id)
            return

        # ── Per-key validation and saving ────────────────────────────────
        reply = ""
        save_ok = False

        if key == "caption":
            save_ok = await _save_setting(user_id, "caption", raw)
            reply = (
                f"{meta['icon']} Caption template saved.\n\n"
                f"Preview:\n{raw}"
            )

        elif key == "rename_tag":
            save_ok = await _save_setting(user_id, "rename_tag", raw)
            style = (await _get_settings(user_id)).get("rename_style", "PREFIX")
            sample = f"[{raw}] example_file.mp4"
            reply = (
                f"{meta['icon']} Rename tag saved.\n\n"
                f"Style: {style}\n"
                f"Sample result: {sample}"
            )

        elif key == "word_delete":
            words = _parse_word_delete(raw)
            if not words:
                await message.reply_text(
                    "No valid words found. Use space or comma-separated format.\n"
                    "Example: spam, ads, promo",
                    parse_mode=ParseMode.DISABLED,
                )
                return
            save_ok = await _save_setting(user_id, "word_delete", words)
            reply = (
                f"{meta['icon']} Word delete list saved.\n\n"
                f"Words that will be removed: {', '.join(words)}"
            )

        elif key == "word_replace":
            pairs = _parse_word_replace(raw)
            if not pairs:
                await message.reply_text(
                    "Could not parse any replacement rules.\n"
                    "Use format: old->new, old2->new2",
                    parse_mode=ParseMode.DISABLED,
                )
                return
            save_ok = await _save_setting(user_id, "word_replace", pairs)
            formatted = "\n".join(f"{o} -> {n}" for o, n in pairs.items())
            reply = f"{meta['icon']} Word replace rules saved.\n\n{formatted}"

        elif key == "custom_chat_id":
            chat_id_val, topic_id = _parse_chat_id(raw)
            if chat_id_val is None:
                await message.reply_text(
                    "Invalid chat ID format.\n\n"
                    "Use: @username, -100xxxxxxxxxx, or -100xxxxxxxxxx/topic_id",
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
                    "Timeout verifying that chat. Please try again.",
                    parse_mode=ParseMode.DISABLED,
                )
                return
            except Exception as e:
                await message.reply_text(
                    f"Could not access that chat: {chat_id_val}\n"
                    f"Make sure the bot is a member/admin there.\n\n"
                    f"Error: {str(e)[:100]}",
                    parse_mode=ParseMode.DISABLED,
                )
                return

            value = {"chat_id": chat_id_val}
            if topic_id is not None:
                value["topic_id"] = topic_id

            save_ok = await _save_setting(user_id, "custom_chat_id", value)
            topic_str = f", topic {topic_id}" if topic_id else ""
            reply = (
                f"{meta['icon']} Custom forward chat saved.\n\n"
                f"Chat: {chat_name}{topic_str}\n"
                f"All downloads will be forwarded there."
            )

        elif key == "file_name_template":
            if "{ext}" not in raw:
                await message.reply_text(
                    "Your template must include {ext} so the file extension is preserved.\n\n"
                    f"Example: {meta.get('example')}",
                    parse_mode=ParseMode.DISABLED,
                )
                return
            save_ok = await _save_setting(user_id, "file_name_template", raw)
            reply = (
                f"{meta['icon']} File name template saved.\n\n"
                f"Template: {raw}"
            )

        elif key == "blocked_extensions":
            exts = _parse_blocked_extensions(raw)
            if not exts:
                await message.reply_text(
                    "No valid extensions found.\n"
                    "Use comma-separated list: exe, zip, apk",
                    parse_mode=ParseMode.DISABLED,
                )
                return
            save_ok = await _save_setting(user_id, "blocked_extensions", exts)
            reply = (
                f"{meta['icon']} Blocked extensions saved.\n\n"
                f"Files with these types will be skipped: {', '.join(exts)}"
            )

        elif key == "max_file_size_mb":
            is_premium = await _is_premium(user_id)
            val = _parse_max_size(raw, is_premium)
            if val is None:
                limit = 2000 if is_premium else 500
                await message.reply_text(
                    f"Invalid size. Enter a number between 1 and {limit} (MB).\n"
                    f"Example: 200",
                    parse_mode=ParseMode.DISABLED,
                )
                return
            save_ok = await _save_setting(user_id, "max_file_size_mb", val)
            reply = (
                f"{meta['icon']} Max file size set to {val} MB.\n"
                f"Files larger than this will be skipped."
            )

        else:
            _conv.pop(user_id, None)
            return

        if not save_ok:
            await message.reply_text(
                "Database error while saving. Please try again.",
                parse_mode=ParseMode.DISABLED,
            )
            return

        _conv.pop(user_id, None)
        await message.reply_text(reply, parse_mode=ParseMode.DISABLED)
        await _refresh_panel(client, state, user_id)

        LOGGER.info(f"[Settings] user={user_id} updated {key}")

    # ── Refresh the floating panel message ───────────────────────────────

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
