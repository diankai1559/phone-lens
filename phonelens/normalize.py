"""Value parsing and unit normalisation.

Numbers arrive as "9070mAh", "9h56m", "1小时18分钟", "119.9fps", "4.61GHz".
Everything is converted to a canonical unit before comparison.
"""

from __future__ import annotations

import re
from typing import Any, Optional, Tuple

NUMBER_RE = re.compile(r"(\d+(?:\.\d+)?)")

DURATION_UNITS = {
    "小时": 60.0, "h": 60.0, "hr": 60.0, "hour": 60.0,
    "分钟": 1.0, "分": 1.0, "min": 1.0, "mins": 1.0, "m": 1.0,
}


CN_DIGITS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def cn_to_int(text: str) -> int:
    if "十" not in text:
        return CN_DIGITS.get(text, 0) if len(text) == 1 else 0
    head, _, tail = text.partition("十")
    tens = CN_DIGITS.get(head, 1) if head else 1
    ones = CN_DIGITS.get(tail, 0) if tail else 0
    return tens * 10 + ones


def replace_cn_numbers(text: str) -> str:
    def repl(match: re.Match) -> str:
        value = cn_to_int(match.group(1))
        return f"{value}{match.group(2)}" if value else match.group(0)

    return re.sub(r"([一二两三四五六七八九十]{1,3})\s*个?\s*(小时|分钟|分)", repl, text)


def parse_duration(text: str) -> Optional[float]:
    """Return minutes, supporting '9小时56分', '九小时五十六分', '9h56m', '73分钟'."""
    original = str(text or "").strip().lower()
    partial = re.search(r"(\d+(?:\.\d+)?)\s*分半", original)
    if partial:
        return float(partial.group(1)) + 0.5
    half = re.search(r"([\d一二两三四五六七八九十]+)\s*个?半\s*小时", original)
    if half:
        head = half.group(1)
        base = int(head) if head.isdigit() else cn_to_int(head)
        if base:
            return base * 60 + 30
    raw = replace_cn_numbers(original)
    raw = raw.replace("半个小时", "30分钟").replace("个半小时", "个30分钟").replace("半", "30分")
    if not raw:
        return None
    pairs = re.findall(r"(\d+(?:\.\d+)?)\s*个?\s*(小时|分钟|分|hr|h|mins|min|m)", raw)
    if not pairs:
        if "小时" in raw:
            num = NUMBER_RE.search(raw)
            return float(num.group(1)) * 60 if num else None
        return None
    total = 0.0
    for num, unit in pairs:
        total += float(num) * DURATION_UNITS.get(unit, 1.0)
    return round(total, 2)


def parse_quantity(value: Any, unit_hint: str = "") -> Tuple[Optional[float], str]:
    if isinstance(value, (int, float)):
        return float(value), unit_hint
    raw = str(value or "").strip()
    if not raw:
        return None, unit_hint
    raw_lower = raw.lower()

    if unit_hint in ("min", "h"):
        minutes = parse_duration(raw)
        if minutes is not None:
            value = minutes if unit_hint == "min" else round(minutes / 60.0, 2)
            return value, unit_hint

    match = NUMBER_RE.search(raw_lower)
    if not match:
        return None, unit_hint
    number = float(match.group(1))

    if "mah" in raw_lower:
        return number, "mAh"
    if "wh" in raw_lower:
        return number, "Wh"
    if "nit" in raw_lower or "尼特" in raw:
        return number, "nit"
    if "ghz" in raw_lower:
        return number, "GHz"
    if "hz" in raw_lower:
        return number, "Hz"
    if "mm" in raw_lower:
        return number, "mm"
    if re.search(r"\bw\b", raw_lower) or "瓦" in raw:
        return number, "W"
    if "fps" in raw_lower or "帧" in raw:
        return number, "fps"
    if "°c" in raw_lower or "℃" in raw or "度" in raw:
        return number, "C"
    if re.search(r"\bg\b", raw_lower) or "克" in raw:
        return number, "g"
    if "元" in raw or "cny" in raw_lower:
        return number, "CNY"
    if unit_hint == "h":
        minutes = parse_duration(raw)
        if minutes is not None:
            return round(minutes / 60.0, 2), "h"
    return number, unit_hint


def to_hours(minutes: float) -> float:
    return round(minutes / 60.0, 2)


def condition_signature(condition: dict, keys: list) -> str:
    parts = []
    for key in keys:
        value = condition.get(key)
        if value in (None, "", 0):
            parts.append(f"{key}=?")
        else:
            parts.append(f"{key}={value}")
    return "|".join(parts)
