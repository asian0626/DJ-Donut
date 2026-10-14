from platforms.youtube import (
    _extract_media_info,
    extract_stream_url_sync,
    _fetch_autoplay_sync,
    fetch_autoplay_recommendation
)
from platforms.spotify import (
    SPOTIFY_URL_REGEX,
    get_spotify_client,
    parse_spotify_html,
    fetch_spotify_html_sync,
    fetch_spotify_oembed_sync,
    extract_spotify_ytdl_sync,
    resolve_spotify_sync,
    resolve_spotify
)
from platforms.netease import (
    NETEASE_URL_REGEX,
    resolve_netease_sync,
    resolve_netease
)
from platforms.apple_music import (
    APPLE_MUSIC_URL_REGEX,
    format_apple_artwork,
    resolve_apple_music_sync,
    resolve_apple_music
)
