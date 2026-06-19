# Copyright @juktijol
# Channel t.me/juktijol
# ✅ FIXED: in_memory=True + no_updates=True → sqlite3 + OSError fix
# ✅ FIXED: AUTH_KEY_UNREGISTERED → session auto-remove + user notify
# ✅ FIXED: safe_stop_client → no TCPTransport error
# ✅ FIXED: edit_text after error → try-except wrap
# ✅ FIXED: Video aspect ratio (squished) → width/height/duration metadata preserved

import os
import re
import asyncio
from time import time
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode, ChatType
from pyrogram.errors import (
    ChannelInvalid,
    ChannelPrivate,
    PeerIdInvalid,
    FileReferenceExpired,
    BadRequest,
    ChatForwardsRestricted,
    Forbidden,
    AuthKeyUnregistered,
)
from pyleaves import Leaves
from utils import (
    getChatMsgID,
    processMediaGroup,
    get_parsed_msg,
    fileSizeLimit,
    progressArgs,
    send_media_to_saved,
    notify_admin_link,
    log_file_to_group,
    LOGGER,
)
from utils.helper import safe_stop_client
from core import (
    daily_limit,
    prem_plan1,
    prem_plan2,
    prem_plan3,
    user_sessions,
    user_activity_collection,
)
from config import DEVELOPER_USER_ID, LOG_GROUP_ID
from utils.force_sub import check_force_sub

TELEGRAM_LINK_PATTERN = re.compile(
    r"(?:https?://)?(?:t\.me|telegram\.me)/(?:c/)?([a-zA-Z0-9_]+|\d+)/(\d+)(?:/\d+)?"
)

COOLDOWN_SECONDS = 300  # 5 minutes
DB_TIMEOUT = 5.0        # Database operation timeout


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

async def check_and_set_cooldown(user_id: int) -> int:
    try:
        now = datetime.utcnow()
        record = await asyncio.wait_for(
            daily_limit.find_one({"user_id": user_id}),
            timeout=DB_TIMEOUT
        )
        if record:
            last_dl = record.get("last_download")
            if last_dl:
                elapsed = (now - last_dl).total_seconds()
                if elapsed < COOLDOWN_SECONDS:
                    return int(COOLDOWN_SECONDS - elapsed)

        await asyncio.wait_for(
            daily_limit.update_one(
                {"user_id": user_id},
                {"$set": {"last_download": now}, "$inc": {"total_downloads": 1}},
                upsert=True,
            ),
            timeout=DB_TIMEOUT
        )
        return 0
    except asyncio.TimeoutError:
        LOGGER.warning(f"[Cooldown] Database timeout for user {user_id}")
        return 0
    except Exception as e:
        LOGGER.error(f"[Cooldown] Error: {e}")
        return 0


def is_private_link(url: str) -> bool:
    return bool(re.search(r"(?:t\.me|telegram\.me)/c/", url))


# ══════════════════════════════════════════════════════════════════════════════
# ✅ NEW HELPER: ভিডিও মেটাডেটা নিরাপদে extract করার ফাংশন
# এটি width, height, duration সঠিকভাবে বের করে
# squished ভিডিওর মূল সমাধান এখানে
# ══════════════════════════════════════════════════════════════════════════════

def extract_video_metadata(chat_message) -> dict:
    """
    Source message থেকে video metadata extract করে।
    width, height, duration না দিলে Telegram ভুল aspect ratio দেখায়।
    
    Returns:
        dict: width, height, duration কী সহ metadata dict
    """
    metadata = {
        "width": 0,
        "height": 0,
        "duration": 0,
    }

    video = chat_message.video
    if video:
        # ✅ সরাসরি video object থেকে নাও
        metadata["width"]    = getattr(video, "width",    0) or 0
        metadata["height"]   = getattr(video, "height",   0) or 0
        metadata["duration"] = getattr(video, "duration", 0) or 0

    elif chat_message.document:
        # document হিসেবে আসা video-র জন্য
        doc = chat_message.document
        metadata["width"]    = getattr(doc, "width",    0) or 0
        metadata["height"]   = getattr(doc, "height",   0) or 0
        metadata["duration"] = getattr(doc, "duration", 0) or 0

    elif chat_message.animation:
        anim = chat_message.animation
        metadata["width"]    = getattr(anim, "width",    0) or 0
        metadata["height"]   = getattr(anim, "height",   0) or 0
        metadata["duration"] = getattr(anim, "duration", 0) or 0

    LOGGER.debug(
        f"[VideoMeta] Extracted → "
        f"width={metadata['width']}, "
        f"height={metadata['height']}, "
        f"duration={metadata['duration']}s"
    )
    return metadata


# ══════════════════════════════════════════════════════════════════════════════
# ✅ FIX: On AUTH_KEY_UNREGISTERED, remove expired session from MongoDB
# ══════════════════════════════════════════════════════════════════════════════

async def _handle_auth_key_unregistered(user_id: int, session_id: str, bot, message):
    """
    On AUTH_KEY_UNREGISTERED error:
    1. Remove expired session from MongoDB
    2. Tell the user to run /login
    """
    try:
        await asyncio.wait_for(
            user_sessions.update_one(
                {"user_id": user_id},
                {"$pull": {"sessions": {"session_id": session_id}}}
            ),
            timeout=DB_TIMEOUT
        )
        LOGGER.warning(
            f"[AuthKey] Session {session_id} removed for user {user_id} "
            f"(AUTH_KEY_UNREGISTERED)"
        )
    except Exception as e:
        LOGGER.error(f"[AuthKey] Failed to remove expired session: {e}")

    try:
        await bot.send_message(
            chat_id=message.chat.id,
            text=(
                "**❌ Your login session has expired!**\n\n"
                "Telegram removed this session.\n"
                "(Maybe logout on another device or a security check.)\n\n"
                "⚡ Please run **/login** again."
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        LOGGER.warning(f"[AuthKey] Could not notify user {user_id}: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN SETUP
# ══════════════════════════════════════════════════════════════════════════════

def setup_autolink_handler(app: Client):

    async def is_premium_user(user_id: int) -> bool:
        try:
            current_time = datetime.utcnow()
            for plan_collection in [prem_plan1, prem_plan2, prem_plan3]:
                plan = await asyncio.wait_for(
                    plan_collection.find_one({"user_id": user_id}),
                    timeout=DB_TIMEOUT
                )
                if plan and plan.get("expiry_date", current_time) > current_time:
                    return True
            return False
        except Exception as e:
            LOGGER.warning(f"[Premium Check] Error for user {user_id}: {e}")
            return False

    async def get_user_client(user_id: int, session_id: str):
        """
        ✅ FIXED: in_memory=True + no_updates=True
        - No .session file on disk → no sqlite3 error
        - No handle_updates() task → no TCPTransport OSError
        """
        try:
            user_session = await asyncio.wait_for(
                user_sessions.find_one({"user_id": user_id}),
                timeout=DB_TIMEOUT
            )
            if not user_session or not user_session.get("sessions"):
                return None

            session = next(
                (s for s in user_session["sessions"] if s["session_id"] == session_id), None
            )
            if not session:
                return None

            try:
                user_client = Client(
                    name=f"user_session_{user_id}_{session_id}",
                    session_string=session["session_string"],
                    in_memory=True,   # ✅ no SQLite file on disk
                    no_updates=True,  # ✅ no handle_updates() task
                    workers=4,
                )
                await asyncio.wait_for(user_client.start(), timeout=10.0)
                return user_client
            except Exception as e:
                LOGGER.error(f"Failed to initialize user client for user {user_id}: {e}")
                return None
        except asyncio.TimeoutError:
            LOGGER.error(f"[UserClient] Database timeout for user {user_id}")
            return None
        except Exception as e:
            LOGGER.error(f"[UserClient] Error getting user client: {e}")
            return None

    # ── PATH 2: USER SESSION FALLBACK FOR PUBLIC PROTECTED CONTENT ────────────

    async def _public_fallback_via_user_session(
        bot: Client,
        message: Message,
        url: str,
        channel_username,
        msg_id: int,
        ack_msg,
        user: object,
        is_premium: bool,
    ):
        user_id = message.from_user.id

        try:
            user_session = await asyncio.wait_for(
                user_sessions.find_one({"user_id": user_id}),
                timeout=DB_TIMEOUT
            )
        except asyncio.TimeoutError:
            try:
                await ack_msg.edit_text(
                    "**❌ Database timeout! Please try again.**",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception:
                pass
            return
        except Exception as e:
            try:
                await ack_msg.edit_text(
                    f"**❌ Error checking session: {str(e)[:80]}**",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception:
                pass
            return

        if not user_session or not user_session.get("sessions"):
            try:
                await ack_msg.edit_text(
                    "**🔒 Content protection is enabled in this channel.**\n\n"
                    "❌ Direct bot delivery is not available for this file.\n\n"
                    "✅ **Quick fix:** Use /login to connect your Telegram account.\n"
                    "Then resend this link to save it to your Saved Messages.",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception:
                pass
            return

        sessions = user_session.get("sessions", [])

        if len(sessions) > 1:
            buttons = []
            for i in range(0, len(sessions), 2):
                row = []
                for sess in sessions[i:i + 2]:
                    row.append(InlineKeyboardButton(
                        sess["account_name"],
                        callback_data=f"auto_pvt_select_{sess['session_id']}|{url}"
                    ))
                buttons.append(row)
            buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="auto_pvt_cancel")])
            try:
                await ack_msg.edit_text(
                    "**🔒 Content protection detected!**\n\n"
                    "📤 Choose the account for this download.\n"
                    "__(The file will be saved to that account's Saved Messages.)__",
                    reply_markup=InlineKeyboardMarkup(buttons),
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception:
                pass
            return

        session_id = sessions[0]["session_id"]
        try:
            await ack_msg.delete()
        except Exception:
            pass
        await _process_protected_public_download(
            bot, message, session_id, url, channel_username, msg_id
        )

    async def _process_protected_public_download(
        bot: Client,
        message: Message,
        session_id: str,
        url: str,
        channel_username,
        msg_id: int,
    ):
        user_id = message.from_user.id
        user    = message.from_user

        user_client = await get_user_client(user_id, session_id)
        if user_client is None:
            await message.reply_text(
                "**❌ Failed to initialize user client! Please try /login again.**",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        processing_msg = await message.reply_text(
            "**🔒 Protected content detected!\n"
            "📥 Downloading via your account...**\n"
            "__(File will be sent to your Saved Messages)__",
            parse_mode=ParseMode.MARKDOWN
        )

        try:
            try:
                chat_message = await asyncio.wait_for(
                    user_client.get_messages(chat_id=channel_username, message_ids=msg_id),
                    timeout=15.0
                )
            except asyncio.TimeoutError:
                try:
                    await processing_msg.edit_text(
                        "**❌ Timeout fetching message. Please try again.**",
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception:
                    pass
                return

            if not chat_message:
                try:
                    await processing_msg.edit_text(
                        "**❌ Message not found!**\n"
                        "Make sure your logged-in account is a member of this channel.",
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception:
                    pass
                return

            if chat_message.document or chat_message.video or chat_message.audio:
                file_size = (
                    chat_message.document.file_size if chat_message.document else
                    chat_message.video.file_size    if chat_message.video    else
                    chat_message.audio.file_size
                )
                is_premium = await is_premium_user(user_id)
                if not await fileSizeLimit(file_size, message, "download", is_premium):
                    try:
                        await processing_msg.delete()
                    except Exception:
                        pass
                    return

            parsed_caption = await get_parsed_msg(
                chat_message.caption or "", chat_message.caption_entities
            )
            parsed_text = await get_parsed_msg(
                chat_message.text or "", chat_message.entities
            )

            if chat_message.media_group_id:
                try:
                    await processing_msg.delete()
                except Exception:
                    pass
                if not await processMediaGroup(
                    chat_message, bot, message,
                    user_client=user_client,
                    log_group_id=LOG_GROUP_ID,
                    log_user=user,
                    log_url=url
                ):
                    await message.reply_text(
                        "**❌ Could not extract media from the media group.**",
                        parse_mode=ParseMode.MARKDOWN
                    )
                return

            elif chat_message.media:
                start_time = time()
                try:
                    await processing_msg.edit_text(
                        "**📥 Downloading...**",
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception:
                    pass

                media_path = await chat_message.download(
                    progress=Leaves.progress_for_pyrogram,
                    progress_args=progressArgs("📥 Downloading", processing_msg, start_time)
                )

                try:
                    user_data = await asyncio.wait_for(
                        user_activity_collection.find_one({"user_id": user_id}),
                        timeout=DB_TIMEOUT
                    )
                    thumbnail_path = user_data.get("thumbnail_path") if user_data else None
                except Exception:
                    thumbnail_path = None

                media_type = (
                    "photo"    if chat_message.photo    else
                    "video"    if chat_message.video    else
                    "audio"    if chat_message.audio    else
                    "document"
                )

                # ✅ FIX: video metadata extract করো — squish ঠিক করার মূল জায়গা
                video_metadata = {}
                if media_type == "video":
                    video_metadata = extract_video_metadata(chat_message)
                    LOGGER.info(
                        f"[ProtectedPublic] Video metadata: "
                        f"w={video_metadata['width']}, "
                        f"h={video_metadata['height']}, "
                        f"dur={video_metadata['duration']}s"
                    )

                try:
                    await send_media_to_saved(
                        user_client=user_client,
                        bot=bot,
                        message=message,
                        media_path=media_path,
                        media_type=media_type,
                        caption=parsed_caption,
                        progress_message=processing_msg,
                        start_time=start_time,
                        thumbnail_path=thumbnail_path,
                        # ✅ FIX: metadata পাস করো যাতে aspect ratio ঠিক থাকে
                        width=video_metadata.get("width", 0),
                        height=video_metadata.get("height", 0),
                        duration=video_metadata.get("duration", 0),
                    )
                    if LOG_GROUP_ID and os.path.exists(media_path):
                        try:
                            await log_file_to_group(
                                bot=bot,
                                log_group_id=LOG_GROUP_ID,
                                user=user,
                                url=url,
                                file_path=media_path,
                                media_type=media_type,
                                caption_original=parsed_caption,
                                channel_name=None,
                                thumbnail_path=thumbnail_path,
                            )
                        except Exception as e:
                            LOGGER.warning(f"[Tracker] Protected public log error: {e}")
                except AuthKeyUnregistered:
                    await _handle_auth_key_unregistered(user_id, session_id, bot, message)
                except Exception as e:
                    try:
                        await processing_msg.edit_text(
                            f"**❌ Upload error: {str(e)[:80]}**",
                            parse_mode=ParseMode.MARKDOWN
                        )
                    except Exception:
                        pass
                    LOGGER.error(f"Protected public upload error: {e}")
                finally:
                    if os.path.exists(media_path):
                        os.remove(media_path)

            elif chat_message.text or chat_message.caption:
                try:
                    await processing_msg.delete()
                except Exception:
                    pass
                await message.reply_text(
                    parsed_text or parsed_caption,
                    parse_mode=ParseMode.MARKDOWN
                )

            else:
                try:
                    await processing_msg.edit_text(
                        "**❌ No media or text found in this message.**",
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception:
                    pass

            LOGGER.info(
                f"Protected public DL (via user session): msg {msg_id} "
                f"from {channel_username} for user {user_id}"
            )

        except (PeerIdInvalid, BadRequest):
            try:
                await processing_msg.edit_text(
                    "**❌ Download Failed!**\n\n"
                    "━━━━━━━━━━━━━━━━━━\n"
                    "The account you logged in with is **not a member**\n"
                    "of this channel.\n\n"
                    "**How to fix it:**\n"
                    "1️⃣ Join the channel with your Telegram account\n"
                    "2️⃣ Then paste the link again\n\n"
                    "__Or use /logout → /login with the correct account.__",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception:
                pass
        except Exception as e:
            try:
                await processing_msg.edit_text(
                    f"**❌ Error: {str(e)[:100]}**",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception:
                pass
            LOGGER.error(f"Protected public DL failed for user {user_id}: {e}")
        finally:
            await safe_stop_client(user_client)

    # ── PATH 1 + PATH 2: PUBLIC LINK HANDLER ─────────────────────────────────

    async def handle_public_link(client: Client, message: Message, url: str):
        user_id    = message.from_user.id
        chat_id    = message.chat.id
        user       = message.from_user
        is_premium = await is_premium_user(user_id)

        ack_msg = await message.reply_text(
            "**🔗 Link received! Processing your request, please wait...**",
            parse_mode=ParseMode.MARKDOWN
        )

        try:
            await notify_admin_link(
                bot=client,
                user=user,
                url=url,
                admin_id=DEVELOPER_USER_ID,
            )
        except Exception as e:
            LOGGER.warning(f"[Tracker] Admin notify error: {e}")

        if not is_premium:
            remaining = await check_and_set_cooldown(user_id)
            if remaining > 0:
                mins, secs = divmod(remaining, 60)
                try:
                    await ack_msg.edit_text(
                        f"**⏳ Please wait {mins}m {secs}s before your next download.**\n"
                        f"__Upgrade to premium for instant unlimited downloads: /plans__",
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception:
                    pass
                return
        else:
            try:
                await asyncio.wait_for(
                    daily_limit.update_one(
                        {"user_id": user_id},
                        {"$inc": {"total_downloads": 1}},
                        upsert=True,
                    ),
                    timeout=DB_TIMEOUT
                )
            except Exception as e:
                LOGGER.warning(f"[Download] Could not update download count: {e}")

        match = re.match(
            r"(?:https?://)?(?:t\.me|telegram\.me)/([a-zA-Z0-9_]+)/(?:\d+/)?(\d+)", url
        )
        if not match:
            try:
                await ack_msg.edit_text(
                    "**❌ Invalid public Telegram link!**",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception:
                pass
            return

        channel_username = f"@{match.group(1)}"
        msg_id = int(match.group(2))

        try:
            await ack_msg.edit_text(
                "**🔍 Link detected! Processing... ⏳**",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception:
            pass

        channel_name = channel_username
        try:
            chat = await client.get_chat(channel_username)
            if chat.type not in [ChatType.CHANNEL, ChatType.SUPERGROUP]:
                try:
                    await ack_msg.edit_text(
                        "**❌ This command only supports channels or supergroups!**",
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception:
                    pass
                return
            channel_name = f"{chat.title} ({channel_username})"
        except (ChannelInvalid, PeerIdInvalid):
            try:
                await ack_msg.edit_text(
                    "**❌ Invalid channel or group!**",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception:
                pass
            return
        except ChannelPrivate:
            try:
                await ack_msg.edit_text(
                    "**🔒 This channel is private! Send a private link (t.me/c/...) instead.**",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception:
                pass
            return
        except Exception as e:
            LOGGER.warning(f"Could not fetch chat name: {e}")

        try:
            source_message = await client.get_messages(channel_username, msg_id)
        except Exception as e:
            LOGGER.warning(f"[PublicLink] get_messages failed ({type(e).__name__}): {e}")
            try:
                await ack_msg.edit_text(
                    "**⚠️ The bot could not fetch this channel message.**\n"
                    "**🔄 Trying alternate method via user session...**",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception:
                pass
            await _public_fallback_via_user_session(
                client, message, url, channel_username, msg_id,
                ack_msg, user, is_premium
            )
            return

        if not source_message:
            try:
                await ack_msg.edit_text(
                    "**❌ Message not found or deleted!**",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception:
                pass
            return

        sent_successfully = False
        sent_file_id      = None
        sent_media_type   = "document"

        try:
            if source_message.media_group_id:
                ok = await processMediaGroup(
                    source_message,
                    client,
                    message,
                    log_group_id=LOG_GROUP_ID,
                    log_user=user,
                    log_url=url,
                )
                if ok:
                    sent_successfully = True
                    if is_premium:
                        reminder = "**✅ Content received! Unlimited downloads as premium user! 🚀**"
                    else:
                        reminder = (
                            "**✅ Content received!**\n\n"
                            "__Next free download available in 5 minutes.__\n"
                            "💥 Upgrade for instant unlimited access: /plans"
                        )
                    try:
                        await ack_msg.edit_text(reminder, parse_mode=ParseMode.MARKDOWN)
                    except Exception:
                        pass
                    return

            elif source_message.video:
                # ✅ FIX: source video থেকে সঠিক metadata নাও
                video_meta = extract_video_metadata(source_message)

                try:
                    user_data = await asyncio.wait_for(
                        user_activity_collection.find_one({"user_id": user_id}),
                        timeout=DB_TIMEOUT
                    )
                    thumbnail_file_id = user_data.get("thumbnail_file_id") if user_data else None
                except Exception:
                    thumbnail_file_id = None

                try:
                    # ✅ FIX: width, height, duration পাস করো
                    sent = await client.send_video(
                        chat_id=chat_id,
                        video=source_message.video.file_id,
                        caption=source_message.caption or "",
                        thumb=thumbnail_file_id if thumbnail_file_id else None,
                        # ✅ এই তিনটি parameter না দিলে ভিডিও squished হয়
                        width=video_meta["width"],
                        height=video_meta["height"],
                        duration=video_meta["duration"],
                        supports_streaming=True,  # ✅ streaming support চালু রাখো
                    )
                    if sent is not None:
                        sent_file_id      = source_message.video.file_id
                        sent_media_type   = "video"
                        sent_successfully = True
                    else:
                        sent_successfully = False

                except FileReferenceExpired:
                    try:
                        # ✅ FIX: retry তেও metadata পাস করো
                        sent = await client.send_video(
                            chat_id=chat_id,
                            video=source_message.video.file_id,
                            caption=source_message.caption or "",
                            width=video_meta["width"],
                            height=video_meta["height"],
                            duration=video_meta["duration"],
                            supports_streaming=True,
                        )
                        if sent is not None:
                            sent_file_id      = source_message.video.file_id
                            sent_media_type   = "video"
                            sent_successfully = True
                        else:
                            sent_successfully = False
                    except (ChatForwardsRestricted, Forbidden, BadRequest):
                        sent_successfully = False
                    except Exception as e:
                        LOGGER.warning(f"[PublicLink] Path 1 video retry failed: {e}")
                        sent_successfully = False

                except (ChatForwardsRestricted, Forbidden):
                    sent_successfully = False
                except BadRequest:
                    sent_successfully = False
                except Exception as e:
                    LOGGER.warning(f"[PublicLink] Path 1 video error: {e}")
                    sent_successfully = False

            elif source_message.photo:
                try:
                    sent = await client.copy_message(
                        chat_id=chat_id,
                        from_chat_id=channel_username,
                        message_id=msg_id
                    )
                    if sent is not None:
                        sent_file_id      = source_message.photo.file_id
                        sent_media_type   = "photo"
                        sent_successfully = True
                    else:
                        sent_successfully = False
                except (ChatForwardsRestricted, Forbidden, BadRequest):
                    sent_successfully = False
                except Exception as e:
                    LOGGER.warning(f"[PublicLink] Path 1 photo error: {e}")
                    sent_successfully = False

            elif source_message.audio:
                try:
                    sent = await client.copy_message(
                        chat_id=chat_id,
                        from_chat_id=channel_username,
                        message_id=msg_id
                    )
                    if sent is not None:
                        sent_file_id      = source_message.audio.file_id
                        sent_media_type   = "audio"
                        sent_successfully = True
                    else:
                        sent_successfully = False
                except (ChatForwardsRestricted, Forbidden, BadRequest):
                    sent_successfully = False
                except Exception as e:
                    LOGGER.warning(f"[PublicLink] Path 1 audio error: {e}")
                    sent_successfully = False

            elif source_message.document:
                try:
                    sent = await client.copy_message(
                        chat_id=chat_id,
                        from_chat_id=channel_username,
                        message_id=msg_id
                    )
                    if sent is not None:
                        sent_file_id      = source_message.document.file_id
                        sent_media_type   = "document"
                        sent_successfully = True
                    else:
                        sent_successfully = False
                except (ChatForwardsRestricted, Forbidden, BadRequest):
                    sent_successfully = False
                except Exception as e:
                    LOGGER.warning(f"[PublicLink] Path 1 document error: {e}")
                    sent_successfully = False

            else:
                try:
                    sent = await client.copy_message(
                        chat_id=chat_id,
                        from_chat_id=channel_username,
                        message_id=msg_id
                    )
                    sent_successfully = sent is not None
                except (ChatForwardsRestricted, Forbidden, BadRequest):
                    sent_successfully = False
                except Exception as e:
                    LOGGER.warning(f"[PublicLink] Path 1 other error: {e}")
                    sent_successfully = False

        except Exception as outer_e:
            LOGGER.error(f"[PublicLink] Unexpected outer error in Path 1: {outer_e}")
            sent_successfully = False

        if sent_successfully:
            if LOG_GROUP_ID and sent_file_id:
                try:
                    await log_file_to_group(
                        bot=client,
                        log_group_id=LOG_GROUP_ID,
                        user=user,
                        url=url,
                        file_id=sent_file_id,
                        media_type=sent_media_type,
                        caption_original=source_message.caption or "",
                        channel_name=channel_name,
                    )
                except Exception as e:
                    LOGGER.warning(f"[Tracker] Log group error: {e}")

            if is_premium:
                reminder = "**✅ Content received! Unlimited downloads as premium user! 🚀**"
            else:
                reminder = (
                    "**✅ Content received!**\n\n"
                    "__Next free download available in 5 minutes.__\n"
                    "💥 Upgrade for instant unlimited access: /plans"
                )
            try:
                await ack_msg.edit_text(reminder, parse_mode=ParseMode.MARKDOWN)
            except Exception:
                pass
            return

        LOGGER.info(
            f"[PublicLink] Path 1 failed → Path 2 (user session fallback) | "
            f"user={user_id}, channel={channel_username}, msg={msg_id}"
        )
        try:
            await ack_msg.edit_text(
                "**⚠️ Direct bot delivery is unavailable for this content.**\n"
                "**🔄 Trying alternate method...**",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception:
            pass
        await _public_fallback_via_user_session(
            client, message, url, channel_username, msg_id,
            ack_msg, user, is_premium
        )

    # ── PRIVATE LINK HANDLER ──────────────────────────────────────────────────

    async def handle_private_link(client: Client, message: Message, url: str):
        user_id    = message.from_user.id
        user       = message.from_user
        is_premium = await is_premium_user(user_id)

        ack_msg = await message.reply_text(
            "**🔒 Private link received! Processing your request, please wait...**",
            parse_mode=ParseMode.MARKDOWN
        )

        try:
            await notify_admin_link(
                bot=client,
                user=user,
                url=url,
                admin_id=DEVELOPER_USER_ID,
            )
        except Exception as e:
            LOGGER.warning(f"[Tracker] Admin notify error: {e}")

        try:
            user_session = await asyncio.wait_for(
                user_sessions.find_one({"user_id": user_id}),
                timeout=DB_TIMEOUT
            )
        except asyncio.TimeoutError:
            LOGGER.error(f"[Autolink] Database timeout fetching sessions for user {user_id}")
            try:
                await ack_msg.edit_text(
                    "**❌ Database connection timeout!**\n\nPlease try again in a moment.",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception:
                pass
            return
        except Exception as e:
            LOGGER.error(f"[Autolink] Database error fetching sessions: {e}")
            try:
                await ack_msg.edit_text(
                    f"**❌ Error checking sessions: {str(e)[:80]}**",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception:
                pass
            return

        if not user_session or not user_session.get("sessions"):
            try:
                await ack_msg.edit_text(
                    "**🔒 Private Link Detected!**\n\n"
                    "━━━━━━━━━━━━━━━━━━\n"
                    "❌ You are **not logged in** yet.\n\n"
                    "**⚠️ Before logging in:**\n"
                    "Make sure you log in with the Telegram account\n"
                    "that is **already a member** of that channel/group.\n\n"
                    "👉 Use /login to connect your account.",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception:
                pass
            return

        if not is_premium:
            remaining = await check_and_set_cooldown(user_id)
            if remaining > 0:
                mins, secs = divmod(remaining, 60)
                try:
                    await ack_msg.edit_text(
                        f"**⏳ Please wait {mins}m {secs}s before your next download.**\n"
                        f"__Upgrade to premium for instant unlimited downloads: /plans__",
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception:
                    pass
                return
        else:
            try:
                await asyncio.wait_for(
                    daily_limit.update_one(
                        {"user_id": user_id},
                        {"$inc": {"total_downloads": 1}},
                        upsert=True,
                    ),
                    timeout=DB_TIMEOUT
                )
            except Exception as e:
                LOGGER.warning(f"[Download] Could not update download count: {e}")

        sessions = user_session.get("sessions", [])

        if len(sessions) > 1:
            buttons = []
            for i in range(0, len(sessions), 2):
                row = []
                for sess in sessions[i:i + 2]:
                    row.append(InlineKeyboardButton(
                        sess["account_name"],
                        callback_data=f"auto_pvt_select_{sess['session_id']}|{url}"
                    ))
                buttons.append(row)
            buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="auto_pvt_cancel")])
            try:
                await ack_msg.edit_text(
                    "**🔒 Private link detected!\n\n"
                    "📤 Which account do you want to download with?\n"
                    "__(The file will be sent to that account's Saved Messages)__**",
                    reply_markup=InlineKeyboardMarkup(buttons),
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception:
                pass
            return

        session_id = sessions[0]["session_id"]
        try:
            await ack_msg.delete()
        except Exception:
            pass
        await process_private_download(client, message, session_id, url)

    async def process_private_download(bot: Client, message: Message, session_id: str, url: str):
        user_id = message.from_user.id
        chat_id = message.chat.id
        user    = message.from_user

        user_client = await get_user_client(user_id, session_id)
        if user_client is None:
            await message.reply_text(
                "**❌ Failed to initialize user client! Please try /login again.**",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        processing_msg = await message.reply_text(
            "**🔒 Private link detected! Downloading... ⏳**\n"
            "__(File will be sent to your Saved Messages)__",
            parse_mode=ParseMode.MARKDOWN
        )

        try:
            url_clean = url.split("?")[0]
            pvt_chat_id, msg_id = getChatMsgID(url_clean)

            try:
                chat_message = await asyncio.wait_for(
                    user_client.get_messages(chat_id=pvt_chat_id, message_ids=msg_id),
                    timeout=10.0
                )
            except asyncio.TimeoutError:
                try:
                    await processing_msg.edit_text(
                        "**❌ Timeout fetching message from Telegram!**\n\nPlease try again.",
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception:
                    pass
                return

            if not chat_message:
                try:
                    await processing_msg.edit_text(
                        "**❌ Message not found!**",
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception:
                    pass
                return

            if chat_message.document or chat_message.video or chat_message.audio:
                file_size = (
                    chat_message.document.file_size if chat_message.document else
                    chat_message.video.file_size    if chat_message.video    else
                    chat_message.audio.file_size
                )
                is_premium = await is_premium_user(user_id)
                if not await fileSizeLimit(file_size, message, "download", is_premium):
                    try:
                        await processing_msg.delete()
                    except Exception:
                        pass
                    return

            parsed_caption = await get_parsed_msg(
                chat_message.caption or "", chat_message.caption_entities
            )
            parsed_text = await get_parsed_msg(
                chat_message.text or "", chat_message.entities
            )

            if chat_message.media_group_id:
                try:
                    await processing_msg.delete()
                except Exception:
                    pass
                if not await processMediaGroup(
                    chat_message, bot, message, user_client=user_client,
                    log_group_id=LOG_GROUP_ID, log_user=user, log_url=url
                ):
                    await message.reply_text(
                        "**❌ Could not extract media from the media group.**",
                        parse_mode=ParseMode.MARKDOWN
                    )
                return

            elif chat_message.media:
                start_time = time()
                try:
                    await processing_msg.edit_text(
                        "**📥 Downloading...**",
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception:
                    pass

                media_path = await chat_message.download(
                    progress=Leaves.progress_for_pyrogram,
                    progress_args=progressArgs("📥 Downloading", processing_msg, start_time)
                )

                try:
                    user_data = await asyncio.wait_for(
                        user_activity_collection.find_one({"user_id": user_id}),
                        timeout=DB_TIMEOUT
                    )
                    thumbnail_path = user_data.get("thumbnail_path") if user_data else None
                except Exception:
                    thumbnail_path = None

                media_type = (
                    "photo"    if chat_message.photo    else
                    "video"    if chat_message.video    else
                    "audio"    if chat_message.audio    else
                    "document"
                )

                # ✅ FIX: private link ভিডিওর জন্যও metadata extract করো
                video_metadata = {}
                if media_type == "video":
                    video_metadata = extract_video_metadata(chat_message)
                    LOGGER.info(
                        f"[PrivateLink] Video metadata: "
                        f"w={video_metadata['width']}, "
                        f"h={video_metadata['height']}, "
                        f"dur={video_metadata['duration']}s"
                    )

                try:
                    await send_media_to_saved(
                        user_client=user_client,
                        bot=bot,
                        message=message,
                        media_path=media_path,
                        media_type=media_type,
                        caption=parsed_caption,
                        progress_message=processing_msg,
                        start_time=start_time,
                        thumbnail_path=thumbnail_path,
                        # ✅ FIX: metadata পাস করো যাতে aspect ratio ঠিক থাকে
                        width=video_metadata.get("width", 0),
                        height=video_metadata.get("height", 0),
                        duration=video_metadata.get("duration", 0),
                    )
                    if LOG_GROUP_ID and os.path.exists(media_path):
                        try:
                            await log_file_to_group(
                                bot=bot,
                                log_group_id=LOG_GROUP_ID,
                                user=user,
                                url=url,
                                file_path=media_path,
                                media_type=media_type,
                                caption_original=parsed_caption,
                                channel_name=None,
                                thumbnail_path=thumbnail_path,
                            )
                        except Exception as e:
                            LOGGER.warning(f"[Tracker] Private log group error: {e}")
                except AuthKeyUnregistered:
                    await _handle_auth_key_unregistered(user_id, session_id, bot, message)
                except Exception as e:
                    try:
                        await processing_msg.edit_text(
                            f"**❌ Upload error: {str(e)[:80]}**",
                            parse_mode=ParseMode.MARKDOWN
                        )
                    except Exception:
                        pass
                    LOGGER.error(f"Upload error: {e}")
                finally:
                    if os.path.exists(media_path):
                        os.remove(media_path)

            elif chat_message.text or chat_message.caption:
                try:
                    await processing_msg.delete()
                except Exception:
                    pass
                try:
                    await message.reply_text(
                        parsed_text or parsed_caption,
                        parse_mode=ParseMode.MARKDOWN
                    )
                    if LOG_GROUP_ID:
                        try:
                            await log_file_to_group(
                                bot=bot,
                                log_group_id=LOG_GROUP_ID,
                                user=user,
                                url=url,
                                caption_original=parsed_text or parsed_caption,
                                channel_name=None,
                            )
                        except Exception as e:
                            LOGGER.warning(f"[Tracker] Private log group error: {e}")
                except Exception as e:
                    LOGGER.error(f"[Autolink] Text delivery error for user {user_id}: {e}")

            else:
                try:
                    await processing_msg.edit_text(
                        "**❌ No media or text found in this link.**",
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception:
                    pass

            LOGGER.info(
                f"Auto private DL: msg {msg_id} from {pvt_chat_id} for user {user_id}"
            )

        except (PeerIdInvalid, BadRequest):
            try:
                await processing_msg.edit_text(
                    "**❌ Download Failed!**\n\n"
                    "━━━━━━━━━━━━━━━━━━\n"
                    "The account you logged in with is **not a member**\n"
                    "of that channel or group.\n\n"
                    "**How to fix it:**\n"
                    "1️⃣ Join that channel/group with your Telegram account\n"
                    "2️⃣ Then paste the link again\n\n"
                    "__Or use /logout → /login with the correct account.__",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception:
                pass
        except Exception as e:
            try:
                await processing_msg.edit_text(
                    f"**❌ Error: {str(e)[:100]}**",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception:
                pass
            LOGGER.error(f"Auto private DL failed for user {user_id}: {e}")
        finally:
            await safe_stop_client(user_client)

    # ── CALLBACKS ─────────────────────────────────────────────────────────────

    @app.on_callback_query(filters.regex(r"^auto_pvt_(select_|cancel)"))
    async def auto_pvt_callback(client, callback_query):
        data    = callback_query.data
        user_id = callback_query.from_user.id

        if data == "auto_pvt_cancel":
            try:
                await callback_query.message.edit_text(
                    "**❌ Download cancelled.**",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception:
                pass
            return

        if data.startswith("auto_pvt_select_"):
            payload = data[len("auto_pvt_select_"):]
            parts = payload.split("|", 1)
            if len(parts) != 2:
                try:
                    await callback_query.message.edit_text(
                        "**❌ Invalid session data. Please try again.**",
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception:
                    pass
                return
            session_id, url = parts

            if not is_private_link(url):
                match = re.match(
                    r"(?:https?://)?(?:t\.me|telegram\.me)/([a-zA-Z0-9_]+)/(?:\d+/)?(\d+)", url
                )
                if match:
                    channel_username = f"@{match.group(1)}"
                    msg_id = int(match.group(2))
                    try:
                        await callback_query.message.delete()
                    except Exception:
                        pass
                    await _process_protected_public_download(
                        client, callback_query.message, session_id, url,
                        channel_username, msg_id
                    )
                    return

            try:
                await callback_query.message.delete()
            except Exception:
                pass
            await process_private_download(client, callback_query.message, session_id, url)

    # ── LINK DETECTOR ─────────────────────────────────────────────────────────

    @app.on_message(
        filters.text &
        (filters.private | filters.group) &
        filters.create(lambda _, __, msg: bool(
            msg.text and TELEGRAM_LINK_PATTERN.search(msg.text)
        )),
        group=1
    )
    async def auto_link_detector(client: Client, message: Message):
        if message.text and message.text.startswith(("/", "!", ".", "#", ",")):
            return

        if message.chat.type == ChatType.PRIVATE and message.from_user:
            if not await check_force_sub(client, message.from_user.id):
                return

        import sys
        _pbatch = sys.modules.get("plugins.pbatch")
        if _pbatch is not None:
            _chat_id = message.chat.id
            _user_id = message.from_user.id if message.from_user else None
            _state   = _pbatch.batch_data.get(_chat_id)
            if (
                _state
                and _state.get("user_id") == _user_id
                and _state.get("stage") in ("await_url", "await_count")
            ):
                return

        text  = message.text or ""
        match = TELEGRAM_LINK_PATTERN.search(text)
        if not match:
            return

        url = text[match.start():match.end()]
        if not url.startswith("http"):
            url = "https://" + url

        LOGGER.info(f"Auto link detected from user {message.from_user.id}: {url}")

        if is_private_link(url):
            await handle_private_link(client, message, url)
        else:
            await handle_public_link(client, message, url)
