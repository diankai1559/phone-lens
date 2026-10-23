"""Source registry and health probe.

Probing is parallel-ish and non-blocking: results only shape the collection
strategy. A dead source degrades the report, it never aborts the run.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, List

import requests

from .config import UA, load_asr_key, load_llm
from .models import SourceStatus

OFFICIAL_HOSTS = {
    "official_xiaomi": "https://www.mi.com/",
    "ecommerce_jd": "https://item.jd.com/100012043978.html",
}


def _timed(name: str, fn: Callable[[], tuple]) -> SourceStatus:
    start = time.time()
    try:
        ok, detail = fn()
    except Exception as exc:  # noqa: BLE001 - health probe must not raise
        ok, detail = False, str(exc)
    return SourceStatus(
        name=name,
        status="ok" if ok else "blocked",
        detail=detail,
        latency_ms=int((time.time() - start) * 1000),
    )


def _bili(client) -> tuple:
    return client.health()


def _http(host: str) -> Callable[[], tuple]:
    def probe() -> tuple:
        resp = requests.get(host, headers={"User-Agent": UA}, timeout=5)
        return resp.status_code < 500, f"HTTP {resp.status_code}"

    return probe


def probe_all(client) -> List[SourceStatus]:
    checks = [
        ("bilibili", lambda: _bili(client)),
        ("official_xiaomi", _http(OFFICIAL_HOSTS["official_xiaomi"])),
        ("ecommerce_jd", _http(OFFICIAL_HOSTS["ecommerce_jd"])),
    ]
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(_timed, name, fn) for name, fn in checks]
        statuses = [f.result() for f in futures]

    llm = load_llm()
    statuses.append(
        SourceStatus(
            name="llm_extractor",
            status="ok" if llm.enabled else "need_key",
            detail=llm.model if llm.enabled else "未配置 DEEPSEEK_API_KEY / SILICONFLOW_API_KEY",
        )
    )
    statuses.append(
        SourceStatus(
            name="asr_fallback",
            status="ok" if load_asr_key() else "need_key",
            detail="SiliconFlow SenseVoice" if load_asr_key() else "未配置 SILICONFLOW_API_KEY",
        )
    )
    return statuses


def strategy(statuses: List[SourceStatus]) -> dict:
    table = {s.name: s.status for s in statuses}
    return {
        "subtitle": table.get("bilibili") == "ok",
        "asr": table.get("asr_fallback") == "ok",
        "llm": table.get("llm_extractor") == "ok",
    }
