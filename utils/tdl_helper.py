"""
tdl (Telegram Download Library) 集成模块
https://github.com/iyear/tdl

将 Pyrogram session_string 转换为 tdl 会话文件，
使用 tdl 二进制进行高速多线程下载。
"""

import os
import json
import base64
import struct
import hashlib
import asyncio
import shutil
from pathlib import Path
from typing import Optional

from utils.logging_setup import LOGGER

# ── tdl 会话目录 ─────────────────────────────────────────────────
TDL_SESSION_DIR = Path(__file__).parent.parent / "sessions" / "tdl"

# ── DC 服务器地址映射 ────────────────────────────────────────────
DC_ADDR_MAP = {
    1: "149.154.175.50:443",
    2: "149.154.167.50:443",
    3: "149.154.169.50:443",
    4: "149.154.175.100:443",
    5: "91.108.56.100:443",
}


def is_tdl_installed() -> bool:
    """检查 tdl 是否已安装。"""
    return shutil.which("tdl") is not None


def pyrogram_session_to_tdl(session_string: str, user_id: int) -> Optional[str]:
    """
    将 Pyrogram session_string 转换为 tdl 会话文件。

    参数:
        session_string: Pyrogram 的 session_string (base64)
        user_id: 用户 ID，用于命名会话文件

    返回:
        tdl 会话文件路径，失败返回 None
    """
    try:
        # ── 解码 Pyrogram session_string ─────────────────────────
        # 格式: base64_urlsafe( struct.pack("!B256s", dc_id, auth_key) )
        try:
            decoded = base64.urlsafe_b64decode(session_string + "=" * (-len(session_string) % 4))
        except Exception:
            decoded = base64.b64decode(session_string)

        if len(decoded) < 257:
            LOGGER.error(f"[tdl] session_string 太短: {len(decoded)} bytes")
            return None

        dc_id = decoded[0]
        auth_key = decoded[1:257]

        # ── 计算 auth_key_id (SHA1) ──────────────────────────────
        auth_key_id = hashlib.sha1(auth_key).digest()

        # ── 获取服务器地址 ────────────────────────────────────────
        addr = DC_ADDR_MAP.get(dc_id, "149.154.167.50:443")

        # ── 构建 tdl 会话 JSON ────────────────────────────────────
        tdl_session = {
            "dc": dc_id,
            "addr": addr,
            "auth_key": base64.b64encode(auth_key).decode(),
            "auth_key_id": base64.b64encode(auth_key_id).decode(),
        }

        # ── 写入文件 ──────────────────────────────────────────────
        TDL_SESSION_DIR.mkdir(parents=True, exist_ok=True)
        session_file = TDL_SESSION_DIR / f"{user_id}.json"
        session_file.write_text(json.dumps(tdl_session, indent=2))

        LOGGER.info(
            f"[tdl] 会话已创建: {session_file} "
            f"(dc={dc_id}, addr={addr})"
        )
        return str(session_file)

    except Exception as e:
        LOGGER.error(f"[tdl] 会话转换失败: {e}")
        return None


def get_tdl_session_path(user_id: int) -> Optional[str]:
    """获取已存在的 tdl 会话文件路径。"""
    session_file = TDL_SESSION_DIR / f"{user_id}.json"
    if session_file.exists():
        return str(session_file)
    return None


def build_message_link(chat_id: int, message_id: int, is_private: bool = False) -> str:
    """
    构建 Telegram 消息链接，供 tdl 使用。

    参数:
        chat_id: 聊天 ID（如 -1001234567890）
        message_id: 消息 ID
        is_private: 是否为私有频道

    返回:
        tdl 可用的消息链接
    """
    if is_private or str(chat_id).startswith("-100"):
        # 私有频道/超级群组: t.me/c/chat_id/message_id
        raw_id = str(chat_id).replace("-100", "")
        return f"https://t.me/c/{raw_id}/{message_id}"
    elif str(chat_id).startswith("-"):
        # 普通群组
        raw_id = str(chat_id).lstrip("-")
        return f"https://t.me/c/{raw_id}/{message_id}"
    else:
        # 公开频道/用户
        return f"https://t.me/c/{chat_id}/{message_id}"


async def download_with_tdl(
    message_link: str,
    session_file: str,
    download_dir: str,
    timeout: int = 600,
) -> Optional[str]:
    """
    使用 tdl 下载文件。

    参数:
        message_link: Telegram 消息链接
        session_file: tdl 会话文件路径
        download_dir: 下载目录
        timeout: 超时时间（秒）

    返回:
        下载的文件路径，失败返回 None
    """
    if not is_tdl_installed():
        LOGGER.warning("[tdl] tdl 未安装，跳过")
        return None

    cmd = [
        "tdl", "dl",
        "-u", message_link,
        "-d", download_dir,
        "--session", session_file,
        "--reconnect-timeout", "30",
        "--template", "{{ .DialogID }}_{{ .MessageID }}_{{ .FileName }}",
    ]

    LOGGER.info(f"[tdl] 开始下载: {' '.join(cmd)}")

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            process.kill()
            LOGGER.error(f"[tdl] 下载超时 ({timeout}s)")
            return None

        stdout_text = stdout.decode() if stdout else ""
        stderr_text = stderr.decode() if stderr else ""

        if process.returncode != 0:
            LOGGER.error(
                f"[tdl] 下载失败 (code={process.returncode}):\n"
                f"stdout: {stdout_text[:500]}\n"
                f"stderr: {stderr_text[:500]}"
            )
            return None

        # ── 从输出中查找下载的文件路径 ───────────────────────────
        # tdl 输出格式: "Downloaded: /path/to/file" 或类似
        for line in stdout_text.split("\n"):
            line = line.strip()
            if "Downloaded" in line or "downloaded" in line:
                # 尝试提取路径
                parts = line.split(":", 1)
                if len(parts) > 1:
                    path = parts[1].strip().strip('"').strip("'")
                    if os.path.exists(path):
                        LOGGER.info(f"[tdl] 下载完成: {path}")
                        return path

        # ── 回退：扫描下载目录中最新的文件 ────────────────────────
        if os.path.isdir(download_dir):
            files = sorted(
                Path(download_dir).iterdir(),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for f in files:
                if f.is_file():
                    LOGGER.info(f"[tdl] 下载完成 (扫描): {f}")
                    return str(f)

        LOGGER.warning(f"[tdl] 未找到下载文件:\nstdout: {stdout_text[:500]}")
        return None

    except FileNotFoundError:
        LOGGER.error("[tdl] tdl 二进制未找到")
        return None
    except Exception as e:
        LOGGER.error(f"[tdl] 下载异常: {e}")
        return None


async def download_media_group_with_tdl(
    message_links: list[str],
    session_file: str,
    download_dir: str,
    timeout: int = 900,
) -> list[str]:
    """
    使用 tdl 下载媒体组中的所有文件。

    参数:
        message_links: 消息链接列表
        session_file: tdl 会话文件路径
        download_dir: 下载目录
        timeout: 超时时间（秒）

    返回:
        下载的文件路径列表
    """
    if not is_tdl_installed():
        return []

    downloaded = []
    for link in message_links:
        path = await download_with_tdl(
            link, session_file, download_dir, timeout
        )
        if path:
            downloaded.append(path)

    return downloaded