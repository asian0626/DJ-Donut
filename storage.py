"""Persistent storage for guild settings, user favorites and queue snapshots.

Design goals
------------
* **Reads never touch disk.** The whole data set is small, so it lives in
  memory and every getter is a dict lookup (safe to call from async code).
* **Writes never block the event loop.** Mutations only mark rows dirty; a
  single daemon thread coalesces them (debounce) and writes just the changed
  rows to SQLite in one transaction. Call :func:`close_storage` on shutdown
  to flush synchronously.
* **Crash-safe.** SQLite (WAL) replaces the old "rewrite the whole JSON file
  on every change" approach. A legacy ``guild_settings.json`` is imported
  automatically on first run.
"""
import copy
import json
import logging
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from settings import DB_FILE, LEGACY_SETTINGS_FILE

logger = logging.getLogger("DJDonut.storage")

MAX_FAVORITES = 100
FLUSH_DEBOUNCE_SECONDS = 1.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id TEXT NOT NULL,
    key      TEXT NOT NULL,
    value    TEXT NOT NULL,
    PRIMARY KEY (guild_id, key)
);
CREATE TABLE IF NOT EXISTS favorites (
    user_id TEXT PRIMARY KEY,
    songs   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS queue_snapshots (
    guild_id   TEXT    NOT NULL,
    node_id    INTEGER NOT NULL,
    data       TEXT    NOT NULL,
    updated_at REAL    NOT NULL,
    PRIMARY KEY (guild_id, node_id)
);
"""

# A dirty marker is (kind, ident). ``ident`` is a str, or (guild_id, node_id) for queues.
_Dirty = Tuple[str, Any]


class Storage:
    def __init__(self, db_path: str, debounce: float = FLUSH_DEBOUNCE_SECONDS):
        self.db_path = db_path
        self.debounce = debounce
        self._lock = threading.RLock()      # guards the in-memory data
        self._db_lock = threading.Lock()    # serializes sqlite access
        self._guilds: Dict[str, Dict[str, Any]] = {}
        self._favorites: Dict[str, List[dict]] = {}
        self._queues: Dict[Tuple[str, int], dict] = {}
        self._dirty: Set[_Dirty] = set()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._conn: Optional[sqlite3.Connection] = None
        self._load()

    # ------------------------------------------------------------------ db
    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            parent = os.path.dirname(self.db_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=10)
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
            except sqlite3.Error:
                logger.debug("Could not enable WAL", exc_info=True)
            conn.executescript(_SCHEMA)
            conn.commit()
            self._conn = conn
        return self._conn

    def _load(self) -> None:
        with self._db_lock:
            conn = self._connect()
            for gid, key, raw in conn.execute("SELECT guild_id, key, value FROM guild_settings"):
                try:
                    self._guilds.setdefault(gid, {})[key] = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("Dropping corrupt setting %s/%s", gid, key)
            for uid, raw in conn.execute("SELECT user_id, songs FROM favorites"):
                try:
                    songs = json.loads(raw)
                    if isinstance(songs, list):
                        self._favorites[uid] = songs
                except json.JSONDecodeError:
                    logger.warning("Dropping corrupt favorites for user %s", uid)
            for gid, node_id, raw in conn.execute("SELECT guild_id, node_id, data FROM queue_snapshots"):
                try:
                    self._queues[(gid, int(node_id))] = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("Dropping corrupt queue snapshot %s/%s", gid, node_id)
        if not self._guilds and not self._favorites:
            self._import_legacy_json()

    def _apply_legacy_dict(self, legacy: Dict[str, Any]) -> None:
        with self._lock:
            for key, val in legacy.items():
                if key in ("_favorites", "_user_favorites") and isinstance(val, dict):
                    for uid, songs in val.items():
                        if isinstance(songs, list):
                            self._favorites[str(uid)] = songs
                            self._dirty.add(("fav", str(uid)))
                elif isinstance(val, dict):
                    self._guilds[str(key)] = val
                    self._dirty.add(("guild", str(key)))

    def _import_legacy_json(self) -> None:
        for path in (os.path.join(os.path.dirname(self.db_path), "guild_settings.json"), LEGACY_SETTINGS_FILE):
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    legacy = json.load(fh)
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("Could not import legacy settings %s: %s", path, exc)
                continue
            if not isinstance(legacy, dict):
                continue
            self._apply_legacy_dict(legacy)
            self.flush()
            try:
                os.replace(path, path + ".migrated")
            except OSError:
                logger.debug("Could not rename legacy file %s", path, exc_info=True)
            logger.info("Imported legacy settings from %s into SQLite", path)
            return

    # ------------------------------------------------------------- flushing
    def _mark_dirty(self, kind: str, ident: Any) -> None:
        self._dirty.add((kind, ident))
        self._ensure_thread()
        self._wake.set()

    def _ensure_thread(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._writer_loop, name="storage_writer", daemon=True)
            self._thread.start()

    def _writer_loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait()
            if self._stop.is_set():
                break
            time.sleep(self.debounce)   # coalesce bursts of writes
            self._wake.clear()
            try:
                self.flush()
            except Exception:
                logger.exception("Storage flush failed")

    @staticmethod
    def _write_snapshot_row(conn: sqlite3.Connection, kind: str, ident: Any, data: Any) -> None:
        if kind == "guild":
            conn.execute("DELETE FROM guild_settings WHERE guild_id=?", (ident,))
            for key, val in (data or {}).items():
                conn.execute(
                    "INSERT INTO guild_settings(guild_id, key, value) VALUES (?,?,?)",
                    (ident, key, json.dumps(val, ensure_ascii=False)),
                )
        elif kind == "fav":
            if data is None:
                conn.execute("DELETE FROM favorites WHERE user_id=?", (ident,))
            else:
                conn.execute(
                    "INSERT OR REPLACE INTO favorites(user_id, songs) VALUES (?,?)",
                    (ident, json.dumps(data, ensure_ascii=False)),
                )
        elif kind == "queue":
            gid, node_id = ident
            if data is None:
                conn.execute("DELETE FROM queue_snapshots WHERE guild_id=? AND node_id=?", (gid, node_id))
            else:
                conn.execute(
                    "INSERT OR REPLACE INTO queue_snapshots(guild_id, node_id, data, updated_at) VALUES (?,?,?,?)",
                    (gid, node_id, json.dumps(data, ensure_ascii=False), time.time()),
                )

    def flush(self) -> None:
        """Synchronously persist everything that changed since the last flush."""
        with self._lock:
            dirty, self._dirty = self._dirty, set()
            snapshot: Dict[_Dirty, Any] = {}
            for kind, ident in dirty:
                if kind == "guild":
                    snapshot[(kind, ident)] = copy.deepcopy(self._guilds.get(ident))
                elif kind == "fav":
                    snapshot[(kind, ident)] = copy.deepcopy(self._favorites.get(ident))
                elif kind == "queue":
                    snapshot[(kind, ident)] = copy.deepcopy(self._queues.get(ident))
        if not snapshot:
            return
        try:
            with self._db_lock:
                conn = self._connect()
                with conn:
                    for (kind, ident), data in snapshot.items():
                        self._write_snapshot_row(conn, kind, ident, data)
        except sqlite3.Error:
            logger.exception("SQLite write failed; will retry")
            with self._lock:
                self._dirty |= dirty

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=5)
        self.flush()
        with self._db_lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None

    # --------------------------------------------------------- guild settings
    def all_guild_settings(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {gid: dict(cfg) for gid, cfg in self._guilds.items()}

    def get_guild_setting(self, guild_id: int, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._guilds.get(str(guild_id), {}).get(key, default)

    def save_guild_setting(self, guild_id: int, key: str, value: Any) -> None:
        gid = str(guild_id)
        with self._lock:
            self._guilds.setdefault(gid, {})[key] = value
            self._mark_dirty("guild", gid)

    # -------------------------------------------------------------- favorites
    def get_user_favorites(self, user_id: int) -> List[dict]:
        with self._lock:
            return list(self._favorites.get(str(user_id), []))

    def add_user_favorite(self, user_id: int, song_item: dict) -> Tuple[bool, int]:
        uid = str(user_id)
        new_title = (song_item.get("title") or "").strip().lower()
        new_url = (song_item.get("url") or "").strip()
        with self._lock:
            songs = self._favorites.setdefault(uid, [])
            for existing in songs:
                if not isinstance(existing, dict):
                    continue
                ex_url = (existing.get("url") or "").strip()
                ex_title = (existing.get("title") or "").strip().lower()
                if (new_url and ex_url == new_url) or (new_title and ex_title == new_title):
                    return False, len(songs)
            if len(songs) >= MAX_FAVORITES:
                songs.pop(0)
            songs.append(song_item)
            self._mark_dirty("fav", uid)
            return True, len(songs)

    def remove_user_favorite(self, user_id: int, index: int) -> Optional[dict]:
        uid = str(user_id)
        with self._lock:
            songs = self._favorites.get(uid)
            if not songs or index < 1 or index > len(songs):
                return None
            removed = songs.pop(index - 1)
            self._mark_dirty("fav", uid)
            return removed

    def clear_user_favorites(self, user_id: int) -> int:
        uid = str(user_id)
        with self._lock:
            songs = self._favorites.get(uid)
            if not songs:
                return 0
            count = len(songs)
            self._favorites[uid] = []
            self._mark_dirty("fav", uid)
            return count

    # ---------------------------------------------------------- queue snapshots
    def save_queue_snapshot(self, guild_id: int, node_id: int, data: dict) -> None:
        key = (str(guild_id), int(node_id))
        with self._lock:
            if self._queues.get(key) == data:
                return
            self._queues[key] = data
            self._mark_dirty("queue", key)

    def delete_queue_snapshot(self, guild_id: int, node_id: int) -> None:
        key = (str(guild_id), int(node_id))
        with self._lock:
            if key in self._queues:
                del self._queues[key]
                self._mark_dirty("queue", key)

    def load_queue_snapshots(self, node_id: int) -> Dict[int, dict]:
        with self._lock:
            return {int(gid): copy.deepcopy(data) for (gid, nid), data in self._queues.items() if nid == int(node_id)}


_store: Optional[Storage] = None
_store_lock = threading.Lock()


def init_storage(db_path: Optional[str] = None) -> Storage:
    """(Re)create the global store. Tests pass a temp path."""
    global _store
    with _store_lock:
        if _store is not None:
            _store.close()
        _store = Storage(db_path or DB_FILE)
        return _store


def get_store() -> Storage:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = Storage(DB_FILE)
    return _store


def flush_storage() -> None:
    if _store is not None:
        _store.flush()


def close_storage() -> None:
    global _store
    with _store_lock:
        if _store is not None:
            _store.close()
            _store = None


# ---- module-level API (kept signature-compatible with the old config.py) ----
def load_all_guild_settings() -> Dict[str, Dict[str, Any]]:
    return get_store().all_guild_settings()


load_guild_settings = load_all_guild_settings


def get_guild_setting(guild_id: int, key: str, default: Any = None) -> Any:
    return get_store().get_guild_setting(guild_id, key, default)


def save_guild_setting(guild_id: int, key: str, value: Any) -> None:
    get_store().save_guild_setting(guild_id, key, value)


def get_user_favorites(user_id: int) -> List[dict]:
    return get_store().get_user_favorites(user_id)


def add_user_favorite(user_id: int, song_item: dict) -> Tuple[bool, int]:
    return get_store().add_user_favorite(user_id, song_item)


def remove_user_favorite(user_id: int, index: int) -> Optional[dict]:
    return get_store().remove_user_favorite(user_id, index)


def clear_user_favorites(user_id: int) -> int:
    return get_store().clear_user_favorites(user_id)


def save_queue_snapshot(guild_id: int, node_id: int, data: dict) -> None:
    get_store().save_queue_snapshot(guild_id, node_id, data)


def delete_queue_snapshot(guild_id: int, node_id: int) -> None:
    get_store().delete_queue_snapshot(guild_id, node_id)


def load_queue_snapshots(node_id: int) -> Dict[int, dict]:
    return get_store().load_queue_snapshots(node_id)
