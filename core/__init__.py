from typing import TYPE_CHECKING, Any

from core.models import (
    BufferedAudioSource,
    GuildMusicState,
    Song,
    apply_encoder_bitrate,
    get_playback_elapsed,
    has_dj_permission,
    is_vc_active,
    is_vc_connected,
    is_vc_paused,
    is_vc_playing,
    make_after_callback,
    safe_disconnect,
    safe_pause_resume,
    safe_stop,
    safe_voice_connect,
)

if TYPE_CHECKING:
    from core.dispatcher import (
        find_best_bot_for_channel,
        get_bot_guild,
        get_bot_in_user_room,
        get_or_fetch_bot_guild,
        get_user_voice_channel,
    )
    from core.node import NODE_BOTS, MusicNodeBot, check_lavalink_health
    from core.player import play_next


def __getattr__(name: str) -> Any:
    if name in ("MusicNodeBot", "NODE_BOTS", "check_lavalink_health"):
        from core import node
        return getattr(node, name)
    if name in (
        "find_best_bot_for_channel",
        "get_bot_guild",
        "get_bot_in_user_room",
        "get_or_fetch_bot_guild",
        "get_user_voice_channel",
    ):
        from core import dispatcher
        return getattr(dispatcher, name)
    if name == "play_next":
        from core.player import play_next
        return play_next
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "NODE_BOTS",
    "BufferedAudioSource",
    "GuildMusicState",
    "MusicNodeBot",
    "Song",
    "apply_encoder_bitrate",
    "check_lavalink_health",
    "find_best_bot_for_channel",
    "get_bot_guild",
    "get_bot_in_user_room",
    "get_or_fetch_bot_guild",
    "get_playback_elapsed",
    "get_user_voice_channel",
    "has_dj_permission",
    "is_vc_active",
    "is_vc_connected",
    "is_vc_paused",
    "is_vc_playing",
    "make_after_callback",
    "play_next",
    "safe_disconnect",
    "safe_pause_resume",
    "safe_stop",
    "safe_voice_connect",
]
