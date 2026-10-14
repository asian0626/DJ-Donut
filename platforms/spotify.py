import os
import re
import json
import ssl
import urllib.request
import urllib.parse
import asyncio
from typing import Optional, List, Dict, Any
from config import (
    MEDIA_EXECUTOR,
    logger,
    get_thread_ytdl,
    get_cached_metadata,
    set_cached_metadata,
    get_http_session
)

SPOTIFY_URL_REGEX = re.compile(
    r'(?:https?://(?:open\.)?spotify\.com/(?:intl-[a-zA-Z-]+/)?|spotify:)(track|playlist|album|artist)[:/]([a-zA-Z0-9]+)'
)

_spotify_client = None

def get_spotify_client():
    global _spotify_client
    if _spotify_client is not None:
        return _spotify_client
    cid = os.getenv("SPOTIFY_CLIENT_ID")
    csecret = os.getenv("SPOTIFY_CLIENT_SECRET")
    if cid and csecret and cid != "YOUR_SPOTIFY_CLIENT_ID" and csecret != "YOUR_SPOTIFY_CLIENT_SECRET":
        try:
            import spotipy
            from spotipy.oauth2 import SpotifyClientCredentials
            try:
                from spotipy.cache_handler import MemoryCacheHandler
                cache_handler = MemoryCacheHandler()
            except Exception:
                cache_handler = None

            if cache_handler:
                auth_mgr = SpotifyClientCredentials(
                    client_id=cid.strip(),
                    client_secret=csecret.strip(),
                    cache_handler=cache_handler
                )
            else:
                auth_mgr = SpotifyClientCredentials(
                    client_id=cid.strip(),
                    client_secret=csecret.strip()
                )
            _spotify_client = spotipy.Spotify(auth_manager=auth_mgr)
            print("[Spotify API] 成功初始化官方 Spotify API 客戶端！")
        except ImportError:
            pass
        except Exception as e:
            print(f"[Spotify API] 初始化官方客戶端失敗: {e}")
    return _spotify_client

def parse_spotify_html(html: str, item_type: str, item_id: str) -> Optional[dict]:
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if match:
        try:
            raw_data = json.loads(match.group(1))
            page_props = raw_data.get("props", {}).get("pageProps", {})
            entity = page_props.get("state", {}).get("data", {}).get("entity") or page_props.get("entity", {})

            if not entity:
                def search_entity(d):
                    if isinstance(d, dict):
                        if "trackList" in d or ("name" in d and ("artists" in d or "subtitle" in d)):
                            return d
                        for k, v in d.items():
                            res = search_entity(v)
                            if res:
                                return res
                    elif isinstance(d, list):
                        for item in d:
                            res = search_entity(item)
                            if res:
                                return res
                    return None
                entity = search_entity(raw_data) or {}

            if entity:
                if item_type == "track":
                    title = entity.get("name") or entity.get("title") or ""
                    artists = entity.get("artists", [])
                    if isinstance(artists, list) and artists:
                        artist_names = [a.get("name") for a in artists if isinstance(a, dict) and a.get("name")]
                        artist = ", ".join(artist_names)
                    else:
                        artist = entity.get("subtitle") or ""

                    duration_ms = entity.get("duration") or 0
                    duration = int(duration_ms) // 1000 if duration_ms else 0

                    cover_sources = entity.get("coverArt", {}).get("sources", [])
                    thumb = cover_sources[0].get("url") if cover_sources else ""

                    if title:
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
                elif item_type in ("playlist", "album"):
                    title = entity.get("name") or entity.get("title") or ("Spotify 播放清單" if item_type == "playlist" else "Spotify 專輯")
                    cover_sources = entity.get("coverArt", {}).get("sources", [])
                    thumb = cover_sources[0].get("url") if cover_sources else ""

                    track_list = entity.get("trackList") or []
                    if not track_list:
                        track_list = entity.get("tracks", {}).get("items", [])

                    tracks = []
                    for item in track_list:
                        t_title = item.get("title") or item.get("name")
                        if not t_title:
                            continue
                        t_artist = item.get("subtitle") or ""
                        if not t_artist and isinstance(item.get("artists"), list):
                            t_artist = ", ".join(a.get("name") for a in item.get("artists") if isinstance(a, dict) and a.get("name"))
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

                    if tracks:
                        return {
                            "type": item_type,
                            "title": title,
                            "artist": "",
                            "thumbnail": thumb,
                            "duration": sum(tr["duration"] for tr in tracks),
                            "tracks": tracks
                        }
        except Exception as e:
            print(f"[Spotify Parse] JSON 解析錯誤: {e}")

    og_title_m = re.search(r'<meta\s+property=["\']og:title["\']\s+content=["\']([^"\']+)["\']', html)
    og_desc_m = re.search(r'<meta\s+property=["\']og:description["\']\s+content=["\']([^"\']+)["\']', html)
    og_img_m = re.search(r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']+)["\']', html)

    if og_title_m:
        title = og_title_m.group(1).strip()
        artist = ""
        if og_desc_m:
            desc = og_desc_m.group(1).strip()
            parts = desc.split("·")
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

    return None

def fetch_spotify_html_sync(url: str) -> str:
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
    }
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, context=ctx, timeout=8) as response:
        return response.read().decode('utf-8', errors='ignore')

def fetch_spotify_oembed_sync(spotify_url: str) -> Optional[dict]:
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'application/json',
    }
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    api_url = f"https://open.spotify.com/oembed?url={urllib.parse.quote(spotify_url)}"
    req = urllib.request.Request(api_url, headers=headers)
    with urllib.request.urlopen(req, context=ctx, timeout=8) as response:
        return json.loads(response.read().decode('utf-8', errors='ignore'))

def _resolve_spotify_api_sync(sp, item_type: str, item_id: str) -> Optional[dict]:
    try:
        if item_type == 'track':
            track_obj = sp.track(item_id)
            artists = ", ".join(a['name'] for a in track_obj.get('artists', []) if a.get('name'))
            duration = track_obj.get('duration_ms', 0) // 1000
            thumb = ''
            if track_obj.get('album', {}).get('images'):
                thumb = track_obj['album']['images'][0].get('url', '')
            title = track_obj.get('name', '未知標題')
            spotify_url = track_obj.get('external_urls', {}).get('spotify', f"https://open.spotify.com/track/{item_id}")
            return {
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
                    'url': spotify_url,
                    'search_query': f"{title} {artists}".strip()
                }]
            }
        elif item_type == 'playlist':
            pl = sp.playlist(item_id)
            pl_title = pl.get('name', 'Spotify 播放清單')
            pl_thumb = pl['images'][0].get('url', '') if pl.get('images') else ''
            tracks = []
            items = pl.get('tracks', {}).get('items', [])
            for item in items[:200]:
                track_obj = item.get('track')
                if not track_obj or not track_obj.get('name'):
                    continue
                t_title = track_obj.get('name')
                t_artist = ", ".join(a['name'] for a in track_obj.get('artists', []) if a.get('name'))
                t_dur = track_obj.get('duration_ms', 0) // 1000
                t_thumb = ''
                if track_obj.get('album', {}).get('images'):
                    t_thumb = track_obj['album']['images'][0].get('url', '')
                t_url = track_obj.get('external_urls', {}).get('spotify', f"https://open.spotify.com/track/{track_obj.get('id', '')}")
                tracks.append({
                    'title': t_title,
                    'artist': t_artist,
                    'duration': t_dur,
                    'thumbnail': t_thumb or pl_thumb,
                    'url': t_url,
                    'search_query': f"{t_title} {t_artist}".strip()
                })
            return {
                'type': 'playlist',
                'title': pl_title,
                'artist': '',
                'thumbnail': pl_thumb,
                'duration': sum(tr['duration'] for tr in tracks),
                'tracks': tracks
            }
        elif item_type == 'album':
            album = sp.album(item_id)
            album_title = album.get('name', 'Spotify 專輯')
            album_thumb = album['images'][0].get('url', '') if album.get('images') else ''
            album_artist = ", ".join(a['name'] for a in album.get('artists', []) if a.get('name'))
            tracks = []
            items = album.get('tracks', {}).get('items', [])
            for track_obj in items[:200]:
                if not track_obj or not track_obj.get('name'):
                    continue
                t_title = track_obj.get('name')
                t_artist = ", ".join(a['name'] for a in track_obj.get('artists', []) if a.get('name')) or album_artist
                t_dur = track_obj.get('duration_ms', 0) // 1000
                t_url = track_obj.get('external_urls', {}).get('spotify', f"https://open.spotify.com/track/{track_obj.get('id', '')}")
                tracks.append({
                    'title': t_title,
                    'artist': t_artist,
                    'duration': t_dur,
                    'thumbnail': album_thumb,
                    'url': t_url,
                    'search_query': f"{t_title} {t_artist}".strip()
                })
            return {
                'type': 'album',
                'title': album_title,
                'artist': album_artist,
                'thumbnail': album_thumb,
                'duration': sum(tr['duration'] for tr in tracks),
                'tracks': tracks
            }
    except Exception as e:
        print(f"[Spotify API] 官方 API 解析失敗: {e}，自動切換至爬蟲模式...")
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

def resolve_spotify_sync(url: str) -> Optional[dict]:
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

    sp = get_spotify_client()
    if sp:
        res = _resolve_spotify_api_sync(sp, item_type, item_id)
        if res and res.get('tracks'):
            set_cached_metadata(url, res)
            set_cached_metadata(norm_key, res)
            return res

    if item_type == 'track':
        try:
            oembed_data = fetch_spotify_oembed_sync(f"https://open.spotify.com/track/{item_id}")
            if oembed_data and oembed_data.get('title'):
                title = oembed_data.get('title', '')
                thumb = oembed_data.get('thumbnail_url', '')
                author = oembed_data.get('author_name', '')
                res = {
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
                set_cached_metadata(url, res)
                set_cached_metadata(norm_key, res)
                return res
        except Exception as e:
            print(f"[Spotify oEmbed] oEmbed 解析失敗: {e}")

    embed_url = f"https://open.spotify.com/embed/{item_type}/{item_id}"
    try:
        html = fetch_spotify_html_sync(embed_url)
        parsed = parse_spotify_html(html, item_type, item_id)
        if parsed and parsed.get('tracks'):
            set_cached_metadata(url, parsed)
            set_cached_metadata(norm_key, parsed)
            return parsed
    except Exception as e:
        print(f"[Spotify Embed] Embed 爬蟲解析失敗: {e}")

    try:
        ytdl_res = extract_spotify_ytdl_sync(url)
        if ytdl_res:
            res = _format_ytdl_spotify_result(ytdl_res, item_type, url)
            if res and res.get('tracks'):
                set_cached_metadata(url, res)
                set_cached_metadata(norm_key, res)
                return res
    except Exception as e:
        print(f"[Spotify yt-dlp] yt-dlp flat 解析失敗: {e}")

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

    sp = get_spotify_client()
    if sp:
        loop = asyncio.get_event_loop()
        try:
            sp_res = await loop.run_in_executor(MEDIA_EXECUTOR, lambda: _resolve_spotify_api_sync(sp, item_type, item_id))
            if sp_res and sp_res.get('tracks'):
                set_cached_metadata(url, sp_res)
                set_cached_metadata(norm_key, sp_res)
                return sp_res
        except Exception:
            pass

    if item_type == 'track':
        try:
            oembed_data = await fetch_spotify_oembed_async(f"https://open.spotify.com/track/{item_id}")
            if oembed_data and oembed_data.get('title'):
                title = oembed_data.get('title', '')
                thumb = oembed_data.get('thumbnail_url', '')
                author = oembed_data.get('author_name', '')
                res = {
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
                set_cached_metadata(url, res)
                set_cached_metadata(norm_key, res)
                return res
        except Exception:
            pass

    embed_url = f"https://open.spotify.com/embed/{item_type}/{item_id}"
    try:
        html = await fetch_spotify_html_async(embed_url)
        parsed = parse_spotify_html(html, item_type, item_id)
        if parsed and parsed.get('tracks'):
            set_cached_metadata(url, parsed)
            set_cached_metadata(norm_key, parsed)
            return parsed
    except Exception:
        pass

    loop = asyncio.get_event_loop()
    try:
        ytdl_res = await loop.run_in_executor(MEDIA_EXECUTOR, lambda: extract_spotify_ytdl_sync(url))
        if ytdl_res:
            res = _format_ytdl_spotify_result(ytdl_res, item_type, url)
            if res and res.get('tracks'):
                set_cached_metadata(url, res)
                set_cached_metadata(norm_key, res)
                return res
    except Exception:
        pass

    return None

