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

APPLE_MUSIC_URL_REGEX = re.compile(
    r'(?:https?://)?music\.apple\.com/(?:([a-z]{2})/)?(album|playlist|song)/([^/?#]+)(?:/(\d+|pl\.[a-zA-Z0-9]+))?(?:\?.*?\bi=(\d+))?',
    re.IGNORECASE
)

def format_apple_artwork(art_obj) -> str:
    if not art_obj:
        return ''
    raw_url = art_obj.get('dictionary', {}).get('url') if isinstance(art_obj, dict) else ''
    if not raw_url and isinstance(art_obj, dict):
        raw_url = art_obj.get('url', '')
    if raw_url:
        return raw_url.replace('{w}', '600').replace('{h}', '600').replace('{f}', 'jpg')
    return ''

def resolve_apple_music_sync(url: str) -> Optional[dict]:
    cached = get_cached_metadata(url)
    if cached:
        return cached

    if "apple.co" in url:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'})
            ctx = ssl._create_unverified_context()
            with urllib.request.urlopen(req, context=ctx, timeout=5) as resp:
                url = resp.geturl()
        except Exception:
            pass
    m = APPLE_MUSIC_URL_REGEX.search(url)
    if not m:
        return None
    country = m.group(1) or 'us'
    item_type = m.group(2).lower()
    slug_or_id = m.group(3)
    id_part = m.group(4)
    track_id_param = m.group(5)
    norm_key = f"apple:{country}:{item_type}:{track_id_param or id_part or slug_or_id}"
    cached_norm = get_cached_metadata(norm_key)
    if cached_norm:
        return cached_norm

    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    ctx = ssl._create_unverified_context()
    if track_id_param:
        try:
            api_url = f"https://itunes.apple.com/lookup?id={track_id_param}&country={country}"
            req = urllib.request.Request(api_url, headers=headers)
            with urllib.request.urlopen(req, context=ctx, timeout=8) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            results = data.get('results', [])
            if results:
                r = results[0]
                title = r.get('trackName', '未知歌曲')
                artist = r.get('artistName', '')
                duration = (r.get('trackTimeMillis', 0) or 0) // 1000
                thumb = r.get('artworkUrl100', '').replace('100x100bb', '600x600bb')
                res = {
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
                        'url': r.get('trackViewUrl', url),
                        'search_query': f"{title} {artist}".strip()
                    }]
                }
                set_cached_metadata(url, res)
                set_cached_metadata(norm_key, res)
                return res
        except Exception as e:
            print(f"[Apple Music] 單曲參數解析異常: {e}")

    if item_type == 'song':
        song_id = id_part if (id_part and id_part.isdigit()) else (slug_or_id if slug_or_id.isdigit() else None)
        if song_id:
            try:
                api_url = f"https://itunes.apple.com/lookup?id={song_id}&country={country}"
                req = urllib.request.Request(api_url, headers=headers)
                with urllib.request.urlopen(req, context=ctx, timeout=8) as resp:
                    data = json.loads(resp.read().decode('utf-8'))
                results = data.get('results', [])
                if results:
                    r = results[0]
                    title = r.get('trackName', '未知歌曲')
                    artist = r.get('artistName', '')
                    duration = (r.get('trackTimeMillis', 0) or 0) // 1000
                    thumb = r.get('artworkUrl100', '').replace('100x100bb', '600x600bb')
                    res = {
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
                            'url': r.get('trackViewUrl', url),
                            'search_query': f"{title} {artist}".strip()
                        }]
                    }
                    set_cached_metadata(url, res)
                    set_cached_metadata(norm_key, res)
                    return res
            except Exception as e:
                print(f"[Apple Music] 單曲解析異常: {e}")

    if item_type == 'album':
        album_id = id_part if (id_part and id_part.isdigit()) else (slug_or_id if slug_or_id.isdigit() else None)
        if album_id:
            try:
                api_url = f"https://itunes.apple.com/lookup?id={album_id}&entity=song&country={country}"
                req = urllib.request.Request(api_url, headers=headers)
                with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
                    data = json.loads(resp.read().decode('utf-8'))
                results = data.get('results', [])
                if results:
                    col = results[0]
                    col_name = col.get('collectionName', 'Apple Music 專輯')
                    col_artist = col.get('artistName', '')
                    col_thumb = col.get('artworkUrl100', '').replace('100x100bb', '600x600bb')
                    song_results = [r for r in results if r.get('wrapperType') == 'track']
                    tracks = []
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
                            'url': s.get('trackViewUrl', url),
                            'search_query': f"{t_title} {t_artist}".strip()
                        })
                    res = {
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
                    set_cached_metadata(url, res)
                    set_cached_metadata(norm_key, res)
                    return res
            except Exception as e:
                print(f"[Apple Music] 專輯解析異常: {e}")

    if item_type == 'playlist':
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
                html = resp.read().decode('utf-8')
            m_json = re.search(r'<script type=\"application/json\" id=\"serialized-server-data\">(.*?)</script>', html, re.DOTALL)
            if m_json:
                parsed = json.loads(m_json.group(1))
                pdata = parsed if isinstance(parsed, dict) else (parsed[0] if parsed else {})
                data_list = pdata.get('data', [])
                if data_list:
                    inner_data = data_list[0].get('data', {})
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
                    tracks = []
                    for it in items[:200]:
                        t_title = it.get('title')
                        if not t_title:
                            continue
                        t_artist = it.get('artistName', '')
                        t_dur = (it.get('duration', 0) or 0) // 1000
                        t_thumb = format_apple_artwork(it.get('artwork')) or pl_thumb
                        t_url = it.get('contentDescriptor', {}).get('url') or url
                        tracks.append({
                            'title': t_title,
                            'artist': t_artist,
                            'duration': t_dur,
                            'thumbnail': t_thumb,
                            'url': t_url,
                            'search_query': f"{t_title} {t_artist}".strip()
                        })
                    if tracks:
                        res = {
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
                        set_cached_metadata(url, res)
                        set_cached_metadata(norm_key, res)
                        return res
        except Exception as e:
            print(f"[Apple Music] 歌單解析異常: {e}")

    return None

async def resolve_apple_music(url: str) -> Optional[dict]:
    cached = get_cached_metadata(url)
    if cached:
        return cached

    session = await get_http_session()
    resolved_url = url
    if "apple.co" in url:
        try:
            headers_redirect = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'}
            async with session.get(url, headers=headers_redirect, allow_redirects=True) as resp:
                resolved_url = str(resp.url)
        except Exception:
            resolved_url = url

    m = APPLE_MUSIC_URL_REGEX.search(resolved_url)
    if not m:
        return None
    country = m.group(1) or 'us'
    item_type = m.group(2).lower()
    slug_or_id = m.group(3)
    id_part = m.group(4)
    track_id_param = m.group(5)
    norm_key = f"apple:{country}:{item_type}:{track_id_param or id_part or slug_or_id}"
    cached_norm = get_cached_metadata(norm_key)
    if cached_norm:
        return cached_norm

    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }

    if track_id_param:
        try:
            api_url = f"https://itunes.apple.com/lookup?id={track_id_param}&country={country}"
            async with session.get(api_url, headers=headers) as resp:
                data = await resp.json(content_type=None)
            results = data.get('results', [])
            if results:
                r = results[0]
                title = r.get('trackName', '未知歌曲')
                artist = r.get('artistName', '')
                duration = (r.get('trackTimeMillis', 0) or 0) // 1000
                thumb = r.get('artworkUrl100', '').replace('100x100bb', '600x600bb')
                res = {
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
                set_cached_metadata(url, res)
                set_cached_metadata(norm_key, res)
                return res
        except Exception as e:
            print(f"[Apple Music] 單曲參數解析異常: {e}")

    if item_type == 'song':
        song_id = id_part if (id_part and id_part.isdigit()) else (slug_or_id if slug_or_id.isdigit() else None)
        if song_id:
            try:
                api_url = f"https://itunes.apple.com/lookup?id={song_id}&country={country}"
                async with session.get(api_url, headers=headers) as resp:
                    data = await resp.json(content_type=None)
                results = data.get('results', [])
                if results:
                    r = results[0]
                    title = r.get('trackName', '未知歌曲')
                    artist = r.get('artistName', '')
                    duration = (r.get('trackTimeMillis', 0) or 0) // 1000
                    thumb = r.get('artworkUrl100', '').replace('100x100bb', '600x600bb')
                    res = {
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
                    set_cached_metadata(url, res)
                    set_cached_metadata(norm_key, res)
                    return res
            except Exception as e:
                print(f"[Apple Music] 單曲解析異常: {e}")

    if item_type == 'album':
        album_id = id_part if (id_part and id_part.isdigit()) else (slug_or_id if slug_or_id.isdigit() else None)
        if album_id:
            try:
                api_url = f"https://itunes.apple.com/lookup?id={album_id}&entity=song&country={country}"
                async with session.get(api_url, headers=headers) as resp:
                    data = await resp.json(content_type=None)
                results = data.get('results', [])
                if results:
                    col = results[0]
                    col_name = col.get('collectionName', 'Apple Music 專輯')
                    col_artist = col.get('artistName', '')
                    col_thumb = col.get('artworkUrl100', '').replace('100x100bb', '600x600bb')
                    song_results = [r for r in results if r.get('wrapperType') == 'track']
                    tracks = []
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
                    res = {
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
                    set_cached_metadata(url, res)
                    set_cached_metadata(norm_key, res)
                    return res
            except Exception as e:
                print(f"[Apple Music] 專輯解析異常: {e}")

    if item_type == 'playlist':
        try:
            async with session.get(resolved_url, headers=headers) as resp:
                html = await resp.text(errors='ignore')
            m_json = re.search(r'<script type=\"application/json\" id=\"serialized-server-data\">(.*?)</script>', html, re.DOTALL)
            if m_json:
                parsed = json.loads(m_json.group(1))
                pdata = parsed if isinstance(parsed, dict) else (parsed[0] if parsed else {})
                data_list = pdata.get('data', [])
                if data_list:
                    inner_data = data_list[0].get('data', {})
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
                    tracks = []
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
                    if tracks:
                        res = {
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
                        set_cached_metadata(url, res)
                        set_cached_metadata(norm_key, res)
                        return res
        except Exception as e:
            print(f"[Apple Music] 歌單解析異常: {e}")

    return None
