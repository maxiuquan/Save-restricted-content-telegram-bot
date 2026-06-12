# 已更新：多时长定价 — 用户可选择所需天数
# Stars 定价基于 1 Star ≈ $0.0157 USD，$1 ≈ ৳122 BDT
# 所有 Star 金额以 0 或 5 结尾
# 修复：promote_user 在分配新套餐前清除所有旧套餐
# 修复：无重复条目，premium_users 集合保持同步

import uuid
import hashlib
import time
from datetime import datetime, timedelta
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.raw.functions.messages import SendMedia, SetBotPrecheckoutResults
from pyrogram.raw.types import (
    InputMediaInvoice,
    Invoice,
    DataJSON,
    LabeledPrice,
    UpdateBotPrecheckoutQuery,
    UpdateNewMessage,
    MessageService,
    MessageActionPaymentSentMe,
    PeerUser,
    PeerChat,
    PeerChannel,
    ReplyInlineMarkup,
    KeyboardButtonRow,
    KeyboardButtonBuy
)
from pyrogram.handlers import MessageHandler, CallbackQueryHandler, RawUpdateHandler
from pyrogram.enums import ParseMode
from pyrogram.errors import UserIdInvalid, UsernameInvalid, PeerIdInvalid
from config import COMMAND_PREFIX, DEVELOPER_USER_ID
from utils import LOGGER
from core import prem_plan1, prem_plan2, prem_plan3, daily_limit
from core.database import premium_users, downloads_collection

# ─────────────────────────────────────────────────────────────────────────────
# 管理员联系方式
# ─────────────────────────────────────────────────────────────────────────────
ADMIN_USERNAME = "@studyqoroo"

# ─────────────────────────────────────────────────────────────────────────────
# 支付详情
# ─────────────────────────────────────────────────────────────────────────────
BKASH_NUMBER = "01915575697"
NAGAD_NUMBER = "01XXXXXXXXX"
BINANCE_UID  = "1134625758"

# ─────────────────────────────────────────────────────────────────────────────
# 多时长定价
# 汇率：⭐1 Star ≈ $0.0157 USD | $1 ≈ ৳122 BDT
# Stars 金额始终以 0 或 5 结尾
#
# Plan 1 (base ৳5/day):
#   1d=৳10($0.08)≈5⭐  3d=৳30($0.25)≈15⭐  7d=৳50($0.41)≈25⭐
#   30d=৳150($1.23)≈80⭐  90d=৳350($2.87)≈185⭐
#
# Plan 2 (base ৳500/30d):
#   1d=৳20($0.16)≈10⭐  3d=৳60($0.49)≈30⭐  7d=৳120($0.98)≈60⭐
#   30d=৳500($4.10)≈260⭐  90d=৳1200($9.84)≈625⭐
#
# Plan 3 (base ৳1000/30d):
#   1d=৳35($0.29)≈20⭐  3d=৳100($0.82)≈50⭐  7d=৳230($1.89)≈120⭐
#   30d=৳1000($8.20)≈520⭐  90d=৳2500($20.49)≈1305⭐
# ─────────────────────────────────────────────────────────────────────────────

PLAN_DURATIONS = {
    "plan1": {
        "1":  {"days": 1,  "bdt": 10,   "usd": 0.08,  "stars": 5},
        "3":  {"days": 3,  "bdt": 30,   "usd": 0.25,  "stars": 15},
        "7":  {"days": 7,  "bdt": 50,   "usd": 0.41,  "stars": 25},
        "30": {"days": 30, "bdt": 150,  "usd": 1.23,  "stars": 80},
        "90": {"days": 90, "bdt": 350,  "usd": 2.87,  "stars": 185},
    },
    "plan2": {
        "1":  {"days": 1,  "bdt": 20,   "usd": 0.16,  "stars": 10},
        "3":  {"days": 3,  "bdt": 60,   "usd": 0.49,  "stars": 30},
        "7":  {"days": 7,  "bdt": 120,  "usd": 0.98,  "stars": 60},
        "30": {"days": 30, "bdt": 500,  "usd": 4.10,  "stars": 260},
        "90": {"days": 90, "bdt": 1200, "usd": 9.84,  "stars": 625},
    },
    "plan3": {
        "1":  {"days": 1,  "bdt": 35,   "usd": 0.29,  "stars": 20},
        "3":  {"days": 3,  "bdt": 100,  "usd": 0.82,  "stars": 50},
        "7":  {"days": 7,  "bdt": 230,  "usd": 1.89,  "stars": 120},
        "30": {"days": 30, "bdt": 1000, "usd": 8.20,  "stars": 520},
        "90": {"days": 90, "bdt": 2500, "usd": 20.49, "stars": 1305},
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# 套餐定义（功能特性）
# ─────────────────────────────────────────────────────────────────────────────
PLANS = {
    "plan1": {
        "name":            "高级套餐 1",
        "accounts":        1,
        "max_downloads":   1000,
        "private_support": True,
        "inbox_support":   False,
    },
    "plan2": {
        "name":            "高级套餐 2",
        "accounts":        5,
        "max_downloads":   2000,
        "private_support": True,
        "inbox_support":   True,
    },
    "plan3": {
        "name":            "高级套餐 3",
        "accounts":        10,
        "max_downloads":   "Unlimited",
        "private_support": True,
        "inbox_support":   True,
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# 消息模板
# ─────────────────────────────────────────────────────────────────────────────

PLAN_OPTIONS_TEXT = """
💎 **选择你的高级套餐** 💎
**━━━━━━━━━━━━━━━━━━━━━**

✨ **高级套餐 1**
• 1 个账户登录
• 批量下载：最多 1,000 条消息
• 私有频道 / 对话：✅
• 私信 / 机器人内：❌

🌟 **高级套餐 2**
• 5 个账户登录
• 批量下载：最多 2,000 条消息
• 私有频道 / 对话：✅
• 私信 / 机器人内：✅

💎 **高级套餐 3**
• 10 个账户登录
• 批量下载：♾️ 无限制
• 私有频道 / 对话：✅
• 私信 / 机器人内：✅

**━━━━━━━━━━━━━━━━━━━━━**
👇 **点击套餐选择时长：**
"""

PLAN_DURATION_TEXT = """
{plan_emoji} **{plan_name}**
**━━━━━━━━━━━━━━━━━━━━━**

⏱ **选择天数：**

🕒 **1 天**    — ৳{d1_bdt} ( ${d1_usd} )  ≈ ⭐ {d1_stars} 星星
🕒 **3 天**   — ৳{d3_bdt} ( ${d3_usd} )  ≈ ⭐ {d3_stars} 星星
📆 **7 天**   — ৳{d7_bdt} ( ${d7_usd} )  ≈ ⭐ {d7_stars} 星星
📅 **30 天**  — ৳{d30_bdt} ( ${d30_usd} )  ≈ ⭐ {d30_stars} 星星
🗓️ **90 天**  — ৳{d90_bdt} ( ${d90_usd} )  ≈ ⭐ {d90_stars} 星星

**━━━━━━━━━━━━━━━━━━━━━**
💡 _更长的套餐 = 更划算！_
"""

PAYMENT_METHOD_TEXT = """
💳 **选择支付方式**
**━━━━━━━━━━━━━━━━━━━━━**
📦 **套餐：** `{plan_name}`
🗓 **时长：** `{days} 天`
💰 **星星 价格：** `{stars} ⭐`
💵 **美元等值：** `${usd}`
💴 **BDT 等值：** `{bdt} ৳`
**━━━━━━━━━━━━━━━━━━━━━**

选择支付方式：

⭐ **Telegram Stars** — 即时自动激活
📲 **bKash** — 孟加拉移动银行
📲 **Nagad** — 孟加拉移动银行
🪙 **Binance / USDT (TRC20)** — 加密货币支付
📞 **联系管理员** — 其他方式

🇧🇩 __孟加拉用户可方便地使用 bKash 或 Nagad 支付！__

❓ __没有合适的支付方式？联系 {admin}__
"""

BKASH_PAYMENT_TEXT = """
👑 **购买高级会员 — bKash 支付**
**━━━━━━━━━━━━━━━━━━━━━**
📦 **套餐：** `{plan_name}` ({days} 天)
💰 **金额：** `{amount} BDT`
📲 **发送至：** `{number}`
📋 **类型：** `发送付款`
📝 **备注：** `{user_id}`
**━━━━━━━━━━━━━━━━━━━━━**

📌 **分步指南：**
1️⃣ 打开你的 **bKash App**
2️⃣ 点击 **发送付款**
3️⃣ 输入号码：`{number}`
4️⃣ 输入准确金额：`{amount} BDT`
5️⃣ 在 **备注** 字段输入你的用户 ID：`{user_id}`
6️⃣ 确认并完成付款

✅ **付款后**，发送 **交易 ID (TxID)** 或 **截图** 给 {admin}

⚡ 管理员将在几分钟内验证并激活你的高级会员。

🇧🇩 __为孟加拉用户量身打造！__
"""

NAGAD_PAYMENT_TEXT = """
👑 **购买高级会员 — Nagad 支付**
**━━━━━━━━━━━━━━━━━━━━━**
📦 **套餐：** `{plan_name}` ({days} 天)
💰 **金额：** `{amount} BDT`
📲 **发送至：** `{number}`
📋 **类型：** `发送付款`
📝 **备注：** `{user_id}`
**━━━━━━━━━━━━━━━━━━━━━**

📌 **分步指南：**
1️⃣ 打开你的 **Nagad App**
2️⃣ 点击 **发送付款**
3️⃣ 输入号码：`{number}`
4️⃣ 输入准确金额：`{amount} BDT`
5️⃣ 在 **备注** 字段输入你的用户 ID：`{user_id}`
6️⃣ 确认并完成付款

✅ **付款后**，发送 **交易 ID (TxID)** 或 **截图** 给 {admin}

⚡ 管理员将在几分钟内验证并激活你的高级会员。

🇧🇩 __为孟加拉用户量身打造！__
"""

BINANCE_PAYMENT_TEXT = """
🪙 **购买高级会员 — Binance / 加密货币支付**
**━━━━━━━━━━━━━━━━━━━━━**
📦 **套餐：** `{plan_name}` ({days} 天)
💰 **金额：** `{amount_usd} USDT`
🆔 **Binance UID：** `{uid}`
🔗 **网络：** `USDT (TRC20)`
📝 **备注：** `{user_id}`
**━━━━━━━━━━━━━━━━━━━━━**

📌 **分步指南：**
1️⃣ 打开 **Binance** 或任何支持 USDT 的钱包
2️⃣ 前往 **发送 / 转账**
3️⃣ 选择 **USDT** 走 **TRC20 网络**
4️⃣ 输入 Binance UID：`{uid}`
5️⃣ 输入准确金额：`{amount_usd} USDT`
6️⃣ 在 **备注** 字段输入你的用户 ID：`{user_id}`
7️⃣ 确认并完成交易

✅ **付款后**，发送 **交易哈希 / 截图** 给 {admin}

⚡ 管理员将在几分钟内验证并激活你的高级会员。
"""

CONTACT_ADMIN_TEXT = """
📞 **联系管理员 — 其他支付方式**
**━━━━━━━━━━━━━━━━━━━━━**
📦 **你想要的套餐：** `{plan_name}` ({days} 天)
💰 **Stars 价格：** `{stars} ⭐`
💴 **BDT 价格：** `{bdt} ৳`
💵 **USD 价格：** `${usd}`
**━━━━━━━━━━━━━━━━━━━━━**

无法使用以上任意支付方式？
别担心 — 直接联系管理员了解其他选项。

👤 **管理员：** {admin}
💬 **说什么：** 告诉管理员你想要哪个套餐 + 时长，询问可用的支付方式。

💡 **其他可接受的方式可能包括：**
• 🏦 银行转账（孟加拉）
• 💵 其他移动银行应用
• 🤝 双方协商的任何方式

🇧🇩 __我们是孟加拉团队运营的项目 — 我们会尽力帮助你！__
"""

PAYMENT_SUCCESS_TEXT = """
✅ **支付成功 — 高级会员已激活！**

🎉 谢谢你，**{name}**！

**📦 套餐：** `{plan_name}`
**🗓 时长：** `{days} 天`
**⭐ 支付金额：** `{amount}` 星星
**👥 账户数：** `{accounts}`
**📥 最大下载数：** `{max_downloads}`
**📅 有效期至：** `{expiry}`
**🧾 交易 ID：** `{tx_id}`

🚀 你的高级功能现已**立即生效**！
使用 /login 连接你的账户并开始下载。

感谢你的支持！💎
"""

ADMIN_NOTIFICATION_TEXT = """
🌟 **新的高级会员购买！**

👤 **用户：** {name}
🆔 **用户 ID：** `{user_id}`
📛 **用户名：** {username}
📦 **套餐：** `{plan_name}`
🗓 **时长：** `{days} 天`
⭐ **金额：** `{amount}` 星星
📅 **过期时间：** `{expiry}`
🧾 **交易 ID：** `{tx_id}`
"""

active_invoices: dict = {}

# 套餐表情符号映射
PLAN_EMOJIS = {"plan1": "✨", "plan2": "🌟", "plan3": "💎"}


def setup_plan_handler(app: Client):

    # 键盘

    def get_plan_buttons() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✨ 套餐1", callback_data="plan_select_plan1"),
                InlineKeyboardButton("🌟 套餐2", callback_data="plan_select_plan2"),
                InlineKeyboardButton("💎 套餐3", callback_data="plan_select_plan3"),
            ],
        ])

    def get_duration_buttons(plan_key: str) -> InlineKeyboardMarkup:
        """Show duration options for a selected plan."""
        durations = PLAN_DURATIONS[plan_key]
        rows = []
        for dur_key, info in durations.items():
            label = f"{'🕒' if info['days'] < 7 else '📆' if info['days'] == 7 else '📅' if info['days'] == 30 else '🗓️'} {info['days']} 天 — ⭐ {info['stars']}"
            rows.append([InlineKeyboardButton(label, callback_data=f"plan_dur_{plan_key}_{dur_key}")])
        rows.append([InlineKeyboardButton("🔙 返回套餐列表", callback_data="show_plan_options")])
        return InlineKeyboardMarkup(rows)

    def get_payment_method_buttons(plan_key: str, dur_key: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("⭐ Telegram Stars — 即时",  callback_data=f"pay_stars_{plan_key}_{dur_key}")],
            [InlineKeyboardButton("📲 bKash (孟加拉)",         callback_data=f"pay_bkash_{plan_key}_{dur_key}")],
            [InlineKeyboardButton("📲 Nagad (孟加拉)",         callback_data=f"pay_nagad_{plan_key}_{dur_key}")],
            [InlineKeyboardButton("🪙 Binance / USDT 加密货币",      callback_data=f"pay_crypto_{plan_key}_{dur_key}")],
            [InlineKeyboardButton("📞 联系管理员",              callback_data=f"pay_admin_{plan_key}_{dur_key}")],
            [InlineKeyboardButton("🔙 返回选择时长",           callback_data=f"plan_select_{plan_key}")],
        ])

    def get_back_button(plan_key: str, dur_key: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 返回支付选项", callback_data=f"plan_dur_{plan_key}_{dur_key}")],
            [InlineKeyboardButton("🏠 主菜单",               callback_data="menu_home")],
        ])

    # ── 升级用户 ──────────────────────────────────────────────────────

    async def promote_user(user_id: int, plan_key: str, days: int) -> dict:
        plan        = PLANS[plan_key]
        expiry_date = datetime.utcnow() + timedelta(days=days)

        for col in [prem_plan1, prem_plan2, prem_plan3]:
            await col.delete_many({"user_id": user_id})
        await premium_users.delete_many({"user_id": user_id})

        plan_doc = {
            "user_id":         user_id,
            "plan":            plan_key,
            "plan_name":       plan["name"],
            "accounts":        plan["accounts"],
            "max_downloads":   plan["max_downloads"],
            "private_support": plan["private_support"],
            "inbox_support":   plan["inbox_support"],
            "expiry_date":     expiry_date,
            "activated_at":    datetime.utcnow(),
            "duration_days":   days,
        }

        plan_map = {"plan1": prem_plan1, "plan2": prem_plan2, "plan3": prem_plan3}
        await plan_map[plan_key].insert_one(plan_doc.copy())
        await premium_users.update_one({"user_id": user_id}, {"$set": plan_doc}, upsert=True)

        LOGGER.info(f"[Plan] User {user_id} → {plan['name']} {days}d (expires {expiry_date})")
        plan_doc.pop("_id", None)
        return plan_doc

    # ── Telegram Stars 发票 ────────────────────────────────────────────

    async def generate_stars_invoice(client: Client, chat_id: int, user_id: int,
                                      plan_key: str, dur_key: str):
        if active_invoices.get(user_id):
            await client.send_message(
                chat_id,
                "⚠️ **已有另一个购买正在进行中！**\n\n"
                "请先完成或取消那一笔发票。",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        plan   = PLANS[plan_key]
        info   = PLAN_DURATIONS[plan_key][dur_key]
        amount = info["stars"]
        days   = info["days"]

        loading_msg = await client.send_message(
            chat_id,
            f"⏳ **正在为 {plan['name']} ({days} 天) 生成星星发票...**",
            parse_mode=ParseMode.MARKDOWN,
        )

        try:
            active_invoices[user_id] = True
            timestamp       = int(time.time())
            unique_id       = str(uuid.uuid4())[:8]
            invoice_payload = f"plan_{plan_key}_{dur_key}_{user_id}_{amount}_{timestamp}_{unique_id}"
            random_id       = int(hashlib.sha256(invoice_payload.encode()).hexdigest(), 16) % (2 ** 63)

            invoice = Invoice(
                currency="XTR",
                prices=[LabeledPrice(label=f"{plan['name']} {days}天 ({amount} 星星)", amount=amount)],
                max_tip_amount=0,
                suggested_tip_amounts=[],
                recurring=False, test=False,
                name_requested=False, phone_requested=False,
                email_requested=False, shipping_address_requested=False,
                flexible=False,
            )
            media = InputMediaInvoice(
                title=f"购买 {plan['name']} — {days} 天",
                description=(
                    f"解锁 {plan['name']} {days} 天（{amount} 星星）。\n"
                    f"• {plan['accounts']} 个账户登录\n"
                    f"• {plan['max_downloads']} 批量下载"
                ),
                invoice=invoice,
                payload=invoice_payload.encode(),
                provider="STARS",
                provider_data=DataJSON(data="{}"),
            )
            markup = ReplyInlineMarkup(rows=[
                KeyboardButtonRow(buttons=[KeyboardButtonBuy(text=f"支付 {amount} ⭐")])
            ])
            peer = await client.resolve_peer(chat_id)
            await client.invoke(
                SendMedia(peer=peer, media=media, message="", random_id=random_id, reply_markup=markup)
            )
            await client.edit_message_text(
                chat_id, loading_msg.id,
                f"✅ **发票已就绪 — {plan['name']} {days} 天（{amount} 星星）**\n\n"
                "点击上方的 **支付** 按钮完成购买。\n\n"
                "⚡ 支付后套餐将**立即**激活！",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 返回支付选项",
                                          callback_data=f"plan_dur_{plan_key}_{dur_key}")],
                ]),
            )
            LOGGER.info(f"[Stars] Invoice sent for {plan['name']} {days}d to user {user_id}")

        except Exception as e:
            LOGGER.error(f"[Stars] Invoice failed for user {user_id}: {e}")
            await client.edit_message_text(
                chat_id, loading_msg.id,
                "❌ **生成 Stars 发票失败。**\n\n请尝试其他支付方式。",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=get_payment_method_buttons(plan_key, dur_key),
            )
        finally:
            active_invoices.pop(user_id, None)

    # ── /plans  /buy ──────────────────────────────────────────────────────

    async def plans_command(client: Client, message: Message):
        await client.send_message(
            chat_id=message.chat.id,
            text=PLAN_OPTIONS_TEXT,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_plan_buttons(),
        )
        LOGGER.info(f"[Plans] /plans from user {message.from_user.id}")

    # ── /add（管理员）──────────────────────────────────────────────────────

    async def add_premium_command(client: Client, message: Message):
        if message.from_user.id != DEVELOPER_USER_ID:
            await message.reply_text("❌ **仅管理员可使用此命令！**", parse_mode=ParseMode.MARKDOWN)
            return
        # 用法：/add {用户} {1|2|3} [天数]
        # 未指定天数时默认 30 天
        if len(message.command) < 3 or message.command[2] not in ["1", "2", "3"]:
            await message.reply_text(
                "❌ **格式无效！**\n\n用法：`/add {用户名/用户ID} {1, 2, 或 3} [天数]`\n\n"
                "未指定天数时默认 30 天。",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        identifier = message.command[1]
        plan_key   = f"plan{message.command[2]}"
        days       = int(message.command[3]) if len(message.command) >= 4 else 30
        target_id  = None

        try:
            try:
                target_id = int(identifier)
            except ValueError:
                user      = await client.get_users(identifier.lstrip("@"))
                target_id = user.id

            plan_doc = await promote_user(target_id, plan_key, days)
            plan     = PLANS[plan_key]
            expiry   = plan_doc["expiry_date"].strftime("%d %B %Y")

            await message.reply_text(
                f"✅ **用户 `{target_id}` 已成功升级为 {plan['name']}（{days} 天）！**",
                parse_mode=ParseMode.MARKDOWN,
            )
            try:
                await client.send_message(
                    chat_id=target_id,
                    text=(
                        f"🎉 **你的账户已升级为高级会员！**\n\n"
                        f"**📦 套餐：** `{plan['name']}`\n"
                        f"**🗓 时长：** `{days} 天`\n"
                        f"**👥 账户数：** `{plan['accounts']}`\n"
                        f"**📥 最大下载数：** `{plan['max_downloads']}`\n"
                        f"**🔒 私有频道：** ✅\n"
                        f"**📬 私信：** {'✅' if plan['inbox_support'] else '❌'}\n"
                        f"**📅 有效期至：** `{expiry}`\n\n"
                        "🚀 粘贴任意 Telegram 链接即可立即下载！\n"
                        "使用 /login 连接你的账户以访问私有内容。"
                    ),
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception as e:
                LOGGER.warning(f"[Add] Could not notify user {target_id}: {e}")

        except (UserIdInvalid, UsernameInvalid, PeerIdInvalid):
            await message.reply_text(f"❌ **未找到用户：**`{identifier}`", parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            await message.reply_text(f"❌ **错误：**`{str(e)}`", parse_mode=ParseMode.MARKDOWN)
            LOGGER.error(f"[Add] Error: {e}")

    # ── /rm（管理员）──────────────────────────────────────────────────────

    async def remove_premium_command(client: Client, message: Message):
        if message.from_user.id != DEVELOPER_USER_ID:
            await message.reply_text("❌ **仅管理员可使用此命令！**", parse_mode=ParseMode.MARKDOWN)
            return
        if len(message.command) != 2:
            await message.reply_text(
                "❌ **格式无效！**\n\n用法：`/rm {用户名/用户ID}`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        identifier = message.command[1]
        target_id  = None

        try:
            try:
                target_id = int(identifier)
            except ValueError:
                user      = await client.get_users(identifier.lstrip("@"))
                target_id = user.id

            removed = False
            for col in [prem_plan1, prem_plan2, prem_plan3]:
                r = await col.delete_many({"user_id": target_id})
                if r.deleted_count > 0:
                    removed = True
            await premium_users.delete_many({"user_id": target_id})

            if removed:
                await message.reply_text(
                    f"✅ **用户 `{target_id}` 已从所有高级套餐中移除。**",
                    parse_mode=ParseMode.MARKDOWN,
                )
                try:
                    await client.send_message(
                        chat_id=target_id,
                        text=(
                            "⚠️ **高级套餐已移除**\n\n"
                            "你的高级套餐已被管理员移除。\n\n"
                            "如果你认为这是一个错误，请联系客服。\n"
                            f"联系：{ADMIN_USERNAME}\n\n"
                            "使用 /plans 购买新套餐。💎"
                        ),
                        parse_mode=ParseMode.MARKDOWN,
                    )
                except Exception as e:
                    LOGGER.warning(f"[Rm] Could not notify user {target_id}: {e}")
            else:
                await message.reply_text(
                    f"❌ **用户 `{target_id}` 不在任何高级套餐中。**",
                    parse_mode=ParseMode.MARKDOWN,
                )

        except (UserIdInvalid, UsernameInvalid, PeerIdInvalid):
            await message.reply_text(f"❌ **未找到用户：**`{identifier}`", parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            await message.reply_text(f"❌ **错误：**`{str(e)}`", parse_mode=ParseMode.MARKDOWN)
            LOGGER.error(f"[Rm] Error: {e}")

    # ── 回调处理 ──────────────────────────────────────────────────

    async def handle_plan_callback(client: Client, cq: CallbackQuery):
        data    = cq.data
        user_id = cq.from_user.id
        chat_id = cq.message.chat.id
        msg_id  = cq.message.id

        # 已选套餐 → 显示时长选项
        if data.startswith("plan_select_"):
            plan_key = data[len("plan_select_"):]
            if plan_key not in PLANS:
                return await cq.answer("未知套餐。", show_alert=True)
            plan   = PLANS[plan_key]
            emoji  = PLAN_EMOJIS.get(plan_key, "⭐")
            durs   = PLAN_DURATIONS[plan_key]

            text = PLAN_DURATION_TEXT.format(
                plan_emoji=emoji,
                plan_name=plan["name"],
                d1_bdt=durs["1"]["bdt"],   d1_usd=durs["1"]["usd"],   d1_stars=durs["1"]["stars"],
                d3_bdt=durs["3"]["bdt"],   d3_usd=durs["3"]["usd"],   d3_stars=durs["3"]["stars"],
                d7_bdt=durs["7"]["bdt"],   d7_usd=durs["7"]["usd"],   d7_stars=durs["7"]["stars"],
                d30_bdt=durs["30"]["bdt"], d30_usd=durs["30"]["usd"], d30_stars=durs["30"]["stars"],
                d90_bdt=durs["90"]["bdt"], d90_usd=durs["90"]["usd"], d90_stars=durs["90"]["stars"],
            )
            await client.edit_message_text(
                chat_id, msg_id, text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=get_duration_buttons(plan_key),
                disable_web_page_preview=True,
            )
            return await cq.answer()

        # 已选时长 → 支付方式界面
        if data.startswith("plan_dur_"):
            # plan_dur_{plan_key}_{dur_key}
            parts    = data[len("plan_dur_"):].split("_", 1)
            if len(parts) != 2:
                return await cq.answer("无效数据。", show_alert=True)
            plan_key, dur_key = parts
            if plan_key not in PLANS or dur_key not in PLAN_DURATIONS.get(plan_key, {}):
                return await cq.answer("未知套餐/时长。", show_alert=True)
            plan = PLANS[plan_key]
            info = PLAN_DURATIONS[plan_key][dur_key]
            await client.edit_message_text(
                chat_id, msg_id,
                PAYMENT_METHOD_TEXT.format(
                    plan_name=plan["name"],
                    days=info["days"],
                    stars=info["stars"],
                    usd=info["usd"],
                    bdt=info["bdt"],
                    admin=ADMIN_USERNAME,
                ),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=get_payment_method_buttons(plan_key, dur_key),
                disable_web_page_preview=True,
            )
            return await cq.answer()

        # Telegram Stars
        if data.startswith("pay_stars_"):
            rest = data[len("pay_stars_"):]
            parts = rest.split("_", 1)
            if len(parts) != 2:
                return await cq.answer("无效数据。", show_alert=True)
            plan_key, dur_key = parts
            if plan_key not in PLANS or dur_key not in PLAN_DURATIONS.get(plan_key, {}):
                return await cq.answer("未知套餐/时长。", show_alert=True)
            info = PLAN_DURATIONS[plan_key][dur_key]
            await cq.answer(f"正在为 {PLANS[plan_key]['name']} ({info['days']}天) 生成发票...")
            await generate_stars_invoice(client, chat_id, user_id, plan_key, dur_key)
            return

        # bKash
        if data.startswith("pay_bkash_"):
            rest = data[len("pay_bkash_"):]
            parts = rest.split("_", 1)
            if len(parts) != 2:
                return await cq.answer("无效数据。", show_alert=True)
            plan_key, dur_key = parts
            if plan_key not in PLANS or dur_key not in PLAN_DURATIONS.get(plan_key, {}):
                return await cq.answer("未知套餐/时长。", show_alert=True)
            info = PLAN_DURATIONS[plan_key][dur_key]
            await client.edit_message_text(
                chat_id, msg_id,
                BKASH_PAYMENT_TEXT.format(
                    plan_name=PLANS[plan_key]["name"],
                    days=info["days"],
                    amount=info["bdt"],
                    number=BKASH_NUMBER,
                    user_id=user_id,
                    admin=ADMIN_USERNAME,
                ),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=get_back_button(plan_key, dur_key),
            )
            return await cq.answer("bKash 支付说明")

        # Nagad
        if data.startswith("pay_nagad_"):
            rest = data[len("pay_nagad_"):]
            parts = rest.split("_", 1)
            if len(parts) != 2:
                return await cq.answer("无效数据。", show_alert=True)
            plan_key, dur_key = parts
            if plan_key not in PLANS or dur_key not in PLAN_DURATIONS.get(plan_key, {}):
                return await cq.answer("未知套餐/时长。", show_alert=True)
            info = PLAN_DURATIONS[plan_key][dur_key]
            await client.edit_message_text(
                chat_id, msg_id,
                NAGAD_PAYMENT_TEXT.format(
                    plan_name=PLANS[plan_key]["name"],
                    days=info["days"],
                    amount=info["bdt"],
                    number=NAGAD_NUMBER,
                    user_id=user_id,
                    admin=ADMIN_USERNAME,
                ),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=get_back_button(plan_key, dur_key),
            )
            return await cq.answer("Nagad 支付说明")

        # Binance / Crypto
        if data.startswith("pay_crypto_"):
            rest = data[len("pay_crypto_"):]
            parts = rest.split("_", 1)
            if len(parts) != 2:
                return await cq.answer("无效数据。", show_alert=True)
            plan_key, dur_key = parts
            if plan_key not in PLANS or dur_key not in PLAN_DURATIONS.get(plan_key, {}):
                return await cq.answer("未知套餐/时长。", show_alert=True)
            info = PLAN_DURATIONS[plan_key][dur_key]
            await client.edit_message_text(
                chat_id, msg_id,
                BINANCE_PAYMENT_TEXT.format(
                    plan_name=PLANS[plan_key]["name"],
                    days=info["days"],
                    amount_usd=info["usd"],
                    uid=BINANCE_UID,
                    user_id=user_id,
                    admin=ADMIN_USERNAME,
                ),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=get_back_button(plan_key, dur_key),
            )
            return await cq.answer("加密货币支付说明")

        # Contact Admin
        if data.startswith("pay_admin_"):
            rest = data[len("pay_admin_"):]
            parts = rest.split("_", 1)
            if len(parts) != 2:
                return await cq.answer("无效数据。", show_alert=True)
            plan_key, dur_key = parts
            if plan_key not in PLANS or dur_key not in PLAN_DURATIONS.get(plan_key, {}):
                return await cq.answer("未知套餐/时长。", show_alert=True)
            plan = PLANS[plan_key]
            info = PLAN_DURATIONS[plan_key][dur_key]
            await client.edit_message_text(
                chat_id, msg_id,
                CONTACT_ADMIN_TEXT.format(
                    plan_name=plan["name"],
                    days=info["days"],
                    stars=info["stars"],
                    bdt=info["bdt"],
                    usd=info["usd"],
                    admin=ADMIN_USERNAME,
                ),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        f"💬 联系 {ADMIN_USERNAME}",
                        url=f"https://t.me/{ADMIN_USERNAME.lstrip('@')}",
                    )],
                    [InlineKeyboardButton("🔙 返回支付选项",
                                          callback_data=f"plan_dur_{plan_key}_{dur_key}")],
                    [InlineKeyboardButton("🏠 主菜单", callback_data="menu_home")],
                ]),
                disable_web_page_preview=True,
            )
            return await cq.answer("联系管理员进行支付")

        # 返回套餐列表
        if data == "show_plan_options":
            await client.edit_message_text(
                chat_id, msg_id, PLAN_OPTIONS_TEXT,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=get_plan_buttons(),
            )
            return await cq.answer()

        await cq.answer()

    # ── 原始更新：预结算 + Stars 支付成功 ─────────────────────────

    async def raw_update_handler(client: Client, update, users, chats):

        if isinstance(update, UpdateBotPrecheckoutQuery):
            try:
                await client.invoke(
                    SetBotPrecheckoutResults(query_id=update.query_id, success=True)
                )
                LOGGER.info(f"[PreCheckout] Approved {update.query_id} for user {update.user_id}")
            except Exception as e:
                LOGGER.error(f"[PreCheckout] Failed: {e}")
                try:
                    await client.invoke(
                        SetBotPrecheckoutResults(
                            query_id=update.query_id, success=False,
                            error="支付无法处理，请重试。",
                        )
                    )
                except Exception:
                    pass
            return

        if not (
            isinstance(update, UpdateNewMessage)
            and isinstance(update.message, MessageService)
            and isinstance(update.message.action, MessageActionPaymentSentMe)
        ):
            return

        payment = update.message.action

        try:
            user_id = None
            if update.message.from_id and hasattr(update.message.from_id, "user_id"):
                user_id = update.message.from_id.user_id
            if not user_id and users:
                positive = [uid for uid in users if uid > 0]
                user_id  = positive[0] if positive else None
            if not user_id:
                LOGGER.error("[Payment] Could not resolve user_id")
                return

            pid = update.message.peer_id
            if isinstance(pid, PeerUser):       chat_id = pid.user_id
            elif isinstance(pid, PeerChat):     chat_id = pid.chat_id
            elif isinstance(pid, PeerChannel):  chat_id = pid.channel_id
            else:                               chat_id = user_id

            payload  = payment.payload.decode()
            # payload 格式：plan_{plan_key}_{dur_key}_{user_id}_{amount}_{ts}_{uid}
            parts    = payload.split("_")
            if len(parts) < 4 or parts[0] != "plan":
                LOGGER.error(f"[Payment] Unexpected payload: {payload}")
                return

            plan_key = parts[1]
            dur_key  = parts[2]

            if plan_key not in PLANS:
                LOGGER.error(f"[Payment] Unknown plan_key: {plan_key}")
                return

            # 旧发票中可能缺少 dur_key（向后兼容）→ 默认 30 天
            if dur_key not in PLAN_DURATIONS.get(plan_key, {}):
                LOGGER.warning(f"[Payment] Unknown dur_key '{dur_key}', defaulting to 30d")
                dur_key = "30"

            plan        = PLANS[plan_key]
            info        = PLAN_DURATIONS[plan_key][dur_key]
            days        = info["days"]
            tx_id       = payment.charge.id
            amount_paid = payment.total_amount
            user_info   = users.get(user_id)
            full_name   = (
                f"{user_info.first_name} {getattr(user_info, 'last_name', '') or ''}".strip()
                if user_info else "User"
            )
            username = f"@{user_info.username}" if user_info and user_info.username else "@N/A"

            LOGGER.info(f"[Payment] {amount_paid} Stars from {user_id} for {plan_key} {days}d | tx={tx_id}")

            plan_doc = await promote_user(user_id, plan_key, days)
            expiry   = plan_doc["expiry_date"].strftime("%d %B %Y")
            max_dl   = "♾️ Unlimited" if plan["max_downloads"] == "Unlimited" else str(plan["max_downloads"])

            await downloads_collection.insert_one({
                "user_id":    user_id,
                "plan":       plan_key,
                "dur_key":    dur_key,
                "days":       days,
                "tx_id":      tx_id,
                "amount":     amount_paid,
                "method":     "telegram_stars",
                "created_at": datetime.utcnow(),
            })

            try:
                await client.send_message(
                    chat_id=chat_id,
                    text=PAYMENT_SUCCESS_TEXT.format(
                        name=full_name, plan_name=plan["name"],
                        days=days, amount=amount_paid,
                        accounts=plan["accounts"],
                        max_downloads=max_dl, expiry=expiry, tx_id=tx_id,
                    ),
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception as e:
                LOGGER.error(f"[Payment] Could not send success msg: {e}")

            try:
                admin_ids = [DEVELOPER_USER_ID] if isinstance(DEVELOPER_USER_ID, int) else DEVELOPER_USER_ID
                for aid in admin_ids:
                    await client.send_message(
                        chat_id=aid,
                        text=ADMIN_NOTIFICATION_TEXT.format(
                            name=full_name, user_id=user_id, username=username,
                            plan_name=plan["name"], days=days, amount=amount_paid,
                            expiry=expiry, tx_id=tx_id,
                        ),
                        parse_mode=ParseMode.MARKDOWN,
                    )
            except Exception as e:
                LOGGER.error(f"[Payment] Admin notify failed: {e}")

            LOGGER.info(f"[Payment] ✅ {full_name} ({user_id}) → {plan['name']} {days}d | expires {expiry}")

        except Exception as e:
            LOGGER.error(f"[Payment] Unhandled error: {e}")
            try:
                if user_id and chat_id:
                    await client.send_message(
                        chat_id=chat_id,
                        text=(
                            "⚠️ **已收到付款，但激活遇到问题。**\n\n"
                            "请联系客服并提供你的交易 ID。\n"
                            f"客服：{ADMIN_USERNAME}"
                        ),
                        parse_mode=ParseMode.MARKDOWN,
                    )
            except Exception:
                pass

    # ── 注册处理函数 ─────────────────────────────────────────────────

    app.add_handler(
        MessageHandler(
            plans_command,
            filters=filters.command(["plans", "buy"], prefixes=COMMAND_PREFIX)
                    & (filters.private | filters.group),
        ),
        group=1,
    )
    app.add_handler(
        MessageHandler(
            add_premium_command,
            filters=filters.command("add", prefixes=COMMAND_PREFIX) & filters.private,
        ),
        group=1,
    )
    app.add_handler(
        MessageHandler(
            remove_premium_command,
            filters=filters.command("rm", prefixes=COMMAND_PREFIX) & filters.private,
        ),
        group=1,
    )
    app.add_handler(
        CallbackQueryHandler(
            handle_plan_callback,
            filters=filters.regex(
                r"^(plan_select_plan[1-3]"
                r"|plan_dur_plan[1-3]_\d+"
                r"|pay_stars_plan[1-3]_\d+"
                r"|pay_bkash_plan[1-3]_\d+"
                r"|pay_nagad_plan[1-3]_\d+"
                r"|pay_crypto_plan[1-3]_\d+"
                r"|pay_admin_plan[1-3]_\d+"
                r"|show_plan_options)$"
            ),
        ),
        group=2,
    )
    app.add_handler(
        RawUpdateHandler(raw_update_handler),
        group=3,
    )
