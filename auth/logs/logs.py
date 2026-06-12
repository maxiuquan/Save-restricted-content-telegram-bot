import os
import asyncio
import logging
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.enums import ParseMode
from telegraph import Telegraph
from config import DEVELOPER_USER_ID, COMMAND_PREFIX
from utils import LOGGER

# 配置日志记录
logging.basicConfig(level=logging.INFO)
logger = LOGGER

# 初始化 Telegraph 客户端
telegraph = Telegraph()
telegraph.create_account(
    short_name="RestrictedContentDL",
    author_name="Restricted Content Downloader",
    author_url=""
)

def setup_logs_handler(app: Client):
    """设置日志命令和回调查询的处理函数。"""

    async def create_telegraph_page(content: str) -> list:
        """使用给定内容创建 Telegraph 页面（每页不超过20KB），并返回URL列表。"""
        try:
            truncated_content = content[:40000]  # 限制长度以避免 Telegraph 问题
            content_bytes = truncated_content.encode('utf-8')
            max_size_bytes = 20 * 1024  # 每页 20 KB 限制
            pages = []
            page_content = ""
            current_size = 0
            lines = truncated_content.splitlines(keepends=True)

            for line in lines:
                line_bytes = line.encode('utf-8')
                if current_size + len(line_bytes) > max_size_bytes and page_content:
                    response = telegraph.create_page(
                        title="RestrictedContentLogs",
                        html_content=f"<pre>{page_content}</pre>",
                        author_name="Restricted Content Downloader",
                        author_url=""
                    )
                    pages.append(f"https://telegra.ph/{response['path']}")
                    page_content = ""
                    current_size = 0
                page_content += line
                current_size += len(line_bytes)

            if page_content:
                response = telegraph.create_page(
                    title="RestrictedContentLogs",
                    html_content=f"<pre>{page_content}</pre>",
                    author_name="Restricted Content Downloader",
                    author_url=""
                )
                pages.append(f"https://telegra.ph/{response['path']}")

            return pages
        except Exception as e:
            logger.error(f"Failed to create Telegraph page: {e}")
            return []

    @app.on_message(filters.command(["logs"], prefixes=COMMAND_PREFIX) & (filters.private | filters.group))
    async def logs_command(client: Client, message):
        """Handle /logs command to send or display bot logs."""
        user_id = message.from_user.id
        logger.info(f"/logs command received from user {user_id}")

        if user_id != DEVELOPER_USER_ID:
            logger.info("User is not developer, sending restricted message")
            await client.send_message(
                chat_id=message.chat.id,
                text="**❌ 未授权！仅开发者可查看日志！↯**",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        loading_message = await client.send_message(
            chat_id=message.chat.id,
            text="**📜 获取下载器日志中... ↯**",
            parse_mode=ParseMode.MARKDOWN
        )

        await asyncio.sleep(2)

        if not os.path.exists("botlog.txt"):
            await loading_message.edit_text(
                text="**❌ 未找到日志！↯**",
                parse_mode=ParseMode.MARKDOWN
            )
            await asyncio.sleep(3)
            await loading_message.delete()
            return

        logger.info("User is developer, sending log document")
        response = await client.send_document(
            chat_id=message.chat.id,
            document="botlog.txt",
            caption=(
                "**✘ 下载器日志 ↯**\n"
                "**✘━━━━━━━━━━━━━━━━━━━━━━━↯**\n"
                "**✘ 日志导出成功！↯**\n"
                "**✘ 仅限开发者访问 ↯**\n"
                "**✘━━━━━━━━━━━━━━━━━━━━━━━↯**\n"
                "**✘ 选择查看日志的方式：**\n"
                "**✘ 通过内联显示或网页粘贴快速访问 ↯**\n"
                "**✘━━━━━━━━━━━━━━━━━━━━━━━↯**\n"
                "**✘ 开发者访问已授权 ↯**"
            ),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✘ Show Logs ↯", callback_data="display_logs"),
                    InlineKeyboardButton("✘ Web Paste ↯", callback_data="web_paste$")
                ],
                [InlineKeyboardButton("✘ 关闭 ↯", callback_data="close_doc$")]
            ])
        )

        await loading_message.delete()
        return response

    @app.on_callback_query(filters.regex(r"^(close_doc\$|close_logs\$|web_paste\$|display_logs)$"))
    async def handle_callback(client: Client, query: CallbackQuery):
        """Handle callback queries for log actions."""
        user_id = query.from_user.id
        data = query.data
        logger.info(f"Callback query from user {user_id}, data: {data}")

        if user_id != DEVELOPER_USER_ID:
            logger.info("User is not developer, sending callback answer")
            await query.answer(
                text="❌ 未授权！仅开发者可访问日志！↯",
                show_alert=True
            )
            return

        logger.info("用户是开发者，处理回调")
        if data == "close_doc$":
            await query.message.delete()
            await query.answer()
            return
        elif data == "close_logs$":
            await query.message.delete()
            await query.answer()
            return
        elif data == "web_paste$":
            await query.answer("正在上传日志到 Telegraph...")
            await query.message.edit_caption(
                caption="**✘ 上传日志到 Telegraph ↯**",
                parse_mode=ParseMode.MARKDOWN
            )
            if not os.path.exists("botlog.txt"):
                await query.message.edit_caption(
                    caption="**❌ 未找到日志！↯**",
                    parse_mode=ParseMode.MARKDOWN
                )
                await query.answer()
                return
            try:
                with open("botlog.txt", "r", encoding="utf-8") as f:
                    logs_content = f.read()
                telegraph_urls = await create_telegraph_page(logs_content)
                if telegraph_urls:
                    buttons = []
                    for i in range(0, len(telegraph_urls), 2):
                        row = [
                            InlineKeyboardButton(f"✘ 网页第 {i+1} 部分 ↯", url=telegraph_urls[i])
                        ]
                        if i + 1 < len(telegraph_urls):
                            row.append(InlineKeyboardButton(f"✘ 网页第 {i+2} 部分 ↯", url=telegraph_urls[i+1]))
                        buttons.append(row)
                    buttons.append([InlineKeyboardButton("✘ 关闭 ↯", callback_data="close_doc$")])
                    await query.message.edit_caption(
                        caption=(
                            "**✘ 下载器日志 ↯**\n"
                            "**✘━━━━━━━━━━━━━━━━━━━━━━━↯**\n"
                            "**✘ 日志已上传到 Telegraph！↯**\n"
                            "**✘ 仅限开发者访问 ↯**\n"
                            "**✘━━━━━━━━━━━━━━━━━━━━━━━↯**\n"
                            "**✘ 选择页面查看日志：**\n"
                            "**✘ 网页粘贴便捷访问 ↯**\n"
                            "**✘━━━━━━━━━━━━━━━━━━━━━━━↯**\n"
                            "**✘ 开发者访问已授权 ↯**"
                        ),
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=InlineKeyboardMarkup(buttons)
                    )
                else:
                    await query.message.edit_caption(
                        caption="**❌ 无法上传到 Telegraph！↯**",
                        parse_mode=ParseMode.MARKDOWN
                    )
            except Exception as e:
                logger.error(f"Error uploading to Telegraph: {e}")
                await query.message.edit_caption(
                    caption="**❌ 无法上传到 Telegraph！↯**",
                    parse_mode=ParseMode.MARKDOWN
                )
            return
        elif data == "display_logs":
            await send_logs_page(client, query.message.chat.id, query)
            return

    async def send_logs_page(client: Client, chat_id: int, query: CallbackQuery):
        """Send the last 20 lines of botlog.txt, respecting Telegram's 4096-character limit."""
        logger.info(f"Sending latest logs to chat {chat_id}")
        if not os.path.exists("botlog.txt"):
            await client.send_message(
                chat_id=chat_id,
                text="**❌ No Logs Found! ↯**",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        try:
            with open("botlog.txt", "r", encoding="utf-8") as f:
                logs = f.readlines()
            latest_logs = logs[-20:] if len(logs) > 20 else logs
            text = "".join(latest_logs)
            if len(text) > 4096:
                text = text[-4096:]
            await client.send_message(
                chat_id=chat_id,
                text=text if text else "**❌ 无可用日志！↯**",
                parse_mode=ParseMode.DISABLED,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✘ 返回 ↯", callback_data="close_logs$")]
                ])
            )
        except Exception as e:
            logger.error(f"Error sending logs: {e}")
            await client.send_message(
                chat_id=chat_id,
                text="**❌ 获取日志时服务器错误！↯**",
                parse_mode=ParseMode.MARKDOWN
            )