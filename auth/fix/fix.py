# 🔧 通过 Telegram 修复 Async/Await 的命令

import os
import re
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import ParseMode
from config import DEVELOPER_USER_ID, COMMAND_PREFIX
from utils import LOGGER


def setup_fix_handler(app: Client):
    """টেলিগ্রাম থেকে /fix_async কমান্ড দিয়ে ফাইল ফিক্স করুন"""

    async def fix_file_async(filepath: str) -> tuple[bool, int, int]:
        """
        একটি ফাইলে সব সিঙ্ক DB কলে await যোগ করে
        Returns: (success, replaced_count, lines_checked)
        """
        if not os.path.exists(filepath):
            return False, 0, 0

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()

            original_content = content
            replaced_count = 0
            lines_count = len(content.split('\n'))

            # 模式：在 DB 集合调用前添加 await
            patterns = [
                # 查找操作
                (r'(\s)(\w+)\.find_one\(', r'\1await \2.find_one('),
                (r'(\s)(\w+)\.find\(', r'\1await \2.find('),
                (r'(\s)(\w+)\.find_many\(', r'\1await \2.find_many('),
                (r'(\s)(\w+)\.find_one_and_update\(', r'\1await \2.find_one_and_update('),

                # 更新操作
                (r'(\s)(\w+)\.update_one\(', r'\1await \2.update_one('),
                (r'(\s)(\w+)\.update_many\(', r'\1await \2.update_many('),

                # 删除操作
                (r'(\s)(\w+)\.delete_one\(', r'\1await \2.delete_one('),
                (r'(\s)(\w+)\.delete_many\(', r'\1await \2.delete_many('),

                # 插入操作
                (r'(\s)(\w+)\.insert_one\(', r'\1await \2.insert_one('),
                (r'(\s)(\w+)\.insert_many\(', r'\1await \2.insert_many('),

                # 计数/聚合操作
                (r'(\s)(\w+)\.count_documents\(', r'\1await \2.count_documents('),
                (r'(\s)(\w+)\.aggregate\(', r'\1await \2.aggregate('),
                (r'(\s)(\w+)\.distinct\(', r'\1await \2.distinct('),
            ]

            for old_pattern, new_pattern in patterns:
                # 双重 await 检查
                matches = list(re.finditer(old_pattern, content))
                for match in reversed(matches):  # 从后往前匹配，避免索引偏移
                    full_match = match.group(0)
                    if 'await await' not in full_match:
                        content = content[:match.start()] + re.sub(
                            old_pattern, new_pattern, full_match
                        ) + content[match.end():]
                        replaced_count += 1

            # 移除双重 await
            while 'await await' in content:
                content = content.replace('await await ', 'await ')

            if content != original_content:
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(content)
                return True, replaced_count, lines_count
            else:
                return False, 0, lines_count

        except Exception as e:
            LOGGER.error(f"Error fixing {filepath}: {e}")
            return False, 0, 0

    @app.on_message(filters.command("fix_async", prefixes=COMMAND_PREFIX) & filters.private)
    async def fix_async_command(client: Client, message: Message):
        """
        /fix_async - সব প্লাগইন ফাইল অটোমেটিক্যালি ফিক্স করুন
        শুধুমাত্র developer চালাতে পারবেন
        """
        user_id = message.from_user.id
        LOGGER.info(f"/fix_async command from user {user_id}")

        if user_id != DEVELOPER_USER_ID:
            await message.reply_text(
                "**❌ শুধুমাত্র ডেভেলপার এই কমান্ড ব্যবহার করতে পারবেন!**",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        status_msg = await message.reply_text(
            "**🔧 Async/Await ফিক্সিং শুরু হচ্ছে...**\n\n"
            "সব প্লাগইন ফাইল স্ক্যান করা হচ্ছে...",
            parse_mode=ParseMode.MARKDOWN
        )

        # 需要修复的文件列表
        files_to_fix = [
            "plugins/autolink.py",
            "plugins/pvt.py",
            "plugins/pbatch.py",
            "plugins/public.py",
            "plugins/pvdl.py",
            "plugins/info.py",
            "plugins/login.py",
            "plugins/thumb.py",
            "plugins/transfer.py",
            "plugins/plan.py",
            "plugins/settings.py",
            "auth/sudo/sudo.py",
            "auth/restart/restart.py",
            "auth/logs/logs.py",
            "auth/migrate/migrate.py",
            "core/start.py",
            "misc/button_router.py",
        ]

        fixed_files = []
        skipped_files = []
        error_files = []
        total_replaced = 0

        # 逐个修复每个文件
        for filepath in files_to_fix:
            if not os.path.exists(filepath):
                skipped_files.append(f"⏭️ `{filepath}` - পাওয়া যায়নি")
                continue

            success, replaced, lines = await fix_file_async(filepath)

            if success and replaced > 0:
                fixed_files.append(f"✅ `{filepath}` - {replaced} পরিবর্তন")
                total_replaced += replaced
            elif success and replaced == 0:
                skipped_files.append(f"⏭️ `{filepath}` - ইতিমধ্যে ফিক্স করা")
            else:
                error_files.append(f"⚠️ `{filepath}` - ত্রুটি")

        # 生成结果
        result_text = "**✅ Async/Await ফিক্সিং সম্পন্ন!**\n\n"
        result_text += "**━━━━━━━━━━━━━━━━━━━━━━━━**\n\n"

        if fixed_files:
            result_text += "**✨ ফিক্স করা ফাইল:**\n"
            for item in fixed_files:
                result_text += f"{item}\n"
            result_text += "\n"

        if skipped_files:
            result_text += "**⏭️  স্কিপ করা ফাইল:**\n"
            for item in skipped_files[:5]:  # 只显示前5个
                result_text += f"{item}\n"
            if len(skipped_files) > 5:
                result_text += f"... এবং {len(skipped_files) - 5} টি আরও\n"
            result_text += "\n"

        if error_files:
            result_text += "**⚠️ ত্রুটিপূর্ণ ফাইল:**\n"
            for item in error_files:
                result_text += f"{item}\n"
            result_text += "\n"

        result_text += "**━━━━━━━━━━━━━━━━━━━━━━━━**\n\n"
        result_text += f"**📊 সারসংক্ষেপ:**\n"
        result_text += f"• **মোট পরিবর্তন:** `{total_replaced}`\n"
        result_text += f"• **ফিক্স করা:** `{len(fixed_files)}`\n"
        result_text += f"• **স্কিপ করা:** `{len(skipped_files)}`\n"
        result_text += f"• **ত্রুটি:** `{len(error_files)}`\n\n"

        if total_replaced > 0:
            result_text += "**⚡ রিস্টার্টের জন্য প্রস্তুত!**\n"
            result_text += "বট স্বয়ংক্রিয়ভাবে নতুন কোড লোড করবে।\n\n"
            result_text += "**পরবর্তী ধাপ:**\n"
            result_text += "1️⃣ `Render` এ যান এবং রিডিপ্লয় করুন\n"
            result_text += "2️⃣ অথবা `/restart` কমান্ড দিন (যদি সেটা কাজ করে)"
        else:
            result_text += "**ℹ️ সব ফাইল ইতিমধ্যে ফিক্স করা আছে!** ✨"

        # 更新状态消息
        try:
            await status_msg.edit_text(result_text, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            LOGGER.error(f"Failed to update status message: {e}")
            await message.reply_text(result_text, parse_mode=ParseMode.MARKDOWN)

        # 记录日志
        LOGGER.info(
            f"[FIX_ASYNC] সম্পন্ন - Fixed: {len(fixed_files)}, "
            f"Skipped: {len(skipped_files)}, Errors: {len(error_files)}, "
            f"Total changes: {total_replaced}"
        )

    @app.on_message(filters.command("fix_status", prefixes=COMMAND_PREFIX) & filters.private)
    async def fix_status_command(client: Client, message: Message):
        """
        /fix_status - দেখান কোন ফাইল ফিক্স করা প্রয়োজন
        """
        user_id = message.from_user.id

        if user_id != DEVELOPER_USER_ID:
            await message.reply_text(
                "**❌ শুধুমাত্র ডেভেলপার!**",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        status_msg = await message.reply_text(
            "**📋 ফাইল স্ট্যাটাস চেক করা হচ্ছে...**",
            parse_mode=ParseMode.MARKDOWN
        )

        files_to_check = [
            "plugins/autolink.py",
            "plugins/pvt.py",
            "plugins/pbatch.py",
            "plugins/public.py",
            "plugins/pvdl.py",
            "plugins/info.py",
            "auth/sudo/sudo.py",
        ]

        needs_fix = []
        already_fixed = []

        for filepath in files_to_check:
            if not os.path.exists(filepath):
                continue

            with open(filepath, 'r') as f:
                content = f.read()

            # 检查是否存在没有 await 的 find_one 调用
            unfixed_pattern = r'(\s)([a-z_]+)\.find_one\(\{'
            matches = re.findall(unfixed_pattern, content)

            if matches:
                needs_fix.append(f"⚠️ `{filepath}` - {len(matches)} সিঙ্ক কল")
            else:
                already_fixed.append(f"✅ `{filepath}`")

        text = "**📊 Async/Await স্ট্যাটাস:**\n\n"

        if needs_fix:
            text += "**⚠️ ফিক্স করা প্রয়োজন:**\n"
            for item in needs_fix:
                text += f"{item}\n"
            text += "\n"

        if already_fixed:
            text += "**✅ ইতিমধ্যে ফিক্স করা:**\n"
            for item in already_fixed:
                text += f"{item}\n"

        text += "\n**ফিক্স করার জন্য:**\n"
        text += "`/fix_async` দিন"

        await status_msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)
