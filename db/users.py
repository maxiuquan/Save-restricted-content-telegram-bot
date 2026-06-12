#
# db/users.py — MongoDB 用户个人资料数据更新辅助函数。
#
# MongoDB 模式（total_users 集合）：
# {
#   "user_id":       int,         # 主键（不可变）
#   "username":      str | None,  # 可为空 — 用户可能没有
#   "first_name":    str,         # Telegram API 中始终存在
#   "last_name":     str | None,  # 可为空
#   "full_name":     str,         # 计算字段：first_name + last_name
#   "is_premium":    bool,        # Telegram Premium 订阅者标识
#   "is_verified":   bool,        # 已验证账户（蓝勾）
#   "is_scam":       bool,        # Telegram 标记的诈骗账户
#   "is_fake":       bool,        # Telegram 标记的伪造账户
#   "language_code": str | None,  # IETF 语言标签，可为空
#   "dc_id":         int | None,  # Telegram 数据中心 — 通过 get_users() 获取
#   "last_active":   datetime,    # 最后一次 /start 或任何交互
#   "refreshed_at":  datetime,    # 最后一次 /refresh 的 UTC 时间戳
# }

from datetime import datetime, timezone
from utils import LOGGER
from core.database import total_users


async def upsert_user(user) -> dict:
    """
    将 Pyrogram User 对象更新或插入到 total_users 集合中。

    Telegram API 保证的字段：
        user_id, first_name, is_bot, is_premium, is_verified, is_scam, is_fake

    可选 / 可为空字段：
        username, last_name, language_code, dc_id

    返回写入数据库的字段字典。
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
        LOGGER.info(f"[upsert_user] user_id={user.id} 已更新到数据库。")
    except Exception as exc:
        LOGGER.error(f"[upsert_user] user_id={user.id} 数据库错误：{exc}")
        raise

    return doc