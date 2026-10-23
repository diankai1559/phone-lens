"""Official spec retrieval.

mi.com product pages are client-rendered, so the spec table only exists after
JS execution. We try a headless browser (optional dependency), cache the result,
and degrade to "unavailable" instead of guessing.
"""

from __future__ import annotations

import glob
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import requests

from . import metrics
from . import zol
from .cache import Cache, retry
from .config import APP_DIR, UA, LLMConfig
from .models import Claim, SourceRef

SPEC_DIR = APP_DIR / "specs"
BING = "https://cn.bing.com/search"
PROD_RE = re.compile(r"https://www\.mi\.com/prod/[a-z0-9\-]+")


@dataclass
class SpecResult:
    model: str
    url: str
    claims: List[Claim] = field(default_factory=list)
    status: str = "unavailable"
    detail: str = ""


def _chromium() -> Optional[str]:
    override = os.environ.get("PHONELENS_CHROMIUM", "").strip()
    if override and Path(override).exists():
        return override
    pattern = os.path.join(
        os.path.expanduser("~"), "AppData", "Local", "ms-playwright",
        "chromium-*", "chrome-win*", "chrome.exe",
    )
    found = sorted(glob.glob(pattern), reverse=True)
    return found[0] if found else None


def discover(family: str, variant: str) -> Optional[str]:
    query = f"site:mi.com {variant or family}"
    cached = Cache().get("bing:" + query, ttl=7 * 86400)
    if cached is not None:
        return cached or None
    try:
        resp = retry(
            lambda: requests.get(
                BING, params={"q": query}, headers={"User-Agent": UA}, timeout=12
            )
        )
    except Exception:  # noqa: BLE001 - discovery failure is a normal outcome
        return None
    candidates = PROD_RE.findall(resp.text)
    tokens = [t for t in re.split(r"[^a-z0-9]+", (variant or family).lower()) if len(t) >= 3]
    scored = []
    for url in dict.fromkeys(candidates):
        slug = url.rsplit("/", 1)[-1]
        score = sum(1 for token in tokens if token in slug)
        scored.append((score, url))
    scored.sort(reverse=True)
    best = scored[0][1] if scored and scored[0][0] >= 2 else None
    Cache().set("bing:" + query, best or "")
    return best


def _render(url: str) -> str:
    exe = _chromium()
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = (
            p.chromium.launch(headless=True, executable_path=exe)
            if exe
            else p.chromium.launch(headless=True)
        )
        page = browser.new_page(user_agent=UA)
        page.goto(url, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(2000)
        text = page.inner_text("body")
        browser.close()
    return text


def _llm_claims(text: str, model_name: str, url: str, llm: LLMConfig) -> List[Claim]:
    if not llm.enabled:
        return []
    system = (
        "你从手机官网参数页抽取规格。只抽明确写出的值，不要推断。"
        "metric 必须来自枚举。quote 必须逐字摘录原文中包含该值的那一行。"
        "只输出 JSON。"
    )
    body = {
        "model": llm.model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": (
                    f"机型: {model_name}\nmetric 枚举: {sorted(metrics.METRICS)}\n"
                    "常见对应关系：处理器/移动平台→soc；6.9 英寸→screen_size；"
                    "显示帧率→screen_refresh；峰值亮度→screen_peak_brightness；"
                    "电池容量 mAh→battery；有线快充 W→charge_power；无线快充 W→charge_wireless_power；"
                    "重量 g→weight；厚度 mm→thickness；主摄 MP→main_camera；"
                    "潜望/长焦 MP→telephoto；超广角→ultrawide；扬声器→speaker；"
                    "USB 版本→usb；IP 等级→ip_rating。\n"
                    '输出: {"claims":[{"metric":"...","value":"...","unit":"...","quote":"原文一行"}]}\n'
                    "尽量覆盖 soc、screen_size、screen_refresh、battery、charge_power、weight、"
                    "thickness、main_camera、telephoto、ultrawide、speaker。\n\n"
                    f"页面正文:\n{text[:16000]}"
                ),
            },
        ],
    }
    resp = requests.post(
        f"{llm.base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {llm.api_key}", "Content-Type": "application/json"},
        json=body,
        timeout=180,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.M).strip()
    raw = json.loads(content).get("claims") or []

    source = SourceRef(
        bvid="",
        title=f"{model_name} 官方参数页",
        uploader="小米官网",
        url=url,
        method="official_specs",
        fetched_at=time.strftime("%Y-%m-%d"),
    )
    claims: List[Claim] = []
    for item in raw:
        metric = metrics.canonical(item.get("metric", ""))
        quote = str(item.get("quote", "")).strip()
        if not metric or not quote or quote.replace(" ", "") not in text.replace(" ", ""):
            continue
        claims.append(
            Claim(
                id="",
                model=model_name,
                metric=metric,
                value=item.get("value", ""),
                unit=str(item.get("unit") or metrics.unit(metric)),
                condition={},
                quote=quote,
                source=source,
                confidence="official",
            )
        )
    return claims


def _rule_claims(text: str, model_name: str, url: str) -> List[Claim]:
    """Deterministic extraction for the stable spec lines; LLM fills the rest."""
    source = SourceRef(
        bvid="", title=f"{model_name} 官方参数页", uploader="小米官网", url=url,
        method="official_specs", fetched_at=time.strftime("%Y-%m-%d"),
    )
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    rules = [
        ("screen_size", re.compile(r"(\d+(?:\.\d+)?)\s*英寸"), "inch", "英寸"),
        ("screen_refresh", re.compile(r"显示帧率[：:]\s*最高\s*(\d+)\s*Hz"), "Hz", "Hz"),
        ("screen_peak_brightness", re.compile(r"峰值亮度[：:]\s*(\d+)\s*nits?"), "nit", "nit"),
        ("battery", re.compile(r"(\d{4,5})\s*mAh"), "mAh", "mAh"),
        ("charge_power", re.compile(r"(\d+)\s*W\s*有线"), "W", "W"),
        ("charge_wireless_power", re.compile(r"(\d+)\s*W\s*无线"), "W", "W"),
        ("weight", re.compile(r"重量[：:]\s*(\d+(?:\.\d+)?)\s*g"), "g", "g"),
        ("thickness", re.compile(r"厚度[：:]\s*(\d+(?:\.\d+)?)\s*mm"), "mm", "mm"),
    ]
    keywords = {
        "main_camera": (("主摄",), ()),
        "telephoto": (("长焦",), ("超广角",)),
        "ultrawide": (("超广角",), ()),
        "speaker": (("立体声", "扬声器"), ()),
        "usb": (("USB 3", "USB3", "USB 2", "USB2"), ()),
    }
    claims: List[Claim] = []
    seen = set()

    for metric, pattern, unit, _hint in rules:
        for line in lines:
            match = pattern.search(line)
            if not match:
                continue
            if metric in seen:
                break
            value = match.group(1)
            claims.append(Claim("", model_name, metric, value, unit, {}, line, source, "official"))
            seen.add(metric)
            break

    for line in lines:
        if len(line) >= 8 and any(word in line for word in ("移动平台", "骁龙", "天玑", "麒麟")):
            claims.append(Claim("", model_name, "soc", line, "", {}, line, source, "official"))
            seen.add("soc")
            break

    for metric, (words, excludes) in keywords.items():
        if metric in seen:
            continue
        for line in lines:
            if len(line) > 60 or not any(word in line for word in words):
                continue
            if any(word in line for word in excludes):
                continue
            if metric == "telephoto" and "MP" not in line:
                continue
            if metric == "usb" and not re.search(r"USB\s?\d", line):
                continue
            value = line
            if metric in ("main_camera", "telephoto", "ultrawide"):
                match = re.search(r"(\d+\s*MP[^｜|]*?" + ("主摄" if metric == "main_camera" else "长焦" if metric == "telephoto" else "超广角") + ")", line)
                if match:
                    value = match.group(1)
            claims.append(Claim("", model_name, metric, value, "", {}, line, source, "official"))
            seen.add(metric)
            break

    ip = re.search(r"IP(\d{2})", text)
    if ip and "ip_rating" not in seen:
        quote = next((ln for ln in lines if "IP" + ip.group(1) in ln), "IP" + ip.group(1))
        claims.append(Claim("", model_name, "ip_rating", f"IP{ip.group(1)}", "", {}, quote, source, "official"))
    return claims


def _slug_for(model_name: str) -> str:
    name = model_name.lower()
    for zh, en in (("至尊版", "ultra"), ("标准版", ""), ("青春版", "youth"), ("探索版", "explorer")):
        name = name.replace(zh, en)
    return re.sub(r"[^a-z0-9]+", "-", name).strip("-")


def _mentions(text: str, family: str) -> bool:
    token = re.search(r"[a-z]?\d{2,4}", family.lower())
    if not token:
        return True
    squashed = re.sub(r"\s+", "", text.lower())
    return token.group(0) in squashed


def _direct_url(model_name: str) -> Optional[str]:
    if not model_name.lower().startswith(("redmi", "xiaomi")):
        return None
    slug = _slug_for(model_name)
    url = f"https://www.mi.com/prod/{slug}"
    try:
        resp = requests.get(url, headers={"User-Agent": UA}, timeout=10)
    except Exception:  # noqa: BLE001 - fall through to search
        return None
    if resp.status_code == 200 and len(resp.text) > 50000:
        return url
    return None


SIBLINGS = ["Pro", "Pro Max", "Ultra", "至尊版"]
CACHE_VERSION = 2


def _one(family: str, model_name: str, llm: LLMConfig) -> SpecResult:
    SPEC_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = SPEC_DIR / f"{_slug_for(model_name)}.json"
    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            claims = _from_dicts(data.get("claims") or [])
            if claims and data.get("v") == CACHE_VERSION:
                return SpecResult(model_name, data.get("url", ""), claims, data.get("status", "ok"), "cache")
        except (OSError, json.JSONDecodeError, KeyError):
            pass

    url = _direct_url(model_name) or discover(family, model_name)
    spec_url = url.rstrip("/") + "/specs" if url else ""
    claims: List[Claim] = []
    status, detail = "unavailable", "未找到参数页（可能未发布）"

    if url and _slug_matches(url, model_name):
        try:
            text = _render(spec_url)
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            status, detail = "render_failed", str(exc)[:120]
        else:
            if _mentions(text, family):
                merged = _rule_claims(text, model_name, spec_url)
                have = {c.metric for c in merged}
                merged.extend([c for c in _llm_claims(text, model_name, spec_url, llm) if c.metric not in have])
                if merged:
                    claims, status, detail = merged, "ok", f"{len(merged)} 项"

    if not claims:
        db = zol.fetch(model_name)
        if db:
            claims, status, detail = db, "ok_db", f"{len(db)} 项（参数库）"
            spec_url = db[0].source.url

    if not claims:
        return SpecResult(model_name, spec_url, [], status, detail)

    payload = {
        "v": CACHE_VERSION,
        "url": spec_url,
        "status": status,
        "fetched_at": time.strftime("%Y-%m-%d"),
        "claims": [c.to_dict() for c in claims],
    }
    cache_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return SpecResult(model_name, spec_url, claims, status, detail)


def fetch(family: str, variants: List[str], llm: LLMConfig) -> List[SpecResult]:
    models = [f"{family} {v}".strip() if v not in ("", "标准版") else family for v in variants] or [family]
    results: List[SpecResult] = []
    for model_name in models:
        result = _one(family, model_name, llm)
        if not result.claims and model_name == family:
            for sibling in SIBLINGS:
                alt = _one(family, f"{family} {sibling}", llm)
                if alt.claims:
                    alt.detail = f"{family} 参数未找到，改用同系列 {alt.model}"
                    result = alt
                    break
        results.append(result)
    return results


def _slug_matches(url: str, model_name: str) -> bool:
    slug = url.rstrip("/").rsplit("/", 1)[-1].lower()
    lowered = model_name.lower()
    if "pro max" in lowered:
        return slug.endswith("-pro-max")
    if "ultra" in lowered:
        return slug.endswith("-ultra")
    if "pro" in lowered:
        return slug.endswith("-pro")
    if "air" in lowered:
        return slug.endswith("-air")
    return not slug.endswith(("-pro", "-pro-max", "-ultra", "-air"))


def _page_matches(text: str, model_name: str) -> bool:
    head = text[:5000].lower()
    lowered = model_name.lower()
    if "pro max" in lowered:
        return "pro max" in head
    if "ultra" in lowered:
        return "ultra" in head
    if "pro" in lowered:
        return "pro" in head and "pro max" not in head
    if "air" in lowered:
        return "air" in head
    return "标准版" in head or "标准版本" in head


def _from_dicts(items: List[dict]) -> List[Claim]:
    out = []
    for raw in items:
        source = SourceRef(**raw["source"])
        out.append(
            Claim(
                id="",
                model=raw["model"],
                metric=raw["metric"],
                value=raw["value"],
                unit=raw.get("unit", ""),
                condition=raw.get("condition") or {},
                quote=raw.get("quote", ""),
                source=source,
                confidence=raw.get("confidence", "official"),
            )
        )
    return out
