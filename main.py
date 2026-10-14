import asyncio
from config import (
    __version__,
    TOKENS,
    close_http_session
)
from core.node import (
    MusicNodeBot,
    NODE_BOTS
)

async def main():
    if not TOKENS:
        print("\n" + "!" * 65)
        print("【錯誤】未找到任何有效的機器人 Token！")
        print("請打開 .env 檔案並填入 1 ~ 5 隻機器人的 Token：")
        print("BOT_TOKEN_1=你的第1隻機器人Token")
        print("BOT_TOKEN_2=你的第2隻機器人Token")
        print("...")
        print("BOT_TOKEN_5=你的第5隻機器人Token")
        print("!" * 65 + "\n")
        return

    print("=" * 65)
    print(f"🚀 正在初始化多房間音樂機器人矩陣 v{__version__} (共檢測到 {len(TOKENS)} 隻機器人配置)...")
    print("=" * 65)

    try:
        tasks = []
        for node_id, token in TOKENS:
            node_bot = MusicNodeBot(node_id)
            NODE_BOTS.append(node_bot)
            tasks.append(node_bot.start(token))

        await asyncio.gather(*tasks)
    finally:
        await close_http_session()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n所有音樂機器人節點已被手動關閉。")
