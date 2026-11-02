# devggn — premium plans (cached premium set, no repeated scans)

import asyncio
import datetime

from motor.motor_asyncio import AsyncIOMotorClient as MongoCli
from config import MONGO_DB
from devgagan.core.cache import premium_cache

mongo = MongoCli(MONGO_DB)
db = mongo.premium
db = db.premium_db
_transfer_lock = asyncio.Lock()


async def add_premium(user_id, expire_date):
    data = await check_premium(user_id)
    if data and data.get("_id"):
        await db.update_one({"_id": user_id}, {"$set": {"expire_date": expire_date}})
    else:
        await db.insert_one({"_id": user_id, "expire_date": expire_date})
    await premium_cache.invalidate("_all_")


async def remove_premium(user_id):
    await db.delete_one({"_id": user_id})
    await premium_cache.invalidate("_all_")


async def check_premium(user_id):
    return await db.find_one({"_id": user_id})


async def transfer_premium(sender_id, recipient_id):
    """Move the sender's active expiry to the recipient without extending it."""
    if sender_id == recipient_id:
        return False, "same_user", None

    async with _transfer_lock:
        now = datetime.datetime.now(datetime.timezone.utc)
        sender = await db.find_one({"_id": sender_id})
        if not sender or not sender.get("expire_date"):
            return False, "sender_missing", None

        expiry = sender["expire_date"]
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=datetime.timezone.utc)
        if expiry <= now:
            await db.delete_one({"_id": sender_id})
            await premium_cache.invalidate("_all_")
            return False, "sender_expired", None

        recipient = await db.find_one({"_id": recipient_id})
        if recipient and recipient.get("expire_date"):
            recipient_expiry = recipient["expire_date"]
            if recipient_expiry.tzinfo is None:
                recipient_expiry = recipient_expiry.replace(tzinfo=datetime.timezone.utc)
            if recipient_expiry > now:
                return False, "recipient_active", None

        # Re-check immediately before moving, preventing a stale confirmation
        # from transferring a subscription that changed meanwhile.
        current = await db.find_one({"_id": sender_id})
        if not current or current.get("expire_date") != sender["expire_date"]:
            return False, "sender_changed", None
        await db.replace_one(
            {"_id": recipient_id},
            {"_id": recipient_id, "expire_date": sender["expire_date"]},
            upsert=True,
        )
        await db.delete_one({"_id": sender_id})
        await premium_cache.invalidate("_all_")
        return True, "ok", expiry


async def premium_users():
    id_list = []
    async for data in db.find({"_id": {"$gt": 0}}):
        id_list.append(data["_id"])
    return id_list


async def premium_details():
    """Return premium records for the owner report, sorted by expiry."""
    records = []
    async for data in db.find({"_id": {"$gt": 0}}).sort("expire_date", 1):
        records.append({
            "user_id": data.get("_id"),
            "expire_date": data.get("expire_date"),
        })
    return records


async def check_and_remove_expired_users():
    current_time = datetime.datetime.utcnow()
    removed = False
    async for data in db.find():
        expire_date = data.get("expire_date")
        if expire_date and expire_date < current_time:
            await remove_premium(data["_id"])
            removed = True
            print(f"Removed user {data['_id']} due to expired plan.")
    if removed:
        await premium_cache.invalidate("_all_")
