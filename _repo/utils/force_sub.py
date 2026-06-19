# Copyright @juktijol
# Channel t.me/juktijol
#
# utils/force_sub.py — Ultra-fast Force Subscribe System
# ─────────────────────────────────────────────────────────
# ✅ FIXED: stop_propagation() in the correct place
# ✅ FIXED: cache refresh works correctly
# ✅ OPTIMIZED: in-memory TTL cache (sub-millisecond for cached users)
# ✅ OPTIMIZED: API timeout with asyncio.wait_for()
# ✅ UX: polished English message + inline keyboard

import time
import asyncio
from pyrogram import Client, filters
from pyrogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from pyrogram.enums import ChatMemberStatus, ParseMode
from pyrogram.errors import (
    UserNotParticipant,
    ChatAdminRequired,
    ChannelPrivate,
    PeerIdInvalid,
    FloodWait,
)

from utils.logging_setup import LOGGER
from config import FORCE_SUB_CHANNEL, DEVELOPER_USER_ID

# ══════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════

# Cache lifetime (seconds). 300s = 5 minutes.
CACHE_TTL = 300

# Maximum timeout for API calls (seconds)
API_TIMEOUT = 5.0

# Callback data constant
CHECK_SUB_CALLBACK_DATA = "check_sub"

# ══════════════════════════════════════════════════════════════════
# CHANNEL SETUP
# ══════════════════════════════════════════════════════════════════

# Force subscribe channel — feature is disabled when None
_RAW_CHANNEL = FORCE_SUB_CHANNEL  # e.g. "juktijol" or None

if _RAW_CHANNEL:
    # Ensure @ prefix for API calls
    if isinstance(_RAW_CHANNEL, str) and not _RAW_CHANNEL.startswith(("@", "-100")):
        API_CHANNEL: str = f"@{_RAW_CHANNEL}"
    else:
        API_CHANNEL = _RAW_CHANNEL

    CHANNEL_LINK = f"https://t.me/{str(_RAW_CHANNEL).lstrip('@')}"
else:
    API_CHANNEL = None
    CHANNEL_LINK = ""

# ══════════════════════════════════════════════════════════════════
# IN-MEMORY TTL CACHE
# ══════════════════════════════════════════════════════════════════
# Structure: { user_id: (is_subscribed: bool, timestamp: float) }
# - True  → joined,     cached for CACHE_TTL seconds
# - False → not joined, cached for 15 seconds (re-check quickly)
_sub_cache: dict[int, tuple[bool, float]] = {}

NOT_JOINED_CACHE_TTL = 15  # Keep "not joined" result cached for only 15 seconds


def _cache_get(user_id: int) -> bool | None:
    """Read result from cache. Returns None on cache miss."""
    entry = _sub_cache.get(user_id)
    if entry is None:
        return None
    is_sub, ts = entry
    ttl = CACHE_TTL if is_sub else NOT_JOINED_CACHE_TTL
    if time.monotonic() - ts < ttl:
        return is_sub
    # Expired — remove
    _sub_cache.pop(user_id, None)
    return None


def _cache_set(user_id: int, is_sub: bool) -> None:
    _sub_cache[user_id] = (is_sub, time.monotonic())


def _cache_invalidate(user_id: int) -> None:
    """Clear cache when the user joins or a force-refresh is needed."""
    _sub_cache.pop(user_id, None)


# ══════════════════════════════════════════════════════════════════
# CORE CHECK FUNCTION
# ══════════════════════════════════════════════════════════════════

async def check_force_sub(client: Client, user_id: int, refresh: bool = False) -> bool:
    """
    Quickly check whether the user is in the required channel.

    Args:
        client: Pyrogram/Pyrofork Client
        user_id: Telegram user ID
        refresh: If True, bypass cache and run a fresh API call

    Returns:
        True  → member (or FORCE_SUB disabled)
        False → not member
    """
    # Allow all users when force subscribe is disabled
    if not API_CHANNEL:
        return True

    # Developer is always allowed
    if user_id == DEVELOPER_USER_ID:
        return True

    # Cache hit (when refresh=False)
    if not refresh:
        cached = _cache_get(user_id)
        if cached is not None:
            return cached

    # ── Telegram API call ─────────────────────────────────────────
    try:
        member = await asyncio.wait_for(
            client.get_chat_member(API_CHANNEL, user_id),
            timeout=API_TIMEOUT,
        )
        # False when user is KICKED or LEFT
        is_sub = member.status not in (
            ChatMemberStatus.BANNED,
            ChatMemberStatus.LEFT,
        )
        _cache_set(user_id, is_sub)
        return is_sub

    except UserNotParticipant:
        _cache_set(user_id, False)
        return False

    except (ChatAdminRequired, ChannelPrivate, PeerIdInvalid) as e:
        # Bot is not admin in channel, or channel is invalid — fail open (allow user)
        LOGGER.error(f"[ForceSub] Channel error for {API_CHANNEL}: {e}")
        return True

    except FloodWait as e:
        LOGGER.warning(f"[ForceSub] FloodWait {e.value}s — allowing user {user_id}")
        await asyncio.sleep(min(e.value, 5))  # max 5s wait
        return True  # Allow user instead of blocking during flood wait

    except asyncio.TimeoutError:
        LOGGER.warning(f"[ForceSub] API timeout for user {user_id} — allowing")
        return True  # Allow on timeout (better UX)

    except Exception as e:
        LOGGER.error(f"[ForceSub] Unexpected error for user {user_id}: {e}")
        return True  # unknown error — fail open


# ══════════════════════════════════════════════════════════════════
# UI HELPERS
# ══════════════════════════════════════════════════════════════════

def _not_sub_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Join our channel", url=CHANNEL_LINK)],
        [InlineKeyboardButton("⚡ I joined - Continue", callback_data=CHECK_SUB_CALLBACK_DATA)],
    ])


NOT_SUBSCRIBED_TEXT = (
    "⚡ **Access Restricted**\n\n"
    "To use this bot, please join our official channel first.\n\n"
    "Tap the button below to join, then press\n"
    "**⚡ I Joined - Continue**."
)


# ══════════════════════════════════════════════════════════════════
# HANDLER SETUP
# ══════════════════════════════════════════════════════════════════

def setup_force_sub_handler(app: Client):
    """
    Register force-subscribe handlers in the bot.

    - Message interceptor: group=-1 (runs before all handlers)
    - Callback interceptor: group=-1
    - "I Joined" callback: group=0 (normal priority)
    """
    if not API_CHANNEL:
        LOGGER.info("⚠️ FORCE_SUB_CHANNEL is not set - Force Subscribe is disabled.")
        return

    # ── Message Interceptor ───────────────────────────────────────

    @app.on_message(
        filters.private & ~filters.service,
        group=-1,
    )
    async def _msg_interceptor(client: Client, message: Message):
        """
        Runs before each private message.
        Cache hit is <1ms; cache miss is ~200-500ms (network).
        """
        if not message.from_user:
            return

        user_id = message.from_user.id

        # Fast path: developer or FORCE_SUB disabled
        if user_id == DEVELOPER_USER_ID or not API_CHANNEL:
            return

        is_sub = await check_force_sub(client, user_id)

        if not is_sub:
            await message.reply_text(
                NOT_SUBSCRIBED_TEXT,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=_not_sub_keyboard(),
                disable_web_page_preview=True,
            )
            message.stop_propagation()  # ✅ stop other handlers

    # ── Callback Interceptor ──────────────────────────────────────

    @app.on_callback_query(
        ~filters.regex(f"^{CHECK_SUB_CALLBACK_DATA}$"),  # exclude the "joined" button
        group=-1,
    )
    async def _cb_interceptor(client: Client, callback_query: CallbackQuery):
        """
        Check membership before all inline button clicks.
        """
        if not callback_query.from_user:
            return

        user_id = callback_query.from_user.id

        if user_id == DEVELOPER_USER_ID or not API_CHANNEL:
            return

        is_sub = await check_force_sub(client, user_id)

        if not is_sub:
            await callback_query.answer(
                "⚡ Please join the channel first.",
                show_alert=True,
            )
            # Send the join-channel prompt message
            try:
                await callback_query.message.reply_text(
                    NOT_SUBSCRIBED_TEXT,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=_not_sub_keyboard(),
                    disable_web_page_preview=True,
                )
            except Exception:
                pass
            callback_query.stop_propagation()  # ✅

    # ── "I Joined" Button Handler ────────────────────────────────

    @app.on_callback_query(
        filters.regex(f"^{CHECK_SUB_CALLBACK_DATA}$"),
        group=0,
    )
    async def _check_sub_callback(client: Client, callback_query: CallbackQuery):
        """
        Verify with a fresh API call when user taps "I Joined".
        Invalidate cache to ensure a fresh check.
        """
        user_id = callback_query.from_user.id

        # Force fresh check — bypass cache
        _cache_invalidate(user_id)
        is_sub = await check_force_sub(client, user_id, refresh=True)

        if is_sub:
            # ✅ Success
            try:
                await callback_query.message.delete()
            except Exception:
                pass
            await callback_query.answer(
                "⚡ Verified! You can now use the bot.",
                show_alert=True,
            )
            LOGGER.info(f"[ForceSub] User {user_id} verified as member ✅")
        else:
            # ❌ User is not joined yet
            await callback_query.answer(
                "⚡ You are not in the channel yet.\nPlease join and try again.",
                show_alert=True,
            )

    LOGGER.info(f"✅ Force Subscribe enabled - Channel: {API_CHANNEL}")
