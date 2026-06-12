# 已修复：高级用户计数、免费用户负数 Bug、统计数据准确性

import os
import time
import math
import psutil
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode
from pyrogram.errors import ChatWriteForbidden, UserIsBlocked, InputUserDeactivated, FloodWait
from pyrogram import StopPropagation
from datetime import datetime, timedelta
import asyncio
from config import COMMAND_PREFIX, DEVELOPER_USER_ID
from utils import LOGGER
from core import total_users, premium_users, downloads_collection, batches_collection
from db.users import upsert_user
from core.database import prem_plan1, prem_plan2, prem_plan3, daily_limit

BOT_START_TIME = time.time()


def get_readable_time(seconds: float) -> str:
    seconds = int(seconds)
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


def get_readable_size(size_bytes: float) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.2f} TB"


def setup_sudo_handler(app: Client):

    async def update_user_activity(user_id: int):
        current_time = datetime.utcnow()
        await total_users.update_one(
            {"user_id": user_id},
            {"$set": {"user_id": user_id, "last_active": current_time}},
            upsert=True
        )

    async def get_active_users():
        current_time = datetime.utcnow()
        daily_active   = await total_users.count_documents({"last_active": {"$gte": current_time - timedelta(days=1)}})
        weekly_active  = await total_users.count_documents({"last_active": {"$gte": current_time - timedelta(days=7)}})
        monthly_active = await total_users.count_documents({"last_active": {"$gte": current_time - timedelta(days=30)}})
        annual_active  = await total_users.count_documents({"last_active": {"$gte": current_time - timedelta(days=365)}})
        total          = await total_users.count_documents({})
        return daily_active, weekly_active, monthly_active, annual_active, total

    async def get_extended_stats():
        current_time = datetime.utcnow()

        # 修复：正确计算唯一的付费用户数量
        # 从三个 plan 集合中收集所有活跃的 user_id 到一个集合中
        try:
            p1_ids = set()
            p2_ids = set()
            p3_ids = set()

            async for doc in prem_plan1.find(
                {"expiry_date": {"$gt": current_time}}, {"user_id": 1}
            ):
                if "user_id" in doc:
                    p1_ids.add(doc["user_id"])

            async for doc in prem_plan2.find(
                {"expiry_date": {"$gt": current_time}}, {"user_id": 1}
            ):
                if "user_id" in doc:
                    p2_ids.add(doc["user_id"])

            async for doc in prem_plan3.find(
                {"expiry_date": {"$gt": current_time}}, {"user_id": 1}
            ):
                if "user_id" in doc:
                    p3_ids.add(doc["user_id"])

            # 并集：唯一付费用户（即使一个用户拥有多个计划也只计一次）
            all_premium_ids = p1_ids | p2_ids | p3_ids
            total_premium = len(all_premium_ids)

        except Exception as e:
            LOGGER.warning(f"Failed to fetch premium users count: {e}")
            total_premium = 0

        # 总注册用户数
        try:
            total_registered = await total_users.count_documents({})
        except Exception:
            total_registered = 0

        # 修复：免费用户 = 总注册用户 - 高级用户（最低为 0）
        free_users = max(0, total_registered - total_premium)

        # 从 daily_limit 集合统计总下载次数
        try:
            pipeline = [{"$group": {"_id": None, "total": {"$sum": "$total_downloads"}}}]
            cursor = daily_limit.aggregate(pipeline)
            result = await cursor.to_list(length=1)
            total_downloads = result[0]["total"] if result else 0
        except Exception as e:
            LOGGER.warning(f"Failed to fetch downloads count: {e}")
            try:
                total_downloads = await downloads_collection.count_documents({})
            except Exception:
                total_downloads = 0

        # 活跃批次
        try:
            active_batches = await batches_collection.count_documents({"status": "active"})
        except Exception as e:
            LOGGER.warning(f"Failed to fetch active batches count: {e}")
            active_batches = 0

        uptime_str = get_readable_time(time.time() - BOT_START_TIME)

        try:
            process     = psutil.Process(os.getpid())
            mem_info    = process.memory_info()
            mem_used    = get_readable_size(mem_info.rss)
            total_mem   = get_readable_size(psutil.virtual_memory().total)
            mem_percent = psutil.virtual_memory().percent
        except Exception:
            mem_used    = "N/A"
            total_mem   = "N/A"
            mem_percent = 0.0

        try:
            cpu_percent = psutil.cpu_percent(interval=0.5)
        except Exception:
            cpu_percent = 0.0

        return total_premium, free_users, total_downloads, active_batches, uptime_str, mem_used, total_mem, mem_percent, cpu_percent

    # ══════════════════════════════════════════════════════════════════════
    # /stats 命令
    # ══════════════════════════════════════════════════════════════════════

    @app.on_message(filters.command("stats", prefixes=COMMAND_PREFIX) & filters.private)
    async def stats_command(client: Client, message: Message):
        user_id = message.from_user.id
        if user_id != DEVELOPER_USER_ID:
            return

        await update_user_activity(user_id)
        LOGGER.info(f"/stats command received from developer {user_id}")

        loading_msg = await message.reply_text(
            "**✘ 正在获取统计... ↯**",
            parse_mode=ParseMode.MARKDOWN
        )

        daily_active, weekly_active, monthly_active, annual_active, total = await get_active_users()
        total_premium, free_users, total_downloads, active_batches, uptime_str, mem_used, total_mem, mem_percent, cpu_percent = await get_extended_stats()

        mongo_total = await total_users.count_documents({})

        stats_message = (
            "**✘《 Restricted Content Downloader — 统计 ↯ 》**\n"
            "**✘━━━━━━━━━━━━━━━━━━━━━━━↯**\n"
            "**✘《 用户活跃度 ↯ 》**\n"
            f"**✘ 日活   :** `{daily_active}`\n"
            f"**✘ 周活  :** `{weekly_active}`\n"
            f"**✘ 月活 :** `{monthly_active}`\n"
            f"**✘ 年活  :** `{annual_active}`\n"
            f"**✘ 总用户数    :** `{total}`\n"
            f"**✘ MongoDB 用户  :** `{mongo_total}` _(数据库计数)_\n"
            "**✘━━━━━━━━━━━━━━━━━━━━━━━↯**\n"
            "**✘《 高级与下载 ↯ 》**\n"
            f"**✘ 高级用户    :** `{total_premium}`\n"
            f"**✘ 免费用户       :** `{free_users}`\n"
            f"**✘ 总下载数  :** `{total_downloads}`\n"
            f"**✘ 活跃批次   :** `{active_batches}`\n"
            "**✘━━━━━━━━━━━━━━━━━━━━━━━↯**\n"
            "**✘《 服务器信息 ↯ 》**\n"
            f"**✘ 运行时间     :** `{uptime_str}`\n"
            f"**✘ CPU 使用  :** `{cpu_percent:.1f}%`\n"
            f"**✘ RAM 使用   :** `{mem_used} / {total_mem} ({mem_percent:.1f}%)`\n"
            "**✘━━━━━━━━━━━━━━━━━━━━━━━↯**\n"
            ""
        )

        await loading_msg.edit_text(
            stats_message,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [
                    
                    InlineKeyboardButton("👥 用户列表 ↯", callback_data="users_page_0"),
                ]
            ])
        )

    # ══════════════════════════════════════════════════════════════════════
    # /users 命令
    # ══════════════════════════════════════════════════════════════════════

    USERS_PER_PAGE = 20

    async def build_users_text(page: int) -> tuple:
        total_count = await total_users.count_documents({})
        total_pages = max(1, math.ceil(total_count / USERS_PER_PAGE))
        page = max(0, min(page, total_pages - 1))

        skip = page * USERS_PER_PAGE
        cursor = total_users.find(
            {},
            {"user_id": 1, "name": 1, "username": 1, "first_name": 1, "last_name": 1, "_id": 0}
        ).sort("_id", -1).skip(skip).limit(USERS_PER_PAGE)

        users_list = await cursor.to_list(length=USERS_PER_PAGE)

        lines = [
            f"**✘《 ইউজার লিস্ট — Page {page + 1}/{total_pages} ↯ 》**\n"
            f"**✘ মোট ইউজার: `{total_count}` জন ↯**\n"
            "**✘━━━━━━━━━━━━━━━━━━━━━━━↯**\n"
        ]

        for i, user in enumerate(users_list, start=skip + 1):
            uid   = user.get("user_id", "N/A")
            name  = user.get("name") or ""
            if not name:
                first = user.get("first_name", "")
                last  = user.get("last_name", "")
                name  = f"{first} {last}".strip()
            if not name:
                name = "Unknown"
            uname     = user.get("username")
            uname_str = f"@{uname}" if uname else "N/A"
            lines.append(
                f"**{i}.** 👤 `{name}`\n"
                f"    🆔 `{uid}` | 📛 {uname_str}\n"
            )

        if not users_list:
            lines.append("_কোনো ইউজার পাওয়া যায়নি।_")

        lines.append("**✘━━━━━━━━━━━━━━━━━━━━━━━↯**")
        return "".join(lines), total_pages

    def build_users_keyboard(page: int, total_pages: int) -> InlineKeyboardMarkup:
        buttons = []
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("◀ আগের", callback_data=f"users_page_{page - 1}"))
        nav_row.append(InlineKeyboardButton(f"📄 {page + 1}/{total_pages}", callback_data="users_noop"))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton("পরের ▶", callback_data=f"users_page_{page + 1}"))
        buttons.append(nav_row)
        buttons.append([InlineKeyboardButton("✘ বন্ধ করো ↯", callback_data="users_close")])
        return InlineKeyboardMarkup(buttons)

    @app.on_message(filters.command("users", prefixes=COMMAND_PREFIX) & filters.private)
    async def users_command(client: Client, message: Message):
        user_id = message.from_user.id
        if user_id != DEVELOPER_USER_ID:
            return
        LOGGER.info(f"/users command from developer {user_id}")
        loading = await message.reply_text("**✘ MongoDB থেকে ইউজার লোড হচ্ছে... ↯**", parse_mode=ParseMode.MARKDOWN)
        try:
            text, total_pages = await build_users_text(page=0)
            keyboard = build_users_keyboard(page=0, total_pages=total_pages)
            await loading.edit_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)
        except Exception as e:
            LOGGER.error(f"/users error: {e}")
            await loading.edit_text(f"**❌ Error: `{e}`**", parse_mode=ParseMode.MARKDOWN)

    @app.on_callback_query(filters.regex(r"^users_page_(\d+)$"))
    async def users_page_callback(client, callback_query):
        if callback_query.from_user.id != DEVELOPER_USER_ID:
            await callback_query.answer("❌ শুধু ডেভেলপার দেখতে পারবে!", show_alert=True)
            return
        page = int(callback_query.data.split("_")[2])
        try:
            text, total_pages = await build_users_text(page=page)
            keyboard = build_users_keyboard(page=page, total_pages=total_pages)
            await callback_query.message.edit_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)
            await callback_query.answer(f"Page {page + 1}/{total_pages}")
        except Exception as e:
            LOGGER.error(f"users_page_callback error: {e}")
            await callback_query.answer("❌ Error loading page!", show_alert=True)

    @app.on_callback_query(filters.regex(r"^users_close$"))
    async def users_close_callback(client, callback_query):
        if callback_query.from_user.id != DEVELOPER_USER_ID:
            await callback_query.answer("❌ শুধু ডেভেলপার বন্ধ করতে পারবে!", show_alert=True)
            return
        await callback_query.message.delete()
        await callback_query.answer("✅ বন্ধ হয়েছে")

    @app.on_callback_query(filters.regex(r"^users_noop$"))
    async def users_noop_callback(client, callback_query):
        await callback_query.answer()

    # ══════════════════════════════════════════════════════════════════════
    # /refresh 命令 — 从 Telegram API 更新所有用户信息
    # 用法：/refresh           → 批量刷新所有用户
    #       /refresh <用户ID>  → 按 ID 刷新单个用户
    # ══════════════════════════════════════════════════════════════════════

    @app.on_message(filters.command("refresh", prefixes=COMMAND_PREFIX) & filters.private)
    async def refresh_command(client: Client, message: Message):
        user_id = message.from_user.id
        if user_id != DEVELOPER_USER_ID:
            return

        await update_user_activity(user_id)
        LOGGER.info(f"/refresh command received from developer {user_id}")

        # ── 单个用户刷新：/refresh <用户ID> ──────────────────────
        if len(message.command) >= 2:
            try:
                target_uid = int(message.command[1])
            except ValueError:
                await message.reply_text(
                    "❌ **无效的用户ID！**\n用法：`/refresh 7963315216`",
                    parse_mode=ParseMode.MARKDOWN,
                )
                raise StopPropagation

            try:
                tg_user = await client.get_users(target_uid)
            except Exception as exc:
                LOGGER.warning(f"[Refresh] get_users failed for {target_uid}: {exc}")
                await message.reply_text(
                    f"❌ **无法从 Telegram 获取用户 `{target_uid}`。**\n"
                    f"用户可能已屏蔽/注销。\n\n"
                    f"_错误：{exc}_",
                    parse_mode=ParseMode.MARKDOWN,
                )
                raise StopPropagation

            try:
                doc = await upsert_user(tg_user)
            except Exception as exc:
                LOGGER.error(f"[Refresh] DB upsert failed for {target_uid}: {exc}")
                await message.reply_text(
                    "❌ **保存用户资料时数据库错误。**",
                    parse_mode=ParseMode.MARKDOWN,
                )
                raise StopPropagation

            username_display = f"@{doc['username']}" if doc.get("username") else "(no username)"
            premium_icon     = "✅" if doc.get("is_premium")  else "❌"
            verified_icon    = "✅" if doc.get("is_verified") else "❌"
            scam_icon        = "⚠️" if doc.get("is_scam")    else "✅"
            fake_icon        = "⚠️" if doc.get("is_fake")    else "✅"

            dc_line   = f"\n**📡 DC:** `{doc['dc_id']}`" if doc.get("dc_id") else ""
            lang_line = f"\n**🌐 语言：** `{doc['language_code']}`" if doc.get("language_code") else ""

            reply = (
                f"✅ **用户资料已刷新！**\n"
                f"**━━━━━━━━━━━━━━━━**\n"
                f"**🆔 ID:** `{doc['user_id']}`\n"
                f"**👤 名称：** `{doc['full_name']}`\n"
                f"**📛 用户名：** `{username_display}`\n"
                f"**━━━━━━━━━━━━━━━━**\n"
                f"**💎 高级：** {premium_icon}\n"
                f"**✔️ 已验证：** {verified_icon}\n"
                f"**🚫 诈骗：** {scam_icon}\n"
                f"**🎭 虚假：** {fake_icon}"
                f"{dc_line}"
                f"{lang_line}\n"
                f"**━━━━━━━━━━━━━━━━**\n"
                f"**🕒 刷新时间：** `{doc['refreshed_at'].strftime('%Y-%m-%d %H:%M:%S UTC')}`"
            )
            await message.reply_text(reply, parse_mode=ParseMode.MARKDOWN)
            LOGGER.info(f"[Refresh] Single user refreshed: {target_uid}")
            raise StopPropagation

        # ── 批量刷新：/refresh（无参数）─────────────────────────────
        progress_msg = await message.reply_text(
            "**✘ ডাটাবেজ থেকে ইউজার লোড হচ্ছে... ↯**",
            parse_mode=ParseMode.MARKDOWN
        )

        cursor = total_users.find({}, {"user_id": 1})
        users_list = await cursor.to_list(length=None)
        total_count = len(users_list)

        if total_count == 0:
            await progress_msg.edit_text(
                "**❌ ডাটাবেজে কোনো ইউজার নেই!**",
                parse_mode=ParseMode.MARKDOWN
            )
            raise StopPropagation

        updated_count = 0
        skipped_count = 0
        failed_count = 0
        not_found_count = 0

        await progress_msg.edit_text(
            f"**✘ ইউজার ইনফো আপডেট শুরু হচ্ছে... ↯**\n"
            f"**✘ মোট ইউজার: `{total_count}` জন ↯**\n"
            f"**✘ অপেক্ষা করুন... ↯**",
            parse_mode=ParseMode.MARKDOWN
        )

        for idx, user_doc in enumerate(users_list, start=1):
            uid = user_doc.get("user_id")
            if not uid:
                skipped_count += 1
                continue

            try:
                tg_user = await client.get_users(uid)
            except FloodWait as e:
                LOGGER.warning(f"[Refresh] FloodWait {e.value}s for user {uid}")
                await asyncio.sleep(e.value + 2)
                try:
                    tg_user = await client.get_users(uid)
                except Exception:
                    failed_count += 1
                    continue
            except Exception as e:
                LOGGER.warning(f"[Refresh] Cannot fetch user {uid}: {e}")
                not_found_count += 1
                continue

            try:
                await upsert_user(tg_user)
                updated_count += 1
            except Exception as e:
                LOGGER.error(f"[Refresh] DB update failed for user {uid}: {e}")
                failed_count += 1

            if idx % 10 == 0:
                try:
                    await progress_msg.edit_text(
                        f"**✘ ইউজার ইনফো আপডেট চলছে... ↯**\n"
                        f"**✘ প্রগ্রেস: `{idx}/{total_count}` ↯**\n"
                        f"**✘ আপডেট: `{updated_count}` | স্কিপ: `{skipped_count}` ↯**\n"
                        f"**✘ ব্যর্থ: `{failed_count}` | পাওয়া যায়নি: `{not_found_count}` ↯**",
                        parse_mode=ParseMode.MARKDOWN
                    )
                except FloodWait as e:
                    await asyncio.sleep(e.value)
                except Exception:
                    pass

            await asyncio.sleep(0.3)

        report = (
            "**✘《 ইউজার ইনফো রিফ্রেশ রিপোর্ট ↯ 》**\n"
            "**✘━━━━━━━━━━━━━━━━━━━━━━━↯**\n"
            f"**✘ মোট ইউজার    : `{total_count}` জন ↯**\n"
            f"**✘ আপডেট হয়েছে  : `{updated_count}` জন ↯**\n"
            f"**✘ স্কিপ হয়েছে    : `{skipped_count}` জন ↯**\n"
            f"**✘ ব্যর্থ হয়েছে    : `{failed_count}` জন ↯**\n"
            f"**✘ পাওয়া যায়নি   : `{not_found_count}` জন ↯**\n"
            "**✘━━━━━━━━━━━━━━━━━━━━━━━↯**\n"
            "**✅ রিফ্রেশ সম্পন্ন হয়েছে!**"
        )
        try:
            await progress_msg.edit_text(report, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            await client.send_message(chat_id=user_id, text=report, parse_mode=ParseMode.MARKDOWN)

        LOGGER.info(
            f"[Refresh] Done — Total: {total_count}, Updated: {updated_count}, "
            f"Skipped: {skipped_count}, Failed: {failed_count}, NotFound: {not_found_count}"
        )
        raise StopPropagation

    # ══════════════════════════════════════════════════════════════════════
    # /gcast 命令
    # ══════════════════════════════════════════════════════════════════════

    @app.on_message(filters.command("gcast", prefixes=COMMAND_PREFIX) & filters.private)
    async def gcast_command(client: Client, message: Message):
        user_id = message.from_user.id
        if user_id != DEVELOPER_USER_ID:
            return

        await update_user_activity(user_id)
        LOGGER.info(f"/gcast command received from developer {user_id}")

        if not message.reply_to_message:
            await message.reply_text("**❌ 请回复一条消息以进行广播！**", parse_mode=ParseMode.MARKDOWN)
            return

        broadcast_message = message.reply_to_message
        start_time = datetime.utcnow()
        success_count = 0
        blocked_count = 0
        failed_count = 0
        deactivated_count = 0
        flood_wait_count = 0

        cursor = total_users.find({}, {"user_id": 1})
        users_list = await cursor.to_list(length=None)
        user_ids = [u["user_id"] for u in users_list]

        buttons = InlineKeyboardMarkup([[
            InlineKeyboardButton("更新频道", url="")
        ]])

        for target_user_id in user_ids:
            while True:
                try:
                    sent_message = await client.copy_message(
                        chat_id=target_user_id,
                        from_chat_id=user_id,
                        message_id=broadcast_message.id,
                        reply_markup=buttons,
                        parse_mode=ParseMode.MARKDOWN if broadcast_message.text or broadcast_message.caption else None
                    )
                    try:
                        await client.pin_chat_message(target_user_id, sent_message.id, both_sides=True)
                    except Exception as e:
                        LOGGER.warning(f"Failed to pin gcast message for user {target_user_id}: {e}")
                    success_count += 1
                    await asyncio.sleep(0.5)
                    break
                except FloodWait as e:
                    flood_wait_count += 1
                    await asyncio.sleep(e.value + 5)
                    continue
                except UserIsBlocked:
                    blocked_count += 1
                    break
                except InputUserDeactivated:
                    deactivated_count += 1
                    break
                except Exception as e:
                    failed_count += 1
                    LOGGER.error(f"Failed to send gcast to user {target_user_id}: {e}")
                    break

        time_taken = (datetime.utcnow() - start_time).total_seconds()
        report_message = (
            "**📢 全局广播报告 ↯**\n"
            "**✘━━━━━━━━━━━↯**\n"
            f"**✘ 成功  : {success_count} ↯**\n"
            f"**✘ 已屏蔽     : {blocked_count} ↯**\n"
            f"**✘ 已注销 : {deactivated_count} ↯**\n"
            f"**✘ 失败      : {failed_count} ↯**\n"
            f"**✘ 限流等待 : {flood_wait_count} ↯**\n"
            f"**✘ 耗时  : {int(time_taken)}s ↯**\n"
            "**✘━━━━━━━━━━━↯**\n"
            "**✅ 广播完成！**"
        )
        await client.send_message(chat_id=user_id, text=report_message, parse_mode=ParseMode.MARKDOWN)

    # ══════════════════════════════════════════════════════════════════════
    # /acast 命令
    # ══════════════════════════════════════════════════════════════════════

    @app.on_message(filters.command("acast", prefixes=COMMAND_PREFIX) & filters.private)
    async def acast_command(client: Client, message: Message):
        user_id = message.from_user.id
        if user_id != DEVELOPER_USER_ID:
            return

        await update_user_activity(user_id)
        LOGGER.info(f"/acast command received from developer {user_id}")

        if not message.reply_to_message:
            await message.reply_text(
                "**❌ 请回复一条消息以进行广播！**",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        broadcast_message = message.reply_to_message
        start_time = datetime.utcnow()
        success_count = 0
        blocked_count = 0
        failed_count = 0
        deactivated_count = 0
        flood_wait_count = 0

        cursor = total_users.find({}, {"user_id": 1})
        users_list = await cursor.to_list(length=None)
        user_ids = [u["user_id"] for u in users_list]

        for target_user_id in user_ids:
            while True:
                try:
                    sent_message = await client.forward_messages(
                        chat_id=target_user_id,
                        from_chat_id=user_id,
                        message_ids=broadcast_message.id
                    )
                    try:
                        await client.pin_chat_message(target_user_id, sent_message.id, both_sides=True)
                    except Exception as e:
                        LOGGER.warning(f"Failed to pin acast message for user {target_user_id}: {e}")
                    success_count += 1
                    await asyncio.sleep(0.5)
                    break
                except FloodWait as e:
                    flood_wait_count += 1
                    await asyncio.sleep(e.value + 5)
                    continue
                except UserIsBlocked:
                    blocked_count += 1
                    break
                except InputUserDeactivated:
                    deactivated_count += 1
                    break
                except Exception as e:
                    failed_count += 1
                    LOGGER.error(f"Failed to send acast to user {target_user_id}: {e}")
                    break

        time_taken = (datetime.utcnow() - start_time).total_seconds()
        report_message = (
            "**📢 管理员广播报告 ↯**\n"
            "**✘━━━━━━━━━━━↯**\n"
            f"**✘ 成功  : {success_count} ↯**\n"
            f"**✘ 已屏蔽     : {blocked_count} ↯**\n"
            f"**✘ 已注销 : {deactivated_count} ↯**\n"
            f"**✘ 失败      : {failed_count} ↯**\n"
            f"**✘ 限流等待 : {flood_wait_count} ↯**\n"
            f"**✘ 耗时  : {int(time_taken)}s ↯**\n"
            "**✘━━━━━━━━━━━↯**\n"
            "**✅ 广播完成！**"
        )
        await client.send_message(chat_id=user_id, text=report_message, parse_mode=ParseMode.MARKDOWN)

    # ══════════════════════════════════════════════════════════════════════
    # /send 命令 — 按用户 ID 发送消息给指定用户
    # ══════════════════════════════════════════════════════════════════════

    @app.on_message(filters.command("send", prefixes=COMMAND_PREFIX) & filters.private)
    async def send_command(client: Client, message: Message):
        user_id = message.from_user.id
        if user_id != DEVELOPER_USER_ID:
            return

        await update_user_activity(user_id)
        LOGGER.info(f"/send command received from developer {user_id}")

        if len(message.command) < 2:
            await message.reply_text(
                "**❌ ইউজার আইডি দিন!**\n\n"
                "**Usage:**\n"
                "`/send <user_id>` — Reply to a message to send it to that user.\n\n"
                "**Example:**\n"
                "একটি মেসেজ রিপ্লাই করে `/send 123456789` লিখুন।",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        try:
            target_user_id = int(message.command[1])
        except ValueError:
            await message.reply_text(
                "**❌ 无效的用户ID！请提供数字用户ID。**",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        if not message.reply_to_message:
            await message.reply_text(
                "**❌ 请回复一条消息以发送给用户！**\n\n"
                "**用法：** 回复任意消息并输入 `/send <user_id>`",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        # 检查用户是否存在于数据库中
        user_doc = await total_users.find_one({"user_id": target_user_id})
        if not user_doc:
            await message.reply_text(
                f"**❌ 数据库中未找到用户 `{target_user_id}`！**",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        try:
            await client.copy_message(
                chat_id=target_user_id,
                from_chat_id=user_id,
                message_id=message.reply_to_message.id,
                parse_mode=ParseMode.MARKDOWN if message.reply_to_message.text or message.reply_to_message.caption else None
            )
            target_name = user_doc.get("name") or user_doc.get("first_name") or "Unknown"
            await message.reply_text(
                f"**✅ 消息发送成功！**\n\n"
                f"**👤 名称：** `{target_name}`\n"
                f"**🆔 ID:** `{target_user_id}`",
                parse_mode=ParseMode.MARKDOWN
            )
            LOGGER.info(f"Message sent to user {target_user_id} by developer {user_id}")
        except UserIsBlocked:
            await message.reply_text(
                f"**❌ 用户 `{target_user_id}` 已屏蔽机器人！**",
                parse_mode=ParseMode.MARKDOWN
            )
        except InputUserDeactivated:
            await message.reply_text(
                f"**❌ 用户 `{target_user_id}` 账户已注销！**",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            await message.reply_text(
                f"**❌ 发送消息给用户 `{target_user_id}` 失败！**\n\n"
                f"**错误：** `{str(e)}`",
                parse_mode=ParseMode.MARKDOWN
            )
            LOGGER.error(f"Failed to send message to user {target_user_id}: {e}")

    # ══════════════════════════════════════════════════════════════════════
    # /broadcast 命令 — 向所有用户纯净广播（无额外按钮）
    # ══════════════════════════════════════════════════════════════════════

    @app.on_message(filters.command("broadcast", prefixes=COMMAND_PREFIX) & filters.private)
    async def broadcast_command(client: Client, message: Message):
        user_id = message.from_user.id
        if user_id != DEVELOPER_USER_ID:
            return

        await update_user_activity(user_id)
        LOGGER.info(f"/broadcast command received from developer {user_id}")

        if not message.reply_to_message:
            await message.reply_text(
                "**❌ একটি মেসেজ রিপ্লাই করে /broadcast লিখুন!**\n\n"
                "**Usage:**\n"
                "যে মেসেজটি সবাইকে পাঠাতে চান সেটি রিপ্লাই করে `/broadcast` লিখুন।\n\n"
                "এই কমান্ড সবার কাছে একসাথে মেসেজ পাঠাবে।",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        broadcast_message = message.reply_to_message
        start_time = datetime.utcnow()
        success_count = 0
        blocked_count = 0
        failed_count = 0
        deactivated_count = 0
        flood_wait_count = 0

        cursor = total_users.find({}, {"user_id": 1})
        users_list = await cursor.to_list(length=None)
        user_ids = [u["user_id"] for u in users_list]
        total_count = len(user_ids)

        progress_msg = await message.reply_text(
            f"**📢 ব্রডকাস্ট শুরু হচ্ছে...**\n"
            f"**✘ মোট ইউজার: `{total_count}` জন ↯**",
            parse_mode=ParseMode.MARKDOWN
        )

        for idx, target_user_id in enumerate(user_ids, start=1):
            while True:
                try:
                    await client.copy_message(
                        chat_id=target_user_id,
                        from_chat_id=user_id,
                        message_id=broadcast_message.id,
                        parse_mode=ParseMode.MARKDOWN if broadcast_message.text or broadcast_message.caption else None
                    )
                    success_count += 1
                    await asyncio.sleep(0.5)
                    break
                except FloodWait as e:
                    flood_wait_count += 1
                    await asyncio.sleep(e.value + 5)
                    continue
                except UserIsBlocked:
                    blocked_count += 1
                    break
                except InputUserDeactivated:
                    deactivated_count += 1
                    break
                except Exception as e:
                    failed_count += 1
                    LOGGER.error(f"Failed to send broadcast to user {target_user_id}: {e}")
                    break

            # 每 25 个用户更新一次进度
            if idx % 25 == 0 or idx == total_count:
                try:
                    await progress_msg.edit_text(
                        f"**📢 ব্রডকাস্ট চলছে... ↯**\n"
                        f"**✘ প্রগ্রেস: `{idx}/{total_count}` ↯**\n"
                        f"**✘ সফল: `{success_count}` | ব্লক: `{blocked_count}` | ব্যর্থ: `{failed_count}` ↯**",
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception:
                    pass

        time_taken = (datetime.utcnow() - start_time).total_seconds()
        report_message = (
            "**📢 ব্রডকাস্ট রিপোর্ট ↯**\n"
            "**✘━━━━━━━━━━━↯**\n"
            f"**✘ মোট ইউজার   : {total_count} ↯**\n"
            f"**✘ সফল         : {success_count} ↯**\n"
            f"**✘ ব্লক করেছে   : {blocked_count} ↯**\n"
            f"**✘ নিষ্ক্রিয়     : {deactivated_count} ↯**\n"
            f"**✘ ব্যর্থ        : {failed_count} ↯**\n"
            f"**✘ ফ্লাড ওয়েট   : {flood_wait_count} ↯**\n"
            f"**✘ সময় লেগেছে  : {int(time_taken)}s ↯**\n"
            "**✘━━━━━━━━━━━↯**\n"
            "**✅ সবাইকে মেসেজ পাঠানো হয়েছে!**"
        )
        try:
            await progress_msg.edit_text(report_message, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            await client.send_message(chat_id=user_id, text=report_message, parse_mode=ParseMode.MARKDOWN)
