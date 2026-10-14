import re
import json
import ssl
import urllib.request
import asyncio
from typing import Optional, List, Dict, Any
from config import (
    MEDIA_EXECUTOR,
    logger,
    get_http_session,
    get_cached_metadata,
    set_cached_metadata
)

NETEASE_URL_REGEX = re.compile(
    r'(?:https?://)?(?:[a-zA-Z0-9-]+\.)?(?:music\.163\.com|163cn\.tv|163\.lu)/.*?(\b(?:song|playlist|album)\b).*?(?:id=|\/)(\d+)',
    re.IGNORECASE
)

def resolve_netease_sync(url: str) -> Optional[dict]:
    cached = get_cached_metadata(url)
    if cached:
        return cached

    if "163cn.tv" in url or "163.lu" in url or "/s/" in url:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
            ctx = ssl._create_unverified_context()
            with urllib.request.urlopen(req, context=ctx, timeout=5) as resp:
                url = resp.geturl()
        except Exception:
            pass
    m = NETEASE_URL_REGEX.search(url)
    if not m:
        return None
    item_type = m.group(1).lower()
    item_id = m.group(2)
    norm_key = f"netease:{item_type}:{item_id}"
    cached_norm = get_cached_metadata(norm_key)
    if cached_norm:
        return cached_norm

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://music.163.com/'
    }
    ctx = ssl._create_unverified_context()
    if item_type == 'song':
        try:
            api_url = f"https://music.163.com/api/song/detail/?id={item_id}&ids=[{item_id}]"
            req = urllib.request.Request(api_url, headers=headers)
            with urllib.request.urlopen(req, context=ctx, timeout=8) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            songs = data.get('songs', [])
            if not songs:
                return None
            s = songs[0]
            title = s.get('name', '未知歌曲')
            artists = ", ".join(a.get('name', '') for a in s.get('artists', []) if a.get('name'))
            duration = (s.get('duration', 0) or 0) // 1000
            thumb = s.get('album', {}).get('picUrl', '')
            song_url = f"https://music.163.com/#/song?id={item_id}"
            res = {
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
            set_cached_metadata(url, res)
            set_cached_metadata(norm_key, res)
            return res
        except Exception as e:
            print(f"[Netease] 單曲解析異常: {e}")
            return None
    elif item_type == 'playlist':
        try:
            api_url = f"https://music.163.com/api/v1/playlist/detail?id={item_id}"
            req = urllib.request.Request(api_url, headers=headers)
            with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            pl = data.get('playlist', {})
            pl_name = pl.get('name', '網易雲歌單')
            pl_thumb = pl.get('coverImgUrl', '')
            raw_tracks = pl.get('tracks', [])
            tracks = []
            for t_item in raw_tracks[:200]:
                title = t_item.get('name', '未知歌曲')
                artist_list = t_item.get('ar') or t_item.get('artists') or []
                artists = ", ".join(a.get('name', '') for a in artist_list if a.get('name'))
                duration = (t_item.get('dt', 0) or t_item.get('duration', 0) or 0) // 1000
                thumb = t_item.get('al', {}).get('picUrl') or pl_thumb
                tid = t_item.get('id')
                song_url = f"https://music.163.com/#/song?id={tid}" if tid else url
                tracks.append({
                    'title': title,
                    'artist': artists,
                    'duration': duration,
                    'thumbnail': thumb,
                    'url': song_url,
                    'search_query': f"{title} {artists}".strip()
                })
            res = {
                'platform': 'netease',
                'platform_name': '網易雲音樂',
                'color': 0xC20C0C,
                'type': 'playlist',
                'title': pl_name,
                'artist': pl.get('creator', {}).get('nickname', ''),
                'thumbnail': pl_thumb,
                'duration': sum(t['duration'] for t in tracks),
                'tracks': tracks
            }
            set_cached_metadata(url, res)
            set_cached_metadata(norm_key, res)
            return res
        except Exception as e:
            print(f"[Netease] 歌單解析異常: {e}")
            return None
    elif item_type == 'album':
        try:
            api_url = f"https://music.163.com/api/v1/album/{item_id}"
            req = urllib.request.Request(api_url, headers=headers)
            with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            album = data.get('album', {})
            album_name = album.get('name', '網易雲專輯')
            album_thumb = album.get('picUrl', '')
            raw_songs = data.get('songs', [])
            tracks = []
            for t_item in raw_songs[:200]:
                title = t_item.get('name', '未知歌曲')
                artist_list = t_item.get('ar') or t_item.get('artists') or []
                artists = ", ".join(a.get('name', '') for a in artist_list if a.get('name'))
                duration = (t_item.get('dt', 0) or t_item.get('duration', 0) or 0) // 1000
                thumb = album_thumb
                tid = t_item.get('id')
                song_url = f"https://music.163.com/#/song?id={tid}" if tid else url
                tracks.append({
                    'title': title,
                    'artist': artists,
                    'duration': duration,
                    'thumbnail': thumb,
                    'url': song_url,
                    'search_query': f"{title} {artists}".strip()
                })
            res = {
                'platform': 'netease',
                'platform_name': '網易雲音樂',
                'color': 0xC20C0C,
                'type': 'album',
                'title': album_name,
                'artist': album.get('artist', {}).get('name', ''),
                'thumbnail': album_thumb,
                'duration': sum(t['duration'] for t in tracks),
                'tracks': tracks
            }
            set_cached_metadata(url, res)
            set_cached_metadata(norm_key, res)
            return res
        except Exception as e:
            print(f"[Netease] 專輯解析異常: {e}")
            return None
    return None

async def resolve_netease(url: str) -> Optional[dict]:
    cached = get_cached_metadata(url)
    if cached:
        return cached

    session = await get_http_session()
    resolved_url = url
    if "163cn.tv" in url or "163.lu" in url or "/s/" in url:
        try:
            headers_redirect = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            async with session.get(url, headers=headers_redirect, allow_redirects=True) as resp:
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

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://music.163.com/'
    }

    if item_type == 'song':
        try:
            api_url = f"https://music.163.com/api/song/detail/?id={item_id}&ids=[{item_id}]"
            async with session.get(api_url, headers=headers) as resp:
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
            res = {
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
            set_cached_metadata(url, res)
            set_cached_metadata(norm_key, res)
            return res
        except Exception as e:
            print(f"[Netease] 單曲解析異常: {e}")
            return None
    elif item_type == 'playlist':
        try:
            api_url = f"https://music.163.com/api/v1/playlist/detail?id={item_id}"
            async with session.get(api_url, headers=headers) as resp:
                data = await resp.json(content_type=None)
            pl = data.get('playlist', {})
            pl_name = pl.get('name', '網易雲歌單')
            pl_thumb = pl.get('coverImgUrl', '')
            raw_tracks = pl.get('tracks', [])
            tracks = []
            for t_item in raw_tracks[:200]:
                title = t_item.get('name', '未知歌曲')
                artist_list = t_item.get('ar') or t_item.get('artists') or []
                artists = ", ".join(a.get('name', '') for a in artist_list if a.get('name'))
                duration = (t_item.get('dt', 0) or t_item.get('duration', 0) or 0) // 1000
                thumb = t_item.get('al', {}).get('picUrl') or pl_thumb
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
            res = {
                'platform': 'netease',
                'platform_name': '網易雲音樂',
                'color': 0xC20C0C,
                'type': 'playlist',
                'title': pl_name,
                'artist': pl.get('creator', {}).get('nickname', ''),
                'thumbnail': pl_thumb,
                'duration': sum(t['duration'] for t in tracks),
                'tracks': tracks
            }
            set_cached_metadata(url, res)
            set_cached_metadata(norm_key, res)
            return res
        except Exception as e:
            print(f"[Netease] 歌單解析異常: {e}")
            return None
    elif item_type == 'album':
        try:
            api_url = f"https://music.163.com/api/v1/album/{item_id}"
            async with session.get(api_url, headers=headers) as resp:
                data = await resp.json(content_type=None)
            album = data.get('album', {})
            album_name = album.get('name', '網易雲專輯')
            album_thumb = album.get('picUrl', '')
            raw_songs = data.get('songs', [])
            tracks = []
            for t_item in raw_songs[:200]:
                title = t_item.get('name', '未知歌曲')
                artist_list = t_item.get('ar') or t_item.get('artists') or []
                artists = ", ".join(a.get('name', '') for a in artist_list if a.get('name'))
                duration = (t_item.get('dt', 0) or t_item.get('duration', 0) or 0) // 1000
                thumb = album_thumb
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
            res = {
                'platform': 'netease',
                'platform_name': '網易雲音樂',
                'color': 0xC20C0C,
                'type': 'album',
                'title': album_name,
                'artist': album.get('artist', {}).get('name', ''),
                'thumbnail': album_thumb,
                'duration': sum(t['duration'] for t in tracks),
                'tracks': tracks
            }
            set_cached_metadata(url, res)
            set_cached_metadata(norm_key, res)
            return res
        except Exception as e:
            print(f"[Netease] 專輯解析異常: {e}")
            return None
    return None
