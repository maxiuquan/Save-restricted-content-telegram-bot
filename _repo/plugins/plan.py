# Copyright @juktijol
# Channel t.me/juktijol
# UPDATED: Multi-duration pricing — users choose how many days they want
# Stars pricing based on 1 Star ≈ $0.0157 USD, $1 ≈ ৳122 BDT
# All Star amounts end in 0 or 5
# FIX: promote_user clears all old plans before assigning new one
# FIX: No duplicate entries, premium_users collection stays in sync

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
# ADMIN CONTACT
# ─────────────────────────────────────────────────────────────────────────────
ADMIN_USERNAME = "@studyqoroo"

# ─────────────────────────────────────────────────────────────────────────────
# PAYMENT DETAILS
# ─────────────────────────────────────────────────────────────────────────────
BKASH_NUMBER = "01915575697"
NAGAD_NUMBER = "01XXXXXXXXX"
BINANCE_UID  = "1134625758"

# ─────────────────────────────────────────────────────────────────────────────
# MULTI-DURATION PRICING
# Rate: ⭐1 Star ≈ $0.0157 USD | $1 ≈ ৳122 BDT
# Stars always end in 0 or 5
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
# PLAN DEFINITIONS (features)
# ─────────────────────────────────────────────────────────────────────────────
PLANS = {
    "plan1": {
        "name":            "Plan Premium 1",
        "accounts":        1,
        "max_downloads":   1000,
        "private_support": True,
        "inbox_support":   False,
    },
    "plan2": {
        "name":            "Plan Premium 2",
        "accounts":        5,
        "max_downloads":   2000,
        "private_support": True,
        "inbox_support":   True,
    },
    "plan3": {
        "name":            "Plan Premium 3",
        "accounts":        10,
        "max_downloads":   "Unlimited",
        "private_support": True,
        "inbox_support":   True,
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# MESSAGE TEMPLATES
# ─────────────────────────────────────────────────────────────────────────────

PLAN_OPTIONS_TEXT = """
💎 **Choose Your Premium Plan** 💎
**━━━━━━━━━━━━━━━━━━━━━**

✨ **Plan Premium 1**
• 1 Account Login
• Batch Download: up to 1,000 messages
• Private Channel / Chat: ✅
• Private Inbox / Bot: ❌

🌟 **Plan Premium 2**
• 5 Account Logins
• Batch Download: up to 2,000 messages
• Private Channel / Chat: ✅
• Private Inbox / Bot: ✅

💎 **Plan Premium 3**
• 10 Account Logins
• Batch Download: ♾️ Unlimited
• Private Channel / Chat: ✅
• Private Inbox / Bot: ✅

**━━━━━━━━━━━━━━━━━━━━━**
👇 **Tap a plan to choose your duration:**
"""

PLAN_DURATION_TEXT = """
{plan_emoji} **{plan_name}**
**━━━━━━━━━━━━━━━━━━━━━**

⏱ **Choose how many days:**

🕒 **1 Day**    — ৳{d1_bdt} ( ${d1_usd} )  ≈ ⭐ {d1_stars} Stars
🕒 **3 Days**   — ৳{d3_bdt} ( ${d3_usd} )  ≈ ⭐ {d3_stars} Stars
📆 **7 Days**   — ৳{d7_bdt} ( ${d7_usd} )  ≈ ⭐ {d7_stars} Stars
📅 **30 Days**  — ৳{d30_bdt} ( ${d30_usd} )  ≈ ⭐ {d30_stars} Stars
🗓️ **90 Days**  — ৳{d90_bdt} ( ${d90_usd} )  ≈ ⭐ {d90_stars} Stars

**━━━━━━━━━━━━━━━━━━━━━**
💡 _Longer plans = better value!_
"""

PAYMENT_METHOD_TEXT = """
💳 **Select Payment Method**
**━━━━━━━━━━━━━━━━━━━━━**
📦 **Plan:** `{plan_name}`
🗓 **Duration:** `{days} Days`
💰 **Stars Price:** `{stars} ⭐`
💵 **USD Equivalent:** `${usd}`
💴 **BDT Equivalent:** `{bdt} ৳`
**━━━━━━━━━━━━━━━━━━━━━**

Choose how you would like to pay:

⭐ **Telegram Stars** — Instant automatic activation
📲 **bKash** — Bangladesh mobile banking
📲 **Nagad** — Bangladesh mobile banking
🪙 **Binance / USDT (TRC20)** — Crypto payment
📞 **Contact Admin** — Other arrangements

🇧🇩 __Bangladeshi users can easily pay via bKash or Nagad!__

❓ __Can't find a suitable method? Contact {admin}__
"""

BKASH_PAYMENT_TEXT = """
👑 **Buy Premium — bKash Payment**
**━━━━━━━━━━━━━━━━━━━━━**
📦 **Plan:** `{plan_name}` ({days} Days)
💰 **Amount:** `{amount} BDT`
📲 **Send To:** `{number}`
📋 **Type:** `Send Money`
📝 **Reference / Note:** `{user_id}`
**━━━━━━━━━━━━━━━━━━━━━**

📌 **Step-by-Step Instructions:**
1️⃣ Open your **bKash App**
2️⃣ Tap **Send Money**
3️⃣ Enter the number: `{number}`
4️⃣ Enter the exact amount: `{amount} BDT`
5️⃣ In the **Reference** field, enter your User ID: `{user_id}`
6️⃣ Confirm and complete the payment

✅ **After payment**, send the **Transaction ID (TxID)** or a **Screenshot** to {admin}

⚡ Admin will verify and activate your premium within a few minutes.

🇧🇩 __Made easy for Bangladeshi users!__
"""

NAGAD_PAYMENT_TEXT = """
👑 **Buy Premium — Nagad Payment**
**━━━━━━━━━━━━━━━━━━━━━**
📦 **Plan:** `{plan_name}` ({days} Days)
💰 **Amount:** `{amount} BDT`
📲 **Send To:** `{number}`
📋 **Type:** `Send Money`
📝 **Reference / Note:** `{user_id}`
**━━━━━━━━━━━━━━━━━━━━━**

📌 **Step-by-Step Instructions:**
1️⃣ Open your **Nagad App**
2️⃣ Tap **Send Money**
3️⃣ Enter the number: `{number}`
4️⃣ Enter the exact amount: `{amount} BDT`
5️⃣ In the **Reference** field, enter your User ID: `{user_id}`
6️⃣ Confirm and complete the payment

✅ **After payment**, send the **Transaction ID (TxID)** or a **Screenshot** to {admin}

⚡ Admin will verify and activate your premium within a few minutes.

🇧🇩 __Made easy for Bangladeshi users!__
"""

BINANCE_PAYMENT_TEXT = """
🪙 **Buy Premium — Binance / Crypto Payment**
**━━━━━━━━━━━━━━━━━━━━━**
📦 **Plan:** `{plan_name}` ({days} Days)
💰 **Amount:** `{amount_usd} USDT`
🆔 **Binance UID:** `{uid}`
🔗 **Network:** `USDT (TRC20)`
📝 **Memo / Note:** `{user_id}`
**━━━━━━━━━━━━━━━━━━━━━**

📌 **Step-by-Step Instructions:**
1️⃣ Open **Binance** or any USDT-compatible wallet
2️⃣ Go to **Send / Transfer**
3️⃣ Select **USDT** on **TRC20 network**
4️⃣ Enter Binance UID: `{uid}`
5️⃣ Enter the exact amount: `{amount_usd} USDT`
6️⃣ In the **Memo** field, enter your User ID: `{user_id}`
7️⃣ Confirm and complete the transaction

✅ **After payment**, send the **Transaction Hash / Screenshot** to {admin}

⚡ Admin will verify and activate your premium within a few minutes.
"""

CONTACT_ADMIN_TEXT = """
📞 **Contact Admin — Other Payment Methods**
**━━━━━━━━━━━━━━━━━━━━━**
📦 **Plan you want:** `{plan_name}` ({days} Days)
💰 **Stars Price:** `{stars} ⭐`
💴 **BDT Price:** `{bdt} ৳`
💵 **USD Price:** `${usd}`
**━━━━━━━━━━━━━━━━━━━━━**

Cannot use any of the available payment methods?
No worries — contact our admin directly for other options.

👤 **Admin:** {admin}
💬 **What to say:** Tell the admin which plan + duration you want and ask about available payment methods.

💡 **Other accepted methods may include:**
• 🏦 Bank Transfer (Bangladesh)
• 💵 Other mobile banking apps
• 🤝 Any arrangement by mutual agreement

🇧🇩 __We are a Bangladeshi-run project — we will do our best to help you!__
"""

PAYMENT_SUCCESS_TEXT = """
✅ **Payment Successful — Premium Activated!**

🎉 Thank you, **{name}**!

**📦 Plan:** `{plan_name}`
**🗓 Duration:** `{days} Days`
**⭐ Amount Paid:** `{amount} Stars`
**👥 Accounts:** `{accounts}`
**📥 Max Downloads:** `{max_downloads}`
**📅 Valid Until:** `{expiry}`
**🧾 Transaction ID:** `{tx_id}`

🚀 Your premium features are now **active immediately**!
Use /login to connect your account and start downloading.

Thank you for your support! 💎
"""

ADMIN_NOTIFICATION_TEXT = """
🌟 **New Premium Purchase!**

👤 **User:** {name}
🆔 **User ID:** `{user_id}`
📛 **Username:** {username}
📦 **Plan:** `{plan_name}`
🗓 **Duration:** `{days} Days`
⭐ **Amount:** `{amount} Stars`
📅 **Expires:** `{expiry}`
🧾 **Transaction ID:** `{tx_id}`
"""

active_invoices: dict = {}

# Emoji map for plans
PLAN_EMOJIS = {"plan1": "✨", "plan2": "🌟", "plan3": "💎"}


def setup_plan_handler(app: Client):

    # ── Keyboards ─────────────────────────────────────────────────────────

    def get_plan_buttons() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✨ Plan 1", callback_data="plan_select_plan1"),
                InlineKeyboardButton("🌟 Plan 2", callback_data="plan_select_plan2"),
                InlineKeyboardButton("💎 Plan 3", callback_data="plan_select_plan3"),
            ],
        ])

    def get_duration_buttons(plan_key: str) -> InlineKeyboardMarkup:
        """Show duration options for a selected plan."""
        durations = PLAN_DURATIONS[plan_key]
        rows = []
        for dur_key, info in durations.items():
            label = f"{'🕒' if info['days'] < 7 else '📆' if info['days'] == 7 else '📅' if info['days'] == 30 else '🗓️'} {info['days']} Days — ⭐ {info['stars']}"
            rows.append([InlineKeyboardButton(label, callback_data=f"plan_dur_{plan_key}_{dur_key}")])
        rows.append([InlineKeyboardButton("🔙 Back to Plans", callback_data="show_plan_options")])
        return InlineKeyboardMarkup(rows)

    def get_payment_method_buttons(plan_key: str, dur_key: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("⭐ Telegram Stars — Instant",  callback_data=f"pay_stars_{plan_key}_{dur_key}")],
            [InlineKeyboardButton("📲 bKash (Bangladesh)",         callback_data=f"pay_bkash_{plan_key}_{dur_key}")],
            [InlineKeyboardButton("📲 Nagad (Bangladesh)",         callback_data=f"pay_nagad_{plan_key}_{dur_key}")],
            [InlineKeyboardButton("🪙 Binance / USDT Crypto",      callback_data=f"pay_crypto_{plan_key}_{dur_key}")],
            [InlineKeyboardButton("📞 Contact Admin",              callback_data=f"pay_admin_{plan_key}_{dur_key}")],
            [InlineKeyboardButton("🔙 Back to Duration",           callback_data=f"plan_select_{plan_key}")],
        ])

    def get_back_button(plan_key: str, dur_key: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back to Payment Options", callback_data=f"plan_dur_{plan_key}_{dur_key}")],
            [InlineKeyboardButton("🏠 Main Menu",               callback_data="menu_home")],
        ])

    # ── promote_user ──────────────────────────────────────────────────────

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

    # ── Telegram Stars invoice ────────────────────────────────────────────

    async def generate_stars_invoice(client: Client, chat_id: int, user_id: int,
                                      plan_key: str, dur_key: str):
        if active_invoices.get(user_id):
            await client.send_message(
                chat_id,
                "⚠️ **Another purchase is already in progress!**\n\n"
                "Please complete or cancel that invoice first.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        plan   = PLANS[plan_key]
        info   = PLAN_DURATIONS[plan_key][dur_key]
        amount = info["stars"]
        days   = info["days"]

        loading_msg = await client.send_message(
            chat_id,
            f"⏳ **Generating Stars invoice for {plan['name']} ({days} Days)...**",
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
                prices=[LabeledPrice(label=f"{plan['name']} {days}d ({amount} Stars)", amount=amount)],
                max_tip_amount=0,
                suggested_tip_amounts=[],
                recurring=False, test=False,
                name_requested=False, phone_requested=False,
                email_requested=False, shipping_address_requested=False,
                flexible=False,
            )
            media = InputMediaInvoice(
                title=f"Purchase {plan['name']} — {days} Days",
                description=(
                    f"Unlock {plan['name']} for {days} days ({amount} Stars).\n"
                    f"• {plan['accounts']} account login(s)\n"
                    f"• {plan['max_downloads']} batch downloads"
                ),
                invoice=invoice,
                payload=invoice_payload.encode(),
                provider="STARS",
                provider_data=DataJSON(data="{}"),
            )
            markup = ReplyInlineMarkup(rows=[
                KeyboardButtonRow(buttons=[KeyboardButtonBuy(text=f"Pay {amount} ⭐")])
            ])

            peer = await client.resolve_peer(chat_id)
            await client.invoke(
                SendMedia(peer=peer, media=media, message="", random_id=random_id, reply_markup=markup)
            )
            await client.edit_message_text(
                chat_id, loading_msg.id,
                f"✅ **Invoice Ready — {plan['name']} {days} Days ({amount} Stars)**\n\n"
                "Tap the **Pay** button above to complete your purchase.\n\n"
                "⚡ Premium activates **instantly** after payment!",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back to Payment Options",
                                          callback_data=f"plan_dur_{plan_key}_{dur_key}")],
                ]),
            )
            LOGGER.info(f"[Stars] Invoice sent for {plan['name']} {days}d to user {user_id}")

        except Exception as e:
            LOGGER.error(f"[Stars] Invoice failed for user {user_id}: {e}")
            await client.edit_message_text(
                chat_id, loading_msg.id,
                "❌ **Failed to generate Stars invoice.**\n\nPlease try another payment method.",
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

    # ── /add (admin) ──────────────────────────────────────────────────────

    async def add_premium_command(client: Client, message: Message):
        if message.from_user.id != DEVELOPER_USER_ID:
            await message.reply_text("❌ **Only admins can use this command!**", parse_mode=ParseMode.MARKDOWN)
            return
        # Usage: /add {user} {1|2|3} [days]
        # days defaults to 30 if not specified
        if len(message.command) < 3 or message.command[2] not in ["1", "2", "3"]:
            await message.reply_text(
                "❌ **Invalid format!**\n\nUsage: `/add {username/userid} {1, 2, or 3} [days]`\n\n"
                "Default days = 30 if not specified.",
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
                f"✅ **User `{target_id}` promoted to {plan['name']} ({days} days) successfully!**",
                parse_mode=ParseMode.MARKDOWN,
            )
            try:
                await client.send_message(
                    chat_id=target_id,
                    text=(
                        f"🎉 **Your account has been upgraded to Premium!**\n\n"
                        f"**📦 Plan:** `{plan['name']}`\n"
                        f"**🗓 Duration:** `{days} Days`\n"
                        f"**👥 Accounts:** `{plan['accounts']}`\n"
                        f"**📥 Max Downloads:** `{plan['max_downloads']}`\n"
                        f"**🔒 Private Channel:** ✅\n"
                        f"**📬 Private Inbox:** {'✅' if plan['inbox_support'] else '❌'}\n"
                        f"**📅 Valid Until:** `{expiry}`\n\n"
                        "🚀 Paste any Telegram link to start downloading instantly!\n"
                        "Use /login to connect your account for private content."
                    ),
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception as e:
                LOGGER.warning(f"[Add] Could not notify user {target_id}: {e}")

        except (UserIdInvalid, UsernameInvalid, PeerIdInvalid):
            await message.reply_text(f"❌ **User not found:** `{identifier}`", parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            await message.reply_text(f"❌ **Error:** `{str(e)}`", parse_mode=ParseMode.MARKDOWN)
            LOGGER.error(f"[Add] Error: {e}")

    # ── /rm (admin) ───────────────────────────────────────────────────────

    async def remove_premium_command(client: Client, message: Message):
        if message.from_user.id != DEVELOPER_USER_ID:
            await message.reply_text("❌ **Only admins can use this command!**", parse_mode=ParseMode.MARKDOWN)
            return
        if len(message.command) != 2:
            await message.reply_text(
                "❌ **Invalid format!**\n\nUsage: `/rm {username/userid}`",
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
                    f"✅ **User `{target_id}` removed from all premium plans.**",
                    parse_mode=ParseMode.MARKDOWN,
                )
                try:
                    await client.send_message(
                        chat_id=target_id,
                        text=(
                            "⚠️ **Premium Plan Removed**\n\n"
                            "Your premium plan has been removed by an administrator.\n\n"
                            "If you believe this is a mistake, please contact support.\n"
                            f"Contact: {ADMIN_USERNAME}\n\n"
                            "Use /plans to purchase a new plan. 💎"
                        ),
                        parse_mode=ParseMode.MARKDOWN,
                    )
                except Exception as e:
                    LOGGER.warning(f"[Rm] Could not notify user {target_id}: {e}")
            else:
                await message.reply_text(
                    f"❌ **User `{target_id}` is not in any premium plan.**",
                    parse_mode=ParseMode.MARKDOWN,
                )

        except (UserIdInvalid, UsernameInvalid, PeerIdInvalid):
            await message.reply_text(f"❌ **User not found:** `{identifier}`", parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            await message.reply_text(f"❌ **Error:** `{str(e)}`", parse_mode=ParseMode.MARKDOWN)
            LOGGER.error(f"[Rm] Error: {e}")

    # ── Callback handler ──────────────────────────────────────────────────

    async def handle_plan_callback(client: Client, cq: CallbackQuery):
        data    = cq.data
        user_id = cq.from_user.id
        chat_id = cq.message.chat.id
        msg_id  = cq.message.id

        # Plan selected → show duration options
        if data.startswith("plan_select_"):
            plan_key = data[len("plan_select_"):]
            if plan_key not in PLANS:
                return await cq.answer("Unknown plan.", show_alert=True)
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

        # Duration selected → payment method screen
        if data.startswith("plan_dur_"):
            # plan_dur_{plan_key}_{dur_key}
            parts    = data[len("plan_dur_"):].split("_", 1)
            if len(parts) != 2:
                return await cq.answer("Invalid data.", show_alert=True)
            plan_key, dur_key = parts
            if plan_key not in PLANS or dur_key not in PLAN_DURATIONS.get(plan_key, {}):
                return await cq.answer("Unknown plan/duration.", show_alert=True)
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
                return await cq.answer("Invalid data.", show_alert=True)
            plan_key, dur_key = parts
            if plan_key not in PLANS or dur_key not in PLAN_DURATIONS.get(plan_key, {}):
                return await cq.answer("Unknown plan/duration.", show_alert=True)
            info = PLAN_DURATIONS[plan_key][dur_key]
            await cq.answer(f"Generating invoice for {PLANS[plan_key]['name']} {info['days']}d...")
            await generate_stars_invoice(client, chat_id, user_id, plan_key, dur_key)
            return

        # bKash
        if data.startswith("pay_bkash_"):
            rest = data[len("pay_bkash_"):]
            parts = rest.split("_", 1)
            if len(parts) != 2:
                return await cq.answer("Invalid data.", show_alert=True)
            plan_key, dur_key = parts
            if plan_key not in PLANS or dur_key not in PLAN_DURATIONS.get(plan_key, {}):
                return await cq.answer("Unknown plan/duration.", show_alert=True)
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
            return await cq.answer("bKash payment instructions")

        # Nagad
        if data.startswith("pay_nagad_"):
            rest = data[len("pay_nagad_"):]
            parts = rest.split("_", 1)
            if len(parts) != 2:
                return await cq.answer("Invalid data.", show_alert=True)
            plan_key, dur_key = parts
            if plan_key not in PLANS or dur_key not in PLAN_DURATIONS.get(plan_key, {}):
                return await cq.answer("Unknown plan/duration.", show_alert=True)
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
            return await cq.answer("Nagad payment instructions")

        # Binance / Crypto
        if data.startswith("pay_crypto_"):
            rest = data[len("pay_crypto_"):]
            parts = rest.split("_", 1)
            if len(parts) != 2:
                return await cq.answer("Invalid data.", show_alert=True)
            plan_key, dur_key = parts
            if plan_key not in PLANS or dur_key not in PLAN_DURATIONS.get(plan_key, {}):
                return await cq.answer("Unknown plan/duration.", show_alert=True)
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
            return await cq.answer("Crypto payment instructions")

        # Contact Admin
        if data.startswith("pay_admin_"):
            rest = data[len("pay_admin_"):]
            parts = rest.split("_", 1)
            if len(parts) != 2:
                return await cq.answer("Invalid data.", show_alert=True)
            plan_key, dur_key = parts
            if plan_key not in PLANS or dur_key not in PLAN_DURATIONS.get(plan_key, {}):
                return await cq.answer("Unknown plan/duration.", show_alert=True)
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
                        f"💬 Message {ADMIN_USERNAME}",
                        url=f"https://t.me/{ADMIN_USERNAME.lstrip('@')}",
                    )],
                    [InlineKeyboardButton("🔙 Back to Payment Options",
                                          callback_data=f"plan_dur_{plan_key}_{dur_key}")],
                    [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_home")],
                ]),
                disable_web_page_preview=True,
            )
            return await cq.answer("Contact admin for payment")

        # Back to plan list
        if data == "show_plan_options":
            await client.edit_message_text(
                chat_id, msg_id, PLAN_OPTIONS_TEXT,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=get_plan_buttons(),
            )
            return await cq.answer()

        await cq.answer()

    # ── Raw: pre-checkout + Stars payment success ─────────────────────────

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
                            error="Payment could not be processed. Please try again.",
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
            # payload format: plan_{plan_key}_{dur_key}_{user_id}_{amount}_{ts}_{uid}
            parts    = payload.split("_")
            if len(parts) < 4 or parts[0] != "plan":
                LOGGER.error(f"[Payment] Unexpected payload: {payload}")
                return

            plan_key = parts[1]
            dur_key  = parts[2]

            if plan_key not in PLANS:
                LOGGER.error(f"[Payment] Unknown plan_key: {plan_key}")
                return

            # dur_key might be missing in old invoices (backwards compat) → default 30d
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
                            "⚠️ **Payment received, but activation encountered an issue.**\n\n"
                            "Please contact support with your transaction ID.\n"
                            f"Support: {ADMIN_USERNAME}"
                        ),
                        parse_mode=ParseMode.MARKDOWN,
                    )
            except Exception:
                pass

    # ── Register handlers ─────────────────────────────────────────────────

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
