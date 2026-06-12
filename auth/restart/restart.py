import os
import shutil
import asyncio
import logging
import subprocess
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait
from config import DEVELOPER_USER_ID, COMMAND_PREFIX
from utils import LOGGER

# 配置日志记录
logging.basicConfig(level=logging.INFO)
logger = LOGGER

def check_session_permissions(session_file: str) -> bool:
    """检查会话文件是否可写。"""
    if not os.path.exists(session_file):
        logger.warning(f"Session file {session_file} does not exist")
        return True
    if not os.access(session_file, os.W_OK):
        logger.error(f"Session file {session_file} is not writable")
        try:
            os.chmod(session_file, 0o600)
            logger.info(f"Fixed permissions for {session_file}")
            return os.access(session_file, os.W_OK)
        except Exception as e:
            logger.error(f"Failed to fix permissions for {session_file}: {e}")
            return False
    return True

def setup_restart_handler(app: Client):
    """设置重启和停止命令的处理函数。"""

    @app.on_message(filters.command(["restart", "reboot", "reload"], prefixes=COMMAND_PREFIX) & (filters.private | filters.group))
    async def restart(client: Client, message):
        """处理 /restart、/reboot、/reload 命令以重启机器人。"""
        user_id = message.from_user.id
        logger.info(f"/restart command from user {user_id}")

        try:
            response = await client.send_message(
                chat_id=message.chat.id,
                text="**✘ 正在重启下载器... ↯**",
                parse_mode=ParseMode.MARKDOWN
            )
        except FloodWait as e:
            logger.warning(f"FloodWait during restart message: waiting {e.value + 5} seconds")
            await asyncio.sleep(e.value + 5)
            response = await client.send_message(
                chat_id=message.chat.id,
                text="**✘ 正在重启下载器... ↯**",
                parse_mode=ParseMode.MARKDOWN
            )

        if user_id != DEVELOPER_USER_ID:
            logger.info("用户不是开发者，发送受限提示")
            try:
                await client.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=response.id,
                    text="**❌ 未授权！仅开发者可重启！↯**",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup([
                        [
                            
                            InlineKeyboardButton("✘ 源代码 ↯", url="https://github.com/tawhid120/Save-restricted-content-bot-")
                        ]
                    ])
                )
            except FloodWait as e:
                logger.warning(f"FloodWait during unauthorized message edit: waiting {e.value + 5} seconds")
                await asyncio.sleep(e.value + 5)
                await client.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=response.id,
                    text="**❌ 未授权！仅开发者可重启！↯**",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup([
                        [
                            
                            InlineKeyboardButton("✘ 源代码 ↯", url="https://github.com/tawhid120/Save-restricted-content-bot-")
                        ]
                    ])
                )
            return

        session_file = "RestrictedContentDL.session"
        if not check_session_permissions(session_file):
            try:
                await client.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=response.id,
                    text="**❌ 重启失败：会话文件不可写！↯**",
                    parse_mode=ParseMode.MARKDOWN
                )
            except FloodWait as e:
                logger.warning(f"FloodWait during session error message: waiting {e.value + 5} seconds")
                await asyncio.sleep(e.value + 5)
                await client.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=response.id,
                    text="**❌ 重启失败：会话文件不可写！↯**",
                    parse_mode=ParseMode.MARKDOWN
                )
            return

        directories = ["downloads", "Assets"]
        deleted_dirs = []
        failed_dirs = []
        for directory in directories:
            try:
                if os.path.exists(directory):
                    shutil.rmtree(directory)
                    deleted_dirs.append(directory)
                    logger.info(f"Deleted directory: {directory}")
            except Exception as e:
                failed_dirs.append(directory)
                logger.error(f"Failed to delete directory {directory}: {e}")

        log_file = "botlog.txt"
        if os.path.exists(log_file):
            try:
                os.remove(log_file)
                logger.info(f"Deleted log file: {log_file}")
            except Exception as e:
                logger.error(f"Failed to delete log file {log_file}: {e}")

        start_script = "start.sh"
        if not os.path.exists(start_script):
            logger.error("Start script not found")
            try:
                await client.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=response.id,
                    text="**❌ 重启失败：启动脚本未找到！↯**",
                    parse_mode=ParseMode.MARKDOWN
                )
            except FloodWait as e:
                logger.warning(f"FloodWait during script error message: waiting {e.value + 5} seconds")
                await asyncio.sleep(e.value + 5)
                await client.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=response.id,
                    text="**❌ 重启失败：启动脚本未找到！↯**",
                    parse_mode=ParseMode.MARKDOWN
                )
            return

        try:
            await asyncio.sleep(4)
            await client.edit_message_text(
                chat_id=message.chat.id,
                message_id=response.id,
                text="**✘ 下载器重启成功！↯**",
                parse_mode=ParseMode.MARKDOWN
            )
        except FloodWait as e:
            logger.warning(f"FloodWait during success message: waiting {e.value + 5} seconds")
            await asyncio.sleep(e.value + 5)
            await client.edit_message_text(
                chat_id=message.chat.id,
                message_id=response.id,
                text="**✘ 下载器重启成功！↯**",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(f"Failed to edit restart message: {e}")
            try:
                await client.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=response.id,
                    text="**❌ 重启失败！↯**",
                    parse_mode=ParseMode.MARKDOWN
                )
            except FloodWait as e:
                logger.warning(f"FloodWait during failure message: waiting {e.value + 5} seconds")
                await asyncio.sleep(e.value + 5)
                await client.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=response.id,
                    text="**❌ 重启失败！↯**",
                    parse_mode=ParseMode.MARKDOWN
                )
            return

        try:
            subprocess.run(["bash", start_script], check=True)
            os._exit(0)
        except Exception as e:
            logger.error(f"Failed to execute restart command: {e}")
            try:
                await client.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=response.id,
                    text="**❌ 重启失败！↯**",
                    parse_mode=ParseMode.MARKDOWN
                )
            except FloodWait as e:
                logger.warning(f"FloodWait during final failure message: waiting {e.value + 5} seconds")
                await asyncio.sleep(e.value + 5)
                await client.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=response.id,
                    text="**❌ 重启失败！↯**",
                    parse_mode=ParseMode.MARKDOWN
                )

    @app.on_message(filters.command(["stop", "kill", "off"], prefixes=COMMAND_PREFIX) & (filters.private | filters.group))
    async def stop(client: Client, message):
        """处理 /stop、/kill、/off 命令以停止机器人。"""
        user_id = message.from_user.id
        logger.info(f"/stop command from user {user_id}")

        try:
            response = await client.send_message(
                chat_id=message.chat.id,
                text="**✘ 正在停止下载器... ↯**",
                parse_mode=ParseMode.MARKDOWN
            )
        except FloodWait as e:
            logger.warning(f"FloodWait during stop message: waiting {e.value + 5} seconds")
            await asyncio.sleep(e.value + 5)
            response = await client.send_message(
                chat_id=message.chat.id,
                text="**✘ 正在停止下载器... ↯**",
                parse_mode=ParseMode.MARKDOWN
            )

        if user_id != DEVELOPER_USER_ID:
            logger.info("User is not developer, sending restricted message")
            try:
                await client.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=response.id,
                    text="**❌ 未授权！仅开发者可停止！↯**",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup([
                        [
                            
                            InlineKeyboardButton("✘ 源代码 ↯", url="https://github.com/tawhid120/Save-restricted-content-bot-")
                        ]
                    ])
                )
            except FloodWait as e:
                logger.warning(f"FloodWait during unauthorized stop message: waiting {e.value + 5} seconds")
                await asyncio.sleep(e.value + 5)
                await client.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=response.id,
                    text="**❌ 未授权！仅开发者可停止！↯**",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup([
                        [
                            
                            InlineKeyboardButton("✘ 源代码 ↯", url="https://github.com/tawhid120/Save-restricted-content-bot-")
                        ]
                    ])
                )
            return

        directories = ["downloads", "Assets"]
        deleted_dirs = []
        failed_dirs = []
        for directory in directories:
            try:
                if os.path.exists(directory):
                    shutil.rmtree(directory)
                    deleted_dirs.append(directory)
                    logger.info(f"Deleted directory: {directory}")
            except Exception as e:
                failed_dirs.append(directory)
                logger.error(f"Failed to delete directory {directory}: {e}")

        log_file = "botlog.txt"
        if os.path.exists(log_file):
            try:
                os.remove(log_file)
                logger.info(f"Deleted log file: {log_file}")
            except Exception as e:
                logger.error(f"Failed to delete log file {log_file}: {e}")

        try:
            await client.edit_message_text(
                chat_id=message.chat.id,
                message_id=response.id,
                text="**✘ 下载器停止成功！↯**",
                parse_mode=ParseMode.MARKDOWN
            )
        except FloodWait as e:
            logger.warning(f"FloodWait during stop success message: waiting {e.value + 5} seconds")
            await asyncio.sleep(e.value + 5)
            await client.edit_message_text(
                chat_id=message.chat.id,
                message_id=response.id,
                text="**✘ 下载器停止成功！↯**",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(f"Failed to edit stop message: {e}")
            try:
                await client.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=response.id,
                    text="**❌ 停止失败！↯**",
                    parse_mode=ParseMode.MARKDOWN
                )
            except FloodWait as e:
                logger.warning(f"FloodWait during stop failure message: waiting {e.value + 5} seconds")
                await asyncio.sleep(e.value + 5)
                await client.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=response.id,
                    text="**❌ 停止失败！↯**",
                    parse_mode=ParseMode.MARKDOWN
                )
            return

        try:
            subprocess.run(["pkill", "-f", "main.py"], check=True)
            os._exit(0)
        except Exception as e:
            logger.error(f"Failed to stop bot: {e}")
            try:
                await client.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=response.id,
                    text="**❌ 停止失败！↯**",
                    parse_mode=ParseMode.MARKDOWN
                )
            except FloodWait as e:
                logger.warning(f"FloodWait during final stop failure message: waiting {e.value + 5} seconds")
                await asyncio.sleep(e.value + 5)
                await client.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=response.id,
                    text="**❌ 停止失败！↯**",
                    parse_mode=ParseMode.MARKDOWN
                )