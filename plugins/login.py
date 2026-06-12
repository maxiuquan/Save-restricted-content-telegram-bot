#
# 改进的登录系统 — 仅需手机号（无需用户提供 API_ID/API_HASH）
# 使用配置中机器人自己的 API_ID 和 API_HASH 生成会话。
# 所有用户（免费+高级）均可使用 /login。
# 免费用户：最多1个账户。高级用户：根据套餐限制。
# ✅ 已修复：超时处理 + 更完善的错误提示

import os
import uuid
import asyncio
import time
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

# 超时常量
TIMEOUT_OTP = 600   # 10 分钟
TIMEOUT_2FA = 300   # 5 分钟
DB_TIMEOUT = 5.0    # 数据库超时

# 内存会话状态: { chat_id: {...} }
session_data = {}

# 会话清理：删除超过超时的过期会话
_CLEANUP_INTERVAL = 300  # 5分钟检查一次
_LOGIN_TIMEOUT = 600  # 10分钟会话超时

async def _cleanup_expired_sessions():
    """清理过期的登录会话"""
    while True:
        await asyncio.sleep(_CLEANUP_INTERVAL)
        now = time.time()
        expired = []
        for chat_id, data in session_data.items():
            # 检查会话是否超过超时时间
            created_at = data.get("_created_at", 0)
            if now - created_at > _LOGIN_TIMEOUT:
                expired.append(chat_id)
        for chat_id in expired:
            try:
                # 取消定时任务（如果存在）
                cancel_task = session_data[chat_id].get("_cancel_task")
                if cancel_task and not cancel_task.done():
                    cancel_task.cancel()
            except Exception:
                pass
            del session_data[chat_id]


def setup_login_handler(app: Client):

    # ── 套餐限制 ────────────────────────────────────────────────────────

    async def get_plan_limits(user_id: int) -> tuple[bool, int]:
        """
        返回 (is_premium, max_accounts)。
        免费用户：(False, 1) — 可使用 1 个账户登录。
        高级用户：根据套餐设置账户上限。
        ✅ 已修复：超时 + 错误处理
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
            LOGGER.warning(f"[登录] 用户 {user_id} 套餐检查数据库超时")
            return False, 1  # 超时时默认视为免费用户
        except Exception as e:
            LOGGER.error(f"[登录] 用户 {user_id} 套餐检查错误: {e}")
            return False, 1

        return False, 1

    # ── /login 命令 ─────────────────────────────────────────────────────

    @app.on_message(filters.command("login", prefixes=COMMAND_PREFIX) & (filters.private | filters.group))
    async def login_command(client: Client, message: Message):
        user_id = message.from_user.id
        LOGGER.info(f"/login command received from user {user_id}")

        try:
            is_premium, max_accounts = await get_plan_limits(user_id)
        except Exception as e:
            LOGGER.error(f"Plan check error for user {user_id}: {e}")
            is_premium, max_accounts = False, 1

        # 检查现有会话数 (Motor async) ✅ 已修复: 超时
        try:
            user_session = await asyncio.wait_for(
                user_sessions.find_one({"user_id": user_id}),
                timeout=DB_TIMEOUT
            ) or {"sessions": []}
        except asyncio.TimeoutError:
            LOGGER.warning(f"[登录] 获取用户 {user_id} 会话时数据库超时")
            await message.reply_text(
                "**⏳ 数据库超时。请稍后重试。**",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        except Exception as e:
            LOGGER.error(f"获取用户 {user_id} 会话时出错: {e}")
            user_session = {"sessions": []}

        current_sessions = user_session.get("sessions", [])
        if len(current_sessions) >= max_accounts:
            plan_note = (
                "升级套餐以添加更多账户：/plans"
                if not is_premium
                else "请先使用 /logout 移除现有账户。"
            )
            await message.reply_text(
                f"**❌ 已达到 {max_accounts} 个账户"
                f"的上限！**\n\n"
                f"{plan_note}",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        # 初始化状态
        session_data[message.chat.id] = {"user_id": user_id, "stage": "phone"}

        plan_label = "✨ 高级" if is_premium else "🆓 免费"
        await message.reply_text(
            f"**🔐 登录设置** ({plan_label})\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "**⚠️ 重要 — 登录前请阅读：**\n\n"
            "✅ 请使用**已是私密频道或群组成**的\n"
            "    Telegram 账户登录。\n\n"
            "❌ 如果你的账户**不是**该频道/群组的\n"
            "    成员，下载将**失败**。\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "📱 请发送你的**手机号**（含国家代码）：\n\n"
            "**示例：** `+8801XXXXXXXXX`\n\n"
            "__会话将安全存储。随时使用 /logout 移除。__",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ 取消", callback_data="login_cancel"),
            ]]),
        )

    # ── /logout 命令 ────────────────────────────────────────────────────

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
            LOGGER.warning(f"[登出] 用户 {user_id} 数据库超时")
            await message.reply_text(
                "**⏳ 数据库超时。请重试。**",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        except Exception as e:
            LOGGER.error(f"Session fetch error: {e}")
            user_session = None

        if not user_session or not user_session.get("sessions"):
            await message.reply_text(
                "**❌ 你尚未登录任何账户。**",
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
                f"**✅ 已成功退出 '{sessions[0]['account_name']}'！**",
                parse_mode=ParseMode.MARKDOWN,
            )
            LOGGER.info(f"用户 {user_id} 已退出账户 '{sessions[0]['account_name']}'")
        else:
            buttons = _build_account_buttons(sessions, "logout_select")
            buttons.append([InlineKeyboardButton("❌ 取消", callback_data="login_cancel")])
            await message.reply_text(
                "**🚪 选择要退出的账户：**",
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode=ParseMode.MARKDOWN,
            )

    # ── 回调处理器 ───────────────────────────────────────────────────

    @app.on_callback_query(filters.regex(r"^(login_cancel|login_restart|logout_select_.+)$"))
    async def login_callback_handler(client, callback_query):
        data    = callback_query.data
        chat_id = callback_query.message.chat.id
        user_id = callback_query.from_user.id

        if data == "login_cancel":
            _clear_state(chat_id)
            try:
                await callback_query.message.edit_text(
                    "**❌ 已取消。使用 /login 重新开始。**",
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
                    "**🔄 已重新开始。请发送你的手机号：**\n\n"
                    "**示例：** `+8801XXXXXXXXX`",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("❌ 取消", callback_data="login_cancel"),
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
            except Exception:
                user_session = None

            if not user_session:
                await callback_query.answer("未找到会话。", show_alert=True)
                return

            sessions = user_session.get("sessions", [])
            target   = next((s for s in sessions if s["session_id"] == session_id), None)
            if not target:
                await callback_query.answer("未找到账户。", show_alert=True)
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
                LOGGER.error(f"会话更新错误: {e}")

            _cleanup_session_file(user_id, session_id)
            try:
                await callback_query.message.edit_text(
                    f"**✅ 已成功退出 '{target['account_name']}'！**",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except MessageNotModified:
                pass
            LOGGER.info(f"User {user_id} logged out of {target['account_name']}")
            return

    # ── 文本处理器：驱动登录对话 ────────────────────────

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
                    "**❌ 无效的手机号。**\n\n"
                    "请包含国家代码。\n"
                    "**示例：** `+8801XXXXXXXXX`",
                    parse_mode=ParseMode.MARKDOWN,
                )
                return

            state["phone"] = text
            sending_msg = await message.reply_text(
                "**📨 正在发送验证码...**",
                parse_mode=ParseMode.MARKDOWN,
            )
            await _send_otp(client, message, sending_msg, state)

        elif stage == "otp":
            otp = "".join(c for c in text if c.isdigit())
            state["otp"] = otp
            validating_msg = await message.reply_text(
                "**🔄 正在验证验证码...**",
                parse_mode=ParseMode.MARKDOWN,
            )
            await _validate_otp(client, message, validating_msg, state)

        elif stage == "2fa":
            state["password"] = text
            await _validate_2fa(client, message, state)

    # ═══════════════════════════════════════════════════════════════════════
    # 内部辅助函数
    # ═══════════════════════════════════════════════════════════════════════

    async def _send_otp(client: Client, message: Message, status_msg, state: dict):
        """连接 Pyrogram 用户客户端并请求发送验证码。"""
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
                "**✅ 验证码已发送！**\n\n"
                "请在 Telegram 中收到的验证码。\n\n"
                "__提示：可以带空格输入，如 `1 2 3 4 5`__",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 重新开始", callback_data="login_restart")],
                    [InlineKeyboardButton("❌ 取消",  callback_data="login_cancel")],
                ]),
            )

        except asyncio.TimeoutError:
            await _safe_edit(status_msg, "**❌ 连接超时。请重试。**")
            _clear_state(chat_id)
            try:
                await user_client.disconnect()
            except Exception:
                pass

        except PhoneNumberInvalid:
            await _safe_edit(status_msg, "**❌ 无效的手机号。请重试。**")
            _clear_state(chat_id)
            try:
                await user_client.disconnect()
            except Exception:
                pass

        except ApiIdInvalid:
            await _safe_edit(status_msg, "**❌ API 配置错误。请联系支持。**")
            _clear_state(chat_id)
            try:
                await user_client.disconnect()
            except Exception:
                pass

        except FloodWait as e:
            await _safe_edit(
                status_msg,
                f"**⏳ 请求过于频繁。请等待 {e.value} 秒后重试。**",
            )
            _clear_state(chat_id)
            try:
                await user_client.disconnect()
            except Exception:
                pass

        except Exception as e:
            LOGGER.error(f"OTP send error for user {user_id}: {e}")
            await _safe_edit(
                status_msg,
                f"**❌ 发送验证码失败。**\n\n错误：`{str(e)[:100]}`",
            )
            _clear_state(chat_id)
            try:
                await user_client.disconnect()
            except Exception:
                pass

    async def _validate_otp(client: Client, message: Message, status_msg, state: dict):
        """尝试使用提供的验证码登录。"""
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
            except Exception:
                pass

        except PhoneCodeInvalid:
            await _safe_edit(
                status_msg,
                "**❌ 验证码错误。请重试。**",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 重新开始", callback_data="login_restart")],
                    [InlineKeyboardButton("❌ 取消",  callback_data="login_cancel")],
                ]),
            )

        except PhoneCodeExpired:
            await _safe_edit(
                status_msg,
                "**❌ 验证码已过期。请重新开始。**",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 重新开始", callback_data="login_restart")],
                    [InlineKeyboardButton("❌ 取消",  callback_data="login_cancel")],
                ]),
            )
            _clear_state(chat_id)

        except SessionPasswordNeeded:
            state["stage"] = "2fa"
            asyncio.create_task(_twofa_timeout(client, chat_id, state))
            await _safe_edit(
                status_msg,
                "**🔒 已启用两步验证。**\n\n"
                "请发送你的 **两步验证密码**：",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 重新开始", callback_data="login_restart")],
                    [InlineKeyboardButton("❌ 取消",  callback_data="login_cancel")],
                ]),
            )

        except asyncio.TimeoutError:
            await _safe_edit(
                status_msg,
                "**❌ 验证超时。请重试。**",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 重新开始", callback_data="login_restart")],
                ]),
            )
            _clear_state(chat_id)

        except Exception as e:
            LOGGER.error(f"OTP validation error: {e}")
            await _safe_edit(
                status_msg,
                f"**❌ 验证失败。**\n\n错误：`{str(e)[:100]}`",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 重新开始", callback_data="login_restart")],
                ]),
            )
            _clear_state(chat_id)

    async def _validate_2fa(client: Client, message: Message, state: dict):
        """验证两步验证密码。"""
        chat_id     = message.chat.id
        user_client = state["client_obj"]
        password    = state["password"]

        status_msg = await message.reply_text(
            "**🔄 正在验证密码...**",
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
            except Exception:
                pass

        except PasswordHashInvalid:
            await _safe_edit(
                status_msg,
                "**❌ 两步验证密码错误。请重试：**",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 重新开始", callback_data="login_restart")],
                    [InlineKeyboardButton("❌ 取消",  callback_data="login_cancel")],
                ]),
            )

        except asyncio.TimeoutError:
            await _safe_edit(status_msg, "**❌ 两步验证超时。请重试。**")
            _clear_state(chat_id)

        except Exception as e:
            LOGGER.error(f"2FA validation error: {e}")
            await _safe_edit(
                status_msg,
                f"**❌ 两步验证失败。**\n\n错误：`{str(e)[:100]}`",
            )
            _clear_state(chat_id)

    async def _generate_session(client: Client, message: Message, state: dict):
        """导出会话字符串并持久化存储到数据库。"""
        chat_id     = message.chat.id
        user_id     = state["user_id"]
        session_id  = state["session_id"]
        user_client = state["client_obj"]

        try:
            me = await asyncio.wait_for(user_client.get_me(), timeout=15.0)
            account_name = f"{me.first_name} {me.last_name or ''}".strip()
            session_str = await asyncio.wait_for(user_client.export_session_string(), timeout=15.0)

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
                try:
                    await client.send_message(
                        chat_id=chat_id,
                        text="**⏳ 保存会话时数据库超时。请重新使用 /login。**",
                        parse_mode=ParseMode.MARKDOWN,
                    )
                except Exception:
                    pass
                _clear_state(chat_id)
                return

            await asyncio.sleep(1)
            await user_client.disconnect()
            _cleanup_session_file(user_id, session_id)
            _clear_state(chat_id)

            try:
                is_premium, _ = await get_plan_limits(user_id)
            except Exception:
                is_premium = False

            plan_note = (
                "💎 你拥有**高级权限** — 粘贴任意私密链接即可立即下载！"
                if is_premium
                else "🆓 **免费用户：** 你现在可以访问私密内容（有 5 分钟冷却时间）。\n"
                     "升级到高级版可享受无限制访问：/plans"
            )

            try:
                await client.send_message(
                    chat_id=chat_id,
                    text=(
                        f"**✅ 已成功以 '{account_name}' 身份登录！**\n\n"
                        f"{plan_note}\n\n"
                        "__随时使用 /logout 移除你的会话。__"
                    ),
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception as e:
                LOGGER.warning(f"[Session] Success notification failed (FloodWait?): {e}")
            LOGGER.info(f"Session saved for user {user_id} as {account_name}")

        except Exception as e:
            LOGGER.error(f"Session generation error for user {user_id}: {e}")
            try:
                await client.send_message(
                    chat_id=chat_id,
                    text=f"**❌ 保存会话失败。**\n\n错误：`{str(e)[:100]}`",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass
            _clear_state(chat_id)
            try:
                await user_client.disconnect()
            except Exception:
                pass

    async def _otp_timeout(client: Client, chat_id: int, state: dict):
        await asyncio.sleep(TIMEOUT_OTP)
        if session_data.get(chat_id, {}).get("stage") == "otp":
            await _disconnect_state_client(chat_id)
            _clear_state(chat_id)
            try:
                await client.send_message(
                    chat_id=chat_id,
                    text="**⏰ 验证码已过期。请使用 /login 重新尝试。**",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass

    async def _twofa_timeout(client: Client, chat_id: int, state: dict):
        await asyncio.sleep(TIMEOUT_2FA)
        if session_data.get(chat_id, {}).get("stage") == "2fa":
            await _disconnect_state_client(chat_id)
            _clear_state(chat_id)
            try:
                await client.send_message(
                    chat_id=chat_id,
                    text="**⏰ 两步验证超时。请使用 /login 重新尝试。**",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass

    def _clear_state(chat_id: int):
        """移除指定聊天的对话状态。"""
        session_data.pop(chat_id, None)

    async def _disconnect_state_client(chat_id: int):
        """断开存储在会话状态中的活动 Pyrogram 客户端连接。"""
        state = session_data.get(chat_id, {})
        user_client = state.get("client_obj")
        if user_client:
            try:
                await user_client.disconnect()
            except Exception:
                pass

    def _cleanup_session_file(user_id: int, session_id: str):
        """从磁盘删除临时 .session 文件。"""
        path = f"temp_session_{user_id}_{session_id}.session"
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception as e:
                LOGGER.warning(f"Could not remove session file {path}: {e}")

    def _build_account_buttons(sessions: list, prefix: str) -> list:
        """根据会话列表构建双列内联键盘。"""
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
        """安全地编辑消息。遇到 FloodWait 时回退为发送新消息。"""
        try:
            await message.edit_text(
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=reply_markup,
            )
        except MessageNotModified:
            pass
        except FloodWait as e:
            LOGGER.warning(
                f"[SafeEdit] FloodWait {e.value}s on edit, "
                "sending new message instead"
            )
            try:
                await message.reply_text(
                    text,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=reply_markup,
                )
            except Exception:
                pass
        except Exception as e:
            LOGGER.error(f"Message edit error: {e}")


def cleanup_session_data():
    """清理所有登录会话数据"""
    global session_data
    session_data.clear()
    LOGGER.info("[Login] 会话数据已清理")