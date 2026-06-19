# Copyright @juktijol
# Channel t.me/juktijol
#
# auth/migrate/migrate.py
# Developer Telegram থেকে /migrate command দিয়ে
# সব database ItsSmartTool-এ merge করতে পারবে।

import asyncio
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from motor.motor_asyncio import AsyncIOMotorClient
from config import DEVELOPER_USER_ID, MONGO_URL
from utils import LOGGER


def setup_migrate_handler(app: Client):

    async def do_migration(client: Client, status_msg, mongo_uri: str):
        """সব database থেকে ItsSmartTool-এ data merge করো।"""

        mc = AsyncIOMotorClient(mongo_uri)
        target = mc["ItsSmartTool"]

        target_total_users   = target["total_users"]
        target_premium_users = target["premium_users"]
        target_prem_plan1    = target["prem_plan1"]
        target_prem_plan2    = target["prem_plan2"]
        target_prem_plan3    = target["prem_plan3"]
        target_sessions      = target["user_sessions"]
        target_activity      = target["user_activity"]

        # ── সব database scan করো ──────────────────────────────────────────
        db_names = await mc.list_database_names()
        skip_dbs = {"admin", "local", "config", "ItsSmartTool"}

        total_migrated = 0
        total_skipped  = 0
        log_lines      = []

        def add_log(text: str):
            log_lines.append(text)
            LOGGER.info(f"[Migrate] {text}")

        for db_name in db_names:
            if db_name in skip_dbs:
                continue

            src_db = mc[db_name]
            collections = await src_db.list_collection_names()
            add_log(f"📂 {db_name}: {collections}")

            for col_name in collections:
                src_col   = src_db[col_name]
                doc_count = await src_col.count_documents({})
                if doc_count == 0:
                    continue

                col_lower = col_name.lower()
                m = s = 0

                # ── Session collection ────────────────────────────────────
                if "session" in col_lower:
                    async for doc in src_col.find({}):
                        uid = _find_uid(doc)
                        if not uid:
                            s += 1; continue
                        existing = await target_sessions.find_one({"user_id": uid})
                        if existing:
                            old_ids = {x.get("session_id") for x in existing.get("sessions", [])}
                            new_sess = [x for x in doc.get("sessions", []) if x.get("session_id") not in old_ids]
                            if new_sess:
                                await target_sessions.update_one(
                                    {"user_id": uid},
                                    {"$push": {"sessions": {"$each": new_sess}}}
                                )
                                m += len(new_sess)
                            else:
                                s += 1
                        else:
                            await target_sessions.insert_one({**doc, "user_id": uid})
                            m += 1

                # ── Premium / Plan collection ─────────────────────────────
                elif "premium" in col_lower or "plan" in col_lower:
                    if "plan1" in col_lower:
                        tgt = target_prem_plan1
                    elif "plan2" in col_lower:
                        tgt = target_prem_plan2
                    elif "plan3" in col_lower:
                        tgt = target_prem_plan3
                    else:
                        tgt = target_premium_users

                    async for doc in src_col.find({}):
                        uid = _find_uid(doc)
                        if not uid:
                            s += 1; continue
                        existing = await tgt.find_one({"user_id": uid})
                        if existing:
                            new_exp = doc.get("expiry_date")
                            old_exp = existing.get("expiry_date")
                            if new_exp and old_exp and new_exp > old_exp:
                                await tgt.update_one({"user_id": uid}, {"$set": {**doc, "user_id": uid}})
                                m += 1
                            else:
                                s += 1
                        else:
                            await tgt.insert_one({**doc, "user_id": uid})
                            m += 1

                # ── Activity collection ───────────────────────────────────
                elif "activity" in col_lower:
                    async for doc in src_col.find({}):
                        uid = _find_uid(doc)
                        if not uid:
                            s += 1; continue
                        existing = await target_activity.find_one({"user_id": uid})
                        if not existing:
                            await target_activity.insert_one({**doc, "user_id": uid})
                            m += 1
                        else:
                            s += 1

                # ── Users / Total users collection ────────────────────────
                elif any(x in col_lower for x in ["user", "member"]):
                    async for doc in src_col.find({}):
                        uid = _find_uid(doc)
                        if not uid:
                            s += 1; continue
                        existing = await target_total_users.find_one({"user_id": uid})
                        if not existing:
                            new_doc = {
                                "user_id":    uid,
                                "first_name": doc.get("first_name", ""),
                                "last_name":  doc.get("last_name", ""),
                                "name":       doc.get("name") or doc.get("first_name", ""),
                                "username":   doc.get("username", ""),
                                "last_active": doc.get("last_active") or datetime.utcnow(),
                                "_from": f"{db_name}.{col_name}",
                            }
                            await target_total_users.insert_one(new_doc)
                            m += 1
                        else:
                            s += 1
                else:
                    continue

                add_log(f"  ✅ {col_name}: +{m} migrated, {s} skipped")
                total_migrated += m
                total_skipped  += s

        # ── Final counts ──────────────────────────────────────────────────
        counts = {}
        for col_name in ["total_users", "premium_users", "prem_plan1", "prem_plan2", "prem_plan3", "user_sessions", "user_activity"]:
            counts[col_name] = await target[col_name].count_documents({})

        mc.close()
        return total_migrated, total_skipped, counts, log_lines


    def _find_uid(doc: dict):
        """Document থেকে valid user_id বের করো।"""
        for field in ["user_id", "user", "id"]:
            val = doc.get(field)
            if isinstance(val, int) and val > 100000:
                return val
        return None


    @app.on_message(
        filters.command("migrate") &
        filters.private &
        filters.user(DEVELOPER_USER_ID)
    )
    async def migrate_command(client: Client, message):
        status = await message.reply_text(
            "**⏳ Migration শুরু হচ্ছে...**\n"
            "সব database scan করা হচ্ছে। একটু অপেক্ষা করো।",
            parse_mode=ParseMode.MARKDOWN
        )

        try:
            migrated, skipped, counts, logs = await do_migration(
                client, status, MONGO_URL
            )

            # ── Result message ────────────────────────────────────────────
            count_text = "\n".join(
                f"  `{k}`: **{v}**" for k, v in counts.items()
            )

            result_text = (
                "**✅ Migration সম্পন্ন!**\n\n"
                f"📊 **Migrated:** `{migrated}`\n"
                f"⏭️ **Skipped:** `{skipped}`\n\n"
                "**📋 ItsSmartTool এর এখনকার অবস্থা:**\n"
                f"{count_text}\n\n"
                "✅ বট restart করার দরকার নেই, সাথে সাথে কাজ করবে!"
            )

            await status.edit_text(result_text, parse_mode=ParseMode.MARKDOWN)

            # ── Log file পাঠাও ───────────────────────────────────────────
            log_text = "\n".join(logs)
            if len(log_text) > 3000:
                log_text = log_text[-3000:]

            await client.send_message(
                chat_id=message.chat.id,
                text=f"**📋 Migration Log:**\n```\n{log_text}\n```",
                parse_mode=ParseMode.MARKDOWN
            )

        except Exception as e:
            LOGGER.error(f"[Migrate] Error: {e}")
            await status.edit_text(
                f"**❌ Migration failed!**\n\nError: `{str(e)}`",
                parse_mode=ParseMode.MARKDOWN
            )
