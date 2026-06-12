# utils/force_sub.py — 超快速强制订阅系统
# ─────────────────────────────────────────────────────────
# ✅ 已修复：stop_propagation() 放在正确位置
# ✅ 已修复：缓存刷新正常工作
# ✅ 已优化：内存 TTL 缓存（缓存命中亚毫秒级）
# ✅ 已优化：使用 asyncio.wait_for() 设置 API 超时
# ✅ 用户体验：简洁中文消息 + 内联键盘

import time
import asyncio
from pyrogram import Client, filters
from pyrogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from pyrogram.enums import ChatMemberStatus, ParseMode
from pyrogram.errors import (
    UserNotParticipant,
    ChatAdminRequired,
    ChannelPrivate,
    PeerIdInvalid,
    FloodWait,
)

from utils.logging_setup import LOGGER
from config import FORCE_SUB_CHANNEL, DEVELOPER_USER_ID

# ══════════════════════════════════════════════════════════════════
# 配置
# ══════════════════════════════════════════════════════════════════

# 缓存有效期（秒）。300 秒 = 5 分钟。
CACHE_TTL = 300

# API 调用的最大超时时间（秒）
API_TIMEOUT = 5.0

# 回调数据常量
CHECK_SUB_CALLBACK_DATA = "check_sub"

# ══════════════════════════════════════════════════════════════════
# 频道设置
# ══════════════════════════════════════════════════════════════════

# 强制订阅频道 — 设为 None 时功能禁用
_RAW_CHANNEL = FORCE_SUB_CHANNEL

if _RAW_CHANNEL:
    # 确保 @ 前缀用于 API 调用
    if isinstance(_RAW_CHANNEL, str) and not _RAW_CHANNEL.startswith(("@", "-100")):
        API_CHANNEL: str = f"@{_RAW_CHANNEL}"
    else:
        API_CHANNEL = _RAW_CHANNEL

    CHANNEL_LINK = f"https://t.me/{str(_RAW_CHANNEL).lstrip('@')}"
else:
    API_CHANNEL = None
    CHANNEL_LINK = ""

# ══════════════════════════════════════════════════════════════════
# 内存 TTL 缓存
# ══════════════════════════════════════════════════════════════════
# 结构：{ user_id: (is_subscribed: bool, timestamp: float) }
# - True  → 已加入，缓存 CACHE_TTL 秒
# - False → 未加入，缓存 15 秒（快速重新检查）
_sub_cache: dict[int, tuple[bool, float]] = {}

NOT_JOINED_CACHE_TTL = 15  # "未加入"结果仅缓存 15 秒


def _cache_get(user_id: int) -> bool | None:
    """从缓存读取结果。缓存未命中返回 None。"""
    entry = _sub_cache.get(user_id)
    if entry is None:
        return None
    is_sub, ts = entry
    ttl = CACHE_TTL if is_sub else NOT_JOINED_CACHE_TTL
    if time.monotonic() - ts < ttl:
        return is_sub
    # 已过期 — 移除
    _sub_cache.pop(user_id, None)
    return None


def _cache_set(user_id: int, is_sub: bool) -> None:
    _sub_cache[user_id] = (is_sub, time.monotonic())


def _cache_invalidate(user_id: int) -> None:
    """当用户加入或需要强制刷新时清除缓存。"""
    _sub_cache.pop(user_id, None)


# ══════════════════════════════════════════════════════════════════
# 核心检查函数
# ══════════════════════════════════════════════════════════════════

async def check_force_sub(client: Client, user_id: int, refresh: bool = False) -> bool:
    """
    快速检查用户是否在所需频道中。

    参数：
        client：Pyrogram/Pyrofork 客户端
        user_id：Telegram 用户 ID
        refresh：如为 True，绕过缓存执行新的 API 调用

    返回：
        True  → 成员（或强制订阅已禁用）
        False → 非成员
    """
    # 强制订阅禁用时允许所有用户
    if not API_CHANNEL:
        return True

    # 开发者始终允许
    if user_id == DEVELOPER_USER_ID:
        return True

    # 缓存命中（refresh=False 时）
    if not refresh:
        cached = _cache_get(user_id)
        if cached is not None:
            return cached

    # ── Telegram API 调用 ─────────────────────────────────────────
    try:
        member = await asyncio.wait_for(
            client.get_chat_member(API_CHANNEL, user_id),
            timeout=API_TIMEOUT,
        )
        # 用户被踢出或离开时为 False
        is_sub = member.status not in (
            ChatMemberStatus.BANNED,
            ChatMemberStatus.LEFT,
        )
        _cache_set(user_id, is_sub)
        return is_sub

    except UserNotParticipant:
        _cache_set(user_id, False)
        return False

    except (ChatAdminRequired, ChannelPrivate, PeerIdInvalid) as e:
        # Bot 不是频道管理员，或频道无效 — 失败开放（允许用户）
        LOGGER.error(f"[ForceSub] {API_CHANNEL} 频道错误：{e}")
        return True

    except FloodWait as e:
        LOGGER.warning(f"[ForceSub] FloodWait {e.value}秒 — 允许用户 {user_id}")
        await asyncio.sleep(min(e.value, 5))  # 最多等待 5 秒
        return True  # 洪水等待期间允许用户，而不是阻止

    except asyncio.TimeoutError:
        LOGGER.warning(f"[ForceSub] 用户 {user_id} API 超时 — 允许")
        return True  # 超时时允许（更好的用户体验）

    except Exception as e:
        LOGGER.error(f"[ForceSub] 用户 {user_id} 意外错误：{e}")
        return True  # 未知错误 — 失败开放


# ══════════════════════════════════════════════════════════════════
# 界面辅助函数
# ══════════════════════════════════════════════════════════════════

def _not_sub_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ 加入频道", url=CHANNEL_LINK)],
        [InlineKeyboardButton("⚡ 已加入 - 继续", callback_data=CHECK_SUB_CALLBACK_DATA)],
    ])


NOT_SUBSCRIBED_TEXT = (
    "⚡ **访问受限**\n\n"
    "要使用本机器人，请先加入我们的官方频道。\n\n"
    "点击下方按钮加入，然后点击\n"
    "**⚡ 已加入 - 继续**。"
)


# ══════════════════════════════════════════════════════════════════
# 处理器设置
# ══════════════════════════════════════════════════════════════════

def setup_force_sub_handler(app: Client):
    """
    在 Bot 中注册强制订阅处理器。

    - 消息拦截器：group=-1（在所有处理器之前运行）
    - 回调拦截器：group=-1
    - "我已加入"回调：group=0（普通优先级）
    """
    if not API_CHANNEL:
        LOGGER.info("⚠️ FORCE_SUB_CHANNEL 未设置 - 强制订阅已禁用。")
        return

    # ── 消息拦截器 ───────────────────────────────────────

    @app.on_message(
        filters.private & ~filters.service,
        group=-1,
    )
    async def _msg_interceptor(client: Client, message: Message):
        """
        在每条私聊消息之前运行。
        缓存命中 <1ms；缓存未命中约 200-500ms（网络）。
        """
        if not message.from_user:
            return

        user_id = message.from_user.id

        # 快速路径：开发者或强制订阅已禁用
        if user_id == DEVELOPER_USER_ID or not API_CHANNEL:
            return

        is_sub = await check_force_sub(client, user_id)

        if not is_sub:
            await message.reply_text(
                NOT_SUBSCRIBED_TEXT,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=_not_sub_keyboard(),
                disable_web_page_preview=True,
            )
            message.stop_propagation()  # ✅ 阻止其他处理器

    # ── 回调拦截器 ──────────────────────────────────────

    @app.on_callback_query(
        ~filters.regex(f"^{CHECK_SUB_CALLBACK_DATA}$"),  # 排除"已加入"按钮
        group=-1,
    )
    async def _cb_interceptor(client: Client, callback_query: CallbackQuery):
        """
        在所有内联按钮点击之前检查成员资格。
        """
        if not callback_query.from_user:
            return

        user_id = callback_query.from_user.id

        if user_id == DEVELOPER_USER_ID or not API_CHANNEL:
            return

        is_sub = await check_force_sub(client, user_id)

        if not is_sub:
            await callback_query.answer(
                "⚡ 请先加入频道。",
                show_alert=True,
            )
            # 发送加入频道的提示消息
            try:
                await callback_query.message.reply_text(
                    NOT_SUBSCRIBED_TEXT,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=_not_sub_keyboard(),
                    disable_web_page_preview=True,
                )
            except Exception:
                pass
            callback_query.stop_propagation()  # ✅

    # ── "我已加入"按钮处理器 ────────────────────────────────

    @app.on_callback_query(
        filters.regex(f"^{CHECK_SUB_CALLBACK_DATA}$"),
        group=0,
    )
    async def _check_sub_callback(client: Client, callback_query: CallbackQuery):
        """
        当用户点击"我已加入"时，通过新的 API 调用验证。
        使缓存失效以确保全新检查。
        """
        user_id = callback_query.from_user.id

        # 强制全新检查 — 绕过缓存
        _cache_invalidate(user_id)
        is_sub = await check_force_sub(client, user_id, refresh=True)

        if is_sub:
            # ✅ 成功
            try:
                await callback_query.message.delete()
            except Exception:
                pass
            await callback_query.answer(
                "⚡ 验证通过！你现在可以使用机器人了。",
                show_alert=True,
            )
            LOGGER.info(f"[ForceSub] 用户 {user_id} 验证为成员 ✅")
        else:
            # ❌ 用户尚未加入
            await callback_query.answer(
                "⚡ 你尚未加入频道。\n请加入后再试。",
                show_alert=True,
            )

    LOGGER.info(f"✅ 强制订阅已启用 - 频道：{API_CHANNEL}")