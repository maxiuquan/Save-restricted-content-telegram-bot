# ytdl_yt.py — /yt + /dl Command Handler (Pyrofork)
#
# Features:
#   • /yt <link>  →  YouTube লিংক দিলে Video/Audio choice দেখাবে
#   • /dl <link or name>  →  Video/Audio choice দেখাবে
#   • Choice এর পর video হলে quality picker, audio হলে quality picker দেখাবে।
#   • সব pending state yt.py-এর pending_downloads dict-এ store হয়,
#     তাই do_video_download / do_audio_download সেখান থেকেই কাজ করে।
#   • Auto-detect (command ছাড়া link পাঠালে) সম্পূর্ণ বন্ধ।
#
# __init__.py-তে import এবং setup:
#   from .ytdl_yt import setup_ytdl_yt_handler
#   setup_ytdl_yt_handler(app)

import asyncio
import os

from pyrogram import Client, filters
from pyrogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from pyrogram.enums import ParseMode

from .ythelpers import (
    TEMP_DIR, MAX_DURATION, executor,
    VIDEO_QUALITY_OPTIONS, AUDIO_QUALITY_OPTIONS,
    LOGGER,
    generate_token, youtube_parser, extract_video_id,
    fetch_thumbnail, fetch_metadata_from_url,
    search_youtube_url, search_youtube_metadata,
    extract_meta_fields, build_user_info,
    _get_available_formats,
    build_video_quality_markup, build_audio_quality_markup,
    resolve_video_qualities, resolve_audio_qualities,
    format_views, format_dur,
    clean_download, clean_temp_files,
)

# yt.py-এর shared pending dict import করো — একই state ব্যবহার হবে
from .yt import (
    pending_downloads,
    do_video_download,
    do_audio_download,
    _build_split_prompt_markup,
    SPLIT_PROMPT_TEXT,
)

# ─── Helpers ──────────────────────────────────────────────────────────────────

def _build_type_choice_markup(token: str) -> InlineKeyboardMarkup:
    """Video নাকি Audio — এই দুটো বাটন দেখাবে।"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎬 Video",  callback_data=f"YDLV|{token}"),
            InlineKeyboardButton("🎵 Audio",  callback_data=f"YDLA|{token}"),
        ],
        [
            InlineKeyboardButton("❌ Cancel", callback_data=f"YX|{token}"),
        ],
    ])


# ─── Core: show type-choice prompt ───────────────────────────────────────────

async def _show_type_choice(client: Client, message: Message, query: str):
    """
    YouTube URL বা search query দিলে এই function:
    1. URL resolve করে
    2. Metadata fetch করে
    3. Thumbnail সহ Video/Audio choice দেখায়
    """
    chat_id   = message.chat.id
    user_info = build_user_info(message)

    status = await message.reply_text("**🔍 Fetching YouTube Info...**")
    if not status:
        return

    # URL resolve
    video_url = youtube_parser(query)
    if not video_url:
        await status.edit_text("**🔍 Searching YouTube...**")
        video_url = await search_youtube_url(query)
        if not video_url:
            await status.edit_text(
                "**❌ No results found. Please check the link or try a different query.**"
            )
            return

    await status.edit_text("**📡 Fetching Video Info...**")
    meta = await fetch_metadata_from_url(video_url)
    if not meta:
        meta = await search_youtube_metadata(query)
    if not meta:
        await status.edit_text("**❌ Could not fetch video info. Try again.**")
        return

    title, channel, duration, view_count, safe_title = extract_meta_fields(meta)
    video_id = extract_video_id(video_url)

    token    = generate_token(message.from_user.id)
    temp_dir = TEMP_DIR / token
    temp_dir.mkdir(exist_ok=True)
    thumb_out = str(temp_dir / "thumb.jpg")

    await status.edit_text("**🖼️ Fetching Thumbnail...**")
    thumb_path = await fetch_thumbnail(video_id, thumb_out)

    # State save করো — video/audio উভয়ের জন্য base data
    pending_downloads[token] = {
        "url":        video_url,
        "meta":       meta,
        "user_id":    message.from_user.id,
        "user_info":  user_info,
        "chat_id":    chat_id,
        "msg_id":     status.id,
        "thumb_path": thumb_path,
    }

    caption = (
        f"🔗 **Title:** `{title}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"👁️‍🗨️ **Views:** {format_views(view_count)}\n"
        f"⏱️ **Duration:** {format_dur(duration)}\n"
        f"👤 **Channel:** {channel}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"**Select download type:**"
    )
    markup = _build_type_choice_markup(token)

    try:
        if thumb_path and os.path.exists(thumb_path):
            await status.delete()
            sent = await client.send_photo(
                chat_id,
                photo=thumb_path,
                caption=caption,
                reply_markup=markup,
                parse_mode=ParseMode.MARKDOWN,
            )
            if sent:
                pending_downloads[token]["msg_id"] = sent.id
        else:
            await status.edit_text(
                caption,
                reply_markup=markup,
                disable_web_page_preview=True,
            )
    except Exception as e:
        LOGGER.error(f"_show_type_choice send error: {e}")


# ─── Command: /yt ─────────────────────────────────────────────────────────────

async def _yt_command_handler(client: Client, message: Message):
    """
    /yt <YouTube link>
    → লিংক দিলে Video/Audio type choice দেখাবে।
    Auto-detect নেই — শুধু এই command দিয়েই কাজ হবে।
    """
    query = ""
    parts = message.text.split(None, 1)
    if len(parts) > 1:
        query = parts[1].strip()

    # reply থেকেও নেওয়া যাবে
    if not query and message.reply_to_message:
        rtm = message.reply_to_message
        if rtm.text:
            query = rtm.text.strip()
        elif rtm.caption:
            query = rtm.caption.strip()

    if not query:
        await message.reply_text(
            "**❌ Please provide a YouTube link.**\n"
            "**Usage:** `/yt <link>`\n\n"
            "**Example:**\n"
            "`/yt https://youtu.be/dQw4w9WgXcQ`"
        )
        return

    LOGGER.info(f"/yt | User: {message.from_user.id} | Query: {query}")
    await _show_type_choice(client, message, query)


# ─── Command: /dl ─────────────────────────────────────────────────────────────

async def _dl_command_handler(client: Client, message: Message):
    """
    /dl <YouTube link or search query>
    → Video/Audio type choice দেখাবে
    """
    query = ""
    parts = message.text.split(None, 1)
    if len(parts) > 1:
        query = parts[1].strip()

    # reply থেকেও নেওয়া যাবে
    if not query and message.reply_to_message:
        rtm = message.reply_to_message
        if rtm.text:
            query = rtm.text.strip()
        elif rtm.caption:
            query = rtm.caption.strip()

    if not query:
        await message.reply_text(
            "**❌ Please provide a YouTube link or video name.**\n"
            "**Usage:** `/dl <link or name>`\n\n"
            "**Examples:**\n"
            "`/dl https://youtu.be/dQw4w9WgXcQ`\n"
            "`/dl Bohemian Rhapsody Queen`"
        )
        return

    LOGGER.info(f"/dl | User: {message.from_user.id} | Query: {query}")
    await _show_type_choice(client, message, query)


# ─── Callback: user chose Video ───────────────────────────────────────────────

async def _ydlv_callback(client: Client, callback_query: CallbackQuery):
    """User 'Video' বাটনে ক্লিক করেছে — quality picker দেখাবে।"""
    token = callback_query.data.split("|")[1]
    data  = pending_downloads.get(token)

    if not data:
        await callback_query.answer("❌ Session expired. Please try again.", show_alert=True)
        try:
            await callback_query.message.edit_caption("**❌ Session expired.**")
        except Exception:
            try:
                await callback_query.message.edit_text("**❌ Session expired.**")
            except Exception:
                pass
        return

    if data["user_id"] != callback_query.from_user.id:
        await callback_query.answer("❌ This is not your session.", show_alert=True)
        return

    await callback_query.answer("🎬 Fetching video qualities...", show_alert=False)

    meta                            = data["meta"]
    title, channel, duration, view_count, _ = extract_meta_fields(meta)
    video_url                       = data["url"]
    chat_id                         = data["chat_id"]
    msg_id                          = data["msg_id"]

    # Format fetch
    try:
        await callback_query.message.edit_caption("**📡 Fetching Available Video Qualities...**")
    except Exception:
        try:
            await callback_query.message.edit_text("**📡 Fetching Available Video Qualities...**")
        except Exception:
            pass

    loop     = asyncio.get_running_loop()
    fmt_data = await loop.run_in_executor(executor, _get_available_formats, video_url)
    video_qualities = resolve_video_qualities(fmt_data["video_heights"])

    pending_downloads[token]["video_qualities"] = video_qualities

    # Long video → split prompt
    if duration > MAX_DURATION:
        pending_downloads[token]["split"] = True

        split_caption = (
            f"🎬 **Title:** `{title}`\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"👁️‍🗨️ **Views:** {format_views(view_count)}\n"
            f"**🔗 Url:** [Watch On YouTube]({video_url})\n"
            f"⏱️ **Duration:** {format_dur(duration)}\n"
            f"👤 **Channel:** {channel}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"**Bro File Size Exceeds 2 GB Limit❌**\n"
            f"**Do You Want Spilted Downloader⬇️?**\n"
            f"**Click Below Buttons For Navigation**"
        )
        markup = _build_split_prompt_markup(token, "YSPV")
        try:
            await callback_query.message.edit_caption(split_caption, reply_markup=markup)
        except Exception:
            try:
                await callback_query.message.edit_text(
                    split_caption, reply_markup=markup, disable_web_page_preview=True
                )
            except Exception:
                pass
        return

    caption = (
        f"🎬 **Title:** `{title}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"👁️‍🗨️ **Views:** {format_views(view_count)}\n"
        f"**🔗 Url:** [Watch On YouTube]({video_url})\n"
        f"⏱️ **Duration:** {format_dur(duration)}\n"
        f"👤 **Channel:** {channel}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"**Select video quality to download:**"
    )
    markup = build_video_quality_markup(token, video_qualities, cb_prefix="YV")

    try:
        await callback_query.message.edit_caption(caption, reply_markup=markup)
    except Exception:
        try:
            await callback_query.message.edit_text(
                caption, reply_markup=markup, disable_web_page_preview=True
            )
        except Exception:
            pass


# ─── Callback: user chose Audio ───────────────────────────────────────────────

async def _ydla_callback(client: Client, callback_query: CallbackQuery):
    """User 'Audio' বাটনে ক্লিক করেছে — quality picker দেখাবে।"""
    token = callback_query.data.split("|")[1]
    data  = pending_downloads.get(token)

    if not data:
        await callback_query.answer("❌ Session expired. Please try again.", show_alert=True)
        try:
            await callback_query.message.edit_caption("**❌ Session expired.**")
        except Exception:
            try:
                await callback_query.message.edit_text("**❌ Session expired.**")
            except Exception:
                pass
        return

    if data["user_id"] != callback_query.from_user.id:
        await callback_query.answer("❌ This is not your session.", show_alert=True)
        return

    await callback_query.answer("🎵 Fetching audio qualities...", show_alert=False)

    meta                            = data["meta"]
    title, channel, duration, view_count, _ = extract_meta_fields(meta)
    video_url                       = data["url"]

    try:
        await callback_query.message.edit_caption("**📡 Fetching Available Audio Qualities...**")
    except Exception:
        try:
            await callback_query.message.edit_text("**📡 Fetching Available Audio Qualities...**")
        except Exception:
            pass

    loop     = asyncio.get_running_loop()
    fmt_data = await loop.run_in_executor(executor, _get_available_formats, video_url)
    audio_qualities = resolve_audio_qualities(fmt_data["audio_abrs"])

    pending_downloads[token]["audio_qualities"] = audio_qualities

    # Long audio → split prompt
    if duration > MAX_DURATION:
        pending_downloads[token]["split"] = True

        split_caption = (
            f"🎵 **Title:** `{title}`\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"👁️‍🗨️ **Views:** {format_views(view_count)}\n"
            f"**🔗 Url:** [Listen On YouTube]({video_url})\n"
            f"⏱️ **Duration:** {format_dur(duration)}\n"
            f"👤 **Channel:** {channel}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"**Bro File Size Exceeds 2 GB Limit❌**\n"
            f"**Do You Want Spilted Downloader⬇️?**\n"
            f"**Click Below Buttons For Navigation**"
        )
        markup = _build_split_prompt_markup(token, "YSPA")
        try:
            await callback_query.message.edit_caption(split_caption, reply_markup=markup)
        except Exception:
            try:
                await callback_query.message.edit_text(
                    split_caption, reply_markup=markup, disable_web_page_preview=True
                )
            except Exception:
                pass
        return

    caption = (
        f"🎵 **Title:** `{title}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"👁️‍🗨️ **Views:** {format_views(view_count)}\n"
        f"**🔗 Url:** [Listen On YouTube]({video_url})\n"
        f"⏱️ **Duration:** {format_dur(duration)}\n"
        f"👤 **Channel:** {channel}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"**Select audio quality to download:**"
    )
    markup = build_audio_quality_markup(token, audio_qualities, cb_prefix="YA")

    try:
        await callback_query.message.edit_caption(caption, reply_markup=markup)
    except Exception:
        try:
            await callback_query.message.edit_text(
                caption, reply_markup=markup, disable_web_page_preview=True
            )
        except Exception:
            pass


# ─── Setup function ───────────────────────────────────────────────────────────

def setup_ytdl_yt_handler(app: Client):
    """
    __init__.py থেকে call হয়:
        from .ytdl_yt import setup_ytdl_yt_handler
        setup_ytdl_yt_handler(app)
    """

    # /yt command — group=0 (normal priority)
    app.on_message(
        filters.command(["yt"], prefixes=["/", "!", "."]),
        group=0,
    )(_yt_command_handler)

    # /dl command — group=0 (normal priority)
    app.on_message(
        filters.command(["dl"], prefixes=["/", "!", "."]),
        group=0,
    )(_dl_command_handler)

    # Callback: Video choice
    app.on_callback_query(
        filters.regex(r"^YDLV\|"),
    )(_ydlv_callback)

    # Callback: Audio choice
    app.on_callback_query(
        filters.regex(r"^YDLA\|"),
    )(_ydla_callback)

    LOGGER.info("ytdl_yt handlers registered (group=0 /yt, group=0 /dl, YDLV/YDLA callbacks)")
