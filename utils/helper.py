# Copyright @TheSmartBisnu
# Channel t.me/ItsSmartDev
# Update Author @juktijol
# Channel t.me/juktijol
# ✅ FIXED: sqlite3 closed database + OSError TCPTransport + AUTH_KEY_UNREGISTERED
# ✅ FIXED: Video aspect ratio (squished) → actual video resolution used for width/height
# ✅ FIXED: Thumbnail scale → aspect ratio preserved with scale=320:-2
# ✅ FIXED: Shell message (media=False) in restricted channels → download via msg.media

from asyncio.subprocess import PIPE
import os
import asyncio
from time import time
from typing import Optional
from asyncio import create_subprocess_exec, create_subprocess_shell, wait_for
from PIL import Image
from pyleaves import Leaves
from pyrogram.parser import Parser
from pyrogram.utils import get_channel_id
from pyrogram.types import (
    InputMediaPhoto,
    InputMediaVideo,
    InputMediaDocument,
    InputMediaAudio,
    Voice,
)

from .logging_setup import LOGGER

SIZE_UNITS = ["B", "KB", "MB", "GB", "TB", "PB"]


def get_readable_file_size(size_in_bytes: Optional[float]) -> str:
    if size_in_bytes is None or size_in_bytes < 0:
        return "0B"
    for unit in SIZE_UNITS:
        if size_in_bytes < 1024:
            return f"{size_in_bytes:.2f} {unit}"
        size_in_bytes /= 1024
    return "File too large"


def get_readable_time(seconds: int) -> str:
    result = ""
    (days, remainder) = divmod(seconds, 86400)
    days = int(days)
    if days != 0:
        result += f"{days}d"
    (hours, remainder) = divmod(remainder, 3600)
    hours = int(hours)
    if hours != 0:
        result += f"{hours}h"
    (minutes, seconds) = divmod(remainder, 60)
    minutes = int(minutes)
    if minutes != 0:
        result += f"{minutes}m"
    seconds = int(seconds)
    result += f"{seconds}s"
    return result


async def fileSizeLimit(file_size, message, action_type="download", is_premium=False):
    MAX_FILE_SIZE = 2 * 2097152000 if is_premium else 2097152000
    if file_size > MAX_FILE_SIZE:
        await message.reply(
            f"The file size exceeds the "
            f"{get_readable_file_size(MAX_FILE_SIZE)} limit "
            f"and cannot be {action_type}ed."
        )
        return False
    return True


async def get_parsed_msg(text, entities):
    return Parser.unparse(text, entities or [], is_html=False)


PROGRESS_BAR = """
Percentage: {percentage:.2f}% | {current}/{total}
Speed: {speed}/s
Estimated Time Left: {est_time} seconds
"""


def getChatMsgID(link: str):
    linkps = link.split("/")
    chat_id, message_thread_id, message_id = None, None, None

    try:
        if len(linkps) == 7 and linkps[3] == "c":
            chat_id = get_channel_id(int(linkps[4]))
            message_thread_id = int(linkps[5])
            message_id = int(linkps[6])
        elif len(linkps) == 6:
            if linkps[3] == "c":
                chat_id = get_channel_id(int(linkps[4]))
                message_id = int(linkps[5])
            else:
                chat_id = linkps[3]
                message_thread_id = int(linkps[4])
                message_id = int(linkps[5])
        elif len(linkps) == 5:
            chat_id = linkps[3]
            if chat_id == "m":
                raise ValueError(
                    "Invalid ClientType used to parse this message link"
                )
            message_id = int(linkps[4])
    except (ValueError, TypeError):
        raise ValueError("Invalid post URL. Must end with a numeric ID.")

    if not chat_id or not message_id:
        raise ValueError("Please send a valid Telegram post URL.")

    return chat_id, message_id


async def cmd_exec(cmd, shell=False):
    if shell:
        proc = await create_subprocess_shell(cmd, stdout=PIPE, stderr=PIPE)
    else:
        proc = await create_subprocess_exec(*cmd, stdout=PIPE, stderr=PIPE)
    stdout, stderr = await proc.communicate()
    try:
        stdout = stdout.decode().strip()
    except Exception:
        stdout = "Unable to decode the response!"
    try:
        stderr = stderr.decode().strip()
    except Exception:
        stderr = "Unable to decode the error!"
    return stdout, stderr, proc.returncode


async def get_media_info(path):
    try:
        result = await cmd_exec([
            "ffprobe", "-hide_banner", "-loglevel", "error",
            "-print_format", "json", "-show_format", path,
        ])
    except Exception as e:
        LOGGER.error(
            f"Get Media Info: {e}. Mostly File not found! - File: {path}"
        )
        return 0, None, None
    if result[0] and result[2] == 0:
        fields = eval(result[0]).get("format")
        if fields is None:
            LOGGER.error(f"get_media_info: {result}")
            return 0, None, None
        duration = round(float(fields.get("duration", 0)))
        tags = fields.get("tags", {})
        artist = tags.get("artist") or tags.get("ARTIST") or tags.get("Artist")
        title  = tags.get("title")  or tags.get("TITLE")  or tags.get("Title")
        return duration, artist, title
    return 0, None, None


async def get_video_resolution(video_path: str) -> tuple[int, int]:
    """
    ffprobe দিয়ে video-র actual width ও height বের করো。
    """
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=s=x:p=0",
            video_path,
        ]
        stdout, stderr, returncode = await cmd_exec(cmd)

        if returncode == 0 and stdout and "x" in stdout:
            parts = stdout.strip().split("x")
            if len(parts) == 2:
                w = int(parts[0].strip())
                h = int(parts[1].strip())
                if w > 0 and h > 0:
                    LOGGER.info(
                        f"[Resolution] Detected: {w}x{h} for {video_path}"
                    )
                    return w, h

        LOGGER.warning(
            f"[Resolution] Could not detect for {video_path}, "
            f"using fallback 1280x720. stderr={stderr}"
        )
        return 1280, 720

    except Exception as e:
        LOGGER.warning(
            f"[Resolution] ffprobe error for {video_path}: {e}, "
            f"using fallback 1280x720"
        )
        return 1280, 720


async def get_video_thumbnail(video_file, duration):
    """
    Extract a thumbnail from a video file.
    ✅ FIXED: scale=320:-2 ব্যবহৃত হয়
    """
    os.makedirs("Assets", exist_ok=True)
    base_name = os.path.splitext(os.path.basename(video_file))[0]
    output = os.path.join("Assets", f"thumb_{base_name}_{int(time())}.jpg")

    if duration is None or duration == 0:
        duration = (await get_media_info(video_file))[0]
    if duration == 0:
        duration = 3

    timestamp = min(duration // 3, 10)
    if timestamp == 0:
        timestamp = 1

    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-ss", f"{timestamp}", "-i", video_file,
        "-vf", "scale=320:-2",
        "-q:v", "2", "-frames:v", "1",
        "-threads", "2", output,
    ]
    try:
        _, err, code = await wait_for(cmd_exec(cmd), timeout=30)
        if code != 0 or not os.path.exists(output):
            LOGGER.error(
                f"ffmpeg thumbnail error. File: {video_file} stderr: {err}"
            )
            fallback_cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-ss", "1", "-i", video_file,
                "-vf", "scale=320:-2",
                "-q:v", "2", "-frames:v", "1",
                "-threads", "1", output,
            ]
            _, err2, code2 = await wait_for(cmd_exec(fallback_cmd), timeout=30)
            if code2 != 0 or not os.path.exists(output):
                LOGGER.error(f"Fallback thumbnail also failed: {err2}")
                return None
    except Exception as e:
        LOGGER.error(
            f"Error extracting thumbnail. Name: {video_file}. Error: {e}"
        )
        if os.path.exists(output):
            try:
                os.remove(output)
            except OSError:
                pass
        return None

    LOGGER.info(f"Thumbnail generated: {output}")
    return output


def progressArgs(action: str, progress_message, start_time):
    return (action, progress_message, start_time, PROGRESS_BAR, "▓", "░")


async def safe_stop_client(user_client):
    """
    Stops user client safely.
    """
    if user_client is None:
        return
    try:
        await asyncio.wait_for(user_client.stop(), timeout=8.0)
    except asyncio.TimeoutError:
        LOGGER.warning("[Client] stop() timeout — forcing disconnect")
        try:
            await user_client.disconnect()
        except Exception:
            pass
    except OSError:
        pass
    except Exception as e:
        err_str = str(e).lower()
        if "closed database" in err_str or "programmingerror" in err_str:
            pass
        else:
            LOGGER.warning(
                f"[Client] stop error (harmless): {type(e).__name__}: {e}"
            )


def create_optimized_user_client(session_name: str, session_string: str):
    """
    Creates a temporary user client for download/upload.
    """
    from pyrogram import Client as PyroClient
    return PyroClient(
        name=session_name,
        session_string=session_string,
        in_memory=True,
        no_updates=True,
        workers=4,
        max_concurrent_transmissions=2,
    )


async def send_media_to_saved(
    user_client,
    bot,
    message,
    media_path,
    media_type,
    caption,
    progress_message,
    start_time,
    thumbnail_path=None,
    width: int = 0,
    height: int = 0,
    duration: int = 0,
):
    """
    Upload a file to the user's own Saved Messages using the user client.
    """
    file_size = os.path.getsize(media_path)

    if not await fileSizeLimit(file_size, message, "upload"):
        await progress_message.delete()
        return False

    saved_messages_chat = "me"
    progress_args_tuple = progressArgs(
        "📤 Uploading to Saved Messages", progress_message, start_time
    )
    LOGGER.info(
        f"[USER CLIENT] Uploading to Saved Messages: {media_path} ({media_type})"
    )

    auto_generated_thumb = None

    try:
        if media_type == "photo":
            await user_client.send_photo(
                chat_id=saved_messages_chat,
                photo=media_path,
                caption=caption or "",
                progress=Leaves.progress_for_pyrogram,
                progress_args=progress_args_tuple,
            )

        elif media_type == "video":
            if duration and duration > 0:
                final_duration = duration
            else:
                final_duration, _, _ = await get_media_info(media_path)
                final_duration = final_duration or 0

            final_thumb = None
            if thumbnail_path and os.path.exists(thumbnail_path):
                final_thumb = thumbnail_path
            else:
                auto_generated_thumb = await get_video_thumbnail(
                    media_path, final_duration
                )
                if auto_generated_thumb and os.path.exists(auto_generated_thumb):
                    final_thumb = auto_generated_thumb

            if width and height and width > 0 and height > 0:
                final_width  = width
                final_height = height
            else:
                final_width, final_height = await get_video_resolution(media_path)

            await user_client.send_video(
                chat_id=saved_messages_chat,
                video=media_path,
                duration=final_duration,
                width=final_width,
                height=final_height,
                thumb=final_thumb,
                caption=caption or "",
                supports_streaming=True,
                progress=Leaves.progress_for_pyrogram,
                progress_args=progress_args_tuple,
            )

        elif media_type == "audio":
            audio_duration, artist, title = await get_media_info(media_path)
            final_audio_duration = (
                duration if duration and duration > 0
                else audio_duration or 0
            )
            await user_client.send_audio(
                chat_id=saved_messages_chat,
                audio=media_path,
                duration=final_audio_duration,
                performer=artist,
                title=title,
                thumb=(
                    thumbnail_path
                    if thumbnail_path and os.path.exists(thumbnail_path)
                    else None
                ),
                caption=caption or "",
                progress=Leaves.progress_for_pyrogram,
                progress_args=progress_args_tuple,
            )

        elif media_type == "document":
            await user_client.send_document(
                chat_id=saved_messages_chat,
                document=media_path,
                thumb=(
                    thumbnail_path
                    if thumbnail_path and os.path.exists(thumbnail_path)
                    else None
                ),
                caption=caption or "",
                progress=Leaves.progress_for_pyrogram,
                progress_args=progress_args_tuple,
            )

        else:
            LOGGER.error(f"Unknown media_type: {media_type}")
            await progress_message.delete()
            return False

        await progress_message.delete()

        await bot.send_message(
            chat_id=message.chat.id,
            text=(
                "**✅ File successfully sent to your Saved Messages! 🚀**\n\n"
                "📂 Open **Telegram → Saved Messages** to find your file.\n\n"
                "__(The bot never stores your files — your privacy is protected)__"
            )
        )

        LOGGER.info(
            f"[USER CLIENT] Upload successful to Saved Messages "
            f"for user {message.from_user.id}"
        )
        return True

    except Exception as e:
        LOGGER.error(f"[USER CLIENT] Error uploading to Saved Messages: {e}")
        try:
            await progress_message.delete()
        except Exception:
            pass
        raise

    finally:
        if auto_generated_thumb and os.path.exists(auto_generated_thumb):
            try:
                os.remove(auto_generated_thumb)
                LOGGER.info(
                    f"Cleaned up auto-generated thumb: {auto_generated_thumb}"
                )
            except Exception as e:
                LOGGER.warning(f"Could not remove temp thumbnail: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# ✅ FIXED: processMediaGroup
# ✅ SHELL MESSAGE FIX: Handle media=False messages in restricted channels
# ═══════════════════════════════════════════════════════════════════════════

async def processMediaGroup(
    chat_message,
    bot,
    message,
    user_client=None,
    log_group_id=None,
    log_user=None,
    log_url=None,
):
    """
    Download a media group and upload it via the user client to Saved Messages.

    ✅ FIXED: প্রতিটি video-র জন্য ffprobe দিয়ে actual width/height নেওয়া হচ্ছে
    আগে width/height দেওয়াই হতো না → Telegram নিজের অনুমান করতো → squish হতো

    ✅ SHELL MESSAGE FIX: Restricted channels return shell messages with
    msg.photo=msg.video=msg.document=msg.audio=None, but msg.media (MessageMediaVideo)
    has the actual data. We download via msg.media.
    """
    media_group_messages = await chat_message.get_media_group()
    valid_media  = []
    temp_paths   = []
    auto_thumbs  = []
    invalid_paths = []

    start_time       = time()
    progress_message = await message.reply("**📥 Downloading media group...**")
    LOGGER.info(
        f"Downloading media group with {len(media_group_messages)} items..."
    )

    for msg in media_group_messages:
        # ✅ Shell message repair: restricted channels return shell messages where
        # msg.photo/msg.video/msg.document/msg.audio are all None,
        # but msg.media (MessageMediaVideo etc.) has the actual media data.
        # Pyrogram's download() can download from msg.media directly.
        if not (msg.photo or msg.video or msg.document or msg.audio):
            LOGGER.info(
                f"[MediaGroup] Shell msg id={msg.id}: "
                f"media={type(msg.media).__name__ if msg.media else None} "
                f"chat={msg.chat.id if msg.chat else None}"
            )
            # Try to refetch via user_client for full media attributes
            _chat_id = None
            if hasattr(msg, 'chat') and msg.chat:
                _chat_id = msg.chat.id
            elif hasattr(chat_message, 'chat') and chat_message.chat:
                _chat_id = chat_message.chat.id
            if _chat_id and msg.id and user_client:
                try:
                    refetched = await user_client.get_messages(
                        chat_id=_chat_id, message_ids=msg.id
                    )
                    if refetched and (refetched.photo or refetched.video or refetched.document or refetched.audio):
                        LOGGER.info(
                            f"[MediaGroup] Shell msg id={msg.id} refetch success: "
                            f"p={bool(refetched.photo)} v={bool(refetched.video)} "
                            f"d={bool(refetched.document)} a={bool(refetched.audio)}"
                        )
                        msg = refetched
                    elif refetched and refetched.media:
                        LOGGER.info(
                            f"[MediaGroup] Shell msg id={msg.id} refetch has media={type(refetched.media).__name__}"
                        )
                        msg = refetched
                    elif refetched:
                        LOGGER.info(
                            f"[MediaGroup] Shell msg id={msg.id} refetched but still no media: "
                            f"media={type(refetched.media).__name__ if refetched.media else None}"
                        )
                except Exception as refetch_e:
                    LOGGER.warning(f"[MediaGroup] Shell msg id={msg.id} refetch failed: {refetch_e}")
            else:
                LOGGER.info(
                    f"[MediaGroup] Shell msg id={msg.id} skip refetch: "
                    f"chat_id={_chat_id} user_client={bool(user_client)}"
                )

        # ✅ Process messages with media (including shell messages via msg.media)
        has_media_attr = msg.photo or msg.video or msg.document or msg.audio
        has_media_obj = hasattr(msg, 'media') and msg.media
        if not has_media_attr and not has_media_obj:
            LOGGER.info(
                f"[MediaGroup] Skipping id={msg.id}: no photo/video/doc/audio, no msg.media"
            )
            continue
        if has_media_obj:
            LOGGER.info(
                f"[MediaGroup] Processing id={msg.id}: has_media={has_media_obj}, "
                f"media_type={type(msg.media).__name__}"
            )
        if msg.photo or msg.video or msg.document or msg.audio or (hasattr(msg, 'media') and msg.media):
            media_path = None
            try:
                # For shell messages (no photo/video/doc/audio but has media), must use download()
                if not (msg.photo or msg.video or msg.document or msg.audio):
                    # Shell message: download via msg.media
                    start_ts = time()
                    progress_msg = await message.reply(
                        f"**📥 Downloading shell msg id={msg.id}...**"
                    )
                    media_path = await chat_message.download(
                        progress=Leaves.progress_for_pyrogram,
                        progress_args=progressArgs(
                            "📥 Downloading", progress_msg, start_ts
                        ),
                    )
                    temp_paths.append(media_path)
                    LOGGER.info(f"[MediaGroup] Shell msg id={msg.id} downloaded: {media_path}")
                else:
                    # Normal message: download media
                    media_path = await msg.download(
                        progress=Leaves.progress_for_pyrogram,
                        progress_args=progressArgs(
                            "📥 Downloading", progress_message, start_time
                        ),
                    )
                    temp_paths.append(media_path)

                caption_text = await get_parsed_msg(
                    msg.caption or "", msg.caption_entities
                )

                # Determine media type
                media_type = None
                if msg.photo:
                    media_type = "photo"
                elif msg.video:
                    media_type = "video"
                elif msg.document:
                    media_type = "document"
                elif msg.audio:
                    media_type = "audio"
                elif hasattr(msg, 'media') and msg.media:
                    media_class = type(msg.media).__name__
                    if media_class == 'MessageMediaPhoto':
                        media_type = "photo"
                    elif media_class == 'MessageMediaVideo':
                        media_type = "video"
                    elif media_class == 'MessageMediaDocument':
                        media_type = "document"
                    elif media_class == 'MessageMediaAudio':
                        media_type = "audio"

                if media_type == "photo":
                    valid_media.append(
                        InputMediaPhoto(media=media_path, caption=caption_text)
                    )

                elif media_type == "video":
                    duration, _, _ = await get_media_info(media_path)

                    vid_width, vid_height = await get_video_resolution(media_path)
                    LOGGER.info(
                        f"[MediaGroup] Video resolution: "
                        f"{vid_width}x{vid_height}, duration={duration}s"
                    )

                    thumb = await get_video_thumbnail(media_path, duration)
                    if thumb:
                        auto_thumbs.append(thumb)

                    valid_media.append(InputMediaVideo(
                        media=media_path,
                        caption=caption_text,
                        duration=duration or 0,
                        width=vid_width,
                        height=vid_height,
                        thumb=thumb,
                        supports_streaming=True,
                    ))

                elif media_type == "document":
                    valid_media.append(
                        InputMediaDocument(
                            media=media_path, caption=caption_text
                        )
                    )

                elif media_type == "audio":
                    duration, artist, title = await get_media_info(media_path)
                    valid_media.append(InputMediaAudio(
                        media=media_path,
                        caption=caption_text,
                        duration=duration or 0,
                        performer=artist,
                        title=title,
                    ))

                else:
                    LOGGER.warning(f"[MediaGroup] Unknown media_type: {media_type}")

            except Exception as e:
                LOGGER.info(f"Error downloading media: {e}")
                if media_path and os.path.exists(media_path):
                    invalid_paths.append(media_path)
                continue

    LOGGER.info(f"Valid media count: {len(valid_media)}")

    if valid_media:
        upload_client = user_client if user_client else bot
        upload_target = "me" if user_client else message.chat.id

        try:
            await upload_client.send_media_group(
                chat_id=upload_target, media=valid_media
            )
            await progress_message.delete()

            if user_client:
                await bot.send_message(
                    chat_id=message.chat.id,
                    text=(
                        "**✅ Media group successfully sent to your "
                        "Saved Messages! 🚀**\n\n"
                        "📂 Open **Telegram → Saved Messages** to find "
                        "your files."
                    )
                )
        except Exception as e:
            err_str = str(e).lower()
            if "topics" in err_str or "messages.init" in err_str:
                # Pyrofork false positive — ignore
                LOGGER.info(
                    f"[MediaGroup] Ignoring Pyrofork false error: {e}"
                )
                try:
                    await progress_message.delete()
                except Exception:
                    pass
                if user_client:
                    await bot.send_message(
                        chat_id=message.chat.id,
                        text=(
                            "**✅ Media group successfully sent to your "
                            "Saved Messages! 🚀**\n\n"
                            "📂 Open **Telegram → Saved Messages** to "
                            "find your files."
                        )
                    )
            else:
                await message.reply(
                    "**❌ Could not send the media group as a batch. "
                    "Sending files individually...**"
                )
                for media in valid_media:
                    try:
                        if isinstance(media, InputMediaPhoto):
                            await upload_client.send_photo(
                                chat_id=upload_target,
                                photo=media.media,
                                caption=media.caption,
                            )
                        elif isinstance(media, InputMediaVideo):
                            await upload_client.send_video(
                                chat_id=upload_target,
                                video=media.media,
                                caption=media.caption,
                                duration=getattr(media, "duration", 0),
                                width=getattr(media, "width", 0),
                                height=getattr(media, "height", 0),
                                thumb=getattr(media, "thumb", None),
                                supports_streaming=True,
                            )
                        elif isinstance(media, InputMediaDocument):
                            await upload_client.send_document(
                                chat_id=upload_target,
                                document=media.media,
                                caption=media.caption,
                            )
                        elif isinstance(media, InputMediaAudio):
                            await upload_client.send_audio(
                                chat_id=upload_target,
                                audio=media.media,
                                caption=media.caption,
                                duration=getattr(media, "duration", 0),
                            )
                    except Exception as individual_e:
                        await message.reply(
                            f"**❌ Failed to upload: {individual_e}**"
                        )
                try:
                    await progress_message.delete()
                except Exception:
                    pass

        if log_group_id and log_user:
            from .tracker import log_file_to_group
            for media_item in valid_media:
                media_path_for_log = getattr(media_item, "media",   None)
                caption_for_log    = getattr(media_item, "caption", "") or ""

                if isinstance(media_item, InputMediaPhoto):
                    media_type_for_log = "photo"
                elif isinstance(media_item, InputMediaVideo):
                    media_type_for_log = "video"
                elif isinstance(media_item, InputMediaAudio):
                    media_type_for_log = "audio"
                else:
                    media_type_for_log = "document"

                if (
                    media_path_for_log
                    and isinstance(media_path_for_log, str)
                    and os.path.exists(media_path_for_log)
                ):
                    try:
                        await log_file_to_group(
                            bot=bot,
                            log_group_id=log_group_id,
                            user=log_user,
                            url=log_url or "",
                            file_path=media_path_for_log,
                            media_type=media_type_for_log,
                            caption_original=caption_for_log,
                        )
                    except Exception as log_err:
                        LOGGER.warning(f"[MediaGroup] Log error: {log_err}")

        for path in temp_paths + invalid_paths + auto_thumbs:
            if os.path.exists(path):
                os.remove(path)

        return True

    await progress_message.delete()
    await message.reply("**❌ No valid media found in the media group.**")
    for path in invalid_paths:
        if os.path.exists(path):
            os.remove(path)
    return False


# ═══════════════════════════════════════════════════════════════════════════
# LEGACY: Deprecated — use send_media_to_saved() instead
# ═══════════════════════════════════════════════════════════════════════════

async def send_media(
    bot,
    message,
    media_path,
    media_type,
    caption,
    progress_message,
    start_time,
    thumbnail_path=None,
):
    """
    DEPRECATED: Direct bot uploads are no longer supported.
    Use send_media_to_saved(user_client, bot, ...) instead.
    """
    LOGGER.warning(
        "send_media() is deprecated. Use send_media_to_saved() with user_client."
    )
    await progress_message.edit_text(
        "**⚠️ System error: Please contact support.**"
    )
    await progress_message.delete()
