# Copyright @juktijol
# Channel t.me/juktijol
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from config import DEVELOPER_USER_ID, COMMAND_PREFIX
from utils import LOGGER

ADMIN_HELP_TEXT = """
**✘ Admin Command Panel ↯**
**✘━━━━━━━━━━━━━━━━━━━━━━━↯**

**📊 Stats & Monitoring:**
├ `/stats` — Bot statistics (users, premium, downloads, CPU/RAM)
├ `/users` — Paginated list of all users
├ `/logs` — View or download bot logs
└ `/speedtest` — Run a server speed test

**📢 Broadcast & Messaging:**
├ `/gcast` — Global broadcast (copy + pin)
├ `/acast` — Global broadcast (forward + pin)
├ `/send` — Send message to a specific user by ID
└ `/broadcast` — Broadcast alias

**👑 Premium Management:**
├ `/add {user} {1|2|3}` — Add user to premium plan
└ `/rm {user}` — Remove user from premium

**🔄 Bot Control:**
├ `/restart` — Restart the bot
├ `/stop` — Stop the bot
└ `/set` — Set BotFather command list

**🛠 Database & Fixes:**
├ `/migrate` — Migrate database
├ `/fix_async` — Fix async issues
└ `/fix_status` — Check async fix status

**✘━━━━━━━━━━━━━━━━━━━━━━━↯**
**✘ Developer Access Only ↯**
"""


def setup_admin_handler(app: Client):

    @app.on_message(filters.command("admin", prefixes=COMMAND_PREFIX) & filters.private)
    async def admin_command(client: Client, message):
        user_id = message.from_user.id
        LOGGER.info(f"/admin command received from user {user_id}")

        if user_id != DEVELOPER_USER_ID:
            await client.send_message(
                chat_id=message.chat.id,
                text="**❌ Unauthorized! Only Developer Can Access Admin Panel! ↯**",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        await client.send_message(
            chat_id=message.chat.id,
            text=ADMIN_HELP_TEXT,
            parse_mode=ParseMode.MARKDOWN,
        )
