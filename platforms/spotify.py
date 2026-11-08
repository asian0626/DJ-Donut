import json
import os
import re
from typing import Any, Dict, List, Optional
import urllib.parse

from config import (
    get_cached_metadata,
    get_http_session,
    get_thread_ytdl,
    logger,
    run_media,
    set_cached_metadata,
)

SPOTIFY_URL_REGEX = re.compile(
    r'(?:https?://(?:open\.)?spotify\.com/(?:intl-[a-zA-Z-]+/)?|spotify:)(track|playlist|album|artist)[:/]([a-zA-Z0-9]+)'
)

_spotify_state: Dict[str, Any] = {"client": None}


def get_spotify_client() -> Any:
    if _spotify_state["client"] is not None:
        return _spotify_state["client"]
    cid = os.getenv("SPOTIFY_CLIENT_ID")
    csecret = os.getenv("SPOTIFY_CLIENT_SECRET")
    if not (cid and csecret and cid != "YOUR_SPOTIFY_CLIENT_ID" and csecret != "YOUR_SPOTIFY_CLIENT_SECRET"):
        return None
    try:
        import spotipy
        from spotipy.oauth2 import SpotifyClientCredentials
        try:
            from spotipy.cache_handler import MemoryCacheHandler
            cache_handler = MemoryCacheHandler()
        except Exception:
            cache_handler = None

        auth_kwargs: Dict[str, Any] = {
            "client_id": cid.strip(),
            "client_secret": csecret.strip(),
        }
        if cache_handler:
            auth_kwargs["cache_handler"] = cache_handler
        auth_mgr = SpotifyClientCredentials(**auth_kwargs)
        _spotify_state["client"] = spotipy.Spotify(auth_manager=auth_mgr)
        logger.info("[Spotify API] 成功初始化官方 Spotify API 客戶端！")
    except ImportError:
        logger.debug("Ignored exception in get_spotify_client", exc_info=True)
    except Exception as e:
        logger.warning(f"[Spotify API] 初始化官方客戶端失敗: {e}")
    return _spotify_state["client"]


def _search_entity(d: Any) -> Optional[dict]:
    if isinstance(d, dict):
        if "trackList" in d or ("name" in d and ("artists" in d or "subtitle" in d)):
            return d
        for v in d.values():
            res = _search_entity(v)
            if res:
                return res
    elif isinstance(d, list):
        for item in d:
            res = _search_entity(item)
            if res:
                return res
    return None


def _parse_entity_track(entity: dict, item_id: str) -> Optional[dict]:
    title = entity.get("name") or entity.get("title") or ""
    if not title:
        return None
    artists = entity.get("artists", [])
    if isinstance(artists, list) and artists:
        artist = ", ".join(str(a.get("name")) for a in artists if isinstance(a, dict) and a.get("name"))
    else:
        artist = entity.get("subtitle") or ""
    duration_ms = entity.get("duration") or 0
    duration = int(duration_ms) // 1000 if duration_ms else 0
    cover_sources = entity.get("coverArt", {}).get("sources", [])
    thumb = cover_sources[0].get("url") if cover_sources else ""
    return {
        "type": "track",
        "title": title,
        "artist": artist,
        "thumbnail": thumb,
        "duration": duration,
        "tracks": [{
            "title": title,
            "artist": artist,
            "duration": duration,
            "thumbnail": thumb,
            "url": f"https://open.spotify.com/track/{item_id}",
            "search_query": f"{title} {artist}".strip()
        }]
    }


def _parse_entity_collection(entity: dict, item_type: str) -> Optional[dict]:
    title = entity.get("name") or entity.get("title") or ("Spotify 播放清單" if item_type == "playlist" else "Spotify 專輯")
    cover_sources = entity.get("coverArt", {}).get("sources", [])
    thumb = cover_sources[0].get("url") if cover_sources else ""
    track_list = entity.get("trackList") or entity.get("tracks", {}).get("items", [])
    tracks: List[dict] = []
    for item in track_list:
        t_title = item.get("title") or item.get("name")
        if not t_title:
            continue
        t_artist = item.get("subtitle") or ""
        if not t_artist and isinstance(item.get("artists"), list):
            t_artist = ", ".join(str(a.get("name")) for a in item.get("artists", []) if isinstance(a, dict) and a.get("name"))
        t_dur_ms = item.get("duration") or 0
        t_dur = int(t_dur_ms) // 1000 if t_dur_ms else 0
        uri = item.get("uri") or ""
        tid = uri.split(":")[-1] if uri else ""
        t_url = f"https://open.spotify.com/track/{tid}" if tid else ""
        tracks.append({
            "title": t_title,
            "artist": t_artist,
            "duration": t_dur,
            "thumbnail": thumb,
            "url": t_url,
            "search_query": f"{t_title} {t_artist}".strip()
        })
    if not tracks:
        return None
    return {
        "type": item_type,
        "title": title,
        "artist": "",
        "thumbnail": thumb,
        "duration": sum(tr["duration"] for tr in tracks),
        "tracks": tracks
    }


def _parse_spotify_og_meta(html: str, item_type: str, item_id: str) -> Optional[dict]:
    og_title_m = re.search(r'<meta\s+property=["\']og:title["\']\s+content=["\']([^"\']+)["\']', html)
    if not og_title_m:
        return None
    og_desc_m = re.search(r'<meta\s+property=["\']og:description["\']\s+content=["\']([^"\']+)["\']', html)
    og_img_m = re.search(r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']+)["\']', html)
    title = og_title_m.group(1).strip()
    artist = ""
    if og_desc_m:
        parts = og_desc_m.group(1).strip().split("·")
        if parts:
            artist = parts[0].strip()
    thumb = og_img_m.group(1).strip() if og_img_m else ""
    return {
        "type": item_type,
        "title": title,
        "artist": artist,
        "thumbnail": thumb,
        "duration": 0,
        "tracks": [{
            "title": title,
            "artist": artist,
            "duration": 0,
            "thumbnail": thumb,
            "url": f"https://open.spotify.com/track/{item_id}",
            "search_query": f"{title} {artist}".strip()
        }]
    }


def parse_spotify_html(html: str, item_type: str, item_id: str) -> Optional[dict]:
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if match:
        try:
            raw_data = json.loads(match.group(1))
            page_props = raw_data.get("props", {}).get("pageProps", {})
            entity = page_props.get("state", {}).get("data", {}).get("entity") or page_props.get("entity") or _search_entity(raw_data)
            if entity:
                if item_type == "track":
                    res = _parse_entity_track(entity, item_id)
                    if res:
                        return res
                elif item_type in ("playlist", "album"):
                    res = _parse_entity_collection(entity, item_type)
                    if res:
                        return res
        except Exception as e:
            logger.warning(f"[Spotify Parse] JSON 解析錯誤: {e}")

    return _parse_spotify_og_meta(html, item_type, item_id)


def _api_track_item(track_obj: dict, fallback_thumb: str = "", fallback_artist: str = "") -> Optional[dict]:
    if not track_obj or not track_obj.get('name'):
        return None
    t_title = track_obj.get('name', '未知標題')
    t_artist = ", ".join(a['name'] for a in track_obj.get('artists', []) if a.get('name')) or fallback_artist
    t_dur = track_obj.get('duration_ms', 0) // 1000
    images = track_obj.get('album', {}).get('images') or []
    t_thumb = (images[0].get('url', '') if images else '') or fallback_thumb
    t_url = track_obj.get('external_urls', {}).get('spotify', f"https://open.spotify.com/track/{track_obj.get('id', '')}")
    return {
        'title': t_title,
        'artist': t_artist,
        'duration': t_dur,
        'thumbnail': t_thumb,
        'url': t_url,
        'search_query': f"{t_title} {t_artist}".strip()
    }


def _resolve_spotify_api_sync(sp: Any, item_type: str, item_id: str) -> Optional[dict]:
    try:
        if item_type == 'track':
            tr = _api_track_item(sp.track(item_id))
            if not tr:
                return None
            return {
                'type': 'track',
                'title': tr['title'],
                'artist': tr['artist'],
                'thumbnail': tr['thumbnail'],
                'duration': tr['duration'],
                'tracks': [tr]
            }
        if item_type == 'playlist':
            pl = sp.playlist(item_id)
            pl_thumb = pl['images'][0].get('url', '') if pl.get('images') else ''
            raw_items = pl.get('tracks', {}).get('items', [])
            tracks = [t for item in raw_items[:200] if (t := _api_track_item(item.get('track') or {}, pl_thumb))]
            return {
                'type': 'playlist',
                'title': pl.get('name', 'Spotify 播放清單'),
                'artist': '',
                'thumbnail': pl_thumb,
                'duration': sum(tr['duration'] for tr in tracks),
                'tracks': tracks
            }
        if item_type == 'album':
            album = sp.album(item_id)
            album_thumb = album['images'][0].get('url', '') if album.get('images') else ''
            album_artist = ", ".join(a['name'] for a in album.get('artists', []) if a.get('name'))
            raw_items = album.get('tracks', {}).get('items', [])
            tracks = [t for obj in raw_items[:200] if (t := _api_track_item(obj or {}, album_thumb, album_artist))]
            return {
                'type': 'album',
                'title': album.get('name', 'Spotify 專輯'),
                'artist': album_artist,
                'thumbnail': album_thumb,
                'duration': sum(tr['duration'] for tr in tracks),
                'tracks': tracks
            }
    except Exception as e:
        logger.warning(f"[Spotify API] 官方 API 解析失敗: {e}，自動切換至爬蟲模式...")
    return None


def _format_ytdl_spotify_result(ytdl_res: dict, item_type: str, url: str) -> Optional[dict]:
    title = ytdl_res.get('title', '')
    artist = ytdl_res.get('artist') or ytdl_res.get('uploader') or ''
    thumb = ytdl_res.get('thumbnail', '')
    dur = ytdl_res.get('duration', 0)
    entries = ytdl_res.get('entries', [])
    if entries:
        tracks = []
        for e in entries:
            if not e:
                continue
            etitle = e.get('title', '')
            eartist = e.get('artist') or e.get('uploader') or ''
            tracks.append({
                'title': etitle,
                'artist': eartist,
                'duration': e.get('duration', 0),
                'thumbnail': e.get('thumbnail') or thumb,
                'url': e.get('webpage_url') or e.get('url') or '',
                'search_query': f"{etitle} {eartist}".strip()
            })
        if tracks:
            return {
                'type': item_type,
                'title': title or 'Spotify 播放清單',
                'artist': artist,
                'thumbnail': thumb,
                'duration': sum(tr['duration'] for tr in tracks),
                'tracks': tracks
            }
    elif title:
        return {
            'type': 'track',
            'title': title,
            'artist': artist,
            'thumbnail': thumb,
            'duration': dur,
            'tracks': [{
                'title': title,
                'artist': artist,
                'duration': dur,
                'thumbnail': thumb,
                'url': url,
                'search_query': f"{title} {artist}".strip()
            }]
        }
    return None


def extract_spotify_ytdl_sync(spotify_url: str) -> Optional[dict]:
    try:
        ydl = get_thread_ytdl(extract_flat=True, noplaylist=True)
        return ydl.extract_info(spotify_url, download=False)
    except Exception:
        return None


async def fetch_spotify_html_async(url: str) -> str:
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
    }
    session = await get_http_session()
    async with session.get(url, headers=headers) as resp:
        return await resp.text(errors='ignore')


async def fetch_spotify_oembed_async(spotify_url: str) -> Optional[dict]:
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'application/json',
    }
    api_url = f"https://open.spotify.com/oembed?url={urllib.parse.quote(spotify_url)}"
    session = await get_http_session()
    async with session.get(api_url, headers=headers) as resp:
        if resp.status == 200:
            return await resp.json(content_type=None)
        return None


async def _try_spotify_oembed_track(url: str, item_id: str) -> Optional[dict]:
    try:
        oembed = await fetch_spotify_oembed_async(url)
        if oembed and oembed.get('title'):
            title = oembed.get('title', '').strip()
            author = oembed.get('author_name', '').strip()
            thumb = oembed.get('thumbnail_url', '')
            return {
                'type': 'track',
                'title': title,
                'artist': author,
                'thumbnail': thumb,
                'duration': 0,
                'tracks': [{
                    'title': title,
                    'artist': author,
                    'duration': 0,
                    'thumbnail': thumb,
                    'url': f"https://open.spotify.com/track/{item_id}",
                    'search_query': f"{title} {author}".strip()
                }]
            }
    except Exception:
        logger.debug("Ignored exception in _try_spotify_oembed_track", exc_info=True)
    return None


async def _try_spotify_embed_scrape(item_type: str, item_id: str) -> Optional[dict]:
    try:
        html = await fetch_spotify_html_async(f"https://open.spotify.com/embed/{item_type}/{item_id}")
        parsed = parse_spotify_html(html, item_type, item_id)
        if parsed and parsed.get('tracks'):
            return parsed
    except Exception:
        logger.debug("Ignored exception in _try_spotify_embed_scrape", exc_info=True)
    return None


async def _try_spotify_ytdl(url: str, item_type: str) -> Optional[dict]:
    try:
        ytdl_res = await run_media(lambda u=url: extract_spotify_ytdl_sync(u))
        if ytdl_res:
            res = _format_ytdl_spotify_result(ytdl_res, item_type, url)
            if res and res.get('tracks'):
                return res
    except Exception:
        logger.debug("Ignored exception in _try_spotify_ytdl", exc_info=True)
    return None


async def resolve_spotify(url: str) -> Optional[dict]:
    cached = get_cached_metadata(url)
    if cached:
        return cached

    m = SPOTIFY_URL_REGEX.search(url)
    if not m:
        return None
    item_type = m.group(1)
    item_id = m.group(2)
    norm_key = f"spotify:{item_type}:{item_id}"
    cached_norm = get_cached_metadata(norm_key)
    if cached_norm:
        return cached_norm

    res: Optional[dict] = None
    sp = get_spotify_client()
    if sp:
        res = await run_media(lambda s=sp, it=item_type, ii=item_id: _resolve_spotify_api_sync(s, it, ii))
        if res and not res.get('tracks'):
            res = None

    if not res and item_type == 'track':
        res = await _try_spotify_oembed_track(url, item_id)
    if not res:
        res = await _try_spotify_embed_scrape(item_type, item_id)
    if not res:
        res = await _try_spotify_ytdl(url, item_type)

    if res and res.get('tracks'):
        set_cached_metadata(url, res)
        set_cached_metadata(norm_key, res)
        return res
    return None
