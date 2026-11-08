"""輕量指標與健康檢查端點 (無需額外相依套件)。

提供：
- 執行緒安全計數器 / 延遲統計 (``record_extraction`` 等)
- ``/healthz``  存活檢查 (至少一隻機器人 ready 才回 200)
- ``/metrics``  Prometheus 文字格式
- ``/status``   JSON：各節點狀態、活躍房間與隊列長度

預設僅綁定 127.0.0.1；``METRICS_PORT=0`` 可關閉。
"""
from __future__ import annotations

import functools
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

from aiohttp import web

from settings import METRICS_HOST, METRICS_PORT, __version__, logger

_lock = threading.Lock()
_counters: Dict[str, float] = {
    "extractions_total": 0,
    "extraction_failures_total": 0,
    "extraction_seconds_sum": 0.0,
    "play_failures_total": 0,
    "songs_played_total": 0,
}
_STARTED_AT = time.time()


def inc(name: str, amount: float = 1) -> None:
    with _lock:
        _counters[name] = _counters.get(name, 0) + amount


def snapshot_counters() -> Dict[str, float]:
    with _lock:
        return dict(_counters)


def record_extraction(duration: float, success: bool) -> None:
    with _lock:
        _counters["extractions_total"] += 1
        _counters["extraction_seconds_sum"] += duration
        if not success:
            _counters["extraction_failures_total"] += 1


def timed_extraction(func: Callable[..., Any]) -> Callable[..., Any]:
    """裝飾 ``extract_stream_url_sync``：記錄耗時與成功與否 (回傳首元素為串流 URL)。"""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        start = time.monotonic()
        result = None
        try:
            result = func(*args, **kwargs)
            return result
        finally:
            ok = bool(result and result[0])
            record_extraction(time.monotonic() - start, ok)

    return wrapper


def _node_status(bot: Any) -> Dict[str, Any]:
    states = getattr(bot, "states", {}) or {}
    active = [s for s in states.values() if getattr(s, "is_playing", False)]
    return {
        "node_id": getattr(bot, "node_id", None),
        "name": getattr(bot, "node_name", None),
        "ready": bool(bot.is_ready()) if hasattr(bot, "is_ready") else False,
        "lavalink": bool(getattr(bot, "lavalink_connected", False)),
        "guilds": len(getattr(bot, "guilds", []) or []),
        "active_rooms": len(active),
        "queued_songs": sum(len(getattr(s, "queue", [])) for s in states.values()),
    }


def build_status(bots: Sequence[Any]) -> Dict[str, Any]:
    nodes = [_node_status(b) for b in bots]
    return {
        "version": __version__,
        "uptime_seconds": int(time.time() - _STARTED_AT),
        "nodes": nodes,
        "active_rooms": sum(n["active_rooms"] for n in nodes),
        "queued_songs": sum(n["queued_songs"] for n in nodes),
    }


def render_prometheus(bots: Sequence[Any]) -> str:
    c = snapshot_counters()
    status = build_status(bots)
    lines: List[str] = []

    def metric(name: str, mtype: str, help_text: str, value: float, labels: str = "") -> None:
        lines.append(f"# HELP donut_{name} {help_text}")
        lines.append(f"# TYPE donut_{name} {mtype}")
        lines.append(f"donut_{name}{labels} {value}")

    metric("uptime_seconds", "gauge", "Process uptime", status["uptime_seconds"])
    metric("active_rooms", "gauge", "Voice rooms currently playing", status["active_rooms"])
    metric("queued_songs", "gauge", "Songs waiting in all queues", status["queued_songs"])
    metric("extractions_total", "counter", "Stream extraction attempts", c["extractions_total"])
    metric("extraction_failures_total", "counter", "Failed stream extractions", c["extraction_failures_total"])
    metric("extraction_seconds_sum", "counter", "Total seconds spent extracting streams", round(c["extraction_seconds_sum"], 3))
    metric("play_failures_total", "counter", "Playback failures", c["play_failures_total"])
    metric("songs_played_total", "counter", "Songs started", c["songs_played_total"])
    lines.append("# HELP donut_node_ready Whether a bot node is ready")
    lines.append("# TYPE donut_node_ready gauge")
    lines.extend(
        f'donut_node_ready{{node="{n["node_id"]}"}} {1 if n["ready"] else 0}'
        for n in status["nodes"]
    )
    return "\n".join(lines) + "\n"


def create_app(get_bots: Callable[[], Sequence[Any]]) -> web.Application:
    async def healthz(_: web.Request) -> web.Response:
        bots = get_bots()
        ready = any(_node_status(b)["ready"] for b in bots)
        return web.json_response({"ok": ready}, status=200 if ready else 503)

    async def metrics(_: web.Request) -> web.Response:
        return web.Response(text=render_prometheus(get_bots()), content_type="text/plain")

    async def status(_: web.Request) -> web.Response:
        return web.json_response(build_status(get_bots()))

    app = web.Application()
    app.router.add_get("/healthz", healthz)
    app.router.add_get("/metrics", metrics)
    app.router.add_get("/status", status)
    return app


async def start_metrics_server(get_bots: Callable[[], Sequence[Any]]) -> Optional[web.AppRunner]:
    if not METRICS_PORT:
        return None
    runner = web.AppRunner(create_app(get_bots), access_log=None)
    await runner.setup()
    try:
        await web.TCPSite(runner, METRICS_HOST, METRICS_PORT).start()
    except OSError as e:
        logger.warning(f"Metrics server failed to start on {METRICS_HOST}:{METRICS_PORT}: {e}")
        await runner.cleanup()
        return None
    logger.info(f"Metrics/health endpoint listening on http://{METRICS_HOST}:{METRICS_PORT} (/healthz /metrics /status)")
    return runner
