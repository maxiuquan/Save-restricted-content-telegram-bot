# Unified async Motor-based database module.
# FIX: Bot startup-এ automatic duplicate cleanup + premium_users rebuild
# ✅ FIXED: Connection timeout + retry logic

from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ASCENDING
from utils import LOGGER
from config import MONGO_URL

LOGGER.info("Initialising async Motor database clients...")

# ── Connection timeout configuration ─────────────────────────────────────
# Timeout constants (in milliseconds)
CONNECT_TIMEOUT_MS = 10000      # 10 seconds
SOCKET_TIMEOUT_MS = 10000       # 10 seconds
HEARTBEAT_FREQUENCY_MS = 10000  # 10 seconds

try:
    _main_client = AsyncIOMotorClient(
        MONGO_URL,
        connectTimeoutMS=CONNECT_TIMEOUT_MS,
        socketTimeoutMS=SOCKET_TIMEOUT_MS,
        heartbeatFrequencyMS=HEARTBEAT_FREQUENCY_MS,
        serverSelectionTimeoutMS=5000,  # 5 second server selection timeout
        retryWrites=True,
        maxPoolSize=50,
        minPoolSize=10,
    )
    LOGGER.info("Motor client created successfully with timeout configuration.")
except Exception as exc:
    LOGGER.error(f"Failed to create Motor client: {exc}")
    raise

# বাকি সব কিছু একই থাকবে...
_main_db = _main_client["ItsSmartTool"]

# ── COLLECTION EXPORTS ────────────────────────────────────────────────────
prem_plan1             = _main_db["prem_plan1"]
prem_plan2             = _main_db["prem_plan2"]
prem_plan3             = _main_db["prem_plan3"]
user_sessions          = _main_db["user_sessions"]
premium_users          = _main_db["premium_users"]
downloads_collection   = _main_db["downloads"]
batches_collection     = _main_db["batches"]
daily_limit            = _main_db["daily_limit"]
total_users            = _main_db["total_users"]
user_activity_collection = _main_db["user_activity"]
referrals              = _main_db["referrals"]


# ── INDEX HELPERS ─────────────────────────────────────────────────────────

async def _create_ttl_index(collection, field: str = "expiry_date"):
    try:
        index_name = f"{field}_ttl"
        existing = await collection.index_information()
        if index_name not in existing:
            await collection.create_index(
                [(field, ASCENDING)],
                expireAfterSeconds=0,
                name=index_name,
                sparse=True,
            )
            LOGGER.info(f"TTL index '{index_name}' created on {collection.name}")
    except Exception as e:
        LOGGER.warning(f"[Index] Failed to create TTL index on {collection.name}: {e}")


async def _create_supporting_indexes():
    try:
        for col in (prem_plan1, prem_plan2, prem_plan3, premium_users,
                    user_sessions, downloads_collection, batches_collection,
                    daily_limit, total_users, user_activity_collection,
                    referrals):
            info = await col.index_information()
            if "user_id_1" not in info:
                await col.create_index([("user_id", ASCENDING)], name="user_id_1")

        info = await total_users.index_information()
        if "last_active_1" not in info:
            await total_users.create_index(
                [("last_active", ASCENDING)], name="last_active_1"
            )

        # Referral indexes for efficient lookups
        ref_info = await referrals.index_information()
        if "referrer_id_1" not in ref_info:
            await referrals.create_index(
                [("referrer_id", ASCENDING)], name="referrer_id_1"
            )
        if "referred_user_id_1" not in ref_info:
            await referrals.create_index(
                [("referred_user_id", ASCENDING)], name="referred_user_id_1",
                unique=True,
            )

        LOGGER.info("All supporting indexes created.")
    except Exception as e:
        LOGGER.warning(f"[Index] Failed to create supporting indexes: {e}")


# ── CLEANUP ───────────────────────────────────────────────────────────────

async def _cleanup_premium_duplicates():
    """
    Bot startup-এ একবার চলে।
    1. Expired entries মুছে দেয়।
    2. Duplicate user_id entries ঠিক করে।
    3. premium_users collection fresh rebuild করে।
    ✅ FIXED: Better error handling
    """
    now = datetime.utcnow()
    plan_map = [("prem_plan1", prem_plan1), ("prem_plan2", prem_plan2), ("prem_plan3", prem_plan3)]

    # Step 1: Expired entries মুছে দাও
    for name, col in plan_map:
        try:
            result = await col.delete_many({"expiry_date": {"$lte": now}})
            if result.deleted_count > 0:
                LOGGER.info(f"[Cleanup] {name}: {result.deleted_count} expired removed")
        except Exception as e:
            LOGGER.warning(f"[Cleanup] {name} expired cleanup failed: {e}")

    # Step 2: Duplicate user_id entries ঠিক করো
    for name, col in plan_map:
        try:
            all_docs = await col.find(
                {"expiry_date": {"$gt": now}},
                {"_id": 1, "user_id": 1, "activated_at": 1}
            ).to_list(length=None)

            seen = {}
            to_delete = []

            for doc in all_docs:
                uid = doc.get("user_id")
                if uid is None:
                    continue
                if uid not in seen:
                    seen[uid] = doc
                else:
                    existing_time = seen[uid].get("activated_at") or datetime.min
                    current_time  = doc.get("activated_at") or datetime.min
                    if current_time > existing_time:
                        to_delete.append(seen[uid]["_id"])
                        seen[uid] = doc
                    else:
                        to_delete.append(doc["_id"])

            if to_delete:
                result = await col.delete_many({"_id": {"$in": to_delete}})
                LOGGER.info(f"[Cleanup] {name}: {result.deleted_count} duplicates removed")

        except Exception as e:
            LOGGER.warning(f"[Cleanup] {name} duplicate cleanup failed: {e}")

    # Step 3: premium_users fresh rebuild
    try:
        await premium_users.delete_many({})
        rebuilt = 0
        for plan_key, col in plan_map:
            active_docs = await col.find({"expiry_date": {"$gt": now}}).to_list(length=None)
            for doc in active_docs:
                doc.pop("_id", None)
                await premium_users.update_one(
                    {"user_id": doc["user_id"]},
                    {"$set": doc},
                    upsert=True
                )
                rebuilt += 1
        LOGGER.info(f"[Cleanup] premium_users rebuilt: {rebuilt} entries")
    except Exception as e:
        LOGGER.warning(f"[Cleanup] premium_users rebuild failed: {e}")

    # Step 4: Final count log
    try:
        p1_ids = set()
        p2_ids = set()
        p3_ids = set()
        async for doc in prem_plan1.find({"expiry_date": {"$gt": now}}, {"user_id": 1}):
            p1_ids.add(doc["user_id"])
        async for doc in prem_plan2.find({"expiry_date": {"$gt": now}}, {"user_id": 1}):
            p2_ids.add(doc["user_id"])
        async for doc in prem_plan3.find({"expiry_date": {"$gt": now}}, {"user_id": 1}):
            p3_ids.add(doc["user_id"])

        unique_premium = len(p1_ids | p2_ids | p3_ids)
        total_reg = await total_users.count_documents({})
        LOGGER.info(
            f"[Cleanup] Unique Premium: {unique_premium} | "
            f"Total Users: {total_reg} | "
            f"Free: {max(0, total_reg - unique_premium)}"
        )
    except Exception as e:
        LOGGER.warning(f"[Cleanup] final count failed: {e}")


# ── MAIN INIT ─────────────────────────────────────────────────────────────

async def init_db():
    """Initialize database with indexes and cleanup."""
    try:
        LOGGER.info("Setting up database indexes...")
        for col in (prem_plan1, prem_plan2, prem_plan3, premium_users):
            await _create_ttl_index(col, "expiry_date")

        await _create_supporting_indexes()

        LOGGER.info("Running startup premium cleanup...")
        await _cleanup_premium_duplicates()

        LOGGER.info("✅ Database ready!")
        return True
    except Exception as e:
        LOGGER.error(f"[Init] Database initialization failed: {e}")
        return False

