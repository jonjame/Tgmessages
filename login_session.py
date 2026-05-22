"""
Telegram 账号登录脚本 - 创建会话文件供后续使用
运行: python login_session.py
"""
import asyncio
from pathlib import Path
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

import config

Path(config.SESSIONS_DIR).mkdir(exist_ok=True)


async def main():
    print("=== Telegram 账号登录 ===\n")
    session_name = input("请输入会话名称（如: account1）: ").strip()
    if not session_name:
        print("会话名称不能为空")
        return

    session_path = Path(config.SESSIONS_DIR) / session_name
    if config.API_ID == 0 or not config.API_HASH:
        print("请先在 config.py 中配置 API_ID 和 API_HASH")
        print("获取地址: https://my.telegram.org")
        return

    client = TelegramClient(
        str(session_path),
        config.API_ID,
        config.API_HASH
    )
    await client.start(
        phone=input("请输入手机号（含国家码，如 +8613800138000）: "),
    )
    me = await client.get_me()
    print(f"\n登录成功! 账号: {me.first_name} (@{me.username or '无'})")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
