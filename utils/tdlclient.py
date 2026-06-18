"""
TDLib 客户端封装 — 模拟真实 Telegram 客户端（Telegram Desktop 同款底层库）
使用 python-telegram 库，底层是 TDLib（C++原生库），与官方客户端共享同一代码路径。

TDLib 会话独立于 Pyrogram，需要单独登录（/tdlogin 命令）。
"""

import os
import time
import threading
from pathlib import Path
from typing import Optional

from utils.logging_setup import LOGGER

# ── TDLib 会话目录 ─────────────────────────────────────────────────
TDLIB_SESSIONS_DIR = Path(__file__).parent.parent / "sessions" / "tdlib"

# ── 全局状态: user_id → TDLib 客户端实例 ──────────────────────────
_tdlib_clients: dict[int, "TDLibClient"] = {}


def is_tdlib_available() -> bool:
    """检查 python-telegram 是否可用。"""
    try:
        import telegram.client  # noqa
        return True
    except ImportError:
        return False


def has_tdlib_session(user_id: int) -> bool:
    """检查用户是否已有 TDLib 会话。"""
    # 检查磁盘上的会话文件
    session_dir = TDLIB_SESSIONS_DIR / str(user_id)
    if session_dir.exists() and any(session_dir.iterdir()):
        return True
    # 检查内存中的活跃客户端
    if user_id in _tdlib_clients and _tdlib_clients[user_id].is_logged_in():
        return True
    return False


class TDLibClient:
    """封装 python-telegram 的 TDLib 客户端。

    使用 TDLib 的低级 JSON API 进行登录和文件下载，
    绕过 Pyrogram 的解析层，模拟真实客户端行为。
    """

    def __init__(self, user_id: int, phone: str, api_id: int, api_hash: str):
        self._user_id = user_id
        self._phone = phone
        self._api_id = api_id
        self._api_hash = api_hash
        self._tg = None
        self._logged_in = False
        self._error: Optional[str] = None
        self._session_dir = TDLIB_SESSIONS_DIR / str(user_id)
        self._session_dir.mkdir(parents=True, exist_ok=True)

    # ── 初始化 ─────────────────────────────────────────────────────

    def _ensure_client(self):
        """确保 TDLib 客户端已创建。"""
        if self._tg is not None:
            return
        from telegram.client import Telegram
        self._tg = Telegram(
            api_id=self._api_id,
            api_hash=self._api_hash,
            phone=self._phone,
            database_encryption_key=f"tdlib_enc_{self._user_id}",
            files_directory=str(self._session_dir / "files"),
            library_path=None,
        )
        LOGGER.info(f"[TDLib] 客户端已创建 for user {self._user_id}")

    # ── 登录 ───────────────────────────────────────────────────────

    def send_code(self):
        """发送验证码（设置手机号，TDLib 自动发送验证码）。"""
        self._ensure_client()
        # 使用 TDLib 低级 API 设置手机号
        self._tg._tdlib.send({
            '@type': 'setAuthenticationPhoneNumber',
            'phone_number': self._phone,
            'settings': {'@type': 'phoneNumberAuthenticationSettings'},
        })
        LOGGER.info(f"[TDLib] 已发送验证码请求 for {self._phone}")
        # 等待验证码发送
        time.sleep(3)

    def login(self, code: str, password: str = None):
        """
        使用验证码登录 TDLib。
        返回: True (成功), '2fa' (需要2FA密码), False (失败)
        """
        self._ensure_client()
        if self._tg is None:
            return False

        try:
            # 检查当前授权状态
            state = self._tg.call_method('getAuthorizationState', {})
            state_type = state.get('@type', '')
            LOGGER.info(f"[TDLib] 授权状态: {state_type}")

            # 如果需要验证码，提交验证码
            if state_type == 'authorizationStateWaitCode':
                result = self._tg.call_method('checkAuthenticationCode', {
                    'code': code,
                })
                LOGGER.info(f"[TDLib] checkAuthenticationCode 结果: {result.get('@type', '?')}")
                # 重新检查状态
                state = self._tg.call_method('getAuthorizationState', {})
                state_type = state.get('@type', '')
                LOGGER.info(f"[TDLib] 验证码后状态: {state_type}")

            # 如果需要 2FA 密码
            if state_type == 'authorizationStateWaitPassword':
                if password:
                    result = self._tg.call_method('checkAuthenticationPassword', {
                        'password': password,
                    })
                    LOGGER.info(f"[TDLib] checkAuthenticationPassword 结果: {result.get('@type', '?')}")
                    state = self._tg.call_method('getAuthorizationState', {})
                    state_type = state.get('@type', '')
                    LOGGER.info(f"[TDLib] 2FA 后状态: {state_type}")
                else:
                    return '2fa'

            # 检查是否登录成功
            if state_type == 'authorizationStateReady':
                self._logged_in = True
                LOGGER.info(f"[TDLib] 登录成功! user={self._user_id}")
                return True

            # 如果状态是 waitCode 但我们已经提交了 code，再等一会
            if state_type == 'authorizationStateWaitCode':
                # 可能 code 还没被处理，再试一次
                time.sleep(2)
                state = self._tg.call_method('getAuthorizationState', {})
                state_type = state.get('@type', '')
                LOGGER.info(f"[TDLib] 重试后状态: {state_type}")
                if state_type == 'authorizationStateReady':
                    self._logged_in = True
                    return True

            if state_type == 'authorizationStateWaitPassword':
                return '2fa'

            LOGGER.warning(f"[TDLib] 登录未完成，当前状态: {state_type}")
            return False

        except Exception as e:
            self._error = str(e)
            LOGGER.error(f"[TDLib] 登录异常: {e}")
            return False

    # ── 状态查询 ───────────────────────────────────────────────────

    def is_logged_in(self) -> bool:
        return self._logged_in

    def get_authorization_state(self) -> str:
        """获取当前授权状态。"""
        if self._tg is None:
            return 'client_not_created'
        try:
            state = self._tg.call_method('getAuthorizationState', {})
            return state.get('@type', 'unknown')
        except Exception as e:
            return f'error: {e}'

    # ── 消息获取 ───────────────────────────────────────────────────

    def get_message(self, chat_id: int, message_id: int) -> Optional[dict]:
        """
        使用 TDLib 获取消息（模拟真实客户端 API 路径）。
        返回 TDLib 消息对象 或 None。
        """
        if self._tg is None or not self._logged_in:
            return None
        try:
            return self._tg.call_method('getMessage', {
                'chat_id': chat_id,
                'message_id': message_id,
            })
        except Exception as e:
            LOGGER.error(f"[TDLib] getMessage({chat_id}, {message_id}) 失败: {e}")
            return None

    # ── 文件下载 ───────────────────────────────────────────────────

    def download_file(self, file_id: int, output_path: str) -> Optional[str]:
        """使用 TDLib 下载文件。"""
        if self._tg is None or not self._logged_in:
            return None
        try:
            result = self._tg.call_method('downloadFile', {
                'file_id': file_id,
                'priority': 32,
                'offset': 0,
                'limit': 0,
                'synchronous': True,
            })
            local = result.get('local', {})
            download_path = local.get('path', '')
            if download_path and os.path.exists(download_path):
                # 如果路径不同，复制到目标路径
                if download_path != output_path:
                    import shutil
                    os.makedirs(os.path.dirname(output_path), exist_ok=True)
                    shutil.copy2(download_path, output_path)
                LOGGER.info(f"[TDLib] downloadFile OK: {output_path}")
                return output_path
            LOGGER.warning(f"[TDLib] downloadFile 完成但路径无效: {result}")
            return None
        except Exception as e:
            LOGGER.error(f"[TDLib] downloadFile({file_id}) 失败: {e}")
            return None

    # ── 清理 ───────────────────────────────────────────────────────

    def stop(self):
        """停止 TDLib 客户端。"""
        if self._tg:
            try:
                self._tg.stop()
            except Exception:
                pass
            self._tg = None
        self._logged_in = False
        if self._user_id in _tdlib_clients:
            _tdlib_clients.pop(self._user_id, None)


# ═══════════════════════════════════════════════════════════════════
# 全局辅助函数
# ═══════════════════════════════════════════════════════════════════

def get_or_create_tdlib_client(
    user_id: int,
    phone: str,
    api_id: int,
    api_hash: str,
) -> TDLibClient:
    """获取或创建 TDLib 客户端实例。"""
    if user_id in _tdlib_clients:
        return _tdlib_clients[user_id]
    client = TDLibClient(user_id, phone, api_id, api_hash)
    _tdlib_clients[user_id] = client
    return client


def download_via_tdlib(user_id: int, chat_id: int, message_id: int) -> Optional[str]:
    """
    使用 TDLib 下载消息中的媒体文件。
    返回下载后的文件路径。
    """
    client = _tdlib_clients.get(user_id)
    if client is None or not client.is_logged_in():
        return None

    try:
        msg = client.get_message(chat_id, message_id)
        if msg is None:
            LOGGER.warning(f"[TDLib] 消息 {message_id} 获取失败")
            return None

        content = msg.get('content', {})
        content_type = content.get('@type', '')
        LOGGER.info(f"[TDLib] msg {message_id} content_type={content_type}")

        # 提取文件信息
        file_info = None
        file_name = f"{message_id}"
        if content_type == 'messageVideo':
            video = content.get('video', {})
            file_info = video.get('video', {})
            file_name = video.get('file_name', file_name) or f"{message_id}.mp4"
        elif content_type == 'messagePhoto':
            photo = content.get('photo', {})
            sizes = photo.get('sizes', [])
            if sizes:
                largest = max(sizes, key=lambda s: s.get('width', 0) * s.get('height', 0))
                file_info = largest.get('photo', {})
            file_name = f"{message_id}.jpg"
        elif content_type == 'messageDocument':
            doc = content.get('document', {})
            file_info = doc.get('document', {})
            file_name = doc.get('file_name', file_name) or f"{message_id}.doc"
        elif content_type == 'messageAudio':
            audio = content.get('audio', {})
            file_info = audio.get('audio', {})
            file_name = audio.get('file_name', file_name) or f"{message_id}.mp3"

        if file_info is None:
            LOGGER.warning(f"[TDLib] msg {message_id} 无媒体文件 (content_type={content_type})")
            return None

        file_id = file_info.get('id')
        if file_id is None:
            LOGGER.warning(f"[TDLib] msg {message_id} 无 file_id")
            return None

        output_dir = Path("downloads") / f"tdlib_{user_id}"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(output_dir / file_name)

        downloaded = client.download_file(file_id, output_path)
        if downloaded and os.path.exists(output_path):
            LOGGER.info(f"[TDLib] 下载成功: {output_path}")
            return output_path

        return None

    except Exception as e:
        LOGGER.error(f"[TDLib] 下载异常: {e}")
        return None


def shutdown_all():
    """停止所有 TDLib 客户端。"""
    for user_id in list(_tdlib_clients.keys()):
        try:
            _tdlib_clients[user_id].stop()
        except Exception:
            pass
    _tdlib_clients.clear()