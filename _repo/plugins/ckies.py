# ckies.py — Cookies Manager Plugin (Pyrofork)
# Commands: /adc  →  Add/update cookies file (owner only, reply to .txt file)
#           /rmc  →  Remove/delete cookies file (owner only)
# Place this file inside your bot's plugins/ folder.

import hashlib
import os
import re
import shutil
import time
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from pyrogram.enums import ParseMode

from .ythelpers import LOGGER

# ─── Config ───────────────────────────────────────────────────────────────────
# তোমার bot এর OWNER_ID এখানে বসাও অথবা config থেকে import করো
try:
    from config import OWNER_ID
except ImportError:
    OWNER_ID = 0  # fallback — অবশ্যই সঠিক ID দাও

# Cookies file path — bot root / cookies / SmartYTUtil.txt
COOKIES_PATH = Path(__file__).resolve().parent.parent / "cookies" / "ytcookies.txt"

# ─── State ────────────────────────────────────────────────────────────────────
pending_rmc: dict = {}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def is_valid_netscape_cookies(content: str) -> bool:
    """Netscape cookies format যাচাই করে।"""
    has_header      = False
    has_valid_entry = False
    for line in content.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            if "Netscape" in line or "HTTP Cookie" in line:
                has_header = True
            continue
        if len(line.split("\t")) >= 6:
            has_valid_entry = True
    return has_header or has_valid_entry


def build_rmc_markup(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("❌ Cancel",    callback_data=f"RMC|{token}|cancel"),
            InlineKeyboardButton("Delete ⚙️",   callback_data=f"RMC|{token}|delete"),
        ]
    ])


# ─── Command: /adc ────────────────────────────────────────────────────────────

@Client.on_message(filters.command(["adc"], prefixes=["/", "!", "."]))
async def adc_command(client: Client, message: Message):
    if message.from_user.id != OWNER_ID:
        return

    if not message.reply_to_message:
        await message.reply_text(
            "**❌ Please reply to a Netscape format cookies file.**\n"
            "**Usage:** Reply to a `.txt` cookies file with `/adc`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    replied = message.reply_to_message

    if not replied.document:
        await message.reply_text(
            "**❌ Please reply to a valid cookies file document.**",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # filename চেক
    file_name = replied.document.file_name or ""
    if not file_name.endswith(".txt"):
        await message.reply_text(
            "**❌ File must be a `.txt` Netscape format cookies file.**",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    status = await message.reply_text(
        "**Changing Cookies With New...**",
        parse_mode=ParseMode.MARKDOWN,
    )

    # cookies folder নিশ্চিত করো
    COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = COOKIES_PATH.parent / "cookies_new_temp.txt"

    # ফাইল download করো
    try:
        await client.download_media(replied, file_name=str(temp_path))
    except Exception as e:
        LOGGER.error(f"Cookie download error: {e}")
        await status.edit_text("**Failed To Update Cookies As Not Valid**")
        return

    if not temp_path.exists():
        await status.edit_text("**Failed To Update Cookies As Not Valid**")
        return

    # content পড়ো ও validate করো
    try:
        content = temp_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        LOGGER.error(f"Cookie read error: {e}")
        temp_path.unlink(missing_ok=True)
        await status.edit_text("**Failed To Update Cookies As Not Valid**")
        return

    if not is_valid_netscape_cookies(content):
        temp_path.unlink(missing_ok=True)
        await status.edit_text("**Failed To Update Cookies As Not Valid**")
        return

    # replace করো
    try:
        if COOKIES_PATH.exists():
            COOKIES_PATH.unlink()
        shutil.move(str(temp_path), str(COOKIES_PATH))
        LOGGER.info(f"Cookies updated successfully by owner {message.from_user.id}")
        await status.edit_text("**Successfully Changed The Cookies ✅**")
    except Exception as e:
        LOGGER.error(f"Cookie replace error: {e}")
        temp_path.unlink(missing_ok=True)
        await status.edit_text("**Failed To Update Cookies As Not Valid**")


# ─── Command: /rmc ────────────────────────────────────────────────────────────

@Client.on_message(filters.command(["rmc"], prefixes=["/", "!", "."]))
async def rmc_command(client: Client, message: Message):
    if message.from_user.id != OWNER_ID:
        return

    LOGGER.info(f"Remove cookies requested by owner {message.from_user.id}")

    raw   = f"{time.time()}{message.from_user.id}"
    token = hashlib.md5(raw.encode()).hexdigest()[:12]

    pending_rmc[token] = {
        "user_id": message.from_user.id,
        "chat_id": message.chat.id,
    }

    await message.reply_text(
        "**Do You Want To Cleanup Cookies?**",
        reply_markup=build_rmc_markup(token),
        parse_mode=ParseMode.MARKDOWN,
    )


# ─── Callback: RMC ───────────────────────────────────────────────────────────

@Client.on_callback_query(filters.regex(r"^RMC\|"))
async def rmc_callback(client: Client, callback_query: CallbackQuery):
    if callback_query.from_user.id != OWNER_ID:
        return

    parts = callback_query.data.split("|")
    if len(parts) != 3:
        return

    token  = parts[1]
    action = parts[2]

    data = pending_rmc.get(token)
    if not data:
        await callback_query.answer("❌ Session expired.", show_alert=True)
        try:
            await callback_query.message.edit_text("**❌ Session expired.**")
        except Exception:
            pass
        return

    if action == "cancel":
        pending_rmc.pop(token, None)
        await callback_query.answer("✅ Cancelled")
        try:
            await callback_query.message.edit_text("**❌ Cancelled.**")
        except Exception:
            pass

    elif action == "delete":
        try:
            if COOKIES_PATH.exists():
                COOKIES_PATH.unlink()
                LOGGER.info(f"Cookies deleted by owner {callback_query.from_user.id}")
                await callback_query.message.edit_text("**Successfully Deleted Cookies ❌**")
            else:
                await callback_query.message.edit_text("**No Cookies File Found To Delete.**")
            await callback_query.answer("✅ Done")
        except Exception as e:
            LOGGER.error(f"Cookie delete error: {e}")
            await callback_query.answer("❌ Failed to delete cookies.", show_alert=True)
            try:
                await callback_query.message.edit_text("**❌ Failed To Delete Cookies.**")
            except Exception:
                pass

        pending_rmc.pop(token, None)
