# Copyright @juktijol
# Channel t.me/juktijol
#
# Improved Login System — Phone number only (no API_ID/API_HASH from user)
# Uses bot's own API_ID & API_HASH from config for session generation.
# ALL users (free + premium) can use /login.
# Free users: 1 account max. Premium users: plan-based limits.
# ✅ FIXED: Timeout handling + Better error messages

import os
import uuid
import asyncio
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import (
    ApiIdInvalid,
    PhoneNumberInvalid,
    PhoneCodeInvalid,
    PhoneCodeExpired,
    SessionPasswordNeeded,
    PasswordHashInvalid,
    MessageNotModified,
    FloodWait,
)
from config import COMMAND_PREFIX, API_ID, API_HASH
from utils.logging_setup import LOGGER
from core import prem_plan1, prem_plan2, prem_plan3, user_sessions
from datetime import datetime

# Timeout constants
TIMEOUT_OTP = 600   # 10 minutes
TIMEOUT_2FA = 300   # 5 minutes
DB_TIMEOUT = 5.0    # Database timeout

# In-memory session state: { chat_id: {...} }
session_data = {}


def setup_login_handler(app: Client):

    # ── Plan limits ────────────────────────────────────────────────────────

    async def get_plan_limits(user_id: int) -> tuple[bool, int]:
        """
        Returns (is_premium, max_accounts).
        Free users: (False, 1) — can log in with 1 account.
        Premium users: account limit based on plan.
        ✅ FIXED: Timeout + Error Handling
        """
        current_time = datetime.utcnow()

        try:
            p3 = await asyncio.wait_for(
                prem_plan3.find_one({"user_id": user_id, "expiry_date": {"$gt": current_time}}),
                timeout=DB_TIMEOUT
            )
            if p3:
                return True, 10
            
            p2 = await asyncio.wait_for(
                prem_plan2.find_one({"user_id": user_id, "expiry_date": {"$gt": current_time}}),
                timeout=DB_TIMEOUT
            )
            if p2:
                return True, 5
            
            p1 = await asyncio.wait_for(
                prem_plan1.find_one({"user_id": user_id, "expiry_date": {"$gt": current_time}}),
                timeout=DB_TIMEOUT
            )
            if p1:
                return True, 1
        except asyncio.TimeoutError:
            LOGGER.warning(f"[Login] Database timeout for plan check of user {user_id}")
            return False, 1  # Default to free user on timeout
        except Exception as e:
            LOGGER.error(f"[Login] Plan check error for user {user_id}: {e}")
            return False, 1

        return False, 1

    # ── /login command ─────────────────────────────────────────────────────

    @app.on_message(filters.command("login", prefixes=COMMAND_PREFIX) & (filters.private | filters.group))
    async def login_command(client: Client, message: Message):
        user_id = message.from_user.id
        LOGGER.info(f"/login command received from user {user_id}")

        try:
            is_premium, max_accounts = await get_plan_limits(user_id)
        except Exception as e:
            LOGGER.error(f"Plan check error for user {user_id}: {e}")
            is_premium, max_accounts = False, 1

        # Check existing session count (Motor async) ✅ FIXED: Timeout
        try:
            user_session = await asyncio.wait_for(
                user_sessions.find_one({"user_id": user_id}),
                timeout=DB_TIMEOUT
            ) or {"sessions": []}
        except asyncio.TimeoutError:
            LOGGER.warning(f"[Login] Database timeout fetching sessions for user {user_id}")
            await message.reply_text(
                "**⏳ Database timeout. Please try again in a moment.**",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        except Exception as e:
            LOGGER.error(f"Session fetch error for user {user_id}: {e}")
            user_session = {"sessions": []}

        current_sessions = user_session.get("sessions", [])
        if len(current_sessions) >= max_accounts:
            plan_note = (
                "Upgrade your plan to add more accounts: /plans"
                if not is_premium
                else "Use /logout to remove an existing account first."
            )
            await message.reply_text(
                f"**❌ You have reached the limit of {max_accounts} "
                f"account{'s' if max_accounts > 1 else ''}!**\n\n"
                f"{plan_note}",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        # Initialise state
        session_data[message.chat.id] = {"user_id": user_id, "stage": "phone"}

        plan_label = "✨ Premium" if is_premium else "🆓 Free"
        await message.reply_text(
            f"**🔐 Login Setup** ({plan_label})\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "**⚠️ Important — Read Before You Login:**\n\n"
            "✅ Log in with the Telegram account that is\n"
            "    already a **member** of the private channel\n"
            "    or group you want to download from.\n\n"
            "❌ If your account is **not a member** of that\n"
            "    channel/group, the download will **fail**.\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "📱 Send your **phone number** with country code:\n\n"
            "**Example:** `+8801XXXXXXXXX`\n\n"
            "__Session stored securely. Use /logout to remove anytime.__",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data="login_cancel"),
            ]]),
        )

    # ── /logout command ────────────────────────────────────────────────────

    @app.on_message(filters.command("logout", prefixes=COMMAND_PREFIX) & (filters.private | filters.group))
    async def logout_command(client: Client, message: Message):
        user_id = message.from_user.id
        LOGGER.info(f"/logout command received from user {user_id}")

        try:
            user_session = await asyncio.wait_for(
                user_sessions.find_one({"user_id": user_id}),
                timeout=DB_TIMEOUT
            )
        except asyncio.TimeoutError:
            LOGGER.warning(f"[Logout] Database timeout for user {user_id}")
            await message.reply_text(
                "**⏳ Database timeout. Please try again.**",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        except Exception as e:
            LOGGER.error(f"Session fetch error: {e}")
            user_session = None

        if not user_session or not user_session.get("sessions"):
            await message.reply_text(
                "**❌ You are not logged in to any account.**",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        sessions = user_session.get("sessions", [])

        if len(sessions) == 1:
            try:
                await asyncio.wait_for(
                    user_sessions.delete_one({"user_id": user_id}),
                    timeout=DB_TIMEOUT
                )
            except Exception as e:
                LOGGER.error(f"Session delete error: {e}")
            
            _cleanup_session_file(user_id, sessions[0]["session_id"])
            await message.reply_text(
                f"**✅ Successfully logged out from '{sessions[0]['account_name']}'!**",
                parse_mode=ParseMode.MARKDOWN,
            )
            LOGGER.info(f"User {user_id} logged out of {sessions[0]['account_name']}")
        else:
            buttons = _build_account_buttons(sessions, "logout_select")
            buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="login_cancel")])
            await message.reply_text(
                "**🚪 Select the account to log out from:**",
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode=ParseMode.MARKDOWN,
            )

    # ── Callback handler ───────────────────────────────────────────────────

    @app.on_callback_query(filters.regex(r"^(login_cancel|login_restart|logout_select_.+)$"))
    async def login_callback_handler(client, callback_query):
        data    = callback_query.data
        chat_id = callback_query.message.chat.id
        user_id = callback_query.from_user.id

        if data == "login_cancel":
            _clear_state(chat_id)
            try:
                await callback_query.message.edit_text(
                    "**❌ Cancelled. Use /login to start again.**",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except MessageNotModified:
                pass
            return

        if data == "login_restart":
            await _disconnect_state_client(chat_id)
            session_data[chat_id] = {"user_id": user_id, "stage": "phone"}
            try:
                await callback_query.message.edit_text(
                    "**🔄 Restarted. Please send your phone number:**\n\n"
                    "**Example:** `+8801XXXXXXXXX`",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("❌ Cancel", callback_data="login_cancel"),
                    ]]),
                )
            except MessageNotModified:
                pass
            return

        if data.startswith("logout_select_"):
            session_id = data[len("logout_select_"):]
            try:
                user_session = await asyncio.wait_for(
                    user_sessions.find_one({"user_id": user_id}),
                    timeout=DB_TIMEOUT
                )
            except:
                user_session = None

            if not user_session:
                await callback_query.answer("Session not found.", show_alert=True)
                return

            sessions = user_session.get("sessions", [])
            target   = next((s for s in sessions if s["session_id"] == session_id), None)
            if not target:
                await callback_query.answer("Account not found.", show_alert=True)
                return

            sessions.remove(target)
            try:
                await asyncio.wait_for(
                    user_sessions.update_one(
                        {"user_id": user_id}, {"$set": {"sessions": sessions}}
                    ),
                    timeout=DB_TIMEOUT
                )
            except Exception as e:
                LOGGER.error(f"Session update error: {e}")

            _cleanup_session_file(user_id, session_id)
            try:
                await callback_query.message.edit_text(
                    f"**✅ Successfully logged out from '{target['account_name']}'!**",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except MessageNotModified:
                pass
            LOGGER.info(f"User {user_id} logged out of {target['account_name']}")
            return

    # ── Text handler: drives the login conversation ────────────────────────

    @app.on_message(
        filters.text
        & (filters.private | filters.group)
        & filters.create(lambda _, __, msg: msg.chat.id in session_data),
    )
    async def login_text_handler(client: Client, message: Message):
        chat_id = message.chat.id
        if chat_id not in session_data:
            return

        state = session_data[chat_id]
        stage = state.get("stage")
        text  = message.text.strip() if message.text else ""

        if stage == "phone":
            if not text.startswith("+") or len(text) < 8:
                await message.reply_text(
                    "**❌ Invalid phone number.**\n\n"
                    "Please include the country code.\n"
                    "**Example:** `+8801XXXXXXXXX`",
                    parse_mode=ParseMode.MARKDOWN,
                )
                return

            state["phone"] = text
            sending_msg = await message.reply_text(
                "**📨 Sending verification code...**",
                parse_mode=ParseMode.MARKDOWN,
            )
            await _send_otp(client, message, sending_msg, state)

        elif stage == "otp":
            otp = "".join(c for c in text if c.isdigit())
            state["otp"] = otp
            validating_msg = await message.reply_text(
                "**🔄 Verifying code...**",
                parse_mode=ParseMode.MARKDOWN,
            )
            await _validate_otp(client, message, validating_msg, state)

        elif stage == "2fa":
            state["password"] = text
            await _validate_2fa(client, message, state)

    # ═══════════════════════════════════════════════════════════════════════
    # INTERNAL HELPERS
    # ═══════════════════════════════════════════════════════════════════════

    async def _send_otp(client: Client, message: Message, status_msg, state: dict):
        """Connect a Pyrogram user client and request an OTP."""
        chat_id    = message.chat.id
        user_id    = state["user_id"]
        phone      = state["phone"]
        session_id = str(uuid.uuid4())
        session_name = f"temp_session_{user_id}_{session_id}"

        user_client = Client(
            session_name,
            api_id=API_ID,
            api_hash=API_HASH,
        )

        try:
            await asyncio.wait_for(user_client.connect(), timeout=10.0)
            code = await asyncio.wait_for(user_client.send_code(phone), timeout=10.0)

            state.update({
                "stage":      "otp",
                "session_id": session_id,
                "client_obj": user_client,
                "code":       code,
            })

            asyncio.create_task(_otp_timeout(client, message.chat.id, state))

            await _safe_edit(
                status_msg,
                "**✅ Verification code sent!**\n\n"
                "Please send the OTP you received on Telegram.\n\n"
                "__Tip: Enter it with spaces like `1 2 3 4 5`__",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Restart", callback_data="login_restart")],
                    [InlineKeyboardButton("❌ Cancel",  callback_data="login_cancel")],
                ]),
            )

        except asyncio.TimeoutError:
            await _safe_edit(status_msg, "**❌ Connection timeout. Please try again.**")
            _clear_state(chat_id)
            try:
                await user_client.disconnect()
            except:
                pass

        except PhoneNumberInvalid:
            await _safe_edit(status_msg, "**❌ Invalid phone number. Please try again.**")
            _clear_state(chat_id)
            try:
                await user_client.disconnect()
            except:
                pass

        except ApiIdInvalid:
            await _safe_edit(status_msg, "**❌ API configuration error. Please contact support.**")
            _clear_state(chat_id)
            try:
                await user_client.disconnect()
            except:
                pass

        except FloodWait as e:
            await _safe_edit(
                status_msg,
                f"**⏳ Too many requests. Please wait {e.value} seconds and try again.**",
            )
            _clear_state(chat_id)
            try:
                await user_client.disconnect()
            except:
                pass

        except Exception as e:
            LOGGER.error(f"OTP send error for user {user_id}: {e}")
            await _safe_edit(
                status_msg,
                f"**❌ Failed to send verification code.**\n\nError: `{str(e)[:100]}`",
            )
            _clear_state(chat_id)
            try:
                await user_client.disconnect()
            except:
                pass

    async def _validate_otp(client: Client, message: Message, status_msg, state: dict):
        """Attempt to sign in with the provided OTP."""
        chat_id     = message.chat.id
        user_client = state["client_obj"]
        phone       = state["phone"]
        otp         = state["otp"]
        code        = state["code"]

        try:
            await asyncio.wait_for(
                user_client.sign_in(phone, code.phone_code_hash, otp),
                timeout=10.0
            )
            await _generate_session(client, message, state)
            try:
                await status_msg.delete()
            except:
                pass

        except PhoneCodeInvalid:
            await _safe_edit(
                status_msg,
                "**❌ Incorrect verification code. Please try again.**",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Restart", callback_data="login_restart")],
                    [InlineKeyboardButton("❌ Cancel",  callback_data="login_cancel")],
                ]),
            )

        except PhoneCodeExpired:
            await _safe_edit(
                status_msg,
                "**❌ Verification code expired. Please restart.**",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Restart", callback_data="login_restart")],
                    [InlineKeyboardButton("❌ Cancel",  callback_data="login_cancel")],
                ]),
            )
            _clear_state(chat_id)

        except SessionPasswordNeeded:
            state["stage"] = "2fa"
            asyncio.create_task(_twofa_timeout(client, chat_id, state))
            await _safe_edit(
                status_msg,
                "**🔒 Two-Step Verification is enabled.**\n\n"
                "Please send your **2FA password**:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Restart", callback_data="login_restart")],
                    [InlineKeyboardButton("❌ Cancel",  callback_data="login_cancel")],
                ]),
            )

        except asyncio.TimeoutError:
            await _safe_edit(
                status_msg,
                "**❌ Verification timeout. Please try again.**",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Restart", callback_data="login_restart")],
                ]),
            )
            _clear_state(chat_id)

        except Exception as e:
            LOGGER.error(f"OTP validation error: {e}")
            await _safe_edit(
                status_msg,
                f"**❌ Verification failed.**\n\nError: `{str(e)[:100]}`",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Restart", callback_data="login_restart")],
                ]),
            )
            _clear_state(chat_id)

    async def _validate_2fa(client: Client, message: Message, state: dict):
        """Verify the 2FA password."""
        chat_id     = message.chat.id
        user_client = state["client_obj"]
        password    = state["password"]

        status_msg = await message.reply_text(
            "**🔄 Verifying password...**",
            parse_mode=ParseMode.MARKDOWN,
        )

        try:
            await asyncio.wait_for(
                user_client.check_password(password=password),
                timeout=10.0
            )
            await _generate_session(client, message, state)
            try:
                await status_msg.delete()
            except:
                pass

        except PasswordHashInvalid:
            await _safe_edit(
                status_msg,
                "**❌ Incorrect 2FA password. Please try again:**",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Restart", callback_data="login_restart")],
                    [InlineKeyboardButton("❌ Cancel",  callback_data="login_cancel")],
                ]),
            )

        except asyncio.TimeoutError:
            await _safe_edit(status_msg, "**❌ 2FA timeout. Please try again.**")
            _clear_state(chat_id)

        except Exception as e:
            LOGGER.error(f"2FA validation error: {e}")
            await _safe_edit(
                status_msg,
                f"**❌ 2FA verification failed.**\n\nError: `{str(e)[:100]}`",
            )
            _clear_state(chat_id)

    async def _generate_session(client: Client, message: Message, state: dict):
        """Export session string and persist it to the database."""
        chat_id     = message.chat.id
        user_id     = state["user_id"]
        session_id  = state["session_id"]
        user_client = state["client_obj"]

        try:
            me           = await user_client.get_me()
            account_name = f"{me.first_name} {me.last_name or ''}".strip()
            session_str  = await user_client.export_session_string()

            try:
                await asyncio.wait_for(
                    user_sessions.update_one(
                        {"user_id": user_id},
                        {
                            "$push": {
                                "sessions": {
                                    "session_id":     session_id,
                                    "session_string": session_str,
                                    "account_name":   account_name,
                                }
                            }
                        },
                        upsert=True,
                    ),
                    timeout=DB_TIMEOUT
                )
            except asyncio.TimeoutError:
                LOGGER.error(f"[Session] Database timeout saving session for user {user_id}")
                await client.send_message(
                    chat_id=chat_id,
                    text="**⏳ Database timeout saving session. Please try /login again.**",
                    parse_mode=ParseMode.MARKDOWN,
                )
                _clear_state(chat_id)
                return

            await asyncio.sleep(1)
            await user_client.disconnect()
            _cleanup_session_file(user_id, session_id)
            _clear_state(chat_id)

            try:
                is_premium, _ = await get_plan_limits(user_id)
            except:
                is_premium = False

            plan_note = (
                "💎 You have **premium access** — paste any private link to download instantly!"
                if is_premium
                else "🆓 **Free user:** You can now access private content (5-minute cooldown applies).\n"
                     "Upgrade to Premium for unlimited access: /plans"
            )

            await client.send_message(
                chat_id=chat_id,
                text=(
                    f"**✅ Successfully logged in as '{account_name}'!**\n\n"
                    f"{plan_note}\n\n"
                    "__Use /logout to remove your session anytime.__"
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
            LOGGER.info(f"Session saved for user {user_id} as {account_name}")

        except Exception as e:
            LOGGER.error(f"Session generation error for user {user_id}: {e}")
            await client.send_message(
                chat_id=chat_id,
                text=f"**❌ Failed to save session.**\n\nError: `{str(e)[:100]}`",
                parse_mode=ParseMode.MARKDOWN,
            )
            _clear_state(chat_id)
            try:
                await user_client.disconnect()
            except:
                pass

    async def _otp_timeout(client: Client, chat_id: int, state: dict):
        await asyncio.sleep(TIMEOUT_OTP)
        if session_data.get(chat_id, {}).get("stage") == "otp":
            await _disconnect_state_client(chat_id)
            _clear_state(chat_id)
            try:
                await client.send_message(
                    chat_id=chat_id,
                    text="**⏰ Verification code expired. Please use /login to try again.**",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except:
                pass

    async def _twofa_timeout(client: Client, chat_id: int, state: dict):
        await asyncio.sleep(TIMEOUT_2FA)
        if session_data.get(chat_id, {}).get("stage") == "2fa":
            await _disconnect_state_client(chat_id)
            _clear_state(chat_id)
            try:
                await client.send_message(
                    chat_id=chat_id,
                    text="**⏰ 2FA verification timed out. Please use /login to try again.**",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except:
                pass

    def _clear_state(chat_id: int):
        """Remove conversation state for a chat."""
        session_data.pop(chat_id, None)

    async def _disconnect_state_client(chat_id: int):
        """Disconnect any active Pyrogram client stored in state."""
        state = session_data.get(chat_id, {})
        user_client = state.get("client_obj")
        if user_client:
            try:
                await user_client.disconnect()
            except:
                pass

    def _cleanup_session_file(user_id: int, session_id: str):
        """Delete temporary .session file from disk."""
        path = f"temp_session_{user_id}_{session_id}.session"
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception as e:
                LOGGER.warning(f"Could not remove session file {path}: {e}")

    def _build_account_buttons(sessions: list, prefix: str) -> list:
        """Build a 2-column InlineKeyboard from session list."""
        buttons = []
        for i in range(0, len(sessions), 2):
            row = []
            for s in sessions[i:i + 2]:
                row.append(InlineKeyboardButton(
                    s["account_name"],
                    callback_data=f"{prefix}_{s['session_id']}",
                ))
            buttons.append(row)
        return buttons

    async def _safe_edit(message, text: str, reply_markup=None):
        """Edit a message safely, ignoring MessageNotModified errors."""
        try:
            await message.edit_text(
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=reply_markup,
            )
        except MessageNotModified:
            pass
        except Exception as e:
            LOGGER.error(f"Message edit error: {e}")
