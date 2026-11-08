import asyncio
import contextlib
import signal
from config import (
    __version__,
    TOKENS,
    close_http_session,
    logger,
)
from core.node import (
    MusicNodeBot,
    NODE_BOTS,
)
from metrics import start_metrics_server
from storage import close_storage


async def _shutdown_cluster(tasks: list, metrics_runner: object) -> None:
    for b in NODE_BOTS:
        try:
            b.snapshot_all_states()
        except Exception:
            logger.debug("Snapshot on shutdown failed", exc_info=True)
        try:
            if not b.is_closed():
                await b.close()
        except Exception:
            logger.debug("Bot close failed", exc_info=True)
    for t in tasks:
        if not t.done():
            t.cancel()
    cleanup_fn = getattr(metrics_runner, "cleanup", None)
    if callable(cleanup_fn):
        await cleanup_fn()
    await close_http_session()
    close_storage()


async def main() -> None:
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

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop_event.set)

    metrics_runner = await start_metrics_server(lambda: NODE_BOTS)
    tasks: list = []
    try:
        for node_id, token in TOKENS:
            node_bot = MusicNodeBot(node_id)
            NODE_BOTS.append(node_bot)
            tasks.append(asyncio.create_task(node_bot.start(token)))

        stop_wait = asyncio.create_task(stop_event.wait())
        await asyncio.wait([*tasks, stop_wait], return_when=asyncio.FIRST_COMPLETED)
    finally:
        await _shutdown_cluster(tasks, metrics_runner)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n所有音樂機器人節點已被手動關閉。")
