# devgaganin — premium plan management (owner + user views)

import datetime
import pytz
import asyncio

from devgagan import app
from config import OWNER_ID
from devgagan.core.func import get_seconds
from devgagan.core.mongo import plans_db
from pyrogram import filters

IST = pytz.timezone("Asia/Kolkata")


@app.on_message(filters.command("get") & filters.private & filters.user(OWNER_ID))
async def get_all_premium(client, message):
    """Owner-only report of all currently stored premium subscriptions."""
    records = await plans_db.premium_details()
    now = datetime.datetime.now(IST)
    rows = []
    lookup = asyncio.Semaphore(8)

    async def format_record(index, record):
        user_id = record.get("user_id")
        expiry = record.get("expire_date")
        if not user_id or not expiry:
            return None
        if expiry.tzinfo is None:
            expiry = pytz.utc.localize(expiry)
        expiry_ist = expiry.astimezone(IST)
        remaining = expiry_ist - now
        if remaining.total_seconds() <= 0:
            return None
        days = remaining.days
        hours, remainder = divmod(remaining.seconds, 3600)
        minutes, _ = divmod(remainder, 60)
        identity = str(user_id)
        try:
            async with lookup:
                user = await client.get_users(int(user_id))
            username = f"@{user.username}" if user.username else "no_username"
            display = (user.first_name or "User").replace("\n", " ")
            identity = f"{display} ({username}) | {user_id}"
        except Exception:
            pass
        expiry_str = expiry_ist.strftime("%d-%m-%Y %I:%M %p IST")
        return f"{index}. {identity}\n   Expiry: {expiry_str} | Remaining: {days}d {hours}h {minutes}m"

    formatted = await asyncio.gather(
        *(format_record(index, record) for index, record in enumerate(records, 1))
    )
    rows = [row for row in formatted if row]
    if not rows:
        await message.reply_text("💎 No active premium subscriptions found.")
        return

    header = (
        f"💎 PREMIUM SUBSCRIPTIONS\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Total active users: {len(rows)}\n\n"
    )
    chunks = []
    current = header
    for row in rows:
        if len(current) + len(row) + 2 > 3800:
            chunks.append(current.rstrip())
            current = "💎 PREMIUM SUBSCRIPTIONS (continued)\n━━━━━━━━━━━━━━━━━━━━\n\n"
        current += row + "\n\n"
    if current.strip():
        chunks.append(current.rstrip())
    for chunk in chunks:
        await message.reply_text(chunk, disable_web_page_preview=True)


@app.on_message(filters.command("rem") & filters.user(OWNER_ID))
async def remove_premium(client, message):
    if len(message.command) == 2:
        user_id = int(message.command[1])
        user = await client.get_users(user_id)
        data = await plans_db.check_premium(user_id)

        if data and data.get("_id"):
            await plans_db.remove_premium(user_id)
            await message.reply_text("✅ **Premium removed successfully!**")
            await client.send_message(
                chat_id=user_id,
                text=f"Hey {user.mention},\n\n❌ Your premium access has been removed.\nThanks for using our service 😊",
            )
        else:
            await message.reply_text("⚠️ Unable to remove — that user never had premium.")
    else:
        await message.reply_text("Usage : /rem user_id")


@app.on_message(filters.command("myplan") & filters.private)
async def myplan(client, message):
    user_id = message.from_user.id
    user = message.from_user.mention
    data = await plans_db.check_premium(user_id)
    if data and data.get("expire_date"):
        expiry = data.get("expire_date")
        expiry_ist = expiry.astimezone(IST)
        expiry_str = expiry_ist.strftime("%d-%m-%Y • %I:%M %p")

        current_time = datetime.datetime.now(IST)
        time_left = expiry_ist - current_time

        days = time_left.days
        hours, remainder = divmod(time_left.seconds, 3600)
        minutes, _ = divmod(remainder, 60)

        time_left_str = f"{days}d {hours}h {minutes}m"
        await message.reply_text(
            f"💎 **PREMIUM DASHBOARD**\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 **User** : {user}\n"
            f"⚡ **User ID** : <code>{user_id}</code>\n"
            f"⏰ **Time Left** : `{time_left_str}`\n"
            f"⌛️ **Expiry Date** : `{expiry_str}`\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🚀 Enjoy unlimited speed & batches!"
        )
    else:
        await message.reply_text(
            f"Hey {user},\n\n"
            f"⚠️ You don't have any active premium plan.\n"
            f"💎 Use /pay to unlock all premium features!"
        )


@app.on_message(filters.command("transfer") & filters.private)
async def transfer_premium_command(client, message):
    """Immediately move an active premium plan to another Telegram user ID."""
    if len(message.command) != 2:
        await message.reply_text(
            "Usage: /transfer user_id\n\n"
            "Example: /transfer 123456789\n\n"
            "This moves your remaining premium time to that Telegram account."
        )
        return

    try:
        recipient_id = int(message.command[1])
    except ValueError:
        await message.reply_text("❌ User ID must contain numbers only.")
        return

    sender_id = message.from_user.id
    if recipient_id <= 0 or recipient_id == sender_id:
        await message.reply_text("❌ Enter another valid Telegram user ID.")
        return

    source = await plans_db.check_premium(sender_id)
    if not source or not source.get("expire_date"):
        await message.reply_text("❌ You do not have an active premium plan to transfer.")
        return

    expiry = source["expire_date"]
    if expiry.tzinfo is None:
        expiry = pytz.utc.localize(expiry)
    if expiry <= datetime.datetime.now(pytz.utc):
        await message.reply_text("❌ Your premium plan has already expired.")
        return

    recipient_plan = await plans_db.check_premium(recipient_id)
    if recipient_plan and recipient_plan.get("expire_date"):
        recipient_expiry = recipient_plan["expire_date"]
        if recipient_expiry.tzinfo is None:
            recipient_expiry = pytz.utc.localize(recipient_expiry)
        if recipient_expiry > datetime.datetime.now(pytz.utc):
            await message.reply_text("❌ The recipient already has an active premium plan.")
            return

    success, reason, transferred_expiry = await plans_db.transfer_premium(
        sender_id, recipient_id
    )
    messages = {
        "sender_missing": "❌ You no longer have an active premium plan.",
        "sender_expired": "❌ Your premium plan has expired.",
        "recipient_active": "❌ The recipient already has an active premium plan.",
        "sender_changed": "❌ Your premium plan changed. Please start /transfer again.",
        "same_user": "❌ You cannot transfer premium to yourself.",
    }
    if not success:
        text = messages.get(reason, "❌ Transfer could not be completed.")
        await message.reply_text(text)
        return

    expiry_text = transferred_expiry.astimezone(IST).strftime("%d-%m-%Y %I:%M %p IST")
    await message.reply_text(
        f"✅ Premium transferred successfully to `{recipient_id}`.\n\n"
        f"The transferred plan expires on `{expiry_text}`."
    )
    try:
        await client.send_message(
            recipient_id,
            "🎉 **Premium received!**\n\n"
            f"Your transferred premium is active until `{expiry_text}`.\n"
            "Use /myplan to view your plan details.",
        )
    except Exception:
        pass


@app.on_message(filters.command("check") & filters.user(OWNER_ID))
async def get_premium(client, message):
    if len(message.command) == 2:
        user_id = int(message.command[1])
        user = await client.get_users(user_id)
        data = await plans_db.check_premium(user_id)
        if data and data.get("expire_date"):
            expiry = data.get("expire_date")
            expiry_ist = expiry.astimezone(IST)
            expiry_str = expiry_ist.strftime("%d-%m-%Y • %I:%M %p")

            current_time = datetime.datetime.now(IST)
            time_left = expiry_ist - current_time

            days = time_left.days
            hours, remainder = divmod(time_left.seconds, 3600)
            minutes, _ = divmod(remainder, 60)

            time_left_str = f"{days}d {hours}h {minutes}m"
            await message.reply_text(
                f"💎 **PREMIUM CHECK**\n\n"
                f"👤 **User** : {user.mention}\n"
                f"⚡ **User ID** : <code>{user_id}</code>\n"
                f"⏰ **Time Left** : `{time_left_str}`\n"
                f"⌛️ **Expiry** : `{expiry_str}`"
            )
        else:
            await message.reply_text("❌ No premium data found for this user.")
    else:
        await message.reply_text("Usage : /check user_id")


@app.on_message(filters.command("add") & filters.user(OWNER_ID))
async def give_premium_cmd_handler(client, message):
    if len(message.command) == 4:
        time_zone = datetime.datetime.now(IST)
        current_time = time_zone.strftime("%d-%m-%Y • %I:%M %p")
        user_id = int(message.command[1])
        user = await client.get_users(user_id)
        time = message.command[2] + " " + message.command[3]
        seconds = await get_seconds(time)
        if seconds > 0:
            expiry_time = datetime.datetime.now(pytz.utc) + datetime.timedelta(seconds=seconds)
            await plans_db.add_premium(user_id, expiry_time)
            data = await plans_db.check_premium(user_id)
            expiry = data.get("expire_date")
            expiry_str = expiry.astimezone(IST).strftime("%d-%m-%Y • %I:%M %p")
            await message.reply_text(
                f"✅ **PREMIUM ADDED SUCCESSFULLY**\n\n"
                f"👤 **User** : {user.mention}\n"
                f"⚡ **User ID** : <code>{user_id}</code>\n"
                f"⏰ **Plan** : <code>{time}</code>\n"
                f"⌛️ **Expiry** : `{expiry_str}`",
                disable_web_page_preview=True,
            )
            await client.send_message(
                chat_id=user_id,
                text=(
                    f"🎉 Hey {user.mention},\n"
                    f"Thank you for purchasing premium!\n\n"
                    f"💎 **Plan** : <code>{time}</code>\n"
                    f"⌛️ **Expiry** : `{expiry_str}`\n\n"
                    f"Enjoy lightning-fast saving ⚡✨"
                ),
                disable_web_page_preview=True,
            )
        else:
            await message.reply_text(
                "Invalid time format. Use '1 day', '1 hour', '1 min', '1 month' or '1 year'."
            )
    else:
        await message.reply_text("Usage : /add user_id time (e.g., '1 day' or '1 month')")
