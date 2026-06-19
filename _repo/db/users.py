# Copyright @juktijol
# Channel t.me/juktijol
#
# db/users.py — MongoDB upsert helper for user profile data.
#
# MongoDB Schema (total_users collection):
# {
#   "user_id":       int,         # PRIMARY KEY (immutable)
#   "username":      str | None,  # nullable — user may not have one
#   "first_name":    str,         # always present in Telegram API
#   "last_name":     str | None,  # nullable
#   "full_name":     str,         # computed: first_name + last_name
#   "is_premium":    bool,        # Telegram Premium subscriber flag
#   "is_verified":   bool,        # Verified account (blue tick)
#   "is_scam":       bool,        # Telegram-flagged scam account
#   "is_fake":       bool,        # Telegram-flagged fake account
#   "language_code": str | None,  # IETF language tag, nullable
#   "dc_id":         int | None,  # Telegram DC — available via get_users()
#   "last_active":   datetime,    # last /start or any interaction
#   "refreshed_at":  datetime,    # UTC timestamp of last /refresh
# }

from datetime import datetime, timezone
from utils import LOGGER
from core.database import total_users


async def upsert_user(user) -> dict:
    """
    Upsert a Pyrogram User object into the total_users collection.

    Fields guaranteed by Telegram API:
        user_id, first_name, is_bot, is_premium, is_verified, is_scam, is_fake

    Optional / nullable fields:
        username, last_name, language_code, dc_id

    Returns a dict of the fields that were written to DB.
    """
    now = datetime.now(timezone.utc)

    full_name = " ".join(
        part for part in (user.first_name or "", user.last_name or "") if part
    ).strip() or "Unknown"

    doc = {
        "user_id":       user.id,
        "username":      user.username or None,
        "first_name":    user.first_name or "",
        "last_name":     user.last_name or None,
        "full_name":     full_name,
        "is_premium":    bool(getattr(user, "is_premium", False)),
        "is_verified":   bool(getattr(user, "is_verified", False)),
        "is_scam":       bool(getattr(user, "is_scam", False)),
        "is_fake":       bool(getattr(user, "is_fake", False)),
        "language_code": getattr(user, "language_code", None),
        "dc_id":         getattr(user, "dc_id", None),
        "refreshed_at":  now,
        "last_active":   now,
    }

    try:
        await total_users.update_one(
            {"user_id": user.id},
            {"$set": doc},
            upsert=True,
        )
        LOGGER.info(f"[upsert_user] user_id={user.id} updated in DB.")
    except Exception as exc:
        LOGGER.error(f"[upsert_user] DB error for user_id={user.id}: {exc}")
        raise

    return doc
