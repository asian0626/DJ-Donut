import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any
import discord

logger = logging.getLogger("DJDonut.i18n")

SUPPORTED_LANGUAGES = {
    'en': 'English 🇺🇸',
    'zh-TW': '繁體中文 🇹🇼',
    'zh-CN': '简体中文 🇨🇳',
    'ja': '日本語 🇯🇵',
    'ko': '한국어 🇰🇷',
    'es': 'Español 🇪🇸'
}

DEFAULT_LANGUAGE = 'zh-TW'

GUILD_LANGUAGES: Dict[int, str] = {}

LOCALES_DIR = Path(__file__).resolve().parent / "locales"


def _load_translations() -> Dict[str, Dict[str, str]]:
    """Load ``locales/<lang>.json`` and invert it into ``{key: {lang: text}}``.

    Keeping translations in data files (instead of Python source) lets the
    community contribute a language without touching code.
    """
    merged: Dict[str, Dict[str, str]] = {}
    for lang in SUPPORTED_LANGUAGES:
        path = LOCALES_DIR / f"{lang}.json"
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            logger.warning("Locale file missing: %s", path)
            continue
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("Failed to load locale %s: %s", path, exc)
            continue
        for key, text in data.items():
            merged.setdefault(key, {})[lang] = text
    return merged


TRANSLATIONS: Dict[str, Dict[str, str]] = _load_translations()


def resolve_locale(
    interaction: Optional[discord.Interaction] = None,
    guild_id: Optional[int] = None,
    preferred_lang: Optional[str] = None
) -> str:
    if preferred_lang and preferred_lang in SUPPORTED_LANGUAGES:
        return preferred_lang

    gid = guild_id or (interaction.guild_id if interaction else None)
    if gid and gid in GUILD_LANGUAGES:
        return GUILD_LANGUAGES[gid]

    if interaction and hasattr(interaction, 'locale') and interaction.locale:
        loc_str = str(interaction.locale).lower()
        if 'zh-tw' in loc_str or 'zh-hk' in loc_str or 'zh-mo' in loc_str:
            return 'zh-TW'
        if 'zh' in loc_str:
            return 'zh-CN'
        if 'ja' in loc_str:
            return 'ja'
        if 'ko' in loc_str:
            return 'ko'
        if 'es' in loc_str:
            return 'es'
        if 'en' in loc_str:
            return 'en'

    return DEFAULT_LANGUAGE


def t(
    key: str,
    interaction: Optional[discord.Interaction] = None,
    guild_id: Optional[int] = None,
    lang: Optional[str] = None,
    **kwargs: Any
) -> str:
    target_lang = resolve_locale(interaction=interaction, guild_id=guild_id, preferred_lang=lang)
    entry = TRANSLATIONS.get(key)
    if not entry:
        return key

    text = entry.get(target_lang) or entry.get('en') or entry.get(DEFAULT_LANGUAGE) or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            return text
    return text


def init_guild_languages():
    try:
        from config import load_all_guild_settings
        settings = load_all_guild_settings()
        for gid_str, cfg in settings.items():
            lang_val = cfg.get("lang") or cfg.get("language") if isinstance(cfg, dict) else None
            if lang_val:
                try:
                    GUILD_LANGUAGES[int(gid_str)] = lang_val
                except (ValueError, TypeError):
                    logger.debug("Ignored exception in init_guild_languages", exc_info=True)
    except Exception:
        logger.debug("Ignored exception in init_guild_languages", exc_info=True)


init_guild_languages()
