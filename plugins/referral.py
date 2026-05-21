#
# plugins/referral.py — Advanced Referral System v3.1
#
# FIXED BUGS v3.1:
#   ✅ CRITICAL: _give_premium_reward এখন full traceback সহ error log করে
#   ✅ CRITICAL: asyncio.wait_for timeout সব DB operation-এ
#   ✅ CRITICAL: retry logic (3 attempts) for transient AutoReconnect/NetworkTimeout
#   ✅ NEW: /refmark command — admin manually milestone mark করতে পারবে
#   ✅ NEW: /refgive এখন actual exception type দেখায়
#   ✅ FIX: DuplicateKeyError handle করা হয়েছে (insert_one fallback to update)

import asyncio
import traceback
import urllib.parse
from datetime import datetime, timedelta, timezone
from pyrogram import Client, filters
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)
from pyrogram.enums import ParseMode
from pyrogram.handlers import MessageHandler

from config import COMMAND_PREFIX, DEVELOPER_USER_ID
from utils import LOGGER
from core.database import referrals, prem_plan1, premium_users, total_users

# ─────────────────────────────────────────────────────────────────────────────
# RACE CONDITION PREVENTION
# ─────────────────────────────────────────────────────────────────────────────
_reward_locks: dict[int, asyncio.Lock] = {}


def _get_user_lock(user_id: int) -> asyncio.Lock:
    if user_id not in _reward_locks:
        _reward_locks[user_id] = asyncio.Lock()
    return _reward_locks[user_id]


# ─────────────────────────────────────────────────────────────────────────────
# MILESTONE CONFIG
# ─────────────────────────────────────────────────────────────────────────────

MILESTONE_REWARDS = {
    3:   7,
    5:   15,
    10:  30,
    20:  30,
    30:  45,
    50:  60,
    75:  75,
    100: 90,
}

STREAK_WEEKLY_MIN = 3
STREAK_BONUS_DAYS = 5
MILESTONE_NEAR_THRESHOLD = 2

# DB timeout
_DB_TIMEOUT = 15.0
# Max retry attempts for transient errors
_MAX_RETRIES = 3


# ─────────────────────────────────────────────────────────────────────────────
# DB HELPERS
# ─────────────────────────────────────────────────────────────────────────────

async def _count_referrals(user_id: int) -> int:
    try:
        count = await asyncio.wait_for(
            referrals.count_documents({
                "referrer_id": user_id,
                "$or": [
                    {"_type": {"$exists": False}},
                    {"_type": {"$nin": ["milestone_log", "streak_log"]}},
                ],
            }),
            timeout=_DB_TIMEOUT,
        )
        return count
    except Exception as e:
        LOGGER.error(f"[Referral] _count_referrals error for {user_id}: {type(e).__name__}: {e}")
        return 0


async def _count_referrals_in_period(user_id: int, since: datetime) -> int:
    try:
        count = await asyncio.wait_for(
            referrals.count_documents({
                "referrer_id": user_id,
                "referred_at": {"$gte": since},
                "$or": [
                    {"_type": {"$exists": False}},
                    {"_type": {"$nin": ["milestone_log", "streak_log"]}},
                ],
            }),
            timeout=_DB_TIMEOUT,
        )
        return count
    except Exception as e:
        LOGGER.error(f"[Referral] _count_referrals_in_period error: {type(e).__name__}: {e}")
        return 0


async def _get_milestone_doc(user_id: int) -> dict:
    try:
        doc = await asyncio.wait_for(
            referrals.find_one({"_type": "milestone_log", "user_id": user_id}),
            timeout=_DB_TIMEOUT,
        )
        return doc or {}
    except Exception as e:
        LOGGER.error(f"[Referral] _get_milestone_doc error for {user_id}: {type(e).__name__}: {e}")
        return {}


async def _get_referral_stats(user_id: int) -> dict:
    count = await _count_referrals(user_id)
    milestone_doc = await _get_milestone_doc(user_id)
    rewarded_milestones = milestone_doc.get("rewarded", [])

    next_milestone = None
    for ms in sorted(MILESTONE_REWARDS.keys()):
        if ms not in rewarded_milestones:
            next_milestone = ms
            break

    now = datetime.utcnow()
    weekly  = await _count_referrals_in_period(user_id, now - timedelta(days=7))
    monthly = await _count_referrals_in_period(user_id, now - timedelta(days=30))

    streak_doc = None
    try:
        streak_doc = await asyncio.wait_for(
            referrals.find_one({"_type": "streak_log", "user_id": user_id}),
            timeout=_DB_TIMEOUT,
        )
    except Exception:
        pass
    streak_doc = streak_doc or {}

    current_streak = streak_doc.get("current_streak", 0)
    bonus_days_earned = streak_doc.get("bonus_days_earned", 0)

    return {
        "count": count,
        "weekly": weekly,
        "monthly": monthly,
        "rewarded_milestones": rewarded_milestones,
        "next_milestone": next_milestone,
        "needed_for_next": max(0, (next_milestone - count)) if next_milestone else 0,
        "current_streak": current_streak,
        "bonus_days_earned": bonus_days_earned,
    }


# ─────────────────────────────────────────────────────────────────────────────
# ✅ FIXED: _give_premium_reward
# - Full traceback logging
# - asyncio.wait_for timeouts on every DB op
# - Retry logic (3x) for transient errors (AutoReconnect, NetworkTimeout)
# - DuplicateKeyError fallback (insert → update)
# ─────────────────────────────────────────────────────────────────────────────

async def _give_premium_reward(
    client: Client,
    user_id: int,
    days: int,
    reason: str,
) -> bool:
    """
    ✅ FIXED v3.1:
    - Full exception traceback logging (exc_info equivalent)
    - asyncio.wait_for on all DB operations
    - Retry 3× for transient errors
    - DuplicateKeyError → update_one fallback
    - Returns True on success, False on failure
    """
    from pymongo.errors import AutoReconnect, NetworkTimeout, ServerSelectionTimeoutError, DuplicateKeyError

    TRANSIENT_ERRORS = (AutoReconnect, NetworkTimeout, ServerSelectionTimeoutError)

    async def _with_retry(coro_fn, op_name: str):
        """Execute a coroutine with retries for transient errors."""
        last_err = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                result = await asyncio.wait_for(coro_fn(), timeout=_DB_TIMEOUT)
                return result
            except asyncio.TimeoutError:
                last_err = f"asyncio.TimeoutError (attempt {attempt})"
                LOGGER.warning(f"[Referral] TIMEOUT on {op_name} for user={user_id}, attempt {attempt}/{_MAX_RETRIES}")
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(1.5 * attempt)
            except TRANSIENT_ERRORS as te:
                last_err = f"{type(te).__name__}: {te} (attempt {attempt})"
                LOGGER.warning(f"[Referral] Transient DB error on {op_name} for user={user_id}: {last_err}")
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(1.5 * attempt)
            except DuplicateKeyError as dke:
                # Duplicate key — raise immediately, no retry
                raise dke
            except Exception as e:
                # Non-transient — raise immediately
                raise e
        raise RuntimeError(f"[Referral] {op_name} failed after {_MAX_RETRIES} attempts: {last_err}")

    try:
        expiry_date = datetime.utcnow() + timedelta(days=days)

        LOGGER.info(f"[Referral] _give_premium_reward START: user={user_id}, days={days}, reason={reason}")

        # ── Step 1: Check existing plan ───────────────────────────────────
        try:
            existing = await _with_retry(
                lambda: prem_plan1.find_one({"user_id": user_id}),
                "prem_plan1.find_one"
            )
        except Exception as find_err:
            LOGGER.error(
                f"[Referral] FAILED prem_plan1.find_one for user={user_id}: "
                f"{type(find_err).__name__}: {find_err}\n"
                f"{traceback.format_exc()}"
            )
            return False

        # ── Step 2: Insert/Update/Extend ──────────────────────────────────
        if existing:
            ex_expiry = existing.get("expiry_date")
            if isinstance(ex_expiry, datetime) and ex_expiry > datetime.utcnow():
                # Active plan → extend
                new_expiry = ex_expiry + timedelta(days=days)
                try:
                    await _with_retry(
                        lambda: prem_plan1.update_one(
                            {"user_id": user_id},
                            {"$set": {"expiry_date": new_expiry}},
                        ),
                        "prem_plan1.update_one(extend)"
                    )
                    await _with_retry(
                        lambda: premium_users.update_one(
                            {"user_id": user_id},
                            {"$set": {"expiry_date": new_expiry}},
                            upsert=True,
                        ),
                        "premium_users.update_one(extend)"
                    )
                except Exception as upd_err:
                    LOGGER.error(
                        f"[Referral] FAILED update_one(extend) for user={user_id}: "
                        f"{type(upd_err).__name__}: {upd_err}\n"
                        f"{traceback.format_exc()}"
                    )
                    return False
                expiry_date = new_expiry
                LOGGER.info(f"[Referral] ✅ Reward EXTENDED: user={user_id} +{days}d → expiry={new_expiry.strftime('%Y-%m-%d')}")

            else:
                # Expired plan → replace
                plan_doc = _build_plan_doc(user_id, expiry_date)
                try:
                    await _with_retry(
                        lambda: prem_plan1.replace_one({"user_id": user_id}, plan_doc.copy()),
                        "prem_plan1.replace_one"
                    )
                    plan_doc.pop("_id", None)
                    await _with_retry(
                        lambda: premium_users.update_one(
                            {"user_id": user_id},
                            {"$set": plan_doc},
                            upsert=True,
                        ),
                        "premium_users.update_one(replace)"
                    )
                except Exception as rep_err:
                    LOGGER.error(
                        f"[Referral] FAILED replace_one for user={user_id}: "
                        f"{type(rep_err).__name__}: {rep_err}\n"
                        f"{traceback.format_exc()}"
                    )
                    return False
                LOGGER.info(f"[Referral] ✅ Reward REPLACED: user={user_id} {days}d expiry={expiry_date.strftime('%Y-%m-%d')}")

        else:
            # No plan → insert new
            plan_doc = _build_plan_doc(user_id, expiry_date)
            try:
                from pymongo.errors import DuplicateKeyError
                try:
                    await _with_retry(
                        lambda: prem_plan1.insert_one(plan_doc.copy()),
                        "prem_plan1.insert_one"
                    )
                except DuplicateKeyError:
                    # Race condition: another insert sneaked in → update instead
                    LOGGER.warning(f"[Referral] DuplicateKeyError on insert for user={user_id} — falling back to update_one")
                    await _with_retry(
                        lambda: prem_plan1.update_one(
                            {"user_id": user_id},
                            {"$set": plan_doc},
                            upsert=True,
                        ),
                        "prem_plan1.update_one(fallback)"
                    )

                plan_doc.pop("_id", None)
                await _with_retry(
                    lambda: premium_users.update_one(
                        {"user_id": user_id},
                        {"$set": plan_doc},
                        upsert=True,
                    ),
                    "premium_users.update_one(new)"
                )
            except Exception as ins_err:
                LOGGER.error(
                    f"[Referral] FAILED insert_one for user={user_id}: "
                    f"{type(ins_err).__name__}: {ins_err}\n"
                    f"{traceback.format_exc()}"
                )
                return False
            LOGGER.info(f"[Referral] ✅ Reward NEW: user={user_id} {days}d expiry={expiry_date.strftime('%Y-%m-%d')}")

        # ── Step 3: Notify user (non-critical) ────────────────────────────
        try:
            await client.send_message(
                chat_id=user_id,
                text=(
                    f"🎉 **推荐奖励已解锁！**\n\n"
                    f"**🏆 原因：** {reason}\n"
                    f"**🎁 奖励：** `{days}` 天 **高级套餐1**\n"
                    f"**📅 有效期至：** `{expiry_date.strftime('%Y年%m月%d日')}`\n\n"
                    "感谢你的推荐！💎\n"
                    "继续推荐以获取更多奖励！🚀\n\n"
                    "_使用 /referral 查看你的进度_"
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as notify_err:
            LOGGER.warning(f"[Referral] Could not notify user {user_id}: {type(notify_err).__name__}: {notify_err}")

        return True

    except Exception as e:
        LOGGER.error(
            f"[Referral] _give_premium_reward UNEXPECTED ERROR for user={user_id}: "
            f"{type(e).__name__}: {e}\n"
            f"{traceback.format_exc()}"
        )
        return False


def _build_plan_doc(user_id: int, expiry_date: datetime) -> dict:
    return {
        "user_id": user_id,
        "plan": "plan1",
        "plan_name": "Plan Premium 1 (Referral Reward)",
        "accounts": 1,
        "max_downloads": 1000,
        "private_support": True,
        "inbox_support": False,
        "expiry_date": expiry_date,
        "activated_at": datetime.utcnow(),
        "source": "referral",
    }


# ─────────────────────────────────────────────────────────────────────────────
# ✅ FIXED: _check_and_reward_milestones
# ─────────────────────────────────────────────────────────────────────────────

async def _check_and_reward_milestones(
    client: Client,
    referrer_id: int,
) -> list[int]:
    async with _get_user_lock(referrer_id):
        newly_rewarded = []

        try:
            count = await _count_referrals(referrer_id)
            milestone_doc = await _get_milestone_doc(referrer_id)
            rewarded = milestone_doc.get("rewarded", [])

            LOGGER.info(
                f"[Referral] Milestone check — user={referrer_id}, "
                f"count={count}, already_rewarded={rewarded}"
            )

            for milestone, reward_days in sorted(MILESTONE_REWARDS.items()):
                if count >= milestone and milestone not in rewarded:
                    LOGGER.info(
                        f"[Referral] Attempting milestone {milestone} "
                        f"for user {referrer_id} (+{reward_days}d)"
                    )

                    reason = f"🏅 {milestone} 推荐里程碑"

                    # Give premium FIRST, mark AFTER (bug fix)
                    success = await _give_premium_reward(
                        client, referrer_id, reward_days, reason
                    )

                    if success:
                        try:
                            await asyncio.wait_for(
                                referrals.update_one(
                                    {"_type": "milestone_log", "user_id": referrer_id},
                                    {"$addToSet": {"rewarded": milestone}},
                                    upsert=True,
                                ),
                                timeout=_DB_TIMEOUT,
                            )
                            newly_rewarded.append(milestone)
                            LOGGER.info(
                                f"[Referral] ✅ Milestone {milestone} rewarded & "
                                f"marked for user {referrer_id}"
                            )
                        except Exception as mark_err:
                            LOGGER.error(
                                f"[Referral] Failed to mark milestone {milestone} "
                                f"for {referrer_id}: {type(mark_err).__name__}: {mark_err} "
                                f"(premium WAS given — use /refmark {referrer_id} to fix)"
                            )
                    else:
                        LOGGER.error(
                            f"[Referral] Failed to give milestone {milestone} "
                            f"reward to user {referrer_id} (see errors above)"
                        )

        except Exception as e:
            LOGGER.error(
                f"[Referral] _check_and_reward_milestones error "
                f"for {referrer_id}: {type(e).__name__}: {e}\n"
                f"{traceback.format_exc()}"
            )

        return newly_rewarded


# ─────────────────────────────────────────────────────────────────────────────
# STREAK BONUS
# ─────────────────────────────────────────────────────────────────────────────

async def _check_streak_bonus(client: Client, referrer_id: int):
    try:
        now = datetime.utcnow()
        week_start = now - timedelta(days=7)
        weekly_count = await _count_referrals_in_period(referrer_id, week_start)

        if weekly_count < STREAK_WEEKLY_MIN:
            return

        streak_doc = None
        try:
            streak_doc = await asyncio.wait_for(
                referrals.find_one({"_type": "streak_log", "user_id": referrer_id}),
                timeout=_DB_TIMEOUT,
            )
        except Exception:
            pass
        streak_doc = streak_doc or {}

        last_bonus_week = streak_doc.get("last_bonus_week")
        current_week_num = now.isocalendar()[1]
        current_year = now.year

        if (last_bonus_week and
            last_bonus_week.get("week") == current_week_num and
            last_bonus_week.get("year") == current_year):
            return

        current_streak = streak_doc.get("current_streak", 0) + 1
        bonus_days_earned = streak_doc.get("bonus_days_earned", 0) + STREAK_BONUS_DAYS

        try:
            await asyncio.wait_for(
                referrals.update_one(
                    {"_type": "streak_log", "user_id": referrer_id},
                    {
                        "$set": {
                            "current_streak": current_streak,
                            "bonus_days_earned": bonus_days_earned,
                            "last_bonus_week": {
                                "week": current_week_num,
                                "year": current_year,
                            },
                            "last_updated": now,
                        }
                    },
                    upsert=True,
                ),
                timeout=_DB_TIMEOUT,
            )
        except Exception as e:
            LOGGER.error(f"[Referral] Streak log update error: {type(e).__name__}: {e}")
            return

        reason = (
            f"🔥 连续推荐 #{current_streak} "
            f"（本周 {weekly_count} 次推荐！）"
        )
        success = await _give_premium_reward(
            client, referrer_id, STREAK_BONUS_DAYS, reason
        )

        if success:
            LOGGER.info(
                f"[Referral] Streak bonus given to {referrer_id}: "
                f"streak={current_streak}, bonus={STREAK_BONUS_DAYS}d"
            )

    except Exception as e:
        LOGGER.error(
            f"[Referral] _check_streak_bonus error for {referrer_id}: "
            f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# NEAR MILESTONE NOTIFICATION
# ─────────────────────────────────────────────────────────────────────────────

async def _maybe_notify_near_milestone(
    client: Client,
    referrer_id: int,
    count: int,
    next_milestone: int | None,
    needed: int,
):
    if not next_milestone or needed > MILESTONE_NEAR_THRESHOLD or needed == 0:
        return
    try:
        reward_days = MILESTONE_REWARDS.get(next_milestone, 0)
        await client.send_message(
            chat_id=referrer_id,
            text=(
                f"🔔 **快了！**\n\n"
                f"距离下一次奖励仅差 **{needed}** 个推荐！\n\n"
                f"**🎯 下一里程碑：** `{next_milestone}` 次推荐\n"
                f"**🎁 奖励：** `{reward_days}` 天高级会员\n\n"
                f"继续分享你的推荐链接！🚀\n"
                f"使用 /referral 获取你的链接。"
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# PROCESS REFERRAL
# ─────────────────────────────────────────────────────────────────────────────

async def process_referral(
    client: Client,
    new_user_id: int,
    referrer_id: int,
) -> bool:
    if referrer_id == new_user_id:
        return False
    if referrer_id == DEVELOPER_USER_ID:
        return False

    try:
        existing = await asyncio.wait_for(
            referrals.find_one({
                "referred_user_id": new_user_id,
                "$or": [
                    {"_type": {"$exists": False}},
                    {"_type": {"$nin": ["milestone_log", "streak_log"]}},
                ],
            }),
            timeout=_DB_TIMEOUT,
        )
        if existing:
            LOGGER.info(f"[Referral] Duplicate blocked: {new_user_id}")
            return False
    except Exception as e:
        LOGGER.error(f"[Referral] Duplicate check error: {type(e).__name__}: {e}")
        return False

    try:
        referrer_exists = await asyncio.wait_for(
            total_users.find_one({"user_id": referrer_id}),
            timeout=_DB_TIMEOUT,
        )
        if not referrer_exists:
            LOGGER.warning(f"[Referral] Referrer {referrer_id} not in DB")
            return False
    except Exception as e:
        LOGGER.error(f"[Referral] Referrer check error: {type(e).__name__}: {e}")
        return False

    try:
        await asyncio.wait_for(
            referrals.insert_one({
                "_type": "referral",
                "referrer_id": referrer_id,
                "referred_user_id": new_user_id,
                "referred_at": datetime.utcnow(),
                "is_active": True,
            }),
            timeout=_DB_TIMEOUT,
        )
        LOGGER.info(f"[Referral] Recorded: {new_user_id} referred by {referrer_id}")
    except Exception as e:
        LOGGER.error(f"[Referral] Failed to record referral: {type(e).__name__}: {e}")
        return False

    stats = await _get_referral_stats(referrer_id)
    count = stats["count"]
    next_ms = stats["next_milestone"]
    needed = stats["needed_for_next"]

    try:
        new_user_doc = await asyncio.wait_for(
            total_users.find_one({"user_id": new_user_id}),
            timeout=_DB_TIMEOUT,
        )
        new_name = (
            new_user_doc.get("name") or
            new_user_doc.get("first_name") or
            "某人"
        ) if new_user_doc else "某人"

        if next_ms:
            progress_text = (
                f"\n📊 **进度：** `{count}/{next_ms}` "
                f"（还需 **{needed}** 次即可获得 +{MILESTONE_REWARDS[next_ms]}d 奖励）"
            )
        else:
            progress_text = "\n🏆 **所有里程碑已完成！你是传奇！**"

        await client.send_message(
            chat_id=referrer_id,
            text=(
                f"🔔 **新推荐！**\n\n"
                f"**👤 {new_name}** 通过你的链接加入！\n"
                f"**📊 总推荐数：** `{count}`"
                f"{progress_text}\n\n"
                "继续分享以获取更多奖励！🎁"
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        LOGGER.warning(f"[Referral] Could not notify referrer {referrer_id}: {type(e).__name__}: {e}")

    newly_rewarded = await _check_and_reward_milestones(client, referrer_id)
    await _check_streak_bonus(client, referrer_id)

    if not newly_rewarded:
        updated_stats = await _get_referral_stats(referrer_id)
        await _maybe_notify_near_milestone(
            client, referrer_id,
            updated_stats["count"],
            updated_stats["next_milestone"],
            updated_stats["needed_for_next"],
        )

    return True


# ─────────────────────────────────────────────────────────────────────────────
# UI BUILDERS
# ─────────────────────────────────────────────────────────────────────────────

async def get_referral_text(client: Client, user_id: int) -> str:
    bot_username = await _get_bot_username(client)
    referral_link = f"https://t.me/{bot_username}?start={user_id}"
    stats = await _get_referral_stats(user_id)

    count = stats["count"]
    next_ms = stats["next_milestone"]
    needed = stats["needed_for_next"]
    rewarded = stats["rewarded_milestones"]
    weekly = stats["weekly"]
    monthly = stats["monthly"]
    streak = stats["current_streak"]

    if next_ms and next_ms > 0:
        progress_pct = min((count / next_ms) * 100, 100)
        filled = int(progress_pct / 10)
        bar = "▓" * filled + "░" * (10 - filled)
        reward_days = MILESTONE_REWARDS.get(next_ms, 0)
        progress_line = (
            f"\n`[{bar}]` {progress_pct:.0f}% → "
            f"**{next_ms}** 次 (+{reward_days}d)"
        )
    else:
        bar = "▓" * 10
        progress_line = f"\n`[{bar}]` 100% 🏆 所有里程碑已完成！"

    milestone_lines = []
    for ms, days in sorted(MILESTONE_REWARDS.items()):
        if ms in rewarded:
            icon = "✅"
            status = "已完成"
        elif count >= ms:
            icon = "🔓"
            status = "解锁中..."
        else:
            icon = "🔒"
            status = f"还需 {ms - count} 次"
        milestone_lines.append(
            f"  {icon} **{ms} 次** → +{days}d | _{status}_"
        )

    milestones_text = "\n".join(milestone_lines)

    streak_line = ""
    if streak > 0:
        streak_line = f"\n🔥 **连续推荐：** `{streak}` 周不间断！"
    if weekly >= STREAK_WEEKLY_MIN:
        streak_line += f"\n⚡ **本周：** {weekly} 次（连续激活中！）"

    return (
        f"🔗 **你的推荐链接**\n"
        f"`{referral_link}`\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"**📊 你的数据**\n"
        f"👥 **总计：** `{count}` | 📅 **本月：** `{monthly}` | "
        f"📆 **本周：** `{weekly}`"
        f"{streak_line}"
        f"{progress_line}\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"**🏆 里程碑奖励**\n"
        f"{milestones_text}\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"**🔥 连续推荐奖励**\n"
        f"每周推荐 `{STREAK_WEEKLY_MIN}+` 位朋友 = "
        f"额外 `{STREAK_BONUS_DAYS}` 天奖励！\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"**💡 运作方式**\n"
        f"1️⃣ 与朋友分享你的链接\n"
        f"2️⃣ 他们通过你的链接启动机器人\n"
        f"3️⃣ 你将自动获得奖励！🎁\n\n"
        f"_点击下方按钮分享你的链接！_"
    )


async def _get_bot_username(client: Client) -> str:
    try:
        me = await client.get_me()
        return me.username or "bot"
    except Exception:
        return "bot"


def _referral_keyboard(user_id: int, bot_username: str) -> InlineKeyboardMarkup:
    referral_link = f"https://t.me/{bot_username}?start={user_id}"
    share_text = (
        f"🚀 轻松下载 Telegram 受限内容！\n"
        f"通过我的链接加入：{referral_link}"
    )
    encoded_text = urllib.parse.quote(share_text)
    encoded_link = urllib.parse.quote(referral_link)
    share_url = (
        f"https://t.me/share/url?url={encoded_link}&text={encoded_text}"
    )
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 分享我的链接", url=share_url)],
        [
            InlineKeyboardButton("🏆 排行榜", callback_data="ref_leaderboard"),
            InlineKeyboardButton("🔄 刷新数据", callback_data="ref_refresh"),
        ],
        [InlineKeyboardButton("📋 我的推荐列表", callback_data="ref_mylist")],
    ])


async def get_leaderboard_text() -> str:
    pipeline = [
        {"$match": {"$or": [
            {"_type": {"$exists": False}},
            {"_type": {"$nin": ["milestone_log", "streak_log"]}},
        ]}},
        {"$group": {"_id": "$referrer_id", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 10},
    ]
    try:
        top_users = await referrals.aggregate(pipeline).to_list(length=10)
    except Exception as e:
        LOGGER.error(f"[Referral] Leaderboard error: {type(e).__name__}: {e}")
        return "**🏆 推荐排行榜**\n\n_加载数据时出错。请重试。_"

    if not top_users:
        return (
            "**🏆 推荐排行榜**\n\n"
            "_还没有推荐！争当第一！_"
        )

    medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
    lines = ["**🏆 推荐排行榜 — 前十名**\n━━━━━━━━━━━━━━━━━━\n"]

    for i, entry in enumerate(top_users):
        uid = entry["_id"]
        count = entry["count"]
        if uid is None:
            continue
        try:
            user_doc = await asyncio.wait_for(
                total_users.find_one({"user_id": uid}),
                timeout=_DB_TIMEOUT,
            )
            name = "未知用户"
            if user_doc:
                name = (
                    user_doc.get("name") or
                    user_doc.get("first_name") or
                    f"用户 {uid}"
                )
                if len(name) > 20:
                    name = name[:17] + "..."
        except Exception:
            name = f"用户 {uid}"

        lines.append(f"{medals[i]} **{name}** — `{count}` 次推荐")

    lines.append("\n━━━━━━━━━━━━━━━━━━")
    lines.append("_分享你的链接以登上排行榜！_")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# SETUP
# ─────────────────────────────────────────────────────────────────────────────

def setup_referral_handler(app: Client):

    async def referral_command(client: Client, message: Message):
        user_id = message.from_user.id

        if len(message.command) >= 2 and message.command[1].lower() in (
            "top", "leaderboard"
        ):
            text = await get_leaderboard_text()
            await message.reply_text(
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "🔗 我的推荐链接",
                        callback_data="ref_refresh",
                    )],
                ]),
            )
            return

        bot_username = await _get_bot_username(client)
        text = await get_referral_text(client, user_id)
        keyboard = _referral_keyboard(user_id, bot_username)

        await message.reply_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
        LOGGER.info(f"[/referral] from user {user_id}")

    # ── Callback handler ──────────────────────────────────────────────────

    @app.on_callback_query(
        filters.regex(r"^ref_(refresh|leaderboard|mylist)$")
    )
    async def ref_callback(client: Client, cq: CallbackQuery):
        user_id = cq.from_user.id
        data = cq.data

        if data == "ref_leaderboard":
            text = await get_leaderboard_text()
            try:
                await cq.message.edit_text(
                    text,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton(
                            "🔗 My Referral Link",
                            callback_data="ref_refresh",
                        )],
                    ]),
                )
            except Exception:
                pass
            await cq.answer("🏆 排行榜已更新！")
            return

        if data == "ref_mylist":
            try:
                cursor = referrals.find(
                    {
                        "referrer_id": user_id,
                        "$or": [
                            {"_type": {"$exists": False}},
                            {"_type": {"$nin": ["milestone_log", "streak_log"]}},
                        ],
                    },
                    {"referred_user_id": 1, "referred_at": 1},
                ).sort("referred_at", -1).limit(15)
                recent = await cursor.to_list(length=15)
            except Exception:
                recent = []

            if not recent:
                text = "**📋 你的推荐列表**\n\n_还没有人通过你的链接加入！分享你的链接吧！_"
            else:
                lines = [
                    f"**📋 最近推荐的用户**（最近 {len(recent)} 人）\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                ]
                for r in recent:
                    uid = r.get("referred_user_id", "?")
                    at = r.get("referred_at")
                    at_str = at.strftime("%Y年%m月%d日") if isinstance(at, datetime) else "未知"
                    lines.append(f"👤 `{uid}` — {at_str}")
                text = "\n".join(lines)

            try:
                await cq.message.edit_text(
                    text,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton(
                            "🔗 返回推荐",
                            callback_data="ref_refresh",
                        )],
                    ]),
                )
            except Exception:
                pass
            await cq.answer()
            return

        if data == "ref_refresh":
            bot_username = await _get_bot_username(client)
            text = await get_referral_text(client, user_id)
            keyboard = _referral_keyboard(user_id, bot_username)
            try:
                await cq.message.edit_text(
                    text,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=keyboard,
                    disable_web_page_preview=True,
                )
            except Exception:
                pass
            await cq.answer("✅ Stats refreshed!")

    # ── Admin: /refcheck ──────────────────────────────────────────────────

    @app.on_message(
        filters.command("refcheck", prefixes=COMMAND_PREFIX)
        & filters.private
        & filters.user(DEVELOPER_USER_ID)
    )
    async def refcheck_command(client: Client, message: Message):
        if len(message.command) < 2:
            await message.reply_text(
                "**用法：** `/refcheck <用户ID>`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        try:
            target_id = int(message.command[1])
        except ValueError:
            await message.reply_text("❌ **无效的用户ID！**", parse_mode=ParseMode.MARKDOWN)
            return

        count = await _count_referrals(target_id)
        stats = await _get_referral_stats(target_id)
        milestone_doc = await _get_milestone_doc(target_id)
        rewarded = milestone_doc.get("rewarded", [])

        try:
            cursor = referrals.find(
                {
                    "referrer_id": target_id,
                    "$or": [
                        {"_type": {"$exists": False}},
                        {"_type": {"$nin": ["milestone_log", "streak_log"]}},
                    ],
                },
                {"referred_user_id": 1, "referred_at": 1},
            ).sort("referred_at", -1).limit(10)
            recent = await cursor.to_list(length=10)
        except Exception:
            recent = []

        recent_lines = []
        for r in recent:
            uid = r.get("referred_user_id", "?")
            at = r.get("referred_at")
            at_str = at.strftime("%Y年%m月%d日") if isinstance(at, datetime) else "未知"
            recent_lines.append(f"  • `{uid}` — {at_str}")

        recent_text = "\n".join(recent_lines) if recent_lines else "  _暂无_"

        pending_milestones = [
            ms for ms in sorted(MILESTONE_REWARDS.keys())
            if ms not in rewarded and count >= ms
        ]

        await message.reply_text(
            f"**📊 推荐报告 — `{target_id}`**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"**👥 总推荐数：** `{count}`\n"
            f"**📅 本月：** `{stats['monthly']}` | "
            f"**📆 本周：** `{stats['weekly']}`\n"
            f"**🔥 连续：** `{stats['current_streak']}` 周\n"
            f"**🏆 已领取里程碑：** `{rewarded}`\n"
            f"**⚠️ 待领取（尚未发放）：** `{pending_milestones}`\n\n"
            f"**📋 最近推荐（最近10人）：**\n{recent_text}\n\n"
            f"_快捷修复命令：_\n"
            f"`/add {target_id} 1 22` — 手动授予22天会员\n"
            f"`/refmark {target_id}` — 标记所有符合条件的里程碑为已完成",
            parse_mode=ParseMode.MARKDOWN,
        )

    # ── Admin: /refgive (IMPROVED) ────────────────────────────────────────

    @app.on_message(
        filters.command("refgive", prefixes=COMMAND_PREFIX)
        & filters.private
        & filters.user(DEVELOPER_USER_ID)
    )
    async def refgive_command(client: Client, message: Message):
        if len(message.command) < 2:
            await message.reply_text(
                "**用法：** `/refgive <用户ID> [force]`\n\n"
                "`force` — 重置里程碑日志并发放所有符合条件的奖励。",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        try:
            target_id = int(message.command[1])
        except ValueError:
            await message.reply_text("❌ **Invalid user ID!**", parse_mode=ParseMode.MARKDOWN)
            return

        force_mode = (
            len(message.command) >= 3 and
            message.command[2].lower() == "force"
        )

        count = await _count_referrals(target_id)

        status_msg = await message.reply_text(
            f"**⏳ Processing...**\n"
            f"**Referral count:** `{count}`\n"
            f"**Force mode:** `{'ON' if force_mode else 'OFF'}`\n"
            f"**DB timeout per op:** `{_DB_TIMEOUT}s`\n"
            f"**Retries per op:** `{_MAX_RETRIES}x`",
            parse_mode=ParseMode.MARKDOWN,
        )

        try:
            if force_mode:
                await asyncio.wait_for(
                    referrals.delete_one({"_type": "milestone_log", "user_id": target_id}),
                    timeout=_DB_TIMEOUT,
                )
                LOGGER.info(f"[refgive] Force mode: milestone_log cleared for {target_id}")

            milestone_doc_before = await _get_milestone_doc(target_id)
            rewarded_before = milestone_doc_before.get("rewarded", [])

            newly_rewarded = await _check_and_reward_milestones(client, target_id)

            milestone_doc_after = await _get_milestone_doc(target_id)
            rewarded_after = milestone_doc_after.get("rewarded", [])

            if newly_rewarded:
                reward_summary = []
                for ms in newly_rewarded:
                    days = MILESTONE_REWARDS.get(ms, 0)
                    reward_summary.append(f"  ✅ Milestone **{ms}** → +{days}d premium given")

                await status_msg.edit_text(
                    f"✅ **Rewards given for user `{target_id}`!**\n\n"
                    f"**👥 Referral count:** `{count}`\n"
                    f"**🎁 Rewards given:**\n" + "\n".join(reward_summary) + "\n\n"
                    f"**📋 All rewarded milestones:** `{rewarded_after}`",
                    parse_mode=ParseMode.MARKDOWN,
                )
            else:
                pending = [
                    ms for ms in sorted(MILESTONE_REWARDS.keys())
                    if ms not in rewarded_after and count >= ms
                ]

                if pending:
                    await status_msg.edit_text(
                        f"⚠️ **Milestone eligible but reward STILL failed!**\n\n"
                        f"**👥 Count:** `{count}`\n"
                        f"**Already rewarded:** `{rewarded_after}`\n"
                        f"**Eligible but failed:** `{pending}`\n\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"**Check `/logs` for the actual error.**\n\n"
                        f"**Manual fix (use these commands):**\n"
                        f"`/add {target_id} 1 22` — grant premium directly\n"
                        f"`/refmark {target_id}` — mark milestones as done\n\n"
                        f"_The logs will show the exact exception type/traceback now._",
                        parse_mode=ParseMode.MARKDOWN,
                    )
                else:
                    next_ms_list = [
                        ms for ms in sorted(MILESTONE_REWARDS.keys())
                        if ms not in rewarded_after
                    ]
                    next_needed = (
                        next_ms_list[0] - count
                        if next_ms_list else 0
                    )
                    await status_msg.edit_text(
                        f"ℹ️ **No new rewards for `{target_id}`.**\n\n"
                        f"**👥 Count:** `{count}`\n"
                        f"**Already rewarded:** `{rewarded_after}`\n\n"
                        + (
                            f"_Next milestone: {next_ms_list[0]} refs "
                            f"(need {next_needed} more)_"
                            if next_ms_list else
                            "_All milestones completed!_"
                        ),
                        parse_mode=ParseMode.MARKDOWN,
                    )

        except Exception as e:
            LOGGER.error(f"[refgive] Error: {type(e).__name__}: {e}\n{traceback.format_exc()}")
            try:
                await status_msg.edit_text(
                    f"❌ **Error during refgive!**\n\n`{type(e).__name__}: {str(e)[:200]}`",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass

    # ── NEW: Admin: /refmark <user_id> ───────────────────────────────────
    # Use this after manually granting premium via /add to mark milestones

    @app.on_message(
        filters.command("refmark", prefixes=COMMAND_PREFIX)
        & filters.private
        & filters.user(DEVELOPER_USER_ID)
    )
    async def refmark_command(client: Client, message: Message):
        """
        /refmark <user_id>
        Manually mark all eligible milestones as rewarded.
        Use this after granting premium via /add to sync the milestone log.
        """
        if len(message.command) < 2:
            await message.reply_text(
                "**用法：** `/refmark <用户ID>`\n\n"
                "在数据库中标记所有符合条件的里程碑为已奖励。\n"
                "通过 `/add` 手动授予会员后使用。",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        try:
            target_id = int(message.command[1])
        except ValueError:
            await message.reply_text("❌ **无效的用户ID！**", parse_mode=ParseMode.MARKDOWN)
            return

        count = await _count_referrals(target_id)
        milestone_doc = await _get_milestone_doc(target_id)
        already_rewarded = milestone_doc.get("rewarded", [])

        eligible = [ms for ms in sorted(MILESTONE_REWARDS.keys()) if count >= ms]
        newly_marked = []
        failed_marks = []

        for ms in eligible:
            if ms not in already_rewarded:
                try:
                    await asyncio.wait_for(
                        referrals.update_one(
                            {"_type": "milestone_log", "user_id": target_id},
                            {"$addToSet": {"rewarded": ms}},
                            upsert=True,
                        ),
                        timeout=_DB_TIMEOUT,
                    )
                    newly_marked.append(ms)
                    LOGGER.info(f"[refmark] Marked milestone {ms} for user {target_id}")
                except Exception as e:
                    failed_marks.append(ms)
                    LOGGER.error(
                        f"[refmark] Failed to mark milestone {ms} for {target_id}: "
                        f"{type(e).__name__}: {e}"
                    )

        lines = [
            f"**📋 里程碑标记结果 — `{target_id}`**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"**👥 推荐数：** `{count}`\n"
            f"**符合条件的里程碑：** `{eligible}`\n"
            f"**已奖励（之前）：** `{already_rewarded}`\n"
        ]

        if newly_marked:
            lines.append(f"**✅ 新标记：** `{newly_marked}`")
        if failed_marks:
            lines.append(f"**❌ 标记失败：** `{failed_marks}`")
        if not newly_marked and not failed_marks:
            lines.append("_没有新的里程碑需要标记 — 均已标记完成。_")

        lines.append("\n✅ **完成！运行 `/refcheck` 验证。**")

        await message.reply_text(
            "\n".join(lines),
            parse_mode=ParseMode.MARKDOWN,
        )

    # ── Admin: /refstats ──────────────────────────────────────────────────

    @app.on_message(
        filters.command("refstats", prefixes=COMMAND_PREFIX)
        & filters.private
        & filters.user(DEVELOPER_USER_ID)
    )
    async def refstats_command(client: Client, message: Message):
        try:
            total_ref = await asyncio.wait_for(
                referrals.count_documents({
                    "$or": [
                        {"_type": {"$exists": False}},
                        {"_type": {"$nin": ["milestone_log", "streak_log"]}},
                    ]
                }),
                timeout=_DB_TIMEOUT,
            )

            ur_result = await referrals.aggregate([
                {"$match": {"$or": [
                    {"_type": {"$exists": False}},
                    {"_type": {"$nin": ["milestone_log", "streak_log"]}},
                ]}},
                {"$group": {"_id": "$referrer_id"}},
                {"$count": "count"},
            ]).to_list(length=1)
            unique_referrers = ur_result[0]["count"] if ur_result else 0

            top_result = await referrals.aggregate([
                {"$match": {"$or": [
                    {"_type": {"$exists": False}},
                    {"_type": {"$nin": ["milestone_log", "streak_log"]}},
                ]}},
                {"$group": {"_id": "$referrer_id", "count": {"$sum": 1}}},
                {"$sort": {"count": -1}},
                {"$limit": 1},
            ]).to_list(length=1)
            top_info = "N/A"
            if top_result:
                top_uid = top_result[0]["_id"]
                top_cnt = top_result[0]["count"]
                top_info = f"`{top_uid}` ({top_cnt} referrals)"

            week_ago = datetime.utcnow() - timedelta(days=7)
            weekly_ref = await asyncio.wait_for(
                referrals.count_documents({
                    "referred_at": {"$gte": week_ago},
                    "$or": [
                        {"_type": {"$exists": False}},
                        {"_type": {"$nin": ["milestone_log", "streak_log"]}},
                    ],
                }),
                timeout=_DB_TIMEOUT,
            )

            month_ago = datetime.utcnow() - timedelta(days=30)
            monthly_ref = await asyncio.wait_for(
                referrals.count_documents({
                    "referred_at": {"$gte": month_ago},
                    "$or": [
                        {"_type": {"$exists": False}},
                        {"_type": {"$nin": ["milestone_log", "streak_log"]}},
                    ],
                }),
                timeout=_DB_TIMEOUT,
            )

            milestone_count = await asyncio.wait_for(
                referrals.count_documents({"_type": "milestone_log"}),
                timeout=_DB_TIMEOUT,
            )

            await message.reply_text(
                f"**📊 全局推荐统计**\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"**🔗 总推荐数：** `{total_ref}`\n"
                f"**👤 推荐人数量：** `{unique_referrers}`\n"
                f"**📅 本月：** `{monthly_ref}`\n"
                f"**📆 本周：** `{weekly_ref}`\n"
                f"**🏆 有里程碑的用户：** `{milestone_count}`\n"
                f"**🥇 推荐最多：** {top_info}\n"
                f"━━━━━━━━━━━━━━━━━━",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as e:
            await message.reply_text(
                f"❌ **获取统计信息出错：** `{type(e).__name__}: {e}`",
                parse_mode=ParseMode.MARKDOWN,
            )

    # ── Admin: /reflist ───────────────────────────────────────────────────

    @app.on_message(
        filters.command("reflist", prefixes=COMMAND_PREFIX)
        & filters.private
        & filters.user(DEVELOPER_USER_ID)
    )
    async def reflist_command(client: Client, message: Message):
        if len(message.command) < 2:
            await message.reply_text(
                "**用法：** `/reflist <用户ID>`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        try:
            target_id = int(message.command[1])
        except ValueError:
            await message.reply_text("❌ **无效的用户ID！**", parse_mode=ParseMode.MARKDOWN)
            return

        try:
            cursor = referrals.find(
                {
                    "referrer_id": target_id,
                    "$or": [
                        {"_type": {"$exists": False}},
                        {"_type": {"$nin": ["milestone_log", "streak_log"]}},
                    ],
                },
                {"referred_user_id": 1, "referred_at": 1},
            ).sort("referred_at", -1)
            all_refs = await cursor.to_list(length=None)
        except Exception as e:
            await message.reply_text(
                f"❌ **数据库错误：** `{type(e).__name__}: {e}`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        if not all_refs:
            await message.reply_text(
                f"**未找到 `{target_id}` 的推荐记录。**",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        lines = [
            f"**📋 `{target_id}` 的全部推荐**\n"
            f"总计：**{len(all_refs)}**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
        ]
        for r in all_refs:
            uid = r.get("referred_user_id", "?")
            at = r.get("referred_at")
            at_str = at.strftime("%Y年%m月%d日 %H:%M") if isinstance(at, datetime) else "未知"
            lines.append(f"• `{uid}` — {at_str}")

        text = "\n".join(lines)
        if len(text) > 4000:
            text = text[:3997] + "..."

        await message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    # ── Register /referral command ────────────────────────────────────────

    app.add_handler(
        MessageHandler(
            referral_command,
            filters=filters.command("referral", prefixes=COMMAND_PREFIX)
            & (filters.private | filters.group),
        ),
        group=1,
    )
