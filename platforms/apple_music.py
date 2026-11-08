import json
import re
from typing import Any, List, Optional

from config import get_cached_metadata, get_http_session, logger, set_cached_metadata

APPLE_MUSIC_URL_REGEX = re.compile(
    r'(?:https?://)?music\.apple\.com/(?:([a-z]{2})/)?(album|playlist|song)/([^/?#]+)(?:/(\d+|pl\.[a-zA-Z0-9]+))?(?:\?.*?\bi=(\d+))?',
    re.IGNORECASE
)

_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}


def format_apple_artwork(art_obj: Any) -> str:
    if not isinstance(art_obj, dict):
        return ''
    raw_url = art_obj.get('dictionary', {}).get('url') or art_obj.get('url', '')
    return raw_url.replace('{w}', '600').replace('{h}', '600').replace('{f}', 'jpg') if raw_url else ''


def _build_apple_single_result(r: dict, resolved_url: str) -> dict:
    title = r.get('trackName', '未知歌曲')
    artist = r.get('artistName', '')
    duration = (r.get('trackTimeMillis', 0) or 0) // 1000
    thumb = r.get('artworkUrl100', '').replace('100x100bb', '600x600bb')
    return {
        'platform': 'apple_music',
        'platform_name': 'Apple Music',
        'color': 0xFC3C44,
        'type': 'track',
        'title': title,
        'artist': artist,
        'thumbnail': thumb,
        'duration': duration,
        'tracks': [{
            'title': title,
            'artist': artist,
            'duration': duration,
            'thumbnail': thumb,
            'url': r.get('trackViewUrl', resolved_url),
            'search_query': f"{title} {artist}".strip()
        }]
    }


async def _lookup_apple_track(session: Any, track_id: str, country: str, resolved_url: str, label: str) -> Optional[dict]:
    try:
        api_url = f"https://itunes.apple.com/lookup?id={track_id}&country={country}"
        async with session.get(api_url, headers=_HEADERS) as resp:
            data = await resp.json(content_type=None)
        results = data.get('results', [])
        if results:
            return _build_apple_single_result(results[0], resolved_url)
    except Exception as e:
        logger.warning(f"[Apple Music] {label}: {e}")
    return None


async def _lookup_apple_album(session: Any, album_id: str, country: str, resolved_url: str) -> Optional[dict]:
    try:
        api_url = f"https://itunes.apple.com/lookup?id={album_id}&entity=song&country={country}"
        async with session.get(api_url, headers=_HEADERS) as resp:
            data = await resp.json(content_type=None)
        results = data.get('results', [])
        if not results:
            return None
        col = results[0]
        col_name = col.get('collectionName', 'Apple Music 專輯')
        col_artist = col.get('artistName', '')
        col_thumb = col.get('artworkUrl100', '').replace('100x100bb', '600x600bb')
        song_results = [r for r in results if r.get('wrapperType') == 'track']
        tracks: List[dict] = []
        for s in song_results[:200]:
            t_title = s.get('trackName', '未知歌曲')
            t_artist = s.get('artistName', col_artist)
            t_dur = (s.get('trackTimeMillis', 0) or 0) // 1000
            t_thumb = s.get('artworkUrl100', '').replace('100x100bb', '600x600bb') or col_thumb
            tracks.append({
                'title': t_title,
                'artist': t_artist,
                'duration': t_dur,
                'thumbnail': t_thumb,
                'url': s.get('trackViewUrl', resolved_url),
                'search_query': f"{t_title} {t_artist}".strip()
            })
        return {
            'platform': 'apple_music',
            'platform_name': 'Apple Music',
            'color': 0xFC3C44,
            'type': 'album',
            'title': col_name,
            'artist': col_artist,
            'thumbnail': col_thumb,
            'duration': sum(t['duration'] for t in tracks),
            'tracks': tracks
        }
    except Exception as e:
        logger.warning(f"[Apple Music] 專輯解析異常: {e}")
        return None


def _parse_apple_playlist_sections(inner_data: dict, resolved_url: str) -> Optional[dict]:
    sections = inner_data.get('sections', [])
    pl_name = 'Apple Music 歌單'
    pl_thumb = ''
    if sections and sections[0].get('items'):
        h_item = sections[0]['items'][0]
        pl_name = h_item.get('title') or pl_name
        pl_thumb = format_apple_artwork(h_item.get('artwork'))
    if not pl_name or pl_name == 'Apple Music 歌單':
        seo_title = inner_data.get('seoData', {}).get('pageTitle', '')
        if seo_title:
            pl_name = seo_title.split(' - ')[0]

    best_section = max(sections, key=lambda s: len(s.get('items', []))) if sections else {}
    items = best_section.get('items', [])
    tracks: List[dict] = []
    for it in items[:200]:
        t_title = it.get('title')
        if not t_title:
            continue
        t_artist = it.get('artistName', '')
        t_dur = (it.get('duration', 0) or 0) // 1000
        t_thumb = format_apple_artwork(it.get('artwork')) or pl_thumb
        t_url = it.get('contentDescriptor', {}).get('url') or resolved_url
        tracks.append({
            'title': t_title,
            'artist': t_artist,
            'duration': t_dur,
            'thumbnail': t_thumb,
            'url': t_url,
            'search_query': f"{t_title} {t_artist}".strip()
        })
    if not tracks:
        return None
    return {
        'platform': 'apple_music',
        'platform_name': 'Apple Music',
        'color': 0xFC3C44,
        'type': 'playlist',
        'title': pl_name,
        'artist': 'Apple Music',
        'thumbnail': pl_thumb,
        'duration': sum(t['duration'] for t in tracks),
        'tracks': tracks
    }


async def _resolve_apple_playlist(session: Any, resolved_url: str) -> Optional[dict]:
    try:
        async with session.get(resolved_url, headers=_HEADERS) as resp:
            html = await resp.text(errors='ignore')
        m_json = re.search(r'<script type=\"application/json\" id=\"serialized-server-data\">(.*?)</script>', html, re.DOTALL)
        if not m_json:
            return None
        parsed = json.loads(m_json.group(1))
        pdata = parsed if isinstance(parsed, dict) else (parsed[0] if parsed else {})
        data_list = pdata.get('data', [])
        if data_list:
            return _parse_apple_playlist_sections(data_list[0].get('data', {}), resolved_url)
    except Exception as e:
        logger.warning(f"[Apple Music] 歌單解析異常: {e}")
    return None


def _pick_numeric_id(id_part: Optional[str], slug_or_id: str) -> Optional[str]:
    if id_part and id_part.isdigit():
        return id_part
    if slug_or_id.isdigit():
        return slug_or_id
    return None


async def _dispatch_apple_lookup(
    session: Any,
    item_type: str,
    country: str,
    slug_or_id: str,
    id_part: Optional[str],
    track_i: Optional[str],
    resolved_url: str,
) -> Optional[dict]:
    if track_i:
        res = await _lookup_apple_track(session, track_i, country, resolved_url, "單曲參數解析異常")
        if res:
            return res
    num_id = _pick_numeric_id(id_part, slug_or_id)
    if item_type == 'song' and num_id:
        return await _lookup_apple_track(session, num_id, country, resolved_url, "單曲解析異常")
    if item_type == 'album' and num_id:
        return await _lookup_apple_album(session, num_id, country, resolved_url)
    if item_type == 'playlist':
        return await _resolve_apple_playlist(session, resolved_url)
    return None


async def resolve_apple_music(url: str) -> Optional[dict]:
    cached = get_cached_metadata(url)
    if cached:
        return cached

    session = await get_http_session()
    resolved_url = url
    if "apple.co" in url:
        try:
            async with session.get(
                url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}, allow_redirects=True
            ) as resp:
                resolved_url = str(resp.url)
        except Exception:
            resolved_url = url

    m = APPLE_MUSIC_URL_REGEX.search(resolved_url)
    if not m:
        return None
    country = (m.group(1) or 'tw').lower()
    item_type = m.group(2).lower()
    slug_or_id = m.group(3)
    id_part = m.group(4)
    track_i = m.group(5)

    target_id = track_i or id_part or slug_or_id
    norm_key = f"apple:{country}:{item_type}:{target_id}"
    cached_norm = get_cached_metadata(norm_key)
    if cached_norm:
        return cached_norm

    res = await _dispatch_apple_lookup(
        session, item_type, country, slug_or_id, id_part, track_i, resolved_url
    )
    if res:
        set_cached_metadata(url, res)
        set_cached_metadata(norm_key, res)
    return res
