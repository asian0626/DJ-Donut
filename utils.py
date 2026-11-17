"""Small pure helpers: time parsing/formatting and ffmpeg option building."""
from typing import Any, Optional

from settings import BASSBOOST_PRESETS, EQ_PRESETS, STREAM_USER_AGENT

def parse_time_str(time_str: str) -> Optional[int]:
    time_str = time_str.strip()
    if time_str.isdigit():
        return int(time_str)
    parts = time_str.split(':')
    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
        return int(parts[0]) * 60 + int(parts[1])
    if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit() and parts[2].isdigit():
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    return None

def format_duration(seconds: Optional[int], lang: str = 'zh-TW') -> str:
    if not seconds:
        return "Live / Unknown" if lang == 'en' else "直播 / 未知"
    mins = int(seconds) // 60
    secs = int(seconds) % 60
    return f"{mins:02d}:{secs:02d}"


def get_playback_rate(state: Any) -> float:
    """Media seconds per wall second, including an effect's inherent tempo."""
    effect_rate = {'nightcore': 1.25, 'vaporwave': 0.85}.get(getattr(state, 'filter', 'reset'), 1.0)
    return float(getattr(state, 'playback_speed', 1.0)) * effect_rate


def get_ffmpeg_options(state: Any, seek_seconds: Optional[int] = None) -> dict:
    before_parts = [
        '-nostats -loglevel fatal -threads 1',
        f'-headers "User-Agent: {STREAM_USER_AGENT}\r\nAccept: */*\r\nAccept-Language: en-US,en;q=0.9\r\n"',
        '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -reconnect_on_network_error 1 -reconnect_on_http_error 4xx,5xx',
        '-probesize 64k -analyzeduration 500000'
    ]
    if seek_seconds and seek_seconds > 0:
        before_parts.insert(1, f"-ss {seek_seconds}")
    before = " ".join(before_parts)
    filters = []
    bb = (BASSBOOST_PRESETS.get(getattr(state, 'bassboost', 'off')) or {}).get("ffmpeg")
    if bb:
        filters.append(bb)
    eq = (EQ_PRESETS.get(getattr(state, 'filter', 'reset')) or {}).get("ffmpeg")
    if eq:
        if getattr(state, 'filter', 'reset') in ('nightcore', 'vaporwave'):
            filters.append('aresample=48000')
        filters.append(eq)
    speed = float(getattr(state, 'playback_speed', 1.0))
    if speed != 1.0:
        # atempo changes tempo while preserving pitch, independently of EQ effects.
        filters.append(f"atempo={speed:g}")

    opt = f"-vn -af \"{','.join(filters)}\"" if filters else "-vn"
    return {'before_options': before, 'options': opt}
