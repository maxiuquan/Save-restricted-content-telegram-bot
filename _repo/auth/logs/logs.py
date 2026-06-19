# Copyright @juktijol
# Channel t.me/juktijol
import os
import asyncio
import logging
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.enums import ParseMode
from telegraph import Telegraph
from config import DEVELOPER_USER_ID, COMMAND_PREFIX
from utils import LOGGER

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = LOGGER

# Initialize Telegraph client
telegraph = Telegraph()
telegraph.create_account(
    short_name="RestrictedContentDL",
    author_name="Restricted Content Downloader",
    author_url="https://t.me/juktijol"
)

def setup_logs_handler(app: Client):
    """Set up handlers for logs command and callback queries."""

    async def create_telegraph_page(content: str) -> list:
        """Create Telegraph pages with the given content, each under 20 KB, and return list of URLs."""
        try:
            truncated_content = content[:40000]  # Limit to avoid Telegraph issues
            content_bytes = truncated_content.encode('utf-8')
            max_size_bytes = 20 * 1024  # 20 KB limit per page
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
                        author_url="https://t.me/juktijol"
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
                    author_url="https://t.me/juktijol"
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
                text="**❌ Unauthorized Access Denied! Only the Developer Can View Logs! ↯**",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        loading_message = await client.send_message(
            chat_id=message.chat.id,
            text="**📜 Fetching Restricted Content Logs... ↯**",
            parse_mode=ParseMode.MARKDOWN
        )

        await asyncio.sleep(2)

        if not os.path.exists("botlog.txt"):
            await loading_message.edit_text(
                text="**❌ No Logs Found! ↯**",
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
                "**✘ Restricted Content Downloader Logs ↯**\n"
                "**✘━━━━━━━━━━━━━━━━━━━━━━━↯**\n"
                "**✘ Logs Exported Successfully! ↯**\n"
                "**✘ Access Restricted to Developer Only ↯**\n"
                "**✘━━━━━━━━━━━━━━━━━━━━━━━↯**\n"
                "**✘ Choose an Option to View Logs:**\n"
                "**✘ Fastest Access via Inline Display or Web Paste ↯**\n"
                "**✘━━━━━━━━━━━━━━━━━━━━━━━↯**\n"
                "**✘ Developer Access Granted ↯**"
            ),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✘ Show Logs ↯", callback_data="display_logs"),
                    InlineKeyboardButton("✘ Web Paste ↯", callback_data="web_paste$")
                ],
                [InlineKeyboardButton("✘ Close ↯", callback_data="close_doc$")]
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
                text="❌ Unauthorized! Only the Developer Can Access Logs! ↯",
                show_alert=True
            )
            return

        logger.info("User is developer, processing callback")
        if data == "close_doc$":
            await query.message.delete()
            await query.answer()
            return
        elif data == "close_logs$":
            await query.message.delete()
            await query.answer()
            return
        elif data == "web_paste$":
            await query.answer("Uploading logs to Telegraph...")
            await query.message.edit_caption(
                caption="**✘ Uploading Logs to Telegraph ↯**",
                parse_mode=ParseMode.MARKDOWN
            )
            if not os.path.exists("botlog.txt"):
                await query.message.edit_caption(
                    caption="**❌ No Logs Found! ↯**",
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
                            InlineKeyboardButton(f"✘ Web Part {i+1} ↯", url=telegraph_urls[i])
                        ]
                        if i + 1 < len(telegraph_urls):
                            row.append(InlineKeyboardButton(f"✘ Web Part {i+2} ↯", url=telegraph_urls[i+1]))
                        buttons.append(row)
                    buttons.append([InlineKeyboardButton("✘ Close ↯", callback_data="close_doc$")])
                    await query.message.edit_caption(
                        caption=(
                            "**✘ Restricted Content Downloader Logs ↯**\n"
                            "**✘━━━━━━━━━━━━━━━━━━━━━━━↯**\n"
                            "**✘ Logs Uploaded to Telegraph! ↯**\n"
                            "**✘ Access Restricted to Developer Only ↯**\n"
                            "**✘━━━━━━━━━━━━━━━━━━━━━━━↯**\n"
                            "**✘ Select a Page to View Logs:**\n"
                            "**✘ Web Paste for Easy Access ↯**\n"
                            "**✘━━━━━━━━━━━━━━━━━━━━━━━↯**\n"
                            "**✘ Developer Access Granted ↯**"
                        ),
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=InlineKeyboardMarkup(buttons)
                    )
                else:
                    await query.message.edit_caption(
                        caption="**❌ Unable to Upload to Telegraph! ↯**",
                        parse_mode=ParseMode.MARKDOWN
                    )
            except Exception as e:
                logger.error(f"Error uploading to Telegraph: {e}")
                await query.message.edit_caption(
                    caption="**❌ Unable to Upload to Telegraph! ↯**",
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
                text=text if text else "**❌ No Logs Available! ↯**",
                parse_mode=ParseMode.DISABLED,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✘ Back ↯", callback_data="close_logs$")]
                ])
            )
        except Exception as e:
            logger.error(f"Error sending logs: {e}")
            await client.send_message(
                chat_id=chat_id,
                text="**❌ Server Error While Fetching Logs! ↯**",
                parse_mode=ParseMode.MARKDOWN
            )