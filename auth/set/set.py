from pyrogram import filters
from pyrogram.types import BotCommand
from pyrogram.enums import ParseMode
from config import DEVELOPER_USER_ID
from utils import LOGGER

BOT_COMMANDS = [
    BotCommand("start",    "启动下载机器人"),
    BotCommand("help",     "获取帮助菜单和命令"),
    BotCommand("info",     "获取用户信息和计划信息"),
    BotCommand("plans",    "查看可用计划和购买"),
    BotCommand("buy",      "使用 Star 购买高级计划"),
    BotCommand("ytdl",     "从 YouTube 和 1000+ 网站下载"),
    BotCommand("send",     "按 ID 发送消息给指定用户"),
    BotCommand("login",    "登录账户"),
    BotCommand("logout",   "登出账户"),
    BotCommand("profile",  "获取个人资料和计划状态"),
    BotCommand("getthumb", "获取自定义缩略图"),
    BotCommand("setthumb", "设置或更改自定义缩略图"),
    BotCommand("rmthumb",  "移除自定义缩略图"),
]

def setup_set_handler(app):
    @app.on_message(filters.command("set") & filters.user(DEVELOPER_USER_ID))
    async def set_commands(client, message):
        await client.set_bot_commands(BOT_COMMANDS)
        await client.send_message(
            chat_id=message.chat.id,
            text="✅ **BotFather 命令设置成功**↯",
            parse_mode=ParseMode.MARKDOWN
        )
        LOGGER.info(f"BotFather commands set by owner {message.from_user.id}")
