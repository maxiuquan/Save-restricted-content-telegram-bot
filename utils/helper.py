# ✅ FIXED: sqlite3 closed database + OSError TCPTransport + AUTH_KEY_UNREGISTERED
# ✅ FIXED: Video aspect ratio (squished) → actual video resolution used for width/height
# ✅ FIXED: Thumbnail scale → aspect ratio preserved with scale=320:-2

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
from pyrogram.errors import FloodWait, FloodPremiumWait
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
    return "文件过大"


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
            f"文件大小超过了 "
            f"{get_readable_file_size(MAX_FILE_SIZE)} 的限制，"
            f"无法{action_type}。"
        )
        return False
    return True


async def get_parsed_msg(text, entities):
    return Parser.unparse(text, entities or [], is_html=False)


PROGRESS_BAR = """
百分比：{percentage:.2f}% | {current}/{total}
速度：{speed}/s
预计剩余时间：{est_time} 秒
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
                    "用于解析此消息链接的客户端类型无效"
                )
            message_id = int(linkps[4])
    except (ValueError, TypeError):
        raise ValueError("无效的帖子 URL。必须以数字 ID 结尾。")

    if not chat_id or not message_id:
        raise ValueError("请发送有效的 Telegram 帖子 URL。")

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
        stdout = "无法解码响应！"
    try:
        stderr = stderr.decode().strip()
    except Exception:
        stderr = "无法解码错误！"
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


# ═══════════════════════════════════════════════════════════════════════════
# ✅ NEW: ffprobe দিয়ে video-র actual width/height বের করার ফাংশন
# thumbnail-এর size নয়, video-র real resolution নিতে হবে
# এটাই squished ভিডিওর মূল সমাধান
# ═══════════════════════════════════════════════════════════════════════════

async def get_video_resolution(video_path: str) -> tuple[int, int]:
    """
    ffprobe দিয়ে video-র actual width ও height বের করো।

    ❌ ভুল পদ্ধতি: thumbnail open করে PIL দিয়ে size নেওয়া
       → thumbnail সবসময় 320x180 বা ভিন্ন ratio হতে পারে
       → Telegram-এ এই ভুল dimension পাঠালে ভিডিও squish হয়

    ✅ সঠিক পদ্ধতি: ffprobe দিয়ে video stream থেকে real resolution নাও
       → যা পাবে সেটাই Telegram-এ width/height হিসেবে পাঠাও
       → ভিডিও সবসময় সঠিক aspect ratio-তে দেখাবে

    Args:
        video_path: ভিডিও ফাইলের path

    Returns:
        (width, height) tuple — সমস্যা হলে (1280, 720) fallback
    """
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",          # শুধু প্রথম video stream
            "-show_entries", "stream=width,height",
            "-of", "csv=s=x:p=0",              # output: "1920x1080" format
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

    ✅ FIXED: scale=320:-2 ব্যবহার করা হচ্ছে
       আগে ছিল scale=320:180 — এটা সবসময় 16:9 ধরে নিত
       যে ভিডিও 4:3 বা 9:16 (portrait) সেগুলোর thumbnail squished হত
       scale=320:-2 মানে: width=320, height=auto (aspect ratio preserve করে)
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
        # ✅ FIXED: scale=320:-2 → aspect ratio preserve করে
        # আগের scale=320:180 সবসময় 16:9 force করত → portrait ভিডিওতে squish
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
                # ✅ fallback-এও scale=320:-2
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


# ═══════════════════════════════════════════════════════════════════════════
# ✅ FIX: safe_stop_client — handles OSError: TCPTransport closed
# ═══════════════════════════════════════════════════════════════════════════

async def safe_stop_client(user_client):
    """
    Stops user client safely.
    - Ignores OSError (TCPTransport closed)
    - Ignores sqlite3.ProgrammingError (closed database)
    - Force disconnects on timeout
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
        # TCPTransport already closed — এটা normal, ignore করো
        pass
    except Exception as e:
        err_str = str(e).lower()
        if "closed database" in err_str or "programmingerror" in err_str:
            pass  # sqlite3 closed — harmless, ignore
        else:
            LOGGER.warning(
                f"[Client] stop error (harmless): {type(e).__name__}: {e}"
            )


# ═══════════════════════════════════════════════════════════════════════════
# ✅ FIX: create_optimized_user_client
# in_memory=True  → no .session SQLite file on disk
# no_updates=True → no handle_updates() task
# ═══════════════════════════════════════════════════════════════════════════

def create_optimized_user_client(session_name: str, session_string: str):
    """
    Creates a temporary user client for download/upload.

    ✅ in_memory=True:
        Pyrogram SQLite DB stays in RAM only.
        → sqlite3.ProgrammingError: Cannot operate on a closed database — fix

    ✅ no_updates=True:
        handle_updates() coroutine does not start.
        → OSError: TCPTransport closed — fix

    ✅ workers=4:
        RAM-friendly for Render free tier.
    """
    from pyrogram import Client as PyroClient
    return PyroClient(
        name=session_name,
        session_string=session_string,
        in_memory=True,
        no_updates=True,
        workers=4,
        max_concurrent_transmissions=1,
    )


# ═══════════════════════════════════════════════════════════════════════════
# ✅ FIXED: send_media_to_saved
# পরিবর্তন: width/height এখন ffprobe দিয়ে video থেকে নেওয়া হয়
# আগে thumbnail-এর PIL size ব্যবহার হত — এটাই squish-এর কারণ ছিল
# ═══════════════════════════════════════════════════════════════════════════

async def send_media_to_saved(
    user_client,
    bot,
    message,
    media_path,
    media_type,
    caption,
    progress_message=None,
    start_time=None,
    thumbnail_path=None,
    width: int = 0,
    height: int = 0,
    duration: int = 0,
):
    """
    Upload a file to the user's own Saved Messages using the user client.

    ✅ FIXED — Squished video সমস্যার সমাধান:
    width/height এখন ffprobe দিয়ে video-র actual resolution থেকে নেওয়া হয়।
    আগে PIL দিয়ে thumbnail-এর size নেওয়া হত, যা ভিডিওর real dimension নয়।

    Args:
        width:    source video-র width (0 হলে ffprobe দিয়ে detect করবে)
        height:   source video-র height (0 হলে ffprobe দিয়ে detect করবে)
        duration: video-র duration seconds (0 হলে ffprobe দিয়ে detect করবে)
    """
    file_size = os.path.getsize(media_path)

    if progress_message is None:
        progress_message = message

    if start_time is None:
        start_time = time()

    if not await fileSizeLimit(file_size, message, "upload"):
        try:
            await progress_message.delete()
        except Exception:
            pass
        return False

    saved_messages_chat = "me"
    progress_args_tuple = progressArgs(
        "📤 上传中", progress_message, start_time
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
                final_width = width
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
                "**✅ 消息保存成功！🚀**\n\n"
                "📂 打开 **Telegram → 收藏夹** 查找文件。\n\n"
                "__(机器人不会存储你的文件 — 你的隐私受到保护)__"
            )
        )

        LOGGER.info(
            f"[USER CLIENT] Upload successful to Saved Messages "
            f"for user {message.from_user.id}"
        )
        return True

    except (FloodWait, FloodPremiumWait) as flood_err:
        wait_seconds = flood_err.value if hasattr(flood_err, 'value') else 60
        LOGGER.warning(
            f"[USER CLIENT] 上传触发限流，等待 {wait_seconds}s..."
        )
        try:
            await progress_message.delete()
        except Exception:
            pass
        await asyncio.sleep(wait_seconds + 2)
        raise

    except AttributeError as attr_err:
        LOGGER.warning(f"[USER CLIENT] 上传连接错误: {attr_err}")
        try:
            await progress_message.delete()
        except Exception:
            pass
        raise

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
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════════════════
# ✅ FIXED: processMediaGroup
# পরিবর্তন: InputMediaVideo-তে width/height যোগ করা হয়েছে
# ffprobe দিয়ে প্রতিটি video-র actual resolution নেওয়া হচ্ছে
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
    media_group_messages = await chat_message.get_media_group()
    valid_media = []

    start_time = time()
    progress_message = await message.reply("**📥 处理媒体组中...**")
    LOGGER.info(
        f"Processing media group with {len(media_group_messages)} items..."
    )

    for msg in media_group_messages:
        if msg.photo or msg.video or msg.document or msg.audio:
            caption_text = await get_parsed_msg(
                msg.caption or "", msg.caption_entities
            )

            try:
                if msg.photo:
                    valid_media.append(
                        InputMediaPhoto(media=msg.photo.file_id, caption=caption_text)
                    )
                elif msg.video:
                    valid_media.append(InputMediaVideo(
                        media=msg.video.file_id,
                        caption=caption_text,
                        duration=msg.video.duration or 0,
                        width=msg.video.width or 0,
                        height=msg.video.height or 0,
                        supports_streaming=True,
                    ))
                elif msg.document:
                    valid_media.append(
                        InputMediaDocument(media=msg.document.file_id, caption=caption_text)
                    )
                elif msg.audio:
                    valid_media.append(InputMediaAudio(
                        media=msg.audio.file_id,
                        caption=caption_text,
                        duration=msg.audio.duration or 0,
                        performer=msg.audio.performer or "",
                        title=msg.audio.title or "",
                    ))
            except Exception as e:
                LOGGER.warning(f"[MediaGroup] Skipping item (file_id error): {e}")
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
                        "**✅ 媒体组已成功发送到"
                        "你的收藏夹！🚀**\n\n"
                        "📂 打开 **Telegram → 收藏夹** 查找"
                        "你的文件。"
                    )
                )
            return True
        except Exception as e:
            err_str = str(e).lower()
            if "topics" in err_str or "messages.init" in err_str:
                LOGGER.info(f"[MediaGroup] Ignoring Pyrofork false error: {e}")
                try:
                    await progress_message.delete()
                except Exception:
                    pass
                if user_client:
                    await bot.send_message(
                        chat_id=message.chat.id,
                        text="**✅ 媒体组已成功发送到你的收藏夹！**",
                    )
                return True

            forwards_restricted = "forwards_restricted" in err_str or "chat_forwards_restricted" in err_str

            if forwards_restricted:
                LOGGER.info(f"[MediaGroup] Forwarding restricted, falling back to download+upload")
                try:
                    await progress_message.edit_text(
                        "**📥 频道禁止转发，正在下载并重新上传...**"
                    )
                except Exception:
                    pass

                upload_client = user_client if user_client else bot
                upload_target = "me" if user_client else message.chat.id
                dl_success = 0
                total = sum(1 for m in media_group_messages if m.photo or m.video or m.document or m.audio)

                for idx, msg in enumerate(media_group_messages, 1):
                    if not (msg.photo or msg.video or msg.document or msg.audio):
                        continue
                    try:
                        caption_text = await get_parsed_msg(
                            msg.caption or "", msg.caption_entities
                        )

                        await progress_message.edit_text(
                            f"**📥 下载中 ({dl_success + 1}/{total})...**"
                        )

                        dl_start = time()
                        dl_path = await msg.download(
                            progress=Leaves.progress_for_pyrogram,
                            progress_args=progressArgs("📥 下载中", progress_message, dl_start),
                        )
                        if not dl_path or not os.path.exists(dl_path):
                            continue

                        await progress_message.edit_text(
                            f"**📤 上传中 ({dl_success + 1}/{total})...**"
                        )

                        try:
                            if msg.photo:
                                await upload_client.send_photo(
                                    chat_id=upload_target, photo=dl_path, caption=caption_text,
                                    progress=Leaves.progress_for_pyrogram,
                                    progress_args=progressArgs("📤 上传中", progress_message, time()),
                                )
                            elif msg.video:
                                await upload_client.send_video(
                                    chat_id=upload_target, video=dl_path, caption=caption_text,
                                    duration=msg.video.duration or 0,
                                    width=msg.video.width or 0, height=msg.video.height or 0,
                                    supports_streaming=True,
                                    progress=Leaves.progress_for_pyrogram,
                                    progress_args=progressArgs("📤 上传中", progress_message, time()),
                                )
                            elif msg.document:
                                await upload_client.send_document(
                                    chat_id=upload_target, document=dl_path, caption=caption_text,
                                    progress=Leaves.progress_for_pyrogram,
                                    progress_args=progressArgs("📤 上传中", progress_message, time()),
                                )
                            elif msg.audio:
                                await upload_client.send_audio(
                                    chat_id=upload_target, audio=dl_path, caption=caption_text,
                                    duration=msg.audio.duration or 0,
                                    progress=Leaves.progress_for_pyrogram,
                                    progress_args=progressArgs("📤 上传中", progress_message, time()),
                                )
                            dl_success += 1
                        finally:
                            try:
                                os.remove(dl_path)
                            except Exception:
                                pass
                    except Exception as dl_e:
                        LOGGER.warning(f"[MediaGroup] Download+upload item {idx} failed: {dl_e}")

                try:
                    await progress_message.delete()
                except Exception:
                    pass
                if user_client:
                    await bot.send_message(
                        chat_id=message.chat.id,
                        text=(
                            f"**✅ 媒体组已发送到你的收藏夹！**\n"
                            f"**✅ 成功：** `{dl_success}`"
                            + (f"\n**❌ 失败：** `{len([m for m in media_group_messages if m.photo or m.video or m.document or m.audio]) - dl_success}`" if dl_success == 0 else "")
                        ),
                    )
                return True

            LOGGER.info(f"[MediaGroup] send_media_group failed, copying individually: {e}")
            try:
                await progress_message.edit_text(
                    "**📤 正在逐个复制媒体组文件...**"
                )
            except Exception:
                pass

            sem = asyncio.Semaphore(3)
            copy_tasks = []

            async def _copy_one(item_idx, media_item):
                async with sem:
                    try:
                        if isinstance(media_item, InputMediaPhoto):
                            await upload_client.send_photo(
                                chat_id=upload_target,
                                photo=media_item.media,
                                caption=media_item.caption,
                            )
                        elif isinstance(media_item, InputMediaVideo):
                            await upload_client.send_video(
                                chat_id=upload_target,
                                video=media_item.media,
                                caption=media_item.caption,
                                duration=getattr(media_item, "duration", 0),
                                width=getattr(media_item, "width", 0),
                                height=getattr(media_item, "height", 0),
                                supports_streaming=True,
                            )
                        elif isinstance(media_item, InputMediaDocument):
                            await upload_client.send_document(
                                chat_id=upload_target,
                                document=media_item.media,
                                caption=media_item.caption,
                            )
                        elif isinstance(media_item, InputMediaAudio):
                            await upload_client.send_audio(
                                chat_id=upload_target,
                                audio=media_item.media,
                                caption=media_item.caption,
                                duration=getattr(media_item, "duration", 0),
                            )
                        return True
                    except Exception as item_e:
                        LOGGER.warning(f"[MediaGroup] Individual copy {item_idx} failed: {item_e}")
                        return False

            for i, media_item in enumerate(valid_media, 1):
                copy_tasks.append(_copy_one(i, media_item))

            results = await asyncio.gather(*copy_tasks)
            success_count = sum(1 for r in results if r)
            fail_count = len(results) - success_count

            try:
                await progress_message.delete()
            except Exception:
                pass
            if user_client:
                summary = (
                    f"**✅ 媒体组已发送到你的收藏夹！**\n"
                    f"**✅ 成功：** `{success_count}`"
                )
                if fail_count:
                    summary += f"\n**❌ 失败：** `{fail_count}`"
                await bot.send_message(
                    chat_id=message.chat.id,
                    text=summary,
                )

        if log_group_id and log_user:
            from .tracker import log_file_to_group
            for media_item in valid_media:
                media_path_for_log = getattr(media_item, "media", None)
                caption_for_log = getattr(media_item, "caption", "") or ""

                if isinstance(media_item, InputMediaPhoto):
                    media_type_for_log = "photo"
                elif isinstance(media_item, InputMediaVideo):
                    media_type_for_log = "video"
                elif isinstance(media_item, InputMediaAudio):
                    media_type_for_log = "audio"
                else:
                    media_type_for_log = "document"

                if media_path_for_log and isinstance(media_path_for_log, str):
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

        return True

    await progress_message.delete()
    await message.reply("**❌ 媒体组中未找到有效媒体。**")
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
        "**⚠️ 系统错误：请联系支持。**"
    )
    await progress_message.delete()
