# yt.py — YouTube Downloader Plugin (Pyrofork)
# Commands: /yt /video /mp4 /dl  →  video download
#           /mp3 /song /aud      →  audio download
# Place this file inside your bot's plugins/ folder.

import asyncio
import os
import re
import time

from pyrogram import Client, filters
from pyrogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from pyrogram.enums import ParseMode

from .ythelpers import (
    TEMP_DIR, MAX_FILE_SIZE, MAX_DURATION, executor,
    VIDEO_QUALITY_OPTIONS, AUDIO_QUALITY_OPTIONS,
    LOGGER,
    generate_token, youtube_parser, extract_video_id,
    fetch_thumbnail, fetch_metadata_from_url,
    search_youtube_metadata, search_youtube_url,
    extract_meta_fields, build_user_info, find_downloaded_file,
    _get_available_formats, _run_ydl,
    get_video_ydl_opts, get_audio_ydl_opts,
    resolve_video_qualities, resolve_audio_qualities,
    build_video_quality_markup, build_audio_quality_markup,
    format_views, format_dur,
    clean_temp_files, clean_download,
    split_file_ffmpeg, compute_segment_duration,
    get_readable_file_size,
    progress_callback,
)

# ─── State ───────────────────────────────────────────────────────────────────
pending_downloads: dict = {}

# ─── Constants ───────────────────────────────────────────────────────────────
SPLIT_PROMPT_TEXT = (
    "**Bro File Size Exceeds 2 GB Limit❌**\n"
    "**Do You Want Spilted Downloader⬇️?**\n"
    "**Click Below Buttons For Navigation**"
)


def _build_split_prompt_markup(token: str, yes_cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Yes, Split It", callback_data=f"{yes_cb}|{token}"),
            InlineKeyboardButton("❌ Cancel",        callback_data=f"YX|{token}"),
        ]
    ])


# ─── Split upload — Video ────────────────────────────────────────────────────

async def do_split_upload_video(client: Client, token: str):
    data = pending_downloads.get(token)
    if not data:
        return

    file_path  = data.get("file_path")
    temp_id    = data.get("temp_id")
    chat_id    = data["chat_id"]
    msg_id     = data["msg_id"]
    thumb_path = data.get("thumb_path")
    user_info  = data.get("user_info", "Unknown")
    title      = data.get("split_title", "Unknown")
    url        = data["url"]
    view_count = data.get("split_view_count", 0)
    duration   = data.get("media_duration", 0)
    height     = data.get("split_height", 720)

    try:
        await client.edit_message_text(
            chat_id, msg_id,
            f"**✂️ Splitting Video Into Parts...**\n"
            f"**Title:** `{title}`\n"
            f"**━━━━━━━━━━━━━━━━━━━━━**\n"
            f"**Please wait...**",
        )
    except Exception:
        pass

    file_size    = os.path.getsize(file_path)
    segment_dur  = compute_segment_duration(file_size, duration)
    ext          = os.path.splitext(file_path)[1] or ".mp4"
    split_dir    = str(TEMP_DIR / temp_id / "splits")

    loop = asyncio.get_running_loop()
    try:
        parts = await loop.run_in_executor(executor, split_file_ffmpeg, file_path, split_dir, segment_dur, ext)
    except Exception as e:
        LOGGER.error(f"FFmpeg split failed: {e}")
        try:
            await client.edit_message_text(chat_id, msg_id, "**❌ Split Failed. Please try again.**")
        except Exception:
            pass
        clean_temp_files(TEMP_DIR / temp_id)
        pending_downloads.pop(token, None)
        return

    total_parts = len(parts)
    LOGGER.info(f"Splitting video into {total_parts} parts for {title}")

    for i, part_path in enumerate(parts, 1):
        start_time       = time.time()
        last_update_time = [0]

        try:
            await client.edit_message_text(
                chat_id, msg_id,
                f"**📤 Uploading Part {i}/{total_parts}...**\n"
                f"**Title:** `{title}`\n"
                f"**━━━━━━━━━━━━━━━━━━━━━**\n"
                f"**Please wait...**",
            )
        except Exception:
            pass

        part_caption = (
            f"🎬 **Title:** `{title}` — Part {i}/{total_parts}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"👁️‍🗨️ **Views:** {format_views(view_count)}\n"
            f"**🔗 Url:** [Watch On YouTube]({url})\n"
            f"⏱️ **Part Duration:** {format_dur(segment_dur)} | **Total:** {format_dur(duration)}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"**Downloaded By** {user_info}"
        )

        try:
            await client.send_video(
                chat_id,
                video=part_path,
                caption=part_caption,
                thumb=thumb_path if (thumb_path and os.path.exists(thumb_path)) else None,
                duration=segment_dur,
                width=1280,
                height=height,
                supports_streaming=True,
                parse_mode=ParseMode.MARKDOWN,
                progress=progress_callback,
                progress_args=(
                    await client.get_messages(chat_id, msg_id),
                    start_time,
                    last_update_time,
                ),
            )
        except Exception as e:
            LOGGER.error(f"Split video upload failed at part {i}: {e}")
            try:
                await client.edit_message_text(chat_id, msg_id, f"**❌ Upload Failed on Part {i}. Please try again.**")
            except Exception:
                pass
            clean_temp_files(TEMP_DIR / temp_id)
            if thumb_path:
                clean_download(thumb_path)
            pending_downloads.pop(token, None)
            return

    try:
        await client.delete_messages(chat_id, msg_id)
    except Exception:
        pass
    LOGGER.info(f"Delivered split video ({total_parts} parts): {title} → {chat_id}")
    clean_temp_files(TEMP_DIR / temp_id)
    if thumb_path:
        clean_download(thumb_path)
    pending_downloads.pop(token, None)


# ─── Split upload — Audio ────────────────────────────────────────────────────

async def do_split_upload_audio(client: Client, token: str):
    data = pending_downloads.get(token)
    if not data:
        return

    file_path  = data.get("file_path")
    temp_id    = data.get("temp_id")
    chat_id    = data["chat_id"]
    msg_id     = data["msg_id"]
    thumb_path = data.get("thumb_path")
    user_info  = data.get("user_info", "Unknown")
    title      = data.get("split_title", "Unknown")
    channel    = data.get("split_channel", "Unknown")
    url        = data["url"]
    view_count = data.get("split_view_count", 0)
    duration   = data.get("media_duration", 0)

    try:
        await client.edit_message_text(
            chat_id, msg_id,
            f"**✂️ Splitting Audio Into Parts...**\n"
            f"**Title:** `{title}`\n"
            f"**━━━━━━━━━━━━━━━━━━━━━**\n"
            f"**Please wait...**",
        )
    except Exception:
        pass

    file_size   = os.path.getsize(file_path)
    segment_dur = compute_segment_duration(file_size, duration)
    ext         = os.path.splitext(file_path)[1] or ".mp3"
    split_dir   = str(TEMP_DIR / temp_id / "splits")

    loop = asyncio.get_running_loop()
    try:
        parts = await loop.run_in_executor(executor, split_file_ffmpeg, file_path, split_dir, segment_dur, ext)
    except Exception as e:
        LOGGER.error(f"FFmpeg audio split failed: {e}")
        try:
            await client.edit_message_text(chat_id, msg_id, "**❌ Split Failed. Please try again.**")
        except Exception:
            pass
        clean_temp_files(TEMP_DIR / temp_id)
        pending_downloads.pop(token, None)
        return

    total_parts = len(parts)
    LOGGER.info(f"Splitting audio into {total_parts} parts for {title}")

    for i, part_path in enumerate(parts, 1):
        start_time       = time.time()
        last_update_time = [0]

        try:
            await client.edit_message_text(
                chat_id, msg_id,
                f"**📤 Uploading Part {i}/{total_parts}...**\n"
                f"**Title:** `{title}`\n"
                f"**━━━━━━━━━━━━━━━━━━━━━**\n"
                f"**Please wait...**",
            )
        except Exception:
            pass

        part_caption = (
            f"🎵 **Title:** `{title}` — Part {i}/{total_parts}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"👁️‍🗨️ **Views:** {format_views(view_count)}\n"
            f"**🔗 Url:** [Listen On YouTube]({url})\n"
            f"⏱️ **Part Duration:** {format_dur(segment_dur)} | **Total:** {format_dur(duration)}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"**Downloaded By** {user_info}"
        )

        try:
            await client.send_audio(
                chat_id,
                audio=part_path,
                caption=part_caption,
                thumb=thumb_path if (thumb_path and os.path.exists(thumb_path)) else None,
                duration=segment_dur,
                title=f"{title} (Part {i}/{total_parts})",
                performer=channel,
                parse_mode=ParseMode.MARKDOWN,
                progress=progress_callback,
                progress_args=(
                    await client.get_messages(chat_id, msg_id),
                    start_time,
                    last_update_time,
                ),
            )
        except Exception as e:
            LOGGER.error(f"Split audio upload failed at part {i}: {e}")
            try:
                await client.edit_message_text(chat_id, msg_id, f"**❌ Upload Failed on Part {i}. Please try again.**")
            except Exception:
                pass
            clean_temp_files(TEMP_DIR / temp_id)
            if thumb_path:
                clean_download(thumb_path)
            pending_downloads.pop(token, None)
            return

    try:
        await client.delete_messages(chat_id, msg_id)
    except Exception:
        pass
    LOGGER.info(f"Delivered split audio ({total_parts} parts): {title} → {chat_id}")
    clean_temp_files(TEMP_DIR / temp_id)
    if thumb_path:
        clean_download(thumb_path)
    pending_downloads.pop(token, None)


# ─── Video download core ──────────────────────────────────────────────────────

async def do_video_download(client: Client, token: str, quality_key: str):
    data = pending_downloads.get(token)
    if not data:
        return

    url        = data["url"]
    meta       = data["meta"]
    chat_id    = data["chat_id"]
    msg_id     = data["msg_id"]
    thumb_path = data.get("thumb_path")
    user_info  = data.get("user_info", "Unknown")
    do_split   = data.get("split", False)

    title, channel, duration, view_count, safe_title = extract_meta_fields(meta)
    height  = VIDEO_QUALITY_OPTIONS[quality_key]["height"]
    temp_id = generate_token()
    temp_dir = TEMP_DIR / temp_id
    temp_dir.mkdir(exist_ok=True)
    output_base = str(temp_dir / "media")

    try:
        await client.edit_message_text(
            chat_id, msg_id,
            f"**⬇️ Downloading {quality_key} Video...**\n"
            f"**Title:** `{title}`\n"
            f"**━━━━━━━━━━━━━━━━━━━━━**\n"
            f"**Please wait...**",
        )
    except Exception:
        pass

    loop = asyncio.get_running_loop()
    opts = get_video_ydl_opts(output_base, quality_key)

    try:
        await loop.run_in_executor(executor, _run_ydl, opts, url)
    except Exception as e:
        LOGGER.error(f"Video download failed: {e}")
        try:
            await client.edit_message_text(chat_id, msg_id, "**❌ Download Failed. Please try again.**")
        except Exception:
            pass
        clean_temp_files(TEMP_DIR / temp_id)
        pending_downloads.pop(token, None)
        return

    file_path = find_downloaded_file(temp_dir, [".mp4", ".mkv", ".webm"])
    if not file_path:
        try:
            await client.edit_message_text(chat_id, msg_id, "**❌ File not found after download. Try again.**")
        except Exception:
            pass
        clean_temp_files(TEMP_DIR / temp_id)
        pending_downloads.pop(token, None)
        return

    file_size = os.path.getsize(file_path)

    if do_split or file_size > MAX_FILE_SIZE:
        pending_downloads[token]["file_path"]        = file_path
        pending_downloads[token]["temp_id"]          = temp_id
        pending_downloads[token]["media_duration"]   = duration
        pending_downloads[token]["split_title"]      = title
        pending_downloads[token]["split_channel"]    = channel
        pending_downloads[token]["split_view_count"] = view_count
        pending_downloads[token]["split_height"]     = height

        if do_split:
            asyncio.create_task(do_split_upload_video(client, token))
            return

        try:
            await client.edit_message_text(
                chat_id, msg_id,
                SPLIT_PROMPT_TEXT,
                reply_markup=_build_split_prompt_markup(token, "YSPF"),
            )
        except Exception:
            pass
        return

    caption = (
        f"🎬 **Title:** `{title}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"👁️‍🗨️ **Views:** {format_views(view_count)}\n"
        f"**🔗 Url:** [Watch On YouTube]({url})\n"
        f"⏱️ **Duration:** {format_dur(duration)}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"**Downloaded By** {user_info}"
    )

    start_time       = time.time()
    last_update_time = [0]

    try:
        status_msg = await client.get_messages(chat_id, msg_id)
        await client.send_video(
            chat_id,
            video=file_path,
            caption=caption,
            thumb=thumb_path if (thumb_path and os.path.exists(thumb_path)) else None,
            duration=duration,
            width=1280,
            height=height,
            supports_streaming=True,
            parse_mode=ParseMode.MARKDOWN,
            progress=progress_callback,
            progress_args=(status_msg, start_time, last_update_time),
        )
        await client.delete_messages(chat_id, msg_id)
    except Exception as e:
        LOGGER.error(f"Video upload failed: {e}")
        try:
            await client.edit_message_text(chat_id, msg_id, "**❌ Upload Failed. Please try again.**")
        except Exception:
            pass

    LOGGER.info(f"Delivered {quality_key} video: {title} → {chat_id}")
    clean_temp_files(TEMP_DIR / temp_id)
    if thumb_path:
        clean_download(thumb_path)
    pending_downloads.pop(token, None)


# ─── Audio download core ──────────────────────────────────────────────────────

async def do_audio_download(client: Client, token: str, quality_key: str):
    data = pending_downloads.get(token)
    if not data:
        return

    url        = data["url"]
    meta       = data["meta"]
    chat_id    = data["chat_id"]
    msg_id     = data["msg_id"]
    thumb_path = data.get("thumb_path")
    user_info  = data.get("user_info", "Unknown")
    do_split   = data.get("split", False)

    title, channel, duration, view_count, safe_title = extract_meta_fields(meta)
    temp_id  = generate_token()
    temp_dir = TEMP_DIR / temp_id
    temp_dir.mkdir(exist_ok=True)
    output_base = str(temp_dir / "media")

    try:
        await client.edit_message_text(
            chat_id, msg_id,
            f"**🎵 Downloading {quality_key} Audio...**\n"
            f"**Title:** `{title}`\n"
            f"**━━━━━━━━━━━━━━━━━━━━━**\n"
            f"**Please wait...**",
        )
    except Exception:
        pass

    loop = asyncio.get_running_loop()
    opts = get_audio_ydl_opts(output_base, quality_key)

    try:
        await loop.run_in_executor(executor, _run_ydl, opts, url)
    except Exception as e:
        LOGGER.error(f"Audio download failed: {e}")
        try:
            await client.edit_message_text(chat_id, msg_id, "**❌ Download Failed. Please try again.**")
        except Exception:
            pass
        clean_temp_files(TEMP_DIR / temp_id)
        pending_downloads.pop(token, None)
        return

    file_path = find_downloaded_file(temp_dir, [".mp3", ".m4a", ".webm", ".ogg"])
    if not file_path:
        try:
            await client.edit_message_text(chat_id, msg_id, "**❌ File not found after download. Try again.**")
        except Exception:
            pass
        clean_temp_files(TEMP_DIR / temp_id)
        pending_downloads.pop(token, None)
        return

    file_size = os.path.getsize(file_path)

    if do_split or file_size > MAX_FILE_SIZE:
        pending_downloads[token]["file_path"]        = file_path
        pending_downloads[token]["temp_id"]          = temp_id
        pending_downloads[token]["media_duration"]   = duration
        pending_downloads[token]["split_title"]      = title
        pending_downloads[token]["split_channel"]    = channel
        pending_downloads[token]["split_view_count"] = view_count

        if do_split:
            asyncio.create_task(do_split_upload_audio(client, token))
            return

        try:
            await client.edit_message_text(
                chat_id, msg_id,
                SPLIT_PROMPT_TEXT,
                reply_markup=_build_split_prompt_markup(token, "YSPFA"),
            )
        except Exception:
            pass
        return

    caption = (
        f"🎵 **Title:** `{title}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"👁️‍🗨️ **Views:** {format_views(view_count)}\n"
        f"**🔗 Url:** [Listen On YouTube]({url})\n"
        f"⏱️ **Duration:** {format_dur(duration)}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"**Downloaded By** {user_info}"
    )

    start_time       = time.time()
    last_update_time = [0]

    try:
        status_msg = await client.get_messages(chat_id, msg_id)
        await client.send_audio(
            chat_id,
            audio=file_path,
            caption=caption,
            thumb=thumb_path if (thumb_path and os.path.exists(thumb_path)) else None,
            duration=duration,
            title=title,
            performer=channel,
            parse_mode=ParseMode.MARKDOWN,
            progress=progress_callback,
            progress_args=(status_msg, start_time, last_update_time),
        )
        await client.delete_messages(chat_id, msg_id)
    except Exception as e:
        LOGGER.error(f"Audio upload failed: {e}")
        try:
            await client.edit_message_text(chat_id, msg_id, "**❌ Upload Failed. Please try again.**")
        except Exception:
            pass

    LOGGER.info(f"Delivered {quality_key} audio: {title} → {chat_id}")
    clean_temp_files(TEMP_DIR / temp_id)
    if thumb_path:
        clean_download(thumb_path)
    pending_downloads.pop(token, None)


# ─── Command handlers ─────────────────────────────────────────────────────────

async def handle_yt_command(client: Client, message: Message, query: str):
    chat_id  = message.chat.id
    user_info = build_user_info(message)

    status = await message.reply_text("**🔍 Searching YouTube...**")
    if not status:
        return

    video_url = youtube_parser(query)
    if not video_url:
        await status.edit_text("**🔍 Processing query...**")
        video_url = await search_youtube_url(query)
        if not video_url:
            await status.edit_text("**❌ No results found. Try a different query.**")
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

    await status.edit_text("**📡 Fetching Available Video Qualities...**")
    loop     = asyncio.get_running_loop()
    fmt_data = await loop.run_in_executor(executor, _get_available_formats, video_url)
    video_qualities = resolve_video_qualities(fmt_data["video_heights"])

    token    = generate_token(message.from_user.id)
    temp_dir = TEMP_DIR / token
    temp_dir.mkdir(exist_ok=True)
    thumb_out = str(temp_dir / "thumb.jpg")

    await status.edit_text("**🖼️ Fetching Available Thumbnail...**")
    thumb_path = await fetch_thumbnail(video_id, thumb_out)

    pending_downloads[token] = {
        "url":       video_url,
        "meta":      meta,
        "user_id":   message.from_user.id,
        "user_info": user_info,
        "chat_id":   chat_id,
        "msg_id":    status.id,
        "thumb_path": thumb_path,
    }

    if duration > MAX_DURATION:
        pending_downloads[token]["video_qualities"] = video_qualities
        pending_downloads[token]["split"]           = True

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

        if thumb_path and os.path.exists(thumb_path):
            await status.delete()
            sent = await client.send_photo(chat_id, photo=thumb_path, caption=split_caption,
                                           reply_markup=markup, parse_mode=ParseMode.MARKDOWN)
            if sent:
                pending_downloads[token]["msg_id"] = sent.id
        else:
            await status.edit_text(split_caption, reply_markup=markup, disable_web_page_preview=True)
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

    if thumb_path and os.path.exists(thumb_path):
        await status.delete()
        sent = await client.send_photo(chat_id, photo=thumb_path, caption=caption,
                                       reply_markup=markup, parse_mode=ParseMode.MARKDOWN)
        if sent:
            pending_downloads[token]["msg_id"] = sent.id
    else:
        await status.edit_text(caption, reply_markup=markup, disable_web_page_preview=True)


async def handle_audio_command(client: Client, message: Message, query: str):
    chat_id   = message.chat.id
    user_info = build_user_info(message)

    status = await message.reply_text("**🔍 Searching YouTube...**")
    if not status:
        return

    video_url = youtube_parser(query)
    if not video_url:
        await status.edit_text("**🔍 Processing query...**")
        video_url = await search_youtube_url(query)
        if not video_url:
            await status.edit_text("**❌ No results found. Try a different query.**")
            return

    await status.edit_text("**📡 Fetching Audio Info...**")
    meta = await fetch_metadata_from_url(video_url)
    if not meta:
        meta = await search_youtube_metadata(query)
    if not meta:
        await status.edit_text("**❌ Could not fetch audio info. Try again.**")
        return

    title, channel, duration, view_count, safe_title = extract_meta_fields(meta)
    video_id = extract_video_id(video_url)

    await status.edit_text("**📡 Fetching Available Audio Qualities...**")
    loop     = asyncio.get_running_loop()
    fmt_data = await loop.run_in_executor(executor, _get_available_formats, video_url)
    audio_qualities = resolve_audio_qualities(fmt_data["audio_abrs"])

    token    = generate_token(message.from_user.id)
    temp_dir = TEMP_DIR / token
    temp_dir.mkdir(exist_ok=True)
    thumb_out  = str(temp_dir / "thumb.jpg")

    await status.edit_text("**🖼️ Fetching Available Thumbnail...**")
    thumb_path = await fetch_thumbnail(video_id, thumb_out)

    pending_downloads[token] = {
        "url":       video_url,
        "meta":      meta,
        "user_id":   message.from_user.id,
        "user_info": user_info,
        "chat_id":   chat_id,
        "msg_id":    status.id,
        "thumb_path": thumb_path,
    }

    if duration > MAX_DURATION:
        pending_downloads[token]["audio_qualities"] = audio_qualities
        pending_downloads[token]["split"]           = True

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

        if thumb_path and os.path.exists(thumb_path):
            await status.delete()
            sent = await client.send_photo(chat_id, photo=thumb_path, caption=split_caption,
                                           reply_markup=markup, parse_mode=ParseMode.MARKDOWN)
            if sent:
                pending_downloads[token]["msg_id"] = sent.id
        else:
            await status.edit_text(split_caption, reply_markup=markup, disable_web_page_preview=True)
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

    if thumb_path and os.path.exists(thumb_path):
        await status.delete()
        sent = await client.send_photo(chat_id, photo=thumb_path, caption=caption,
                                       reply_markup=markup, parse_mode=ParseMode.MARKDOWN)
        if sent:
            pending_downloads[token]["msg_id"] = sent.id
    else:
        await status.edit_text(caption, reply_markup=markup, disable_web_page_preview=True)


# ─── Pyrogram command filters ─────────────────────────────────────────────────

@Client.on_message(filters.command(["yt", "video", "mp4", "dl"], prefixes=["/", "!", "."]))
async def yt_video_command(client: Client, message: Message):
    query = message.text.split(None, 1)[1].strip() if len(message.text.split(None, 1)) > 1 else ""

    if not query and message.reply_to_message and message.reply_to_message.text:
        query = message.reply_to_message.text.strip()

    if not query:
        await message.reply_text(
            "**❌ Please provide a video name or URL.**\n"
            "**Usage:** `/yt <name or link>`"
        )
        return

    LOGGER.info(f"YT video | User: {message.from_user.id} | Query: {query}")
    await handle_yt_command(client, message, query)


@Client.on_message(filters.command(["mp3", "song", "aud"], prefixes=["/", "!", "."]))
async def yt_audio_command(client: Client, message: Message):
    query = message.text.split(None, 1)[1].strip() if len(message.text.split(None, 1)) > 1 else ""

    if not query and message.reply_to_message and message.reply_to_message.text:
        query = message.reply_to_message.text.strip()

    if not query:
        await message.reply_text(
            "**❌ Please provide a song name or URL.**\n"
            "**Usage:** `/mp3 <name or link>`"
        )
        return

    LOGGER.info(f"YT audio | User: {message.from_user.id} | Query: {query}")
    await handle_audio_command(client, message, query)


# ─── Callback query handlers ──────────────────────────────────────────────────

@Client.on_callback_query(filters.regex(r"^YV\|"))
async def yt_video_cb(client: Client, callback_query: CallbackQuery):
    raw   = callback_query.data
    parts = raw.split("|")
    if len(parts) != 3:
        return

    token       = parts[1]
    quality_key = parts[2]

    if quality_key not in VIDEO_QUALITY_OPTIONS:
        await callback_query.answer("❌ Invalid quality.", show_alert=True)
        return

    data = pending_downloads.get(token)
    if not data:
        await callback_query.answer("❌ Session expired. Please search again.", show_alert=True)
        try:
            await callback_query.message.edit_text("**❌ Session expired. Please search again.**")
        except Exception:
            pass
        return

    if data["user_id"] != callback_query.from_user.id:
        await callback_query.answer("❌ This is not your download session.", show_alert=True)
        return

    await callback_query.answer("⬇️ Download Has Started", show_alert=True)
    try:
        await callback_query.message.edit_text(f"**⬇️ Starting {quality_key} Download...**")
    except Exception:
        pass

    asyncio.create_task(do_video_download(client, token, quality_key))


@Client.on_callback_query(filters.regex(r"^YA\|"))
async def yt_audio_cb(client: Client, callback_query: CallbackQuery):
    raw   = callback_query.data
    parts = raw.split("|")
    if len(parts) != 3:
        return

    token       = parts[1]
    quality_key = parts[2]

    if quality_key not in AUDIO_QUALITY_OPTIONS:
        await callback_query.answer("❌ Invalid quality.", show_alert=True)
        return

    data = pending_downloads.get(token)
    if not data:
        await callback_query.answer("❌ Session expired. Please search again.", show_alert=True)
        try:
            await callback_query.message.edit_text("**❌ Session expired. Please search again.**")
        except Exception:
            pass
        return

    if data["user_id"] != callback_query.from_user.id:
        await callback_query.answer("❌ This is not your download session.", show_alert=True)
        return

    await callback_query.answer("⬇️ Download Has Started", show_alert=True)
    try:
        await callback_query.message.edit_text(f"**🎵 Starting {quality_key} Download...**")
    except Exception:
        pass

    asyncio.create_task(do_audio_download(client, token, quality_key))


@Client.on_callback_query(filters.regex(r"^YSPV\|"))
async def yt_split_yes_video_cb(client: Client, callback_query: CallbackQuery):
    token = callback_query.data.split("|")[1]
    data  = pending_downloads.get(token)

    if not data:
        await callback_query.answer("❌ Session expired.", show_alert=True)
        try:
            await callback_query.message.edit_text("**❌ Session expired.**")
        except Exception:
            pass
        return

    if data["user_id"] != callback_query.from_user.id:
        await callback_query.answer("❌ This is not your session.", show_alert=True)
        return

    video_qualities = data.get("video_qualities", list(VIDEO_QUALITY_OPTIONS.keys()))
    title, channel, duration, view_count, _ = extract_meta_fields(data["meta"])
    url    = data["url"]

    caption = (
        f"🎬 **Title:** `{title}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"👁️‍🗨️ **Views:** {format_views(view_count)}\n"
        f"**🔗 Url:** [Watch On YouTube]({url})\n"
        f"⏱️ **Duration:** {format_dur(duration)}\n"
        f"👤 **Channel:** {channel}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"**Select video quality to download:**"
    )
    markup = build_video_quality_markup(token, video_qualities, cb_prefix="YV")

    await callback_query.answer("✅ Choose Quality To Start Split Download")
    try:
        await callback_query.message.edit_text(caption, reply_markup=markup)
    except Exception:
        pass


@Client.on_callback_query(filters.regex(r"^YSPA\|"))
async def yt_split_yes_audio_cb(client: Client, callback_query: CallbackQuery):
    token = callback_query.data.split("|")[1]
    data  = pending_downloads.get(token)

    if not data:
        await callback_query.answer("❌ Session expired.", show_alert=True)
        try:
            await callback_query.message.edit_text("**❌ Session expired.**")
        except Exception:
            pass
        return

    if data["user_id"] != callback_query.from_user.id:
        await callback_query.answer("❌ This is not your session.", show_alert=True)
        return

    audio_qualities = data.get("audio_qualities", list(AUDIO_QUALITY_OPTIONS.keys()))
    title, channel, duration, view_count, _ = extract_meta_fields(data["meta"])
    url = data["url"]

    caption = (
        f"🎵 **Title:** `{title}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"👁️‍🗨️ **Views:** {format_views(view_count)}\n"
        f"**🔗 Url:** [Listen On YouTube]({url})\n"
        f"⏱️ **Duration:** {format_dur(duration)}\n"
        f"👤 **Channel:** {channel}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"**Select audio quality to download:**"
    )
    markup = build_audio_quality_markup(token, audio_qualities, cb_prefix="YA")

    await callback_query.answer("✅ Choose Quality To Start Split Download")
    try:
        await callback_query.message.edit_text(caption, reply_markup=markup)
    except Exception:
        pass


@Client.on_callback_query(filters.regex(r"^YSPF\|"))
async def yt_split_file_video_cb(client: Client, callback_query: CallbackQuery):
    token = callback_query.data.split("|")[1]
    data  = pending_downloads.get(token)

    if not data:
        await callback_query.answer("❌ Session expired.", show_alert=True)
        return

    if data["user_id"] != callback_query.from_user.id:
        await callback_query.answer("❌ This is not your session.", show_alert=True)
        return

    await callback_query.answer("✅ Starting Split Upload...")
    try:
        await callback_query.message.edit_text("**✂️ Starting Split Upload...**")
    except Exception:
        pass

    asyncio.create_task(do_split_upload_video(client, token))


@Client.on_callback_query(filters.regex(r"^YSPFA\|"))
async def yt_split_file_audio_cb(client: Client, callback_query: CallbackQuery):
    token = callback_query.data.split("|")[1]
    data  = pending_downloads.get(token)

    if not data:
        await callback_query.answer("❌ Session expired.", show_alert=True)
        return

    if data["user_id"] != callback_query.from_user.id:
        await callback_query.answer("❌ This is not your session.", show_alert=True)
        return

    await callback_query.answer("✅ Starting Split Upload...")
    try:
        await callback_query.message.edit_text("**✂️ Starting Split Upload...**")
    except Exception:
        pass

    asyncio.create_task(do_split_upload_audio(client, token))


@Client.on_callback_query(filters.regex(r"^YX\|"))
async def yt_cancel_cb(client: Client, callback_query: CallbackQuery):
    token = callback_query.data.split("|")[1]
    data  = pending_downloads.get(token)

    if data and data["user_id"] != callback_query.from_user.id:
        await callback_query.answer("❌ This is not your session.", show_alert=True)
        return

    if data:
        thumb_path = data.get("thumb_path")
        if thumb_path:
            clean_download(thumb_path)
        temp_id = data.get("temp_id")
        if temp_id:
            clean_temp_files(TEMP_DIR / temp_id)
        clean_temp_files(TEMP_DIR / token)

    pending_downloads.pop(token, None)

    try:
        await callback_query.message.edit_text("**Cancelled ❌ download process...**")
    except Exception:
        pass

    await callback_query.answer("✅ Cancelled")
