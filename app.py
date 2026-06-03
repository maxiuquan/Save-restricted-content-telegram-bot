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
    workers=256,                      # 增加 workers 处理更多并发消息
    max_concurrent_transmissions=10,  # 提升并发传输数，减少排队等待
    # 可选: 如果 pyrofork 支持，可启用更快的事件循环策略
    # no_updates=False 保持默认，让 bot 能正常接收更新
)

LOGGER.info("Bot Client Created Successfully!")
