import asyncio
import contextlib
import logging
import signal
from config import (
    __version__,
    SENTRY_DSN,
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


def _init_opus() -> None:
    import discord
    import discord.opus
    import discord.voice_state

    # 鎖定語音連線使用標準穩定加密協定 (AEAD XChaCha20 Poly1305 RTPSIZE)，
    # 防止 DAVE (MLS E2EE) 協商未完成導致 Discord 語音伺服器丟棄無聲音訊封包
    with contextlib.suppress(Exception):
        discord.voice_state.VoiceConnectionState.max_dave_protocol_version = property(lambda self: 0)
        logger.info("🔒 語音協定已鎖定相容標準加密傳輸 (DAVE 協商已安全降級相容)")

    if discord.opus.is_loaded():
        return
    opus_candidates = [
        "/opt/homebrew/lib/libopus.dylib",
        "/usr/local/lib/libopus.dylib",
        "libopus.dylib",
        "libopus.so.0",
        "libopus.so",
    ]
    for candidate in opus_candidates:
        with contextlib.suppress(Exception):
            discord.opus.load_opus(candidate)
            if discord.opus.is_loaded():
                logger.info(f"🎧 成功載入原生 Opus 音訊庫: {candidate}")
                return
    with contextlib.suppress(Exception):
        discord.opus._OpusStruct.get_opus_version()
        if discord.opus.is_loaded():
            logger.info("🎧 成功載入系統預設 Opus 音訊庫")
            return
    logger.warning("⚠️ 未能載入 libopus 音訊庫，語音編碼可能無法運作！")


def _init_sentry() -> None:
    if not SENTRY_DSN:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.aiohttp import AioHttpIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration

        sentry_sdk.init(
            dsn=SENTRY_DSN,
            integrations=[
                AioHttpIntegration(),
                LoggingIntegration(level=None, event_level=logging.ERROR),
            ],
            release=f"dj-donut@{__version__}",
            traces_sample_rate=0.1,
        )
        logger.info("🛡️ Sentry 錯誤追蹤已成功啟用！")
    except ImportError:
        logger.warning("已配置 SENTRY_DSN 但未安裝 sentry-sdk，如需啟用錯誤監控可執行 pip install 'sentry-sdk[aiohttp]'")
    except Exception as e:
        logger.warning(f"Sentry 初始化失敗: {e}")


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

    _init_opus()
    _init_sentry()

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
