from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from config import DEVELOPER_USER_ID, COMMAND_PREFIX
from utils import LOGGER

ADMIN_HELP_TEXT = """
**✘ 管理员命令面板 ↯**
**✘━━━━━━━━━━━━━━━━━━━━━━━↯**

**📊 统计与监控：**
├ `/stats` — 机器人统计（用户、高级会员、下载量、CPU/内存）
├ `/users` — 分页用户列表
├ `/logs` — 查看或下载机器人日志
└ `/speedtest` — 运行服务器速度测试

**📢 广播与消息：**
├ `/gcast` — 全局广播（复制+置顶）
├ `/acast` — 全局广播（转发+置顶）
├ `/send` — 按 ID 发送消息给指定用户
└ `/broadcast` — 广播别名

**👑 高级会员管理：**
├ `/add {用户} {1|2|3}` — 将用户添加到高级计划
└ `/rm {用户}` — 移除用户的高级会员

**🔄 机器人控制：**
├ `/restart` — 重启机器人
├ `/stop` — 停止机器人
└ `/set` — 设置 BotFather 命令列表

**🛠 数据库与修复：**
├ `/migrate` — 迁移数据库
├ `/fix_async` — 修复异步问题
└ `/fix_status` — 检查异步修复状态

**✘━━━━━━━━━━━━━━━━━━━━━━━↯**
**✘ 仅限开发者访问 ↯**
"""


def setup_admin_handler(app: Client):

    @app.on_message(filters.command("admin", prefixes=COMMAND_PREFIX) & filters.private)
    async def admin_command(client: Client, message):
        user_id = message.from_user.id
        LOGGER.info(f"/admin command received from user {user_id}")

        if user_id != DEVELOPER_USER_ID:
            await client.send_message(
                chat_id=message.chat.id,
                text="**❌ 未授权！仅开发者可访问管理面板！↯**",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        await client.send_message(
            chat_id=message.chat.id,
            text=ADMIN_HELP_TEXT,
            parse_mode=ParseMode.MARKDOWN,
        )
