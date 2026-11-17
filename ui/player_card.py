from typing import Any, Optional
from urllib.parse import quote, urlsplit

import discord

from core.models import GuildMusicState, Song, get_playback_elapsed
from i18n import resolve_locale, t


def _plain_text(value: str, limit: int) -> str:
    text = " ".join(str(value).split())
    if len(text) > limit:
        text = text[:limit - 1] + "…"
    return discord.utils.escape_markdown(discord.utils.escape_mentions(text))


def _http_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            return ""
        return quote(value, safe=":/?#[]@!$&'*+,;=%")[:2000]
    except ValueError:
        return ""


def _clock(seconds: int) -> str:
    minutes, seconds = divmod(max(0, int(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02}:{minutes:02}:{seconds:02}"
    return f"{minutes:02}:{seconds:02}"


def make_player_progress(elapsed: int, total: int, length: int = 24, lang: Optional[str] = None) -> str:
    if total <= 0:
        return f"🔴 {t('player_live', lang=lang)}"
    elapsed = max(0, min(int(elapsed), total))
    length = max(2, min(int(length), 40))
    position = min(length - 1, int(elapsed / total * (length - 1)))
    bar = "━" * position + "●" + "─" * (length - position - 1)
    return f"{_clock(elapsed)} / {_clock(total)}\n\n`{bar}`"


def _song_heading(song: Song, paused: bool, lang: str) -> str:
    status = t('player_paused' if paused else 'player_now_playing', lang=lang)
    badge = "⏸️" if paused else "🟢"
    title = _plain_text(song.title, 160)
    url = _http_url(song.url)
    linked_title = f"[{title}]({url})" if url else title
    artist = _plain_text(song.uploader or t('player_unknown_artist', lang=lang), 100)
    return f"{badge} **{status}**\n\n### {linked_title}\n{artist}"


def _voice_channel(bot: Any, state: GuildMusicState) -> Any:
    if state.voice_channel:
        return state.voice_channel
    guild = bot.get_guild(state.guild_id)
    vc = (guild.voice_client if guild else None) or discord.utils.get(
        getattr(bot, 'voice_clients', []), guild__id=state.guild_id
    )
    return getattr(vc, 'channel', None)


def _footer(bot: Any, state: GuildMusicState, song: Song, lang: str) -> str:
    requester_id = getattr(song.requester, 'id', None)
    requester = f"<@{requester_id}>" if isinstance(requester_id, int) else t('player_unknown_requester', lang=lang)
    channel_id = getattr(_voice_channel(bot, state), 'id', None)
    channel = f"<#{channel_id}>" if isinstance(channel_id, int) else t('player_no_channel', lang=lang)
    requested_by = t('player_requested_by', lang=lang, requester=requester, channel=channel)
    queue_count = t('player_queue_count', lang=lang, count=len(state.queue))
    return f"-# {requested_by}\n-# {queue_count}"


def build_player_card(
    bot: Any,
    state: GuildMusicState,
    controls: discord.ui.ActionRow,
    menu: discord.ui.ActionRow,
    lang: Optional[str] = None,
) -> discord.ui.Container:
    guild_lang = resolve_locale(guild_id=state.guild_id, preferred_lang=lang)
    song = state.current
    if not song:
        return discord.ui.Container(discord.ui.TextDisplay(t('not_playing', lang=guild_lang)), accent_colour=0x5865F2)

    heading: discord.ui.TextDisplay[Any] = discord.ui.TextDisplay(_song_heading(song, state.pause_timestamp > 0, guild_lang))
    thumbnail = _http_url(song.thumbnail)
    top: discord.ui.Item[Any] = heading
    if thumbnail:
        top = discord.ui.Section(heading, accessory=discord.ui.Thumbnail(thumbnail))
    progress = make_player_progress(get_playback_elapsed(state), song.duration, lang=guild_lang)
    return discord.ui.Container(
        top,
        discord.ui.Separator(),
        discord.ui.TextDisplay(progress),
        discord.ui.Separator(),
        controls,
        menu,
        discord.ui.Separator(),
        discord.ui.TextDisplay(_footer(bot, state, song, guild_lang)),
        accent_colour=0x5865F2,
    )
