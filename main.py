import asyncio
from config import (
    __version__,
    logger,
    BotError,
    AudioSourceError,
    TrackNotFoundError,
    VoiceConnectionError,
    PermissionDeniedError,
    TOKENS,
    LAVALINK_URI,
    LAVALINK_PASSWORD,
    COOKIES_FILE,
    YTDL_OPTIONS,
    FFMPEG_OPTIONS,
    BASSBOOST_PRESETS,
    AUDIO_FILTER_PRESETS,
    GUILD_SETTINGS_FILE,
    SPOTIFY_CLIENT_ID,
    SPOTIFY_CLIENT_SECRET,
    SPOTIFY_URL_REGEX,
    NETEASE_URL_REGEX,
    APPLE_MUSIC_URL_REGEX,
    MEDIA_EXECUTOR,
    HAS_WAVELINK,
    wavelink,
    load_guild_settings,
    save_guild_setting,
    get_guild_setting,
    get_ffmpeg_options,
    get_thread_ytdl
)
from core import (
    Song,
    GuildMusicState,
    BufferedAudioSource,
    is_vc_connected,
    is_vc_playing,
    is_vc_active,
    is_vc_paused,
    has_dj_permission,
    get_playback_elapsed,
    MusicNodeBot,
    NODE_BOTS,
    check_lavalink_health,
    play_next,
    get_bot_guild,
    get_or_fetch_bot_guild,
    find_best_bot_for_channel,
    get_bot_in_user_room,
    get_user_voice_channel
)
from platforms import (
    resolve_spotify,
    resolve_netease,
    resolve_apple_music,
    extract_stream_url_sync,
    extract_media_info,
    find_autoplay_candidate
)
from ui import (
    make_progress_bar,
    get_platform_badge,
    build_nowplaying_embed,
    build_queue_embed,
    build_song_queued_embed,
    format_duration,
    parse_time_str,
    MusicControllerView,
    SearchSelectView
)
from commands import (
    register_slash_commands
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

    tasks = []
    for node_id, token in TOKENS:
        node_bot = MusicNodeBot(node_id)
        NODE_BOTS.append(node_bot)
        tasks.append(node_bot.start(token))

    await asyncio.gather(*tasks)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n所有音樂機器人節點已被手動關閉。")
