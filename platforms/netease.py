import re
from typing import Any, List, Optional

from config import get_cached_metadata, get_http_session, logger, set_cached_metadata

NETEASE_URL_REGEX = re.compile(
    r'(?:https?://)?(?:[a-zA-Z0-9-]+\.)?(?:music\.163\.com|163cn\.tv|163\.lu)/.*?(\b(?:song|playlist|album)\b).*?(?:id=|\/)(\d+)',
    re.IGNORECASE
)

_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://music.163.com/'
}


def _format_netease_track_list(raw_tracks: list, fallback_thumb: str, resolved_url: str) -> List[dict]:
    tracks: List[dict] = []
    for t_item in raw_tracks[:200]:
        title = t_item.get('name', '未知歌曲')
        artist_list = t_item.get('ar') or t_item.get('artists') or []
        artists = ", ".join(a.get('name', '') for a in artist_list if a.get('name'))
        duration = (t_item.get('dt', 0) or t_item.get('duration', 0) or 0) // 1000
        thumb = t_item.get('al', {}).get('picUrl') or fallback_thumb
        tid = t_item.get('id')
        song_url = f"https://music.163.com/#/song?id={tid}" if tid else resolved_url
        tracks.append({
            'title': title,
            'artist': artists,
            'duration': duration,
            'thumbnail': thumb,
            'url': song_url,
            'search_query': f"{title} {artists}".strip()
        })
    return tracks


async def _resolve_netease_song(session: Any, item_id: str) -> Optional[dict]:
    try:
        api_url = f"https://music.163.com/api/song/detail/?id={item_id}&ids=[{item_id}]"
        async with session.get(api_url, headers=_HEADERS) as resp:
            data = await resp.json(content_type=None)
        songs = data.get('songs', [])
        if not songs:
            return None
        s = songs[0]
        title = s.get('name', '未知歌曲')
        artists = ", ".join(a.get('name', '') for a in s.get('artists', []) if a.get('name'))
        duration = (s.get('duration', 0) or 0) // 1000
        thumb = s.get('album', {}).get('picUrl', '')
        song_url = f"https://music.163.com/#/song?id={item_id}"
        return {
            'platform': 'netease',
            'platform_name': '網易雲音樂',
            'color': 0xC20C0C,
            'type': 'track',
            'title': title,
            'artist': artists,
            'thumbnail': thumb,
            'duration': duration,
            'tracks': [{
                'title': title,
                'artist': artists,
                'duration': duration,
                'thumbnail': thumb,
                'url': song_url,
                'search_query': f"{title} {artists}".strip()
            }]
        }
    except Exception as e:
        logger.warning(f"[Netease] 單曲解析異常: {e}")
        return None


async def _resolve_netease_playlist(session: Any, item_id: str, resolved_url: str) -> Optional[dict]:
    try:
        api_url = f"https://music.163.com/api/v1/playlist/detail?id={item_id}"
        async with session.get(api_url, headers=_HEADERS) as resp:
            data = await resp.json(content_type=None)
        pl = data.get('playlist', {})
        pl_thumb = pl.get('coverImgUrl', '')
        tracks = _format_netease_track_list(pl.get('tracks', []), pl_thumb, resolved_url)
        return {
            'platform': 'netease',
            'platform_name': '網易雲音樂',
            'color': 0xC20C0C,
            'type': 'playlist',
            'title': pl.get('name', '網易雲歌單'),
            'artist': pl.get('creator', {}).get('nickname', ''),
            'thumbnail': pl_thumb,
            'duration': sum(t['duration'] for t in tracks),
            'tracks': tracks
        }
    except Exception as e:
        logger.warning(f"[Netease] 歌單解析異常: {e}")
        return None


async def _resolve_netease_album(session: Any, item_id: str, resolved_url: str) -> Optional[dict]:
    try:
        api_url = f"https://music.163.com/api/v1/album/{item_id}"
        async with session.get(api_url, headers=_HEADERS) as resp:
            data = await resp.json(content_type=None)
        album = data.get('album', {})
        album_thumb = album.get('picUrl', '')
        tracks = _format_netease_track_list(data.get('songs', []), album_thumb, resolved_url)
        return {
            'platform': 'netease',
            'platform_name': '網易雲音樂',
            'color': 0xC20C0C,
            'type': 'album',
            'title': album.get('name', '網易雲專輯'),
            'artist': album.get('artist', {}).get('name', ''),
            'thumbnail': album_thumb,
            'duration': sum(t['duration'] for t in tracks),
            'tracks': tracks
        }
    except Exception as e:
        logger.warning(f"[Netease] 專輯解析異常: {e}")
        return None


async def resolve_netease(url: str) -> Optional[dict]:
    cached = get_cached_metadata(url)
    if cached:
        return cached

    session = await get_http_session()
    resolved_url = url
    if "163cn.tv" in url or "163.lu" in url or "/s/" in url:
        try:
            async with session.get(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}, allow_redirects=True) as resp:
                resolved_url = str(resp.url)
        except Exception:
            resolved_url = url

    m = NETEASE_URL_REGEX.search(resolved_url)
    if not m:
        return None
    item_type = m.group(1).lower()
    item_id = m.group(2)
    norm_key = f"netease:{item_type}:{item_id}"
    cached_norm = get_cached_metadata(norm_key)
    if cached_norm:
        return cached_norm

    res: Optional[dict] = None
    if item_type == 'song':
        res = await _resolve_netease_song(session, item_id)
    elif item_type == 'playlist':
        res = await _resolve_netease_playlist(session, item_id, resolved_url)
    elif item_type == 'album':
        res = await _resolve_netease_album(session, item_id, resolved_url)

    if res:
        set_cached_metadata(url, res)
        set_cached_metadata(norm_key, res)
    return res
