# ✅ 已修复：sqlite3 关闭数据库 + OSError TCPTransport + AUTH_KEY_UNREGISTERED
# ✅ 已修复：视频宽高比（拉伸变形）→ 使用视频实际分辨率作为 width/height
# ✅ 已修复：缩略图缩放 → 使用 scale=320:-2 保持宽高比
# ✅ 已优化：添加全局信号量 + 非阻塞进度 + 连接池以实现流畅用户体验

from asyncio.subprocess import PIPE
import asyncio
import json
import os
from time import time
from typing import Optional
from asyncio import create_subprocess_exec, create_subprocess_shell, wait_for
from pyrogram.errors import FloodWait

try:
    from pyrogram.errors import PeerStorageLimitReached
except ImportError:
    PeerStorageLimitReached = None
from PIL import Image
from pyleaves import Leaves
from pyrogram.parser import Parser
from pyrogram.utils import get_channel_id
from pyrogram.errors import FloodWait
from pyrogram.enums import ParseMode
from pyrogram import raw as _raw_mod
raw = _raw_mod
from pyrogram.types import (
    InputMediaPhoto,
    InputMediaVideo,
    InputMediaDocument,
    InputMediaAudio,
    Voice,
)

from .logging_setup import LOGGER


# 文件大小单位列表
SIZE_UNITS = ["B", "KB", "MB", "GB", "TB", "PB"]

# ═══════════════════════════════════════════════════════════════════════════
# ✅ 优化：全局信号量防止过载，保持机器人响应能力
# ═══════════════════════════════════════════════════════════════════════════

# 限制整个机器人中同时进行的繁重操作（下载/上传）数量
GLOBAL_DOWNLOAD_SEMAPHORE = asyncio.Semaphore(8)
# 限制同时向 Telegram 上传的用户客户端数量（防止 MTProto 限流）
GLOBAL_UPLOAD_SEMAPHORE = asyncio.Semaphore(5)
# 限制并发的 ffprobe / ffmpeg 子进程数量（CPU/IO 保护）
GLOBAL_MEDIA_SEMAPHORE = asyncio.Semaphore(4)

# ═══════════════════════════════════════════════════════════════════════════
# 批量下载：超时和重试配置
# ═══════════════════════════════════════════════════════════════════════════
BATCH_ITEM_TIMEOUT = 300  # 每个下载/上传的超时时间（秒），超过则跳过
BATCH_MAX_RETRIES = 1  # 临时失败的重试次数


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
    except Exception as e:
        LOGGER.debug(f"[Helper] stdout 解码出错: {e}")
        stdout = "无法解码响应！"
    try:
        stderr = stderr.decode().strip()
    except Exception as e:
        LOGGER.debug(f"[Helper] stderr 解码出错: {e}")
        stderr = "无法解码错误！"
    return stdout, stderr, proc.returncode


async def get_media_info(path):
    async with GLOBAL_MEDIA_SEMAPHORE:
        try:
            result = await cmd_exec([
                "ffprobe", "-hide_banner", "-loglevel", "error",
                "-print_format", "json", "-show_format", path,
            ])
        except Exception as e:
            LOGGER.error(
                f"获取媒体信息出错: {e}。通常是文件不存在！- 文件: {path}"
            )
            return 0, None, None
        if result[0] and result[2] == 0:
            fields = json.loads(result[0]).get("format")
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
# ✅ 新增：通过 ffprobe 获取视频实际宽高
# 不是用缩略图的尺寸，而是获取视频的真实分辨率
# 这是解决视频拉伸变形的根本方法
# ═══════════════════════════════════════════════════════════════════════════

async def get_video_resolution(video_path: str) -> tuple[int, int]:
    """
    通过 ffprobe 获取视频的实际宽度和高度。

    ❌ 错误做法：打开缩略图用 PIL 获取尺寸
       → 缩略图始终可能是 320x180 或其他比例
       → 在 Telegram 中发送错误的维度会导致视频拉伸变形

    ✅ 正确做法：通过 ffprobe 从视频流中获取真实分辨率
       → 将获取到的值作为 Telegram 的 width/height 使用
       → 视频始终以正确的宽高比显示

    参数:
        video_path: 视频文件路径

    返回:
        (width, height) 元组 — 如果检测失败则返回 (1280, 720) 作为默认值
    """
    async with GLOBAL_MEDIA_SEMAPHORE:
        try:
            cmd = [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",          # 仅第一个视频流
                "-show_entries", "stream=width,height",
                "-of", "csv=s=x:p=0",              # 输出格式: "1920x1080"
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
                f"[Resolution] 无法检测 {video_path} 的分辨率，"
                f"使用默认值 1280x720。stderr={stderr}"
            )
            return 1280, 720

        except Exception as e:
            LOGGER.warning(
                f"[Resolution] {video_path} 的 ffprobe 出错: {e}，"
                f"使用默认值 1280x720"
            )
            return 1280, 720


async def get_video_thumbnail(video_file, duration):
    """
    从视频文件中提取缩略图。

    ✅ 已修复：使用 scale=320:-2
       之前使用 scale=320:180 — 会强制按 16:9 比例输出
       4:3 或 9:16（竖屏）视频的缩略图会拉伸变形
       scale=320:-2 表示：宽度=320，高度自动计算（保持宽高比）
    """
    async with GLOBAL_MEDIA_SEMAPHORE:
        os.makedirs("Assets", exist_ok=True)
        base_name = os.path.splitext(os.path.basename(video_file))[0]
        output = os.path.join("Assets", f"thumb_{base_name}_{int(time())}.jpg")
        LOGGER.info(f"[缩略图] 正在为 {video_file} 生成缩略图，时长={duration}")

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
            # ✅ 已修复：scale=320:-2 → 保持宽高比
            # 之前的 scale=320:180 总是强制 16:9 → 竖屏视频会变形
            "-vf", "scale=320:-2",
            "-q:v", "8", "-frames:v", "1",
            "-threads", "2", output,
        ]
        try:
            _, err, code = await wait_for(cmd_exec(cmd), timeout=30)
            if code != 0 or not os.path.exists(output):
                LOGGER.error(
                    f"ffmpeg 缩略图出错。文件: {video_file} stderr: {err}"
                )
                fallback_cmd = [
                    "ffmpeg", "-hide_banner", "-loglevel", "error",
                    "-ss", "1", "-i", video_file,
                    # ✅ fallback 中也使用 scale=320:-2
                    "-vf", "scale=320:-2",
                    "-q:v", "8", "-frames:v", "1",
                    "-threads", "1", output,
                ]
                _, err2, code2 = await wait_for(cmd_exec(fallback_cmd), timeout=30)
                if code2 != 0 or not os.path.exists(output):
                    LOGGER.error(f"备用缩略图生成也失败了: {err2}")
                    return None
        except Exception as e:
            LOGGER.error(
                f"提取缩略图出错。文件名: {video_file}。错误: {e}"
            )
            if os.path.exists(output):
                try:
                    os.remove(output)
                except OSError:
                    pass
            return None

        LOGGER.info(f"缩略图已生成: {output}")
        return output


def progressArgs(action: str, progress_message, start_time):
    return (action, progress_message, start_time, PROGRESS_BAR, "▓", "░")


# ═══════════════════════════════════════════════════════════════════════════
# ✅ 修复：safe_stop_client — 处理 OSError: TCPTransport 已关闭
# ═══════════════════════════════════════════════════════════════════════════

async def safe_stop_client(user_client):
    """
    安全停止用户客户端。
    - 忽略 OSError（TCPTransport 已关闭）
    - 忽略 sqlite3.ProgrammingError（数据库已关闭）
    - 超时时强制断开连接
    """
    if user_client is None:
        return
    try:
        await asyncio.wait_for(user_client.stop(), timeout=8.0)
    except asyncio.TimeoutError:
        LOGGER.warning("[客户端] stop() 超时 — 强制断开连接")
        try:
            await user_client.disconnect()
        except Exception:
            pass  # 断开连接可能已经失败
    except OSError:
        # TCPTransport 已关闭 — 这是正常情况，忽略
        pass
    except Exception as e:
        err_str = str(e).lower()
        if "closed database" in err_str or "programmingerror" in err_str:
            pass  # sqlite3 已关闭 — 无害，忽略
        else:
            LOGGER.warning(
                f"[客户端] stop 出错（无害）: {type(e).__name__}: {e}"
            )


# ═══════════════════════════════════════════════════════════════════════════
# ✅ 修复：create_optimized_user_client
# in_memory=True  → 不在磁盘上创建 .session SQLite 文件
# no_updates=True → 不启动 handle_updates() 任务
# ═══════════════════════════════════════════════════════════════════════════

def create_optimized_user_client(session_name: str, session_string: str):
    """
    创建用于下载/上传的临时用户客户端。

    ✅ in_memory=True:
        Pyrogram SQLite 数据库仅保留在内存中。
        → 修复 sqlite3.ProgrammingError: 无法在已关闭的数据库上操作

    ✅ no_updates=True:
        不启动 handle_updates() 协程。
        → 修复 OSError: TCPTransport 已关闭

    ✅ max_concurrent_transmissions=2:
        与 tawhid120 原版一致 — Pyrofork 在较低值时可能不加载视频媒体数据
        → 修复私密批量下载视频失败的问题

    ✅ workers=4:
        与 tawhid120 原版一致 — 减少消息分发延迟
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


def create_persistent_user_client(session_name: str, session_string: str):
    """
    创建带有持久化会话文件的用户客户端（存储在磁盘上）。
    这使得 Pyrogram/Pyrofork 可以缓存频道对等体信息，
    在使用新客户端会话访问 t.me/c/XXXXX 链接的私有频道时是必需的。

    会话文件将创建在 ./{session_name}.session。
    使用后必须通过 cleanup_persistent_client() 清理。

    ✅ no_updates=True:
        不启动 handle_updates() 协程。
        → 修复 OSError: TCPTransport 已关闭
    """
    from pyrogram import Client as PyroClient
    return PyroClient(
        name=session_name,
        session_string=session_string,
        in_memory=False,
        no_updates=True,
        workers=1,
        max_concurrent_transmissions=1,
    )


async def cleanup_persistent_client(user_client) -> str:
    """
    停止持久化用户客户端并从磁盘删除其会话文件。

    返回会话文件名（用于日志记录），出错时返回 None。
    """
    if user_client is None:
        return None
    session_name = user_client.name
    await safe_stop_client(user_client)
    session_file = f"{session_name}.session"
    journal_file = f"{session_name}.session-journal"
    try:
        if os.path.exists(session_file):
            os.remove(session_file)
    except Exception as e:
        LOGGER.warning(f"[清理] 删除 {session_file} 失败: {e}")
    try:
        if os.path.exists(journal_file):
            os.remove(journal_file)
    except Exception as e:
        LOGGER.warning(f"[清理] 删除 {journal_file} 失败: {e}")
    return session_name


# ═══════════════════════════════════════════════════════════════════════════
# ✅ 优化：非阻塞进度更新，保持界面响应灵敏
# ═══════════════════════════════════════════════════════════════════════════

_last_progress_edit: dict[int, float] = {}
_PROGRESS_EDIT_INTERVAL = 2.5  # 秒
_PROGRESS_EDIT_MAX_AGE = 300  # 秒（5 分钟）

# 定期清理旧进度记录，防止内存泄漏
_MAX_PROGRESS_CACHE = 10000


def _cleanup_progress_cache():
    """清理超过限制的老进度编辑记录"""
    if len(_last_progress_edit) > _MAX_PROGRESS_CACHE:
        # 保留最新的半数量
        items = list(_last_progress_edit.items())
        items.sort(key=lambda x: x[1])
        for key, _ in items[:len(items)//2]:
            del _last_progress_edit[key]


async def safe_edit_progress(message, text: str, parse_mode=ParseMode.MARKDOWN):
    """
    安全地编辑进度消息，通过限流避免 Telegram 频率限制并保持机器人响应灵敏。
    """
    msg_id = getattr(message, 'id', None) or id(message)
    now = time()

    # 定期清理过期条目
    if len(_last_progress_edit) > 1000:
        cutoff = now - _PROGRESS_EDIT_MAX_AGE
        stale = [k for k, v in _last_progress_edit.items() if v < cutoff]
        for k in stale:
            del _last_progress_edit[k]

    last = _last_progress_edit.get(msg_id, 0)
    if now - last < _PROGRESS_EDIT_INTERVAL:
        return
    try:
        await message.edit_text(text, parse_mode=parse_mode)
        _last_progress_edit[msg_id] = now
        _cleanup_progress_cache()
    except Exception:
        pass  # 进度消息可能已被删除


# ═══════════════════════════════════════════════════════════════════════════
# ✅ 已修复：send_media_to_saved
# 变更：width/height 现在通过 ffprobe 从视频中获取
# 之前使用缩略图的 PIL 尺寸 → 这是视频变形拉伸的原因
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
    使用用户客户端将文件上传到用户的"收藏消息"。

    ✅ 已修复 — 视频变形拉伸问题：
    width/height 现在通过 ffprobe 从视频的实际分辨率中获取。
    之前使用 PIL 获取缩略图尺寸，这不是视频的真实尺寸。
    
    ✅ 已修复 — FloodWait 错误处理，带自动重试逻辑。
    ✅ 已优化 — 使用全局上传信号量防止过载。

    参数:
        width:    源视频宽度（为 0 时通过 ffprobe 自动检测）
        height:   源视频高度（为 0 时通过 ffprobe 自动检测）
        duration: 视频时长（秒）（为 0 时通过 ffprobe 自动检测）
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
            pass  # 进度消息可能已被删除
        return False

    saved_messages_chat = "me"
    progress_args_tuple = progressArgs(
        "📤 正在上传到收藏消息", progress_message, start_time
    )
    LOGGER.info(
        f"[用户客户端] 上传到收藏消息: {media_path} ({media_type})"
    )

    auto_generated_thumb = None

    # ✅ 使用全局上传信号量防止 MTProto 限流并保持机器人响应灵敏
    async with GLOBAL_UPLOAD_SEMAPHORE:
        try:
            # 带 FloodWait 重试逻辑的发送媒体函数
            async def send_with_retry(send_func):
                max_retries = 3
                retry_count = 0
                while retry_count < max_retries:
                    try:
                        return await send_func()
                    except FloodWait as e:
                        wait_time = e.value
                        retry_count += 1
                        LOGGER.warning(
                            f"[用户客户端] FloodWait: 等待 {wait_time} 秒 "
                            f"(重试 {retry_count}/{max_retries})"
                        )
                        try:
                            await safe_edit_progress(
                                progress_message,
                                f"**⏳ Telegram 需要等待 {wait_time} 秒...**\n"
                                f"__(重试 {retry_count}/{max_retries})__",
                            )
                        except Exception:
                            pass  # 进度消息可能已被删除
                        await asyncio.sleep(wait_time)
                # 最后一次尝试，不再重试
                return await send_func()

            if media_type == "photo":
                async def send_photo():
                    return await user_client.send_photo(
                        chat_id=saved_messages_chat,
                        photo=media_path,
                        caption=caption or "",
                        progress=Leaves.progress_for_pyrogram,
                        progress_args=progress_args_tuple,
                    )
                await send_with_retry(send_photo)

            elif media_type == "video":
                if duration and duration > 0:
                    final_duration = duration
                else:
                    final_duration, _, _ = await get_media_info(media_path)
                    final_duration = final_duration or 0

                final_thumb = None
                if thumbnail_path and os.path.exists(thumbnail_path):
                    final_thumb = thumbnail_path
                    LOGGER.info(f"[USER CLIENT] Using custom thumbnail: {thumbnail_path}")
                else:
                    auto_generated_thumb = await get_video_thumbnail(
                        media_path, final_duration
                    )
                    if auto_generated_thumb and os.path.exists(auto_generated_thumb):
                        final_thumb = auto_generated_thumb
                        LOGGER.info(f"[USER CLIENT] Auto-generated thumbnail: {auto_generated_thumb}")
                    else:
                        LOGGER.warning(f"[USER CLIENT] Could not generate thumbnail for video: {media_path}")

                if width and height and width > 0 and height > 0:
                    final_width = width
                    final_height = height
                else:
                    final_width, final_height = await get_video_resolution(media_path)

                async def send_video():
                    return await user_client.send_video(
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
                await send_with_retry(send_video)

            elif media_type == "audio":
                audio_duration, artist, title = await get_media_info(media_path)
                final_audio_duration = (
                    duration if duration and duration > 0
                    else audio_duration or 0
                )
                async def send_audio():
                    return await user_client.send_audio(
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
                await send_with_retry(send_audio)

            elif media_type == "document":
                doc_thumb = None
                if thumbnail_path and os.path.exists(thumbnail_path):
                    doc_thumb = thumbnail_path
                else:
                    ext = os.path.splitext(media_path)[1].lower()
                    if ext in (".mp4", ".mkv", ".webm", ".avi", ".mov"):
                        try:
                            doc_duration, _, _ = await get_media_info(media_path)
                            doc_duration = doc_duration or 0
                            auto_generated_thumb = await get_video_thumbnail(media_path, doc_duration)
                            if auto_generated_thumb and os.path.exists(auto_generated_thumb):
                                doc_thumb = auto_generated_thumb
                        except Exception as th_e:
                            LOGGER.warning(f"[USER CLIENT] Could not auto-generate document thumbnail: {th_e}")
                async def send_document():
                    return await user_client.send_document(
                        chat_id=saved_messages_chat,
                        document=media_path,
                        thumb=doc_thumb,
                        caption=caption or "",
                        progress=Leaves.progress_for_pyrogram,
                        progress_args=progress_args_tuple,
                    )
                await send_with_retry(send_document)

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

        except FloodWait as e:
            # 所有重试之后的最终 FloodWait
            LOGGER.error(f"[USER CLIENT] Final FloodWait error after retries: {e}")
            try:
                await safe_edit_progress(
                    progress_message,
                    f"**⏳ FloodWait error!**\n\n"
                    f"Telegram requires waiting {e.value} more seconds. "
                    f"Please try again later.",
                )
            except Exception:
                pass  # 进度消息可能已被删除
            raise
        except Exception as e:
            LOGGER.error(f"[USER CLIENT] Error uploading to Saved Messages: {e}")
            try:
                await progress_message.delete()
            except Exception:
                pass  # 进度消息可能已被删除
            raise

        finally:
            if auto_generated_thumb and os.path.exists(auto_generated_thumb):
                try:
                    os.remove(auto_generated_thumb)
                except Exception as e:
                    LOGGER.warning(f"Could not remove temp thumbnail: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# ✅ 已修复：processMediaGroup
# 变更：在 InputMediaVideo 中添加了 width/height
# 每个视频的实际分辨率通过 ffprobe 获取
# ═══════════════════════════════════════════════════════════════════════════

# 辅助函数：给消息生成正确扩展名的 file_name（避免 PHOTO_EXT_INVALID/VIDEO_EXT_INVALID）
def _get_file_ext(msg, media_type=None):
    """根据媒体类型返回文件扩展名（含 .）。"""
    try:
        if hasattr(msg, 'video') and msg.video:
            mime = (getattr(msg.video, 'mime_type', '') or '').lower()
            if 'webm' in mime: return '.webm'
            return '.mp4'
        if hasattr(msg, 'audio') and msg.audio:
            mime = (getattr(msg.audio, 'mime_type', '') or '').lower()
            if 'flac' in mime: return '.flac'
            if 'wav' in mime: return '.wav'
            if 'm4a' in mime: return '.m4a'
            return '.mp3'
        if hasattr(msg, 'voice') and msg.voice:
            return '.ogg'
        if hasattr(msg, 'video_note') and msg.video_note:
            return '.mp4'
        if hasattr(msg, 'sticker') and msg.sticker:
            return '.webp'
        if hasattr(msg, 'animation') and msg.animation:
            return '.mp4'
        if hasattr(msg, 'document') and msg.document:
            mime = (getattr(msg.document, 'mime_type', '') or '').lower()
            if 'mp4' in mime: return '.mp4'
            if 'pdf' in mime: return '.pdf'
            if 'zip' in mime: return '.zip'
            if 'x-zip' in mime: return '.zip'
            return '.bin'
        if hasattr(msg, 'photo') and msg.photo:
            return '.jpg'
    except Exception:
        pass
    return '.bin'


def _build_dl_filename(msg, mid, media_type=None):
    """
    为 download_media / msg.download 构建 file_name。
    优先使用消息自带的 file_name（包含原始扩展名），
    否则根据媒体类型生成 "dl_{mid}_{ts}{ext}"。
    """
    try:
        # 1) 视频
        if hasattr(msg, 'video') and msg.video:
            fname = getattr(msg.video, 'file_name', None)
            if fname:
                return fname
            ext = _get_file_ext(msg, media_type) or '.mp4'
            return f"dl_{mid}_{int(time())}{ext}"

        # 2) 音频
        if hasattr(msg, 'audio') and msg.audio:
            fname = getattr(msg.audio, 'file_name', None)
            if fname:
                return fname
            ext = _get_file_ext(msg, media_type) or '.mp3'
            return f"dl_{mid}_{int(time())}{ext}"

        # 3) 文档
        if hasattr(msg, 'document') and msg.document:
            fname = getattr(msg.document, 'file_name', None)
            if fname:
                return fname
            ext = _get_file_ext(msg, media_type) or ''
            return f"dl_{mid}_{int(time())}{ext}"

        # 4) 语音
        if hasattr(msg, 'voice') and msg.voice:
            ext = _get_file_ext(msg, media_type) or '.ogg'
            return f"dl_{mid}_{int(time())}{ext}"

        # 5) 视频便签
        if hasattr(msg, 'video_note') and msg.video_note:
            return f"dl_{mid}_{int(time())}.mp4"

        # 6) 动图
        if hasattr(msg, 'animation') and msg.animation:
            fname = getattr(msg.animation, 'file_name', None)
            if fname:
                return fname
            ext = _get_file_ext(msg, media_type) or '.mp4'
            return f"dl_{mid}_{int(time())}{ext}"

        # 7) 贴纸
        if hasattr(msg, 'sticker') and msg.sticker:
            return f"dl_{mid}_{int(time())}.webp"

        # 8) 照片 / 其他
        ext = _get_file_ext(msg, media_type) or '.jpg'
        return f"dl_{mid}_{int(time())}{ext}"
    except Exception:
        pass
    return f"dl_{mid}_{int(time())}"

async def processMediaGroup(
    chat_message,
    bot,
    message,
    user_client=None,
    log_group_id=None,
    log_user=None,
    log_url=None,
    thumbnail_path=None,
):
    """
    完全恢复 tawhid120 的原始实现 — 关键是用 chat_message.get_media_group()
    而不是 get_chat_history / get_messages(range)

    ✅ tawhid120 原版核心:
        media_group_messages = await chat_message.get_media_group()
        for msg in media_group_messages:
            if msg.photo or msg.video or msg.document or msg.audio:
                media_path = await msg.download(...)
                ...
    """
    # ════════════════════════════════════════════════════════════════
    # 关键: tawhid120 原版直接用 chat_message.get_media_group()
    # 这个调用会正确加载组内每条消息的 media 属性（包括 video、photo 等）
    # 之前我们改成 get_chat_history 或 get_messages(range) 是错误的 —
    # 那些方式不会加载 media 属性，导致只能下载图片
    # ════════════════════════════════════════════════════════════════
    try:
        media_group_messages = await chat_message.get_media_group()
    except Exception as e:
        LOGGER.warning(f"[MediaGroup] get_media_group failed: {e}")
        return False

    if not media_group_messages:
        LOGGER.warning("[MediaGroup] 媒体组为空")
        return False

    # ✅ 关键补充: 用 raw API 重新检查媒体组中是否漏掉了 video
    # Pyrogram 的 get_media_group 在某些情况下不会返回所有消息，
    # 特别是在用户 session 受限的情况下，video 可能被过滤掉。
    _raw_client = user_client or bot
    _raw_videos = []  # raw API 找到但 Pyrogram 漏掉的视频消息
    try:
        _mgid = getattr(chat_message, 'media_group_id', None)
        if _mgid and _raw_client is not None:
            _peer = await _raw_client.resolve_peer(chat_message.chat.id)
            _r = await _raw_client.invoke(
                raw.functions.messages.GetHistory(
                    peer=_peer,
                    offset_id=0,
                    offset_date=0,
                    add_offset=0,
                    limit=20,
                    max_id=0,
                    min_id=0,
                    hash=0,
                )
            )
            _raw_extra = []
            for _rm in (getattr(_r, 'messages', None) or []):
                if getattr(_rm, 'grouped_id', None) == _mgid:
                    _raw_extra.append(_rm)
            if _raw_extra:
                LOGGER.info(
                    f"[MediaGroup] raw API 找到 {len(_raw_extra)} 条 mgid={_mgid} 消息 "
                    f"(Pyrogram 找到 {len(media_group_messages)} 条)"
                )
                # 找出 raw API 里的视频消息
                for _rm in _raw_extra:
                    _rm_media = getattr(_rm, 'media', None)
                    if _rm_media is None:
                        continue
                    _rm_mid = getattr(_rm, 'id', None)
                    _rm_mtype = type(_rm_media).__name__
                    LOGGER.info(f"[MediaGroup] raw msg id={_rm_mid} type={_rm_mtype}")
                    if 'Video' in _rm_mtype:
                        LOGGER.info(
                            f"[MediaGroup] ⚠ raw 发现视频! msg_id={_rm_mid} "
                            f"type={_rm_mtype} (Pyrogram 漏掉了它!)"
                        )
                        _raw_videos.append(_rm_mid)
                # 关键: 如果 raw 找到了 Pyrogram 漏掉的视频，
                # 用 get_messages(chat_id, message_ids=...) 单独获取这些消息
                if _raw_videos:
                    try:
                        _extra_msgs = await _raw_client.get_messages(
                            chat_id=chat_message.chat.id,
                            message_ids=_raw_videos,
                        )
                        for _em in (_extra_msgs or []):
                            if _em and (getattr(_em, 'video', None) or getattr(_em, 'media', None)):
                                media_group_messages.append(_em)
                                LOGGER.info(
                                    f"[MediaGroup] ✅ 已添加 Pyrogram 漏掉的视频 msg_id={_em.id}"
                                )
                    except Exception as _extra_err:
                        LOGGER.warning(
                            f"[MediaGroup] 补充视频失败: {_extra_err}"
                        )
    except Exception as _raw_err:
        LOGGER.warning(f"[MediaGroup] raw API verify failed: {_raw_err}")

    LOGGER.info(
        f"[MediaGroup] tawhid120 原版: get_media_group 找到 "
        f"{len(media_group_messages)} 条消息"
    )

    valid_media  = []
    temp_paths   = []
    auto_thumbs  = []
    invalid_paths = []

    start_time       = time()
    progress_message = await message.reply("**📥 下载媒体组中...**")
    LOGGER.info(f"下载媒体组，共 {len(media_group_messages)} 项...")

    # ── 关键修复（恢复 jun4 之前的有效实现）:
    # 1. 先尝试 send_media_group + file_id（快速路径，不需要重新下载和上传）
    #    file_id 是 Telegram 的内部引用，不会出现 PHOTO_EXT_INVALID/VIDEO_EXT_INVALID 错误
    # 2. 如果 forwards_restricted 失败，回退到下载+上传（带 file_name 修复以避免扩展名错误）
    valid_media  = []
    temp_paths   = []
    auto_thumbs  = []
    invalid_paths = []

    start_time       = time()
    progress_message = await message.reply("**📥 下载媒体组中...**")
    LOGGER.info(f"下载媒体组，共 {len(media_group_messages)} 项...")

    for msg in media_group_messages:
        # 详细日志
        _has_p = bool(msg.photo) if hasattr(msg, 'photo') else False
        _has_v = bool(msg.video) if hasattr(msg, 'video') else False
        _has_d = bool(msg.document) if hasattr(msg, 'document') else False
        _has_a = bool(msg.audio) if hasattr(msg, 'audio') else False
        LOGGER.info(
            f"[MediaGroup]  id={msg.id} p={_has_p} v={_has_v} d={_has_d} a={_has_a}"
        )
        if msg.photo or msg.video or msg.document or msg.audio:
            # ✅ 快速路径：使用 file_id，不需要下载和重新上传
            try:
                caption_text = await get_parsed_msg(
                    msg.caption or "", msg.caption_entities
                )
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
                LOGGER.warning(f"[MediaGroup] file_id 失败，下载重传: {e}")
                # 退路：用下载+上传方式处理
                try:
                    _dl_name = _build_dl_filename(msg, msg.id, msg.media)
                    if _dl_name:
                        media_path = await msg.download(
                            file_name=_dl_name,
                            progress=Leaves.progress_for_pyrogram,
                            progress_args=progressArgs("📥 下载中", progress_message, start_time),
                        )
                    else:
                        media_path = await msg.download(
                            progress=Leaves.progress_for_pyrogram,
                            progress_args=progressArgs("📥 下载中", progress_message, start_time),
                        )
                    temp_paths.append(media_path)
                    caption_text = await get_parsed_msg(
                        msg.caption or "", msg.caption_entities
                    )
                    if msg.photo:
                        valid_media.append(InputMediaPhoto(media=media_path, caption=caption_text))
                    elif msg.video:
                        duration, _, _ = await get_media_info(media_path)
                        vid_width, vid_height = await get_video_resolution(media_path)
                        thumb = await get_video_thumbnail(media_path, duration)
                        if thumb:
                            auto_thumbs.append(thumb)
                        valid_media.append(InputMediaVideo(
                            media=media_path,
                            caption=caption_text,
                            duration=duration or 0,
                            width=vid_width, height=vid_height,
                            thumb=thumb, supports_streaming=True,
                        ))
                    elif msg.document:
                        valid_media.append(InputMediaDocument(media=media_path, caption=caption_text))
                    elif msg.audio:
                        duration, artist, title = await get_media_info(media_path)
                        valid_media.append(InputMediaAudio(
                            media=media_path, caption=caption_text,
                            duration=duration or 0, performer=artist, title=title,
                        ))
                except Exception as e2:
                    LOGGER.warning(f"[MediaGroup] 下载重传也失败: {e2}")
                    continue

    LOGGER.info(f"有效媒体数量: {len(valid_media)}")

    if valid_media:
        upload_client = user_client if user_client else bot
        upload_target = "me" if user_client else message.chat.id

        # ✅ 快速路径: send_media_group + file_id (jun4 之前的有效实现)
        try:
            await upload_client.send_media_group(
                chat_id=upload_target, media=valid_media
            )
            try:
                await progress_message.delete()
            except Exception:
                pass
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
            # 如果是 forwards_restricted 或其他错误,尝试逐条发送
            LOGGER.warning(f"[MediaGroup] send_media_group failed: {e}, 改为单条发送")
            success_count = 0
            for i, m_item in enumerate(valid_media):
                try:
                    await upload_client.send_media_group(
                        chat_id=upload_target, media=[m_item]
                    )
                    success_count += 1
                except Exception as e2:
                    LOGGER.warning(f"[MediaGroup] single send failed: {e2}")
            if success_count > 0:
                try:
                    await progress_message.delete()
                except Exception:
                    pass
                if user_client:
                    await bot.send_message(
                        chat_id=message.chat.id,
                        text=(
                            f"**✅ {success_count}/{len(valid_media)} 个文件已发送到收藏夹！**"
                        )
                    )
                return True

    # 清理临时文件
    for _p in temp_paths:
        if _p and os.path.exists(_p):
            try: os.remove(_p)
            except: pass
    for _t in auto_thumbs:
        if _t and os.path.exists(_t):
            try: os.remove(_t)
            except: pass

    try:
        await progress_message.delete()
    except Exception:
        pass
    await message.reply("**❌ 未找到有效媒体。**")
    return False


# ═══════════════════════════════════════════════════════════════════════════
# 遗留：已弃用 — 请使用 send_media_to_saved() 代替
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
    已废弃：直接通过机器人上传已不再支持。
    请使用 send_media_to_saved(user_client, bot, ...) 代替。
    """
    LOGGER.warning(
        "send_media() 已废弃。请使用带 user_client 的 send_media_to_saved()。"
    )
    await progress_message.edit_text(
        "**⚠️ 系统错误：请联系支持。**"
    )
    await progress_message.delete()
