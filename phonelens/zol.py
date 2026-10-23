"""Spec-database fallback (ZOL) for models whose official page is gone.

Older phones drop off mi.com; this keeps the spec baseline alive while labeling
the source honestly as a parameter database, not the vendor page.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import List, Optional

import requests

from .config import UA
from .models import Claim, SourceRef

SEARCH = "https://search.zol.com.cn/s/all.php?kword="
DETAIL = "https://detail.zol.com.cn/%s/%s/param.shtml"

LABELS = {
    "电池容量": ("battery", "mAh"),
    "屏幕尺寸": ("screen_size", "inch"),
    "屏幕刷新率": ("screen_refresh", "Hz"),
    "机身重量": ("weight", "g"),
    "厚度": ("thickness", "mm"),
    "CPU型号": ("soc", ""),
    "处理器": ("soc", ""),
    "有线充电": ("charge_power", "W"),
    "无线充电": ("charge_wireless_power", "W"),
    "后置摄像头": ("main_camera", ""),
    "扬声器": ("speaker", ""),
    "防水防尘": ("ip_rating", ""),
    "USB接口": ("usb", ""),
}


def _search(model_name: str) -> Optional[str]:
    try:
        encoded = urllib.parse.quote(model_name.encode("gbk"))
    except UnicodeEncodeError:
        return None
    try:
        resp = requests.get(SEARCH + encoded, headers={"User-Agent": UA}, timeout=12)
    except Exception:  # noqa: BLE001 - fallback source, failure is expected
        return None
    ids = re.findall(r"detail\.zol\.com\.cn/(\d+)/(\d+)/", resp.text)
    if not ids:
        return None
    return DETAIL % ids[0]


def _lines(html: str) -> List[str]:
    text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", html)
    text = re.sub(r"<[^>]+>", "\n", text)
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    return [ln for ln in lines if ln]


def fetch(model_name: str) -> List[Claim]:
    url = _search(model_name)
    if not url:
        return []
    try:
        resp = requests.get(url, headers={"User-Agent": UA}, timeout=15)
    except Exception:  # noqa: BLE001
        return []
    resp.encoding = "gbk"
    lines = _lines(resp.text)
    source = SourceRef("", f"{model_name} 参数库", "ZOL", url, "spec_db")
    claims: List[Claim] = []
    seen = set()
    for index, line in enumerate(lines[:-1]):
        if line not in LABELS:
            continue
        metric, unit = LABELS[line]
        if metric in seen:
            continue
        value = lines[index + 1]
        if not value or len(value) > 60 or value == line:
            continue
        claims.append(Claim("", model_name, metric, value, unit, {}, value, source, "spec_db"))
        seen.add(metric)
    return claims


def cite(url: str) -> str:
    return f"[参数库]({url})" if url else "参数库"
