#Copyright @juktijol
#Channel t.me/juktijol
from pyrogram import Client
from utils import LOGGER
from config import (
    API_ID,
    API_HASH,
    BOT_TOKEN
)

LOGGER.info("Creating Bot Client From BOT_TOKEN")

app = Client(
    "SmartTools",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workers=1000,
    # ── স্পিড বাড়ানোর জন্য গুরুত্বপূর্ণ সেটিং ──
    max_concurrent_transmissions=5,  # একসাথে ৫টা ফাইল transfer করতে পারবে
)

LOGGER.info("Bot Client Created Successfully!")
