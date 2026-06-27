"""
TelethonUserClient — Telethon 包装器，提供 Pyrofork 兼容接口
Android 设备模拟，直接替代 Pyrofork user_client
"""

import os
import asyncio
import logging
from typing import Optional, List, AsyncGenerator, Any

LOGGER = logging.getLogger(__name__)

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import (
    MessageMediaPhoto, MessageMediaDocument, MessageMediaUnsupported,
    MessageMediaGeo, MessageMediaContact,
    DocumentAttributeVideo, DocumentAttributeAudio, DocumentAttributeAnimated,
    DocumentAttributeSticker,
    InputPeerChannel, InputPeerChat, InputPeerUser, InputPeerSelf,
    PeerChannel, PeerChat, PeerUser,
    InputChannel, InputMessageID,
    MessageEntityBold, MessageEntityItalic, MessageEntityCode,
    MessageEntityPre, MessageEntityTextUrl, MessageEntityUrl,
    MessageEntityMention, MessageEntityHashtag, MessageEntityBotCommand,
    MessageEntityEmail, MessageEntityPhone, MessageEntityCashtag,
    MessageEntityUnderline, MessageEntityStrikethrough, MessageEntityBlockquote,
    MessageEntitySpoiler, MessageEntityCustomEmoji,
    TypeMessageEntity,
)
from telethon.tl.functions.channels import GetMessagesRequest as ChannelsGetMessages
from telethon.tl.functions.messages import (
    GetMessagesRequest, GetHistoryRequest, ForwardMessagesRequest,
    GetDiscussionMessageRequest,
)
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.errors import (
    ChatForwardsRestrictedError, MessageIdInvalidError,
    ChannelPrivateError, ChatWriteForbiddenError,
)
from telethon import utils as telethon_utils


# ── Message wrapper ──────────────────────────────────────────────────────
class TelethonMessage:
    """包装 Telethon Message 对象，提供 Pyrofork 兼容属性"""

    def __init__(self, raw_msg, client: "TelethonUserClient"):
        self._raw = raw_msg
        self._client = client
        self.id = raw_msg.id
        self.empty = False

        # Media flags
        self.media = raw_msg.media is not None and not isinstance(raw_msg.media, MessageMediaUnsupported)
        self._media = raw_msg.media

        # Specific media types
        self.video = self._is_video()
        self.photo = isinstance(raw_msg.media, MessageMediaPhoto)
        self.document = self._is_document()
        self.audio = self._is_audio()
        self.voice = self._is_voice()
        self.video_note = self._is_video_note()
        self.animation = self._is_animation()
        self.sticker = self._is_sticker()

        # Group
        self.media_group_id = getattr(raw_msg, 'grouped_id', None)

        # Text
        self.text = raw_msg.message or ""
        self.caption = raw_msg.message or ""
        self.caption_entities = self._convert_entities(getattr(raw_msg, 'entities', None))

        # Forward info
        fwd = getattr(raw_msg, 'fwd_from', None)
        if fwd:
            from_id = getattr(fwd, 'from_id', None)
            if from_id:
                self.forward_from_chat = getattr(from_id, 'channel_id', None)
                if self.forward_from_chat:
                    self.forward_from_chat = type('obj', (object,), {'id': self.forward_from_chat})()
            self.forward_from_message_id = getattr(fwd, 'channel_post', None)
        else:
            self.forward_from_chat = None
            self.forward_from_message_id = None

    def _is_video(self):
        if not isinstance(self._media, MessageMediaDocument):
            return False
        doc = self._media.document
        for attr in doc.attributes:
            if isinstance(attr, DocumentAttributeVideo) and not attr.round_message:
                return True
        return False

    def _is_document(self):
        return isinstance(self._media, MessageMediaDocument) and not (
            self._is_video() or self._is_audio() or self._is_voice() or
            self._is_video_note() or self._is_animation() or self._is_sticker()
        )

    def _is_audio(self):
        if not isinstance(self._media, MessageMediaDocument):
            return False
        for attr in self._media.document.attributes:
            if isinstance(attr, DocumentAttributeAudio) and not attr.voice:
                return True
        return False

    def _is_voice(self):
        if not isinstance(self._media, MessageMediaDocument):
            return False
        for attr in self._media.document.attributes:
            if isinstance(attr, DocumentAttributeAudio) and attr.voice:
                return True
        return False

    def _is_video_note(self):
        if not isinstance(self._media, MessageMediaDocument):
            return False
        for attr in self._media.document.attributes:
            if isinstance(attr, DocumentAttributeVideo) and attr.round_message:
                return True
        return False

    def _is_animation(self):
        if not isinstance(self._media, MessageMediaDocument):
            return False
        for attr in self._media.document.attributes:
            if isinstance(attr, DocumentAttributeAnimated):
                return True
        return False

    def _is_sticker(self):
        if not isinstance(self._media, MessageMediaDocument):
            return False
        for attr in self._media.document.attributes:
            if isinstance(attr, DocumentAttributeSticker):
                return True
        return False

    def _convert_entities(self, entities):
        if not entities:
            return None
        # Telethon entities are already compatible
        return entities

    async def download(self, file_name: str = None, progress=None, progressArgs=None):
        """下载媒体文件"""
        return await self._client.download_media(
            self._raw, file_name=file_name, progress=progress, progressArgs=progressArgs
        )

    async def get_media_group(self):
        """获取同一媒体组的所有消息"""
        return await self._client._get_media_group(self._raw)


# ── TelethonUserClient ───────────────────────────────────────────────────
class TelethonUserClient:
    """
    Telethon 客户端包装器，提供 Pyrofork 兼容的方法签名。
    使用 Android 设备模拟绕过受限内容。
    """

    def __init__(self, session_string: str, api_id: int, api_hash: str):
        self._session = session_string
        self._api_id = api_id
        self._api_hash = api_hash
        self._client: Optional[TelegramClient] = None
        self.peers_by_id = {}  # Pyrofork 兼容: peer 缓存
        self._peer_cache = {}

    async def start(self):
        """连接到 Telegram"""
        self._client = TelegramClient(
            StringSession(self._session),
            self._api_id,
            self._api_hash,
            device_model="SM-S9180",
            system_version="Android 13",
            app_version="10.14.0",
            lang_code="zh",
            system_lang_code="zh-CN",
        )
        await self._client.connect()
        if not await self._client.is_user_authorized():
            raise RuntimeError("Telethon session not authorized")
        LOGGER.info("[TelethonClient] Connected with Android device emulation")
        return self

    async def disconnect(self):
        if self._client:
            await self._client.disconnect()

    async def stop(self):
        """Pyrofork 兼容: stop() 别名"""
        await self.disconnect()

    async def __aenter__(self):
        return await self.start()

    async def __aexit__(self, *args):
        await self.disconnect()

    # ── Peer resolution ──────────────────────────────────────────────
    async def resolve_peer(self, chat_id):
        """解析 chat_id 为 peer，兼容 Pyrofork"""
        try:
            entity = await self._client.get_entity(chat_id)
            # 缓存
            key = int(str(chat_id).replace('-100', '').replace('-', ''))
            if hasattr(entity, 'access_hash'):
                self.peers_by_id[chat_id] = type('obj', (object,), {
                    'access_hash': entity.access_hash,
                    'channel_id': getattr(entity, 'id', 0),
                })()
                self._peer_cache[chat_id] = entity
            return entity
        except Exception as e:
            LOGGER.warning(f"[TelethonClient] resolve_peer failed for {chat_id}: {e}")
            raise

    # ── Message fetching ─────────────────────────────────────────────
    async def get_chat_history(
        self, chat_id, offset_id: int = 0, limit: int = 100
    ) -> AsyncGenerator[TelethonMessage, None]:
        """获取聊天历史，兼容 Pyrofork 的 get_chat_history"""
        try:
            entity = await self._get_cached_entity(chat_id)
            if not entity:
                entity = await self._client.get_entity(chat_id)

            messages = await self._client.get_messages(
                entity,
                limit=limit,
                offset_id=offset_id,
            )
            if messages:
                for msg in messages:
                    if msg:
                        yield TelethonMessage(msg, self)
        except Exception as e:
            LOGGER.warning(f"[TelethonClient] get_chat_history failed: {e}")

    async def get_messages(self, chat_id, message_ids):
        """获取指定消息，兼容 Pyrofork"""
        try:
            entity = await self._get_cached_entity(chat_id)
            if not entity:
                entity = await self._client.get_entity(chat_id)

            msgs = await self._client.get_messages(entity, ids=message_ids)
            if not msgs:
                return []
            if not isinstance(msgs, list):
                msgs = [msgs]
            return [TelethonMessage(m, self) for m in msgs if m]
        except Exception as e:
            LOGGER.warning(f"[TelethonClient] get_messages failed: {e}")
            return []

    async def _get_cached_entity(self, chat_id):
        if chat_id in self._peer_cache:
            return self._peer_cache[chat_id]
        return None

    # ── Download ─────────────────────────────────────────────────────
    async def download_media(
        self, message, file_name: str = None, progress=None, progressArgs=None
    ):
        """下载媒体，兼容 Pyrofork"""
        raw_msg = message._raw if isinstance(message, TelethonMessage) else message
        return await self._client.download_media(
            raw_msg,
            file=file_name,
            progress_callback=self._make_progress(progress, progressArgs) if progress else None,
        )

    def _make_progress(self, progress_callback, progress_args):
        """将 Pyrofork 的 progress 回调转换为 Telethon 格式"""
        async def wrapper(current, total):
            if progress_callback:
                await progress_callback(current, total, *progress_args)
        return wrapper

    # ── Upload ───────────────────────────────────────────────────────
    async def send_video(self, chat_id, file_path, caption="", duration=0,
                         width=0, height=0, thumb=None, supports_streaming=True,
                         parse_mode=None):
        kwargs = dict(
            caption=caption,
            duration=duration,
            width=width,
            height=height,
            supports_streaming=supports_streaming,
        )
        if thumb and os.path.exists(thumb) if isinstance(thumb, str) else True:
            kwargs['thumb'] = thumb
        return await self._client.send_file(chat_id, file_path, **kwargs)

    async def send_photo(self, chat_id, file_path, caption="", parse_mode=None):
        return await self._client.send_file(chat_id, file_path, caption=caption)

    async def send_document(self, chat_id, file_path, caption="", thumb=None, parse_mode=None):
        kwargs = dict(caption=caption, force_document=True)
        if thumb and os.path.exists(thumb) if isinstance(thumb, str) else True:
            kwargs['thumb'] = thumb
        return await self._client.send_file(chat_id, file_path, **kwargs)

    async def send_audio(self, chat_id, file_path, caption="", duration=0,
                         performer=None, title=None, thumb=None):
        kwargs = dict(caption=caption, duration=duration)
        if performer:
            kwargs['performer'] = performer
        if title:
            kwargs['title'] = title
        if thumb:
            kwargs['thumb'] = thumb
        return await self._client.send_file(chat_id, file_path, **kwargs)

    async def send_video_note(self, chat_id, file_path, duration=0, thumb=None):
        kwargs = dict(video_note=True, duration=duration)
        return await self._client.send_file(chat_id, file_path, **kwargs)

    async def send_voice(self, chat_id, file_path, caption="", duration=0):
        kwargs = dict(voice_note=True, caption=caption)
        if duration:
            kwargs['duration'] = duration
        return await self._client.send_file(chat_id, file_path, **kwargs)

    async def send_message(self, chat_id, text, parse_mode=None, reply_to_message_id=None):
        return await self._client.send_message(chat_id, text, parse_mode=parse_mode,
                                               reply_to=reply_to_message_id)

    # ── Forward / Copy ───────────────────────────────────────────────
    async def forward_messages(self, chat_id, from_chat_id, message_ids):
        try:
            entity = await self._get_cached_entity(from_chat_id)
            if not entity:
                entity = await self._client.get_entity(from_chat_id)
            return await self._client.forward_messages(chat_id, message_ids, entity)
        except ChatForwardsRestrictedError:
            raise
        except Exception as e:
            LOGGER.warning(f"[TelethonClient] forward_messages failed: {e}")
            raise

    async def copy_message(self, chat_id, from_chat_id, message_id):
        try:
            entity = await self._get_cached_entity(from_chat_id)
            if not entity:
                entity = await self._client.get_entity(from_chat_id)
            msg = await self._client.get_messages(entity, ids=message_id)
            if msg:
                return await self._client.send_message(chat_id, msg.message or "")
        except Exception as e:
            LOGGER.warning(f"[TelethonClient] copy_message failed: {e}")
            raise

    # ── Raw API compatibility layer ────────────────────────────────────
    # 提供 Pyrofork 兼容的 raw.functions / raw.types 接口

    class _RawCompat:
        """Pyrofork raw API 兼容层"""
        def __init__(self, client: "TelethonUserClient"):
            self._client = client

        class functions:
            class channels:
                @staticmethod
                async def GetChannels(client, id):
                    from telethon.tl.functions.channels import GetChannelsRequest
                    return await client._client(GetChannelsRequest(id=id))

                @staticmethod
                async def GetMessages(client, channel, id):
                    from telethon.tl.functions.channels import GetMessagesRequest as CGM
                    return await client._client(CGM(channel=channel, id=id))

            class messages:
                @staticmethod
                async def ForwardMessages(client, from_peer, id, to_peer, random_id, noforwards=False):
                    from telethon.tl.functions.messages import ForwardMessagesRequest
                    return await client._client(ForwardMessagesRequest(
                        from_peer=from_peer,
                        id=id,
                        to_peer=to_peer,
                        random_id=random_id,
                        noforwards=noforwards,
                    ))

        class types:
            from telethon.tl.types import (
                InputChannel, InputMessageID, InputPeerChannel, InputPeerSelf,
                MessageMediaUnsupported, MessageMediaPhoto, MessageMediaDocument,
                MessageMediaVideo, Message, DocumentAttributeVideo,
                DocumentAttributeAudio, DocumentAttributeAnimated,
                DocumentAttributeSticker,
            )

            @staticmethod
            def InputPeerChannel(channel_id, access_hash):
                from telethon.tl.types import InputPeerChannel
                return InputPeerChannel(channel_id=channel_id, access_hash=access_hash)

            @staticmethod
            def InputChannel(channel_id, access_hash):
                from telethon.tl.types import InputChannel
                return InputChannel(channel_id=channel_id, access_hash=access_hash)

            @staticmethod
            def InputMessageID(id):
                from telethon.tl.types import InputMessageID
                return InputMessageID(id=id)

            @staticmethod
            def InputPeerSelf():
                from telethon.tl.types import InputPeerSelf
                return InputPeerSelf()

    @property
    def raw(self):
        return self._RawCompat(self)

    async def invoke(self, func):
        """调用 raw API，自动翻译 Pyrofork 函数到 Telethon"""
        from telethon.tl.functions.channels import GetChannelsRequest, GetMessagesRequest as CGM
        from telethon.tl.functions.messages import ForwardMessagesRequest

        # 转换为 Telethon 函数
        func_cls_name = type(func).__name__
        func_module = getattr(type(func), '__module__', '')

        if 'pyrogram' in func_module or 'pyrofork' in func_module:
            # Pyrofork function → translate to Telethon
            if func_cls_name == 'GetChannels':
                telethon_func = GetChannelsRequest(
                    id=[getattr(i, 'channel_id', getattr(i, 'id', 0)) for i in func.id]
                )
            elif func_cls_name == 'GetMessages':
                telethon_func = CGM(
                    channel=func.channel,
                    id=func.id,
                )
            elif func_cls_name == 'ForwardMessages':
                telethon_func = ForwardMessagesRequest(
                    from_peer=func.from_peer,
                    id=func.id,
                    to_peer=func.to_peer,
                    random_id=func.random_id,
                    noforwards=getattr(func, 'noforwards', False),
                )
            else:
                # Unknown function, try to pass through
                LOGGER.warning(f"[TelethonClient] Unknown Pyrofork function: {func_cls_name}, trying passthrough")
                telethon_func = func
        else:
            telethon_func = func

        return await self._client(telethon_func)

    # ── Media group ──────────────────────────────────────────────────
    async def _get_media_group(self, raw_msg):
        """获取同一媒体组的所有消息"""
        grouped_id = getattr(raw_msg, 'grouped_id', None)
        if not grouped_id:
            return []

        try:
            # 获取消息所在的对话
            peer_id = getattr(raw_msg, 'peer_id', None)
            if not peer_id:
                return []

            entity = None
            if isinstance(peer_id, PeerChannel):
                entity = await self._client.get_entity(int(f"-100{peer_id.channel_id}"))
            elif isinstance(peer_id, PeerChat):
                entity = await self._client.get_entity(int(f"-{peer_id.chat_id}"))
            elif isinstance(peer_id, PeerUser):
                entity = await self._client.get_entity(peer_id.user_id)

            if not entity:
                return []

            # 搜索附近的消息
            messages = await self._client.get_messages(
                entity,
                limit=100,
                offset_id=raw_msg.id + 50,
            )
            # 也搜索前面的
            messages_before = await self._client.get_messages(
                entity,
                limit=100,
                offset_id=raw_msg.id - 50,
                reverse=True,
            )

            all_msgs = []
            if messages_before:
                all_msgs.extend(messages_before)
            if messages:
                all_msgs.extend(messages)

            # 过滤同一 media_group_id
            group_msgs = []
            for m in all_msgs:
                if m and getattr(m, 'grouped_id', None) == grouped_id:
                    group_msgs.append(TelethonMessage(m, self))

            return sorted(group_msgs, key=lambda x: x.id)
        except Exception as e:
            LOGGER.warning(f"[TelethonClient] _get_media_group failed: {e}")
            return []