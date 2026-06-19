# Copyright @juktijol
# Channel t.me/juktijol
#
# utils/tracker.py
# ✅ FIXED: Private channel name → user client দিয়ে resolve
# ✅ FIXED: Thread/topic link regex
# ✅ FIXED: Graceful fallback on all errors

import os
import re
from datetime import datetime, timezone, timedelta
from pyrogram import Client
from pyrogram.enums import ParseMode
from pyrogram.errors import (
    FloodWait,
    ChatWriteForbidden,
    ChannelInvalid,
    ChannelPrivate,
    PeerIdInvalid,
    BadRequest,
)
import asyncio

from .logging_setup import LOGGER
from .helper import get_video_thumbnail

# ── IST timezone (UTC+5:30) ────────────────────────────────────────────────
IST = timezone(timedelta(hours=5, minutes=30))


def _now_ist() -> str:
    return datetime.now(IST).strftime("%d %b %Y, %I:%M:%S %p IST")


def _link_type(url: str) -> str:
    if "/c/" in url or "t.me/c/" in url:
        return "🔒 Private"
    return "✅ Public"


def _extract_ids_from_url(url: str) -> tuple:
    """
    URL থেকে (channel_identifier, msg_id) বের করো।

    Formats:
      Public:  t.me/username/123
      Public:  t.me/username/topic/123
      Private: t.me/c/1234567890/123
      Private: t.me/c/1234567890/topic/123
    """
    # Private link — আগে check করো
    pvt = re.search(r"t\.me/c/(\d+)/(?:\d+/)?(\d+)", url)
    if pvt:
        return pvt.group(1), int(pvt.group(2))

    # Public link
    pub = re.search(
        r"t\.me/([a-zA-Z][a-zA-Z0-9_]{3,})/(?:\d+/)?(\d+)", url
    )
    if pub:
        return f"@{pub.group(1)}", int(pub.group(2))

    return None, None


# ══════════════════════════════════════════════════════════════════════════
# ✅ CORE FIX: User client দিয়ে private channel name resolve
# ══════════════════════════════════════════════════════════════════════════

async def _get_user_client_for_resolve(user_id: int):
    """
    MongoDB থেকে user-এর প্রথম session নিয়ে একটা temporary client বানাও।
    শুধু channel name resolve করার জন্য — হালকা ও fast।

    Returns:
        user_client অথবা None
    """
    try:
        # core থেকে import — circular import এড়াতে function-এর ভেতরে
        from core import user_sessions
        from pyrogram import Client as PyroClient

        user_session = await asyncio.wait_for(
            user_sessions.find_one({"user_id": user_id}),
            timeout=5.0
        )

        if not user_session or not user_session.get("sessions"):
            return None

        sessions = user_session["sessions"]
        if not sessions:
            return None

        # প্রথম available session নাও
        session = sessions[0]

        user_client = PyroClient(
            name=f"resolve_{user_id}",
            session_string=session["session_string"],
            in_memory=True,    # ✅ no SQLite file
            no_updates=True,   # ✅ no background task
            workers=1,         # ✅ minimum — শুধু resolve করব
        )
        await asyncio.wait_for(user_client.start(), timeout=8.0)
        return user_client

    except asyncio.TimeoutError:
        LOGGER.warning(f"[Tracker] User client timeout for resolve (user {user_id})")
        return None
    except Exception as e:
        LOGGER.warning(f"[Tracker] Could not create resolve client: {e}")
        return None


async def _resolve_channel_name(
    bot: Client,
    url: str,
    user_id: int | None = None,
) -> str:
    """
    URL থেকে চ্যানেলের নাম বের করো।

    ✅ Strategy (priority order):
      1. Public channel → bot.get_chat() দিয়ে সরাসরি
      2. Private channel → user client দিয়ে get_chat()
         (user সেই channel-এর member, bot নয়)
      3. সব fail → readable fallback

    Args:
        bot:     bot client
        url:     telegram link
        user_id: যে user request করেছে — তার session দিয়ে private resolve
    """
    channel_id, _ = _extract_ids_from_url(url)

    if not channel_id:
        LOGGER.warning(f"[Tracker] Could not parse channel from URL: {url}")
        return url

    is_private = not str(channel_id).startswith("@")

    # ── Public channel → bot দিয়েই পারবে ─────────────────────────────────
    if not is_private:
        try:
            chat  = await bot.get_chat(channel_id)
            title = getattr(chat, "title", None) or channel_id
            LOGGER.info(f"[Tracker] Public channel resolved: {title}")
            return f"{title} ({channel_id})"

        except (ChannelInvalid, ChannelPrivate, PeerIdInvalid, BadRequest):
            clean = str(channel_id).lstrip("@")
            return f"@{clean}"
        except Exception as e:
            LOGGER.warning(f"[Tracker] Public resolve error: {e}")
            return str(channel_id)

    # ── Private channel ────────────────────────────────────────────────────
    raw_id = str(channel_id)  # e.g. "2821790337"

    try:
        from pyrogram.utils import get_channel_id
        cid = get_channel_id(int(raw_id))  # -1002821790337
    except Exception:
        cid = int(raw_id)

    # Step 1: Bot দিয়ে চেষ্টা করো (bot যদি সেই channel-এ থাকে)
    try:
        chat  = await bot.get_chat(cid)
        title = getattr(chat, "title", None) or raw_id
        LOGGER.info(f"[Tracker] Private channel resolved via bot: {title}")
        return f"{title} (Private)"

    except (ChannelInvalid, ChannelPrivate, PeerIdInvalid, BadRequest):
        # Bot নেই → user client দিয়ে চেষ্টা করো
        LOGGER.info(
            f"[Tracker] Bot not in private channel {raw_id} "
            f"→ trying user client"
        )
    except Exception as e:
        LOGGER.warning(f"[Tracker] Bot resolve error for {raw_id}: {e}")

    # Step 2: User client দিয়ে চেষ্টা
    if user_id is None:
        LOGGER.info(
            f"[Tracker] No user_id provided for private resolve → "
            f"fallback to ID"
        )
        return f"Private Channel ({raw_id})"

    user_client = await _get_user_client_for_resolve(user_id)

    if user_client is None:
        LOGGER.info(
            f"[Tracker] No user session for {user_id} → fallback to ID"
        )
        return f"Private Channel ({raw_id})"

    try:
        chat  = await asyncio.wait_for(
            user_client.get_chat(cid),
            timeout=8.0
        )
        title = getattr(chat, "title", None) or raw_id
        LOGGER.info(
            f"[Tracker] Private channel resolved via user client: {title}"
        )
        return f"{title} (Private)"

    except asyncio.TimeoutError:
        LOGGER.warning(
            f"[Tracker] User client get_chat timeout for {raw_id}"
        )
        return f"Private Channel ({raw_id})"

    except (ChannelInvalid, ChannelPrivate, PeerIdInvalid, BadRequest) as e:
        # User-ও সেই channel-এ নেই
        LOGGER.info(
            f"[Tracker] User client also cannot access {raw_id}: "
            f"{type(e).__name__}"
        )
        return f"Private Channel ({raw_id})"

    except Exception as e:
        LOGGER.warning(
            f"[Tracker] User client resolve error for {raw_id}: {e}"
        )
        return f"Private Channel ({raw_id})"

    finally:
        # ✅ সবসময় client বন্ধ করো
        try:
            from .helper import safe_stop_client
            await safe_stop_client(user_client)
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════
# notify_admin_link
# ══════════════════════════════════════════════════════════════════════════

async def notify_admin_link(
    bot: Client,
    user,
    url: str,
    admin_id: int,
    channel_name: str | None = None,
):
    """
    Admin-কে ডাউনলোড রিকোয়েস্ট সম্পর্কে জানায়।

    ✅ user.id দিয়ে private channel name resolve হবে।
    """
    if not admin_id:
        return

    full_name = (
        f"{user.first_name or ''} {user.last_name or ''}".strip()
        or "Unknown"
    )
    username = f"@{user.username}" if user.username else "N/A"
    ltype    = _link_type(url)

    if channel_name is None:
        # ✅ user.id পাস করো — private channel resolve-এ কাজে লাগবে
        channel_name = await _resolve_channel_name(bot, url, user_id=user.id)

    text = (
        "📌 **New Download Request**\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"👤 **Name:** `{full_name}`\n"
        f"🆔 **User ID:** `{user.id}`\n"
        f"📛 **Username:** {username}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🔗 **Link:** `{url}`\n"
        f"📺 **Channel/Group:** `{channel_name}`\n"
        f"🏷 **Type:** {ltype}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🕐 **Time:** `{_now_ist()}`"
    )

    try:
        await bot.send_message(
            chat_id=admin_id,
            text=text,
            parse_mode=ParseMode.MARKDOWN,
        )
    except FloodWait as e:
        await asyncio.sleep(e.value + 2)
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=text,
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as ex:
            LOGGER.error(f"[Tracker] Admin notify failed after FloodWait: {ex}")
    except Exception as e:
        LOGGER.error(f"[Tracker] Admin notify failed: {e}")


# ══════════════════════════════════════════════════════════════════════════
# log_file_to_group
# ══════════════════════════════════════════════════════════════════════════

async def log_file_to_group(
    bot: Client,
    log_group_id: int,
    user,
    url: str,
    file_path: str | None = None,
    file_id: str | None = None,
    media_type: str = "document",
    caption_original: str = "",
    channel_name: str | None = None,
    thumbnail_path: str | None = None,
):
    """
    ডাউনলোড করা ফাইলটি LOG_GROUP_ID-তে পাঠায়।

    ✅ FIXED: channel_name → user client দিয়ে private resolve
    ✅ SIMPLIFIED: video resolution overhead সরানো হয়েছে
                  (performance priority)
    """
    if not log_group_id:
        return

    full_name = (
        f"{user.first_name or ''} {user.last_name or ''}".strip()
        or "Unknown"
    )
    username = f"@{user.username}" if user.username else "N/A"
    ltype    = _link_type(url)

    # ✅ user.id পাস করো
    if channel_name is None:
        channel_name = await _resolve_channel_name(bot, url, user_id=user.id)

    user_footer = (
        "📥 **Downloaded File Log**\n"
        f"👤 **User:** `{full_name}`\n"
        f"🆔 **ID:** `{user.id}`\n"
        f"📛 **Username:** {username}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🔗 **Link:** `{url}`\n"
        f"📺 **Source:** `{channel_name}`\n"
        f"🏷 **Type:** {ltype}\n"
        f"🕐 **Time:** `{_now_ist()}`"
    )

    orig = (caption_original or "").strip()
    if len(orig) > 1000:
        orig = orig[:997] + "..."

    sent_msg = None

    try:
        # ── file_id দিয়ে পাঠানো ───────────────────────────────────────────
        if file_id:
            if media_type == "photo":
                sent_msg = await bot.send_photo(
                    chat_id=log_group_id,
                    photo=file_id,
                    caption=orig,
                )
            elif media_type == "video":
                # ✅ fast path — metadata overhead নেই
                sent_msg = await bot.send_video(
                    chat_id=log_group_id,
                    video=file_id,
                    caption=orig,
                    supports_streaming=True,
                )
            elif media_type == "audio":
                sent_msg = await bot.send_audio(
                    chat_id=log_group_id,
                    audio=file_id,
                    caption=orig,
                )
            else:
                sent_msg = await bot.send_document(
                    chat_id=log_group_id,
                    document=file_id,
                    caption=orig,
                )

        # ── file_path থেকে upload ─────────────────────────────────────────
        elif file_path and os.path.exists(file_path):

            if media_type == "video":
                # Thumbnail — শুধু caller দিলে ব্যবহার করো
                # auto-generate করব না — performance priority
                log_thumb = (
                    thumbnail_path
                    if thumbnail_path and os.path.exists(thumbnail_path)
                    else None
                )

                sent_msg = await bot.send_video(
                    chat_id=log_group_id,
                    video=file_path,
                    thumb=log_thumb,
                    caption=orig,
                    supports_streaming=True,
                )

            elif media_type == "photo":
                sent_msg = await bot.send_photo(
                    chat_id=log_group_id,
                    photo=file_path,
                    caption=orig,
                )

            elif media_type == "audio":
                sent_msg = await bot.send_audio(
                    chat_id=log_group_id,
                    audio=file_path,
                    caption=orig,
                )

            else:
                sent_msg = await bot.send_document(
                    chat_id=log_group_id,
                    document=file_path,
                    caption=orig,
                )

        # ── ফাইল নেই — plain text ─────────────────────────────────────────
        else:
            sent_msg = await bot.send_message(
                chat_id=log_group_id,
                text=orig or "(No content)",
            )

        # ── User info reply ───────────────────────────────────────────────
        if sent_msg:
            try:
                await bot.send_message(
                    chat_id=log_group_id,
                    text=user_footer,
                    reply_to_message_id=sent_msg.id,
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception as e:
                LOGGER.warning(
                    f"[Tracker] Could not send user info reply: {e}"
                )

    except ChatWriteForbidden:
        LOGGER.error(
            "[Tracker] Bot is not admin in the log group or cannot write!"
        )
    except FloodWait as e:
        await asyncio.sleep(e.value + 2)
        LOGGER.warning(f"[Tracker] FloodWait {e.value}s for log group")
    except Exception as e:
        LOGGER.error(f"[Tracker] Log group upload failed: {e}")
