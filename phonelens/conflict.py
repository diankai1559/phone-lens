"""Conflict detection.

Two claims only conflict if they are actually comparable. In practice most
"contradictions" in phone reviews are condition mismatches (brightness, network,
test duration), so this module separates the two cases instead of averaging.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Dict, List, Tuple

from . import metrics
from .models import Claim, Conflict
from .normalize import condition_signature, parse_quantity, to_hours

THRESHOLDS: Dict[str, float] = {
    "price": 0.10,
    "battery": 0.03,
    "charge_time": 0.10,
    "battery_life": 0.25,
    "fps": 0.05,
    "power": 0.15,
    "temp": 0.04,
    "screen_brightness": 0.10,
}

TEXT_METRICS = {"soc"}

COMPARABLE_KEYS = {"brightness_nit", "network", "mode", "from_soc", "to_soc", "with_case"}


def _setting_keys_differ(claims: List[Claim], keys: List) -> bool:
    """Only call it a conflict when a comparability *setting* differs.

    Different games / resolutions / measurement points are simply different
    rows, not a contradiction, so they never reach the conflict section.
    """
    values: Dict[str, set] = defaultdict(set)
    unknown = 0
    for claim in claims:
        if not claim.condition:
            unknown += 1
        for key in keys:
            values[key].add(str(claim.condition.get(key, "?")))
    if unknown and len(claims) > 1:
        return True
    return any(key in COMPARABLE_KEYS and len(values[key]) > 1 for key in keys)


STABLE_METRICS = {"battery", "price", "weight", "charge_power", "screen_size", "screen_refresh"}

PLAUSIBLE = {
    "battery_life": (2.0, 24.0),
    "fps": (10.0, 240.0),
    "temp": (20.0, 60.0),
    "power": (0.3, 30.0),
    "battery": (1000.0, 12000.0),
    "weight": (80.0, 400.0),
    "charge_time": (5.0, 240.0),
    "price": (199.0, 20000.0),
}


def _sanity(claims: List[Claim]) -> None:
    for claim in claims:
        bounds = PLAUSIBLE.get(claim.metric)
        if not bounds or not isinstance(claim.value, (int, float)):
            continue
        low, high = bounds
        if not (low <= float(claim.value) <= high):
            claim.confidence = "low"
            claim.note = claim.note or f"数值超出合理范围 {low:g}–{high:g}"


def _mark_outliers(claims: List[Claim]) -> None:
    """Flag values that are far off the median: usually a transcription error."""
    import statistics

    for metric in STABLE_METRICS:
        numbers = [
            c for c in claims
            if c.metric == metric and isinstance(c.value, (int, float))
        ]
        if len(numbers) < 3:
            continue
        median = statistics.median(float(c.value) for c in numbers)
        if not median:
            continue
        for claim in numbers:
            if abs(float(claim.value) - median) / median > 0.5:
                claim.confidence = "low"
                claim.note = claim.note or "偏离中位数，疑似转写异常"


def normalize_claim(claim: Claim) -> Claim:
    metric = claim.metric
    if metrics.kind(metric) != "number":
        claim.value = str(claim.value).strip()
        return claim
    number, unit = parse_quantity(claim.value, metrics.unit(metric) or claim.unit)
    if metric == "battery_life" and unit in ("min", "分钟"):
        number, unit = to_hours(number or 0.0), "h"
    claim.value = number if number is not None else claim.value
    claim.unit = unit or claim.unit or metrics.unit(metric)
    return claim


def _text_key(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def detect(claims: List[Claim]) -> Tuple[List[Claim], List[Conflict]]:
    claims = [normalize_claim(c) for c in claims]
    _mark_outliers(claims)
    _sanity(claims)
    buckets: Dict[Tuple[str, str], List[Claim]] = defaultdict(list)
    for claim in claims:
        buckets[(claim.model, claim.metric)].append(claim)

    conflicts: List[Conflict] = []
    for (model, metric), group in buckets.items():
        if len(group) < 2:
            continue
        if metric in TEXT_METRICS:
            keys = {_text_key(c.value) for c in group if _text_key(c.value)}
            if len(keys) > 1:
                conflicts.append(
                    Conflict(model=model, metric=metric, claims=group, reason="text_mismatch")
                )
            continue
        if metrics.kind(metric) != "number":
            continue

        numbers = [c for c in group if isinstance(c.value, (int, float))]
        if len(numbers) < 2:
            continue

        threshold = THRESHOLDS.get(metric, 0.20)
        keys = metrics.conditions(metric)

        sub: Dict[str, List[Claim]] = defaultdict(list)
        for claim in numbers:
            sub[condition_signature(claim.condition, keys)].append(claim)

        within_gap = False
        for sig, items in sub.items():
            if len(items) < 2:
                continue
            values = [float(c.value) for c in items]
            high, low = max(values), min(values)
            if high and (high - low) / abs(high) > threshold:
                within_gap = True
                conflicts.append(
                    Conflict(model=model, metric=metric, claims=items, reason="value_gap")
                )

        if len(sub) > 1 and _setting_keys_differ(numbers, keys):
            values = [float(c.value) for c in numbers]
            high, low = max(values), min(values)
            if high and (high - low) / abs(high) > threshold:
                conflicts.append(
                    Conflict(model=model, metric=metric, claims=numbers, reason="condition_mismatch")
                )
        elif within_gap:
            continue

    return claims, _dedupe(conflicts)


def _dedupe(conflicts: List[Conflict]) -> List[Conflict]:
    seen = set()
    out = []
    for conflict in conflicts:
        key = (conflict.model, conflict.metric, conflict.reason)
        if key in seen:
            continue
        seen.add(key)
        out.append(conflict)
    return out


def explain(conflict: Conflict) -> str:
    if conflict.reason == "condition_mismatch":
        return "同指标、测试条件不同，不可直接比较（非真矛盾）"
    if conflict.reason == "value_gap":
        return "相同测试条件下数值仍显著不同，属真实分歧"
    if conflict.reason == "text_mismatch":
        return "定性描述不一致，需人工判断"
    return ""
