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


BRAND_ALIASES = {
    "xiaomi": ["小米"],
    "redmi": ["红米"],
    "apple": ["苹果"],
    "honor": ["荣耀"],
    "huawei": ["华为"],
    "oneplus": ["一加"],
    "realme": ["真我"],
    "samsung": ["三星"],
    "iqoo": ["爱酷"],
}

TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S)


def _candidates(model_name: str) -> List[str]:
    try:
        encoded = urllib.parse.quote(model_name.encode("gbk"))
    except UnicodeEncodeError:
        return []
    try:
        resp = requests.get(SEARCH + encoded, headers={"User-Agent": UA}, timeout=12)
    except Exception:  # noqa: BLE001 - fallback source, failure is expected
        return []
    ids = list(dict.fromkeys(re.findall(r"detail\.zol\.com\.cn/(\d+)/(\d+)/", resp.text)))
    return [DETAIL % pair for pair in ids[:5]]


def _squash(text: str) -> str:
    return re.sub(r"[\s\-_·（）()]+", "", str(text)).lower()


def _title_matches(title: str, model_name: str) -> bool:
    squashed_title = _squash(title)
    variants = [model_name]
    lowered = model_name.lower()
    for brand, aliases in BRAND_ALIASES.items():
        if lowered.startswith(brand):
            rest = model_name[len(brand):].strip()
            variants.extend(f"{alias} {rest}" for alias in aliases)
            break
    return any(_squash(v) in squashed_title for v in variants)


def _detail(url: str) -> Optional[str]:
    try:
        resp = requests.get(url, headers={"User-Agent": UA}, timeout=15)
    except Exception:  # noqa: BLE001
        return None
    resp.encoding = "gbk"
    return resp.text


def fetch(model_name: str) -> List[Claim]:
    for url in _candidates(model_name):
        html = _detail(url)
        if not html:
            continue
        title_match = TITLE_RE.search(html)
        title = title_match.group(1).strip() if title_match else ""
        if not _title_matches(title, model_name):
            continue

        lines = _lines(html)
        source = SourceRef("", title or f"{model_name} 参数库", "ZOL", url, "spec_db")
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
        if claims:
            return claims
    return []


def cite(url: str) -> str:
    return f"[参数库]({url})" if url else "参数库"
