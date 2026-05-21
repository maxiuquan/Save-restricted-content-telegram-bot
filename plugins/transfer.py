# Fixed: All DB calls now use Motor async (await)
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.enums import ParseMode
from pyrogram.errors import UserIdInvalid, UsernameInvalid, PeerIdInvalid
from datetime import datetime
from config import COMMAND_PREFIX, DEVELOPER_USER_ID
from utils import LOGGER
from core import prem_plan1, prem_plan2, prem_plan3

pending_transfers = {}


async def _get_active_plan(user_id: int):
    """Motor async — sender-এর active plan খোঁজে।"""
    current_time = datetime.utcnow()
    for plan_key, collection in [("plan3", prem_plan3), ("plan2", prem_plan2), ("plan1", prem_plan1)]:
        doc = await collection.find_one({"user_id": user_id})
        if doc and doc.get("expiry_date", current_time) > current_time:
            return plan_key, collection, doc
    return None, None, None


def setup_transfer_handler(app: Client):

    @app.on_message(
        filters.command("transfer", prefixes=COMMAND_PREFIX) & filters.private
    )
    async def transfer_command(client: Client, message: Message):
        sender_id = message.from_user.id

        if len(message.command) < 2:
            await message.reply_text(
                "**❌ 格式无效！**\n\n"
                "**用法：** `/transfer <用户ID 或 @用户名>`\n\n"
                "**示例：**\n"
                "`/transfer 123456789`\n"
                "`/transfer @username`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        plan_key, collection, plan_doc = await _get_active_plan(sender_id)
        if not plan_key:
            await message.reply_text(
                "**❌ 你没有任何有效的高级套餐！**\n\n"
                "请先购买套餐再进行转让：/plans",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        expiry_date    = plan_doc.get("expiry_date")
        plan_name      = plan_doc.get("plan_name", plan_key)
        remaining_days = (expiry_date - datetime.utcnow()).days if expiry_date else 0

        identifier = message.command[1].strip()
        target_id  = None

        try:
            try:
                target_id = int(identifier)
            except ValueError:
                uname    = identifier.lstrip("@")
                user     = await client.get_users(uname)
                target_id = user.id
        except (UserIdInvalid, UsernameInvalid, PeerIdInvalid):
            await message.reply_text(
                f"**❌ 用户未找到：** `{identifier}`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        except Exception as e:
            await message.reply_text(
                f"**❌ 错误：** `{str(e)}`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        if target_id == sender_id:
            await message.reply_text(
                "**❌ 你不能将高级会员转让给自己！**",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        if target_id == DEVELOPER_USER_ID:
            await message.reply_text(
                "**❌ 你不能将高级会员转让给开发者！**",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        pending_transfers[sender_id] = {
            "target_id":   target_id,
            "plan_key":    plan_key,
            "plan_name":   plan_name,
            "expiry_date": expiry_date,
            "collection":  collection,
        }

        try:
            target_user  = await client.get_users(target_id)
            target_name  = f"{target_user.first_name} {target_user.last_name or ''}".strip()
            target_label = f"{target_name} (`{target_id}`)"
        except Exception:
            target_label = f"`{target_id}`"

        await message.reply_text(
            f"**⚠️ 转让确认**\n\n"
            f"**📦 套餐：** `{plan_name}`\n"
            f"**📅 剩余：** `{remaining_days}` 天\n"
            f"**👤 接收者：** {target_label}\n\n"
            f"**⚡ 确认后：**\n"
            f"• 你的高级会员将被 **移除**\n"
            f"• 接收者的高级会员将被 **激活**\n\n"
            f"_此操作无法撤销！_",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ 确认", callback_data=f"transfer_confirm_{sender_id}"),
                    InlineKeyboardButton("❌ 取消",  callback_data=f"transfer_cancel_{sender_id}"),
                ]
            ]),
        )

    @app.on_callback_query(filters.regex(r"^transfer_(confirm|cancel)_(\d+)$"))
    async def transfer_callback(client: Client, callback_query: CallbackQuery):
        data       = callback_query.data
        clicker_id = callback_query.from_user.id

        parts     = data.split("_")
        action    = parts[1]
        sender_id = int(parts[2])

        if clicker_id != sender_id:
            await callback_query.answer(
                "❌ 只有发起转让的用户才能确认或取消！",
                show_alert=True,
            )
            return

        transfer_info = pending_transfers.get(sender_id)
        if not transfer_info:
            await callback_query.message.edit_text(
                "**❌ 转让会话已过期！请重新运行 /transfer。**",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        if action == "cancel":
            pending_transfers.pop(sender_id, None)
            await callback_query.message.edit_text(
                "**❌ 转让已取消。**",
                parse_mode=ParseMode.MARKDOWN,
            )
            await callback_query.answer("转让已取消。")
            return

        target_id   = transfer_info["target_id"]
        plan_key    = transfer_info["plan_key"]
        plan_name   = transfer_info["plan_name"]
        expiry_date = transfer_info["expiry_date"]
        src_col     = transfer_info["collection"]

        plan_collections = {
            "plan1": prem_plan1,
            "plan2": prem_plan2,
            "plan3": prem_plan3,
        }

        PLANS_CONFIG = {
            "plan1": {"accounts": 1,  "max_downloads": 1000,        "private_support": True, "inbox_support": False},
            "plan2": {"accounts": 5,  "max_downloads": 2000,        "private_support": True, "inbox_support": True},
            "plan3": {"accounts": 10, "max_downloads": "unlimited",  "private_support": True, "inbox_support": True},
        }
        plan_cfg = PLANS_CONFIG.get(plan_key, {})

        try:
            await src_col.delete_one({"user_id": sender_id})

            for pk, col in plan_collections.items():
                await col.delete_one({"user_id": target_id})

            await plan_collections[plan_key].update_one(
                {"user_id": target_id},
                {
                    "$set": {
                        "user_id":         target_id,
                        "plan":            plan_key,
                        "plan_name":       plan_name,
                        "accounts":        plan_cfg.get("accounts"),
                        "max_downloads":   plan_cfg.get("max_downloads"),
                        "private_support": plan_cfg.get("private_support"),
                        "inbox_support":   plan_cfg.get("inbox_support"),
                        "expiry_date":     expiry_date,
                    }
                },
                upsert=True,
            )

            pending_transfers.pop(sender_id, None)

            remaining_days = (expiry_date - datetime.utcnow()).days if expiry_date else 0

            await callback_query.message.edit_text(
                f"**✅ 转让成功！**\n\n"
                f"**📦 套餐：** `{plan_name}`\n"
                f"**📅 剩余：** `{remaining_days}` 天\n"
                f"**👤 接收者：** `{target_id}`\n\n"
                f"_你的高级会员已成功转让。_",
                parse_mode=ParseMode.MARKDOWN,
            )

            try:
                sender_name = (
                    f"{callback_query.from_user.first_name} "
                    f"{callback_query.from_user.last_name or ''}".strip()
                )
                await client.send_message(
                    chat_id=target_id,
                    text=(
                        f"**🎁 你已收到一个高级套餐！**\n\n"
                        f"**📦 套餐：** `{plan_name}`\n"
                        f"**📅 剩余：** `{remaining_days}` 天\n"
                        f"**👤 赠送者：** `{sender_name}` (`{sender_id}`)\n\n"
                        f"_你的高级会员已激活。使用 /profile 查看状态。_"
                    ),
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception as notify_err:
                LOGGER.warning(f"Could not notify recipient {target_id}: {notify_err}")

            try:
                await client.send_message(
                    chat_id=DEVELOPER_USER_ID,
                    text=(
                        f"**🔄 高级会员转让记录**\n\n"
                        f"**📦 套餐：** `{plan_name}`\n"
                        f"**📤 转出者：** `{sender_id}`\n"
                        f"**📥 接收者：** `{target_id}`\n"
                        f"**📅 到期：** `{expiry_date.strftime('%Y-%m-%d') if expiry_date else 'N/A'}`"
                    ),
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass

            LOGGER.info(
                f"Premium transferred: {plan_name} | sender={sender_id} → recipient={target_id}"
            )
            await callback_query.answer("✅ 转让成功！")

        except Exception as e:
            LOGGER.error(f"Transfer failed: sender={sender_id} target={target_id} error={e}")
            pending_transfers.pop(sender_id, None)
            await callback_query.message.edit_text(
                f"**❌ 转让失败！**\n\n`{str(e)}`",
                parse_mode=ParseMode.MARKDOWN,
            )
            await callback_query.answer("❌ 转让失败！", show_alert=True)
