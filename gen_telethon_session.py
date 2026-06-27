"""
Telethon Session 生成器 — Android 设备模拟
运行此脚本生成 Telethon session string，然后添加到 .env 中

用法:
  python3 gen_telethon_session.py
"""

import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession
from config import API_ID, API_HASH

PHONE = input("请输入手机号 (国际格式, 如 +8613800138000): ").strip()


async def main():
    client = TelegramClient(
        StringSession(),
        API_ID,
        API_HASH,
        device_model="SM-S9180",
        system_version="Android 13",
        app_version="10.14.0",
        lang_code="zh",
        system_lang_code="zh-CN",
    )

    await client.connect()

    if not await client.is_user_authorized():
        await client.send_code_request(PHONE)
        code = input("请输入验证码: ").strip()
        try:
            await client.sign_in(PHONE, code)
        except Exception:
            password = input("需要两步验证密码: ").strip()
            await client.sign_in(password=password)

    session_str = client.session.save()
    print("\n" + "=" * 60)
    print("✅ Telethon Session 生成成功！")
    print("=" * 60)
    print(f"\n请将以下内容添加到 .env 文件中:\n")
    print(f"TELETHON_SESSION={session_str}")
    print("\n" + "=" * 60)

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())