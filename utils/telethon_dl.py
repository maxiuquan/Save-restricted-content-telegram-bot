"""
Telethon 备用下载器 — Android 设备模拟，处理 MessageMediaUnsupported
参考: bisnuray/RestrictedContentDL 的 Telethon 方案

使用方式:
  1. 先运行 gen_telethon_session.py 生成 Telethon session string
  2. 在 .env 中添加 TELETHON_SESSION=你的session字符串
  3. 当 Pyrofork 遇到 MessageMediaUnsupported 时自动切换 Telethon 下载
"""

import os
import asyncio
import logging
from typing import Optional

LOGGER = logging.getLogger(__name__)

try:
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from telethon.tl.types import (
        MessageMediaPhoto, MessageMediaDocument, MessageMediaUnsupported,
        DocumentAttributeVideo, DocumentAttributeAudio,
    )
    TELETHON_AVAILABLE = True
except ImportError:
    TELETHON_AVAILABLE = False
    LOGGER.warning("Telethon not installed. Run: pip install telethon")


async def get_telethon_client(session_string: str, api_id: int, api_hash: str) -> Optional["TelegramClient"]:
    """
    创建 Telethon 客户端（Android 设备模拟）
    """
    if not TELETHON_AVAILABLE:
        return None

    client = TelegramClient(
        StringSession(session_string),
        api_id,
        api_hash,
        device_model="SM-S9180",
        system_version="Android 13",
        app_version="10.14.0",
        lang_code="zh",
        system_lang_code="zh-CN",
    )
    try:
        await asyncio.wait_for(client.connect(), timeout=10)
        if not await client.is_user_authorized():
            LOGGER.warning("[Telethon] Session not authorized")
            await client.disconnect()
            return None
        return client
    except asyncio.TimeoutError:
        LOGGER.warning("[Telethon] Connection timeout")
        return None
    except Exception as e:
        LOGGER.warning(f"[Telethon] Connection failed: {e}")
        return None


async def telethon_download_media(
    client: "TelegramClient",
    chat_id: int,
    message_id: int,
    download_dir: str,
) -> Optional[str]:
    """
    使用 Telethon 下载消息中的媒体文件
    """
    try:
        from telethon.tl.functions.messages import GetMessagesRequest
        from telethon.tl.types import InputMessageID

        # 先用 get_messages 获取消息
        messages = await client.get_messages(
            chat_id,
            ids=message_id,
        )

        if not messages:
            LOGGER.warning(f"[Telethon] No message found for id={message_id}")
            return None

        msg = messages if not isinstance(messages, list) else messages[0]

        if not msg or not msg.media:
            LOGGER.warning(f"[Telethon] Message {message_id} has no media")
            return None

        media_type = type(msg.media).__name__
        LOGGER.info(f"[Telethon] Message {message_id} media type: {media_type}")

        if isinstance(msg.media, MessageMediaUnsupported):
            # Telethon 也返回了 MessageMediaUnsupported，尝试用 raw API
            LOGGER.warning(f"[Telethon] MessageMediaUnsupported detected, trying raw API...")
            return await _telethon_raw_download(client, chat_id, message_id, download_dir)

        # 正常下载
        path = await client.download_media(msg, file=download_dir)
        if path and os.path.exists(path):
            LOGGER.info(f"[Telethon] Downloaded: {path}")
            return path
        else:
            LOGGER.warning(f"[Telethon] Download returned empty for {message_id}")
            return None

    except Exception as e:
        LOGGER.warning(f"[Telethon] download_media failed: {e}")
        return None


async def _telethon_raw_download(
    client: "TelegramClient",
    chat_id: int,
    message_id: int,
    download_dir: str,
) -> Optional[str]:
    """
    Telethon raw API 兜底下载
    """
    try:
        from telethon.tl.functions.channels import GetMessagesRequest
        from telethon.tl.functions.messages import GetMessagesRequest as MsgGetMessages
        from telethon.tl.types import InputChannel, InputMessageID

        # 尝试获取 channel entity
        try:
            entity = await client.get_entity(chat_id)
        except Exception:
            entity = None

        if hasattr(entity, 'access_hash') and hasattr(entity, 'channel_id'):
            # Supergroup/Channel
            channel = InputChannel(
                channel_id=entity.channel_id,
                access_hash=entity.access_hash,
            )
            result = await client(GetMessagesRequest(
                channel=channel,
                id=[InputMessageID(id=message_id)],
            ))
        else:
            # 普通对话
            result = await client(MsgGetMessages(
                id=[InputMessageID(id=message_id)],
            ))

        if result and hasattr(result, 'messages') and result.messages:
            raw_msg = result.messages[0]
            raw_media = getattr(raw_msg, 'media', None)
            media_type = type(raw_media).__name__ if raw_media else 'None'
            LOGGER.info(f"[Telethon] Raw media type: {media_type}")

            if raw_media and not isinstance(raw_media, MessageMediaUnsupported):
                path = await client.download_media(raw_msg, file=download_dir)
                if path and os.path.exists(path):
                    return path

        return None

    except Exception as e:
        LOGGER.warning(f"[Telethon] raw download failed: {e}")
        return None