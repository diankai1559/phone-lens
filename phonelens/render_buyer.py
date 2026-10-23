"""Buyer-facing report.

Answers "should I buy this" instead of showing the pipeline. Specs come from the
official page when available; measurements stay separate and keep their test
conditions.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Sequence, Tuple

from . import metrics
from .models import Claim, CollectResult, Conflict

SPEC_ROWS = [
    ("屏幕", ["screen_size", "screen_refresh", "screen_peak_brightness"]),
    ("处理器", ["soc"]),
    ("电池", ["battery"]),
    ("充电", ["charge_power", "charge_wireless_power"]),
    ("机身", ["weight", "thickness"]),
    ("主摄", ["main_camera"]),
    ("长焦", ["telephoto"]),
    ("超广角", ["ultrawide"]),
    ("扬声器", ["speaker"]),
    ("接口 / 防护", ["usb", "ip_rating"]),
]

EXPERIENCE = [
    ("续航与充电", ["battery_life", "charge_time"]),
    ("性能与发热", ["fps", "fps_1pct_low", "power", "temp"]),
    ("影像", ["main_camera", "ultrawide", "telephoto"]),
]

CONFIDENCE_ORDER = {"official": 0, "spec_db": 1, "high": 2, "medium": 3, "low": 4}

COND_LABELS = {
    "network": "网络", "brightness_nit": "亮度", "game": "游戏", "resolution": "分辨率",
    "quality": "画质", "duration_min": "时长", "duration": "时长", "stat": "口径",
    "storage": "存储", "from_soc": "起始", "to_soc": "充至", "usage": "场景",
    "scenario": "场景", "scene": "场景", "screen": "屏幕", "measure_point": "测温点",
    "position": "测温点", "setting": "设置", "mode": "模式", "type": "类型",
    "测试模型": "测试模型", "note": "说明", "with_case": "戴壳",
    "start": "起始", "end": "充至",
}


def _num(value: object) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number.is_integer() else f"{number:g}"


def _value(claim: Claim) -> str:
    text = _num(claim.value).replace(" ", "")
    unit = (claim.unit or "").replace(" ", "")
    if unit and unit.lower() not in text.lower():
        text += unit
    return text


def _cite(claim: Claim) -> str:
    if claim.source.method == "official_specs":
        return "[官网](" + claim.source.url + ")" if claim.source.url else "官网"
    if claim.source.method == "spec_db":
        return "[参数库](" + claim.source.url + ")" if claim.source.url else "参数库"
    return f"[{claim.id}]({claim.source.url})"


def _cond(claim: Claim) -> str:
    parts = []
    for key, value in sorted(claim.condition.items()):
        if key == "from_soc" and str(value) in ("0", "0.0"):
            continue
        label = COND_LABELS.get(key, key)
        text = str(value).replace(" ", "")
        if key == "duration_min":
            text += "min"
        if len(text) > 18:
            text = text[:18] + "…"
        parts.append(f"{label}{text}")
    return "，".join(parts)


def _with_cond(claim: Claim) -> str:
    cond = _cond(claim)
    return f"（{cond}）" if cond else ""


def _pick(claims: Sequence[Claim], metric: str, limit: int = 2) -> List[Claim]:
    items = [c for c in claims if c.metric == metric]
    good = [c for c in items if c.confidence != "low"]
    items = good or items
    items.sort(key=lambda c: (CONFIDENCE_ORDER.get(c.confidence, 9), -len(str(c.condition))))
    return items[:limit]


def _official(claims: Sequence[Claim], model: str, metric: str) -> List[Claim]:
    return [
        c for c in claims
        if c.model == model and c.metric == metric and c.confidence in ("official", "spec_db")
    ]


def render(
    family: str,
    claims: List[Claim],
    conflicts: List[Conflict],
    results: List[CollectResult],
    overrides: List[Tuple[Claim, Claim]],
    spec_status: List[str],
    query: str,
) -> str:
    models = sorted({c.model for c in claims}) or [family]
    source_label = "官网/参数库参数" if any(c.confidence == "spec_db" for c in claims) else "官网参数"
    lines: List[str] = []
    lines.append(f"# {family} 购买参考")
    lines.append("")
    lines.append(
        f"> {datetime.now().strftime('%Y-%m-%d')}　｜　{source_label} + {len(results)} 个视频实测　｜　"
        "引用可点开原文"
    )
    lines.append("")

    lines.extend(_verdict(claims, results))
    lines.extend(_spec_table(claims, models))
    lines.extend(_experience(claims))
    lines.extend(_notes(claims, conflicts, results, overrides, spec_status))
    lines.extend(_buying(claims))
    lines.append("---")
    lines.append("")
    lines.append("规格以小米官网为准；实测数据保留各自测试条件，条件不同不合并、不取平均。")
    lines.append("")
    return "\n".join(lines)


def _verdict(claims: List[Claim], results: List[CollectResult]) -> List[str]:
    lines = ["## 结论", ""]
    points: List[str] = []

    official_battery = [c for c in claims if c.metric == "battery" and c.confidence in ("official", "spec_db")]
    if not official_battery:
        official_battery = _pick(claims, "battery", 2)
    if official_battery:
        values = " / ".join(f"{c.model} {_value(c)}" for c in official_battery[:2])
        points.append(f"电池 {values}")

    life = [c for c in claims if c.metric == "battery_life" and c.confidence != "low"]
    standard = [c for c in life if "brightness_nit" in c.condition]
    if standard:
        best = max(standard, key=lambda c: float(c.value) if isinstance(c.value, (int, float)) else 0)
        points.append(f"标准续航模型下亮屏 {_value(best)}{_with_cond(best)} {_cite(best)}")
    elif life:
        best = max(life, key=lambda c: float(c.value) if isinstance(c.value, (int, float)) else 0)
        points.append(f"长续航场景亮屏 {_value(best)}{_with_cond(best)} {_cite(best)}")

    full = [c for c in claims if c.metric == "charge_time" and str(c.condition.get("to_soc")) == "100"]
    if full:
        fastest = min(full, key=lambda c: float(c.value) if isinstance(c.value, (int, float)) else 1e9)
        points.append(f"充满约 {_value(fastest)}")

    weight = [c for c in claims if c.metric == "weight" and c.confidence == "official"]
    if weight:
        value = float(weight[0].value) if isinstance(weight[0].value, (int, float)) else 0
        points.append(f"{_value(weight[0])}，{'偏重' if value >= 230 else '重量正常'}")

    price = [c for c in claims if c.metric == "price"]
    values = sorted(float(c.value) for c in price if isinstance(c.value, (int, float)))
    if values:
        points.append(f"{int(values[0])}–{int(values[-1])} 元" if values[0] != values[-1] else f"{int(values[0])} 元")

    lines.append("- " + "；".join(points) + "。" if points else "- 素材不足，未形成结论。")
    degraded = [r for r in results if not r.usable]
    if degraded:
        lines.append(f"- {len(degraded)} 个视频没有取到正文，结论可能不完整。")
    lines.append("")
    return lines


def _spec_table(claims: List[Claim], models: List[str]) -> List[str]:
    lines = ["## 关键参数", ""]
    lines.append("| 项目 | " + " | ".join(models) + " |")
    lines.append("|---" * (len(models) + 1) + "|")
    rendered = 0
    for label, keys in SPEC_ROWS:
        cells = []
        for model in models:
            parts = []
            for key in keys:
                official = _official(claims, model, key)
                source_claims = official or _pick([c for c in claims if c.model == model], key, 2)
                for claim in source_claims:
                    text = _value(claim)
                    if not official:
                        text += "*"
                    elif claim.confidence == "spec_db":
                        text += "†"
                    if text not in parts:
                        parts.append(text)
            cells.append(" / ".join(parts) or "—")
        if all(cell == "—" for cell in cells):
            continue
        rendered += 1
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    if not rendered:
        lines.append("| — | 未获取到参数 |")
    lines.append("")
    if any(c.confidence == "spec_db" for c in claims):
        lines.append("`†` 来自参数库（官网未提供该机型页面）。")
    if any(c.confidence not in ("official", "spec_db") for c in claims):
        lines.append("带 `*` 的为视频实测口径，官网参数未覆盖。")
    lines.append("")
    return lines


def _experience(claims: List[Claim]) -> List[str]:
    lines: List[str] = ["## 实测表现", ""]
    any_section = False
    for title, keys in EXPERIENCE:
        rows = [c for c in claims if c.metric in keys and c.source.method != "official_specs"]
        if not rows:
            continue
        any_section = True
        lines.append(f"### {title}")
        lines.append("")
        grouped: Dict[Tuple[str, str], List[Claim]] = defaultdict(list)
        for claim in rows:
            grouped[(claim.model, claim.metric)].append(claim)
        for (model, metric), items in grouped.items():
            items = [c for c in items if c.confidence != "low"]
            if not items:
                continue
            items.sort(key=lambda c: CONFIDENCE_ORDER.get(c.confidence, 9))
            shown = items[:3]
            text = "；".join(f"{_value(c)}{_with_cond(c)} {_cite(c)}" for c in shown)
            lines.append(f"- **{model} · {metrics.label(metric)}**：{text}")
        lines.append("")
    if not any_section:
        lines.append("未获取到实测数据。")
        lines.append("")
    return lines


def _notes(
    claims: List[Claim],
    conflicts: List[Conflict],
    results: List[CollectResult],
    overrides: List[Tuple[Claim, Claim]],
    spec_status: List[str],
) -> List[str]:
    lines = ["## 需要注意", ""]
    ok = [s for s in spec_status if s.endswith(("ok", "ok_db"))]
    missing = [s.split(":")[0] for s in spec_status if not s.endswith(("ok", "ok_db"))]
    if ok:
        labels = []
        for item in ok:
            name, status = item.split(":")[0], item.rsplit(":", 1)[-1]
            labels.append(f"{name}（参数库）" if status == "ok_db" else name)
        lines.append(f"- 参数来源：{'、'.join(labels)}。")
    if missing:
        lines.append(f"- 官网未找到：{'、'.join(missing)}（可能未发布），这些机型只用视频数据。")
    if overrides:
        lines.append(f"- 视频与官网不一致的 {len(overrides)} 项（如电池、重量）已按官网校准。")

    degraded = [r for r in results if r.method == "none"]
    if degraded:
        lines.append(f"- {len(degraded)} 个视频无字幕且未转写（{'、'.join(r.video.bvid for r in degraded)}），未纳入结论。")
    asr = [r for r in results if r.method == "siliconflow_asr"]
    if asr:
        lines.append(f"- {len(asr)} 个视频使用语音转写，数字可能有同音误差。")

    real = [c for c in conflicts if c.reason == "value_gap"]
    soft = [c for c in conflicts if c.reason == "condition_mismatch"]
    if real:
        lines.append("")
        lines.append("**同口径数值不一致**")
        lines.append("")
        for conflict in real[:3]:
            shown = [c for c in conflict.claims if isinstance(c.value, (int, float))][:3]
            values = " / ".join(f"{_value(c)}（{_cite(c)}）" for c in shown)
            lines.append(f"- {conflict.model} · {metrics.label(conflict.metric)}：{values}")
    if soft:
        lines.append("")
        lines.append("**测试条件不同，不可直接比较**")
        lines.append("")
        for conflict in soft[:3]:
            shown = [c for c in conflict.claims if isinstance(c.value, (int, float))][:3]
            values = " / ".join(f"{_value(c)}{_with_cond(c)} {_cite(c)}" for c in shown)
            lines.append(f"- {conflict.model} · {metrics.label(conflict.metric)}：{values}")
    if not conflicts:
        lines.append("- 未发现同口径下的数据分歧。")
    lines.append("")
    return lines


def _buying(claims: List[Claim]) -> List[str]:
    lines = ["## 怎么选", ""]
    price_rows = [c for c in claims if c.metric == "price"]
    if price_rows:
        lines.append("| 版本 | 参考价 | 出处 |")
        lines.append("|---|---|---|")
        for claim in price_rows[:6]:
            storage = claim.condition.get("storage", "—")
            lines.append(f"| {claim.model} {storage} | {_value(claim)} | {_cite(claim)} |")
        lines.append("")

    advice: List[str] = []
    battery = [c for c in claims if c.metric == "battery" and isinstance(c.value, (int, float))]
    if len({c.model for c in battery}) > 1:
        big = max(battery, key=lambda c: float(c.value))
        small = min(battery, key=lambda c: float(c.value))
        advice.append(f"同系列里 {big.model} 电池 {_value(big)}，{small.model} 为 {_value(small)}")
    life = [c for c in claims if c.metric == "battery_life"]
    standard = [c for c in life if "brightness_nit" in c.condition and isinstance(c.value, (int, float))]
    if standard:
        best = max(standard, key=lambda c: float(c.value))
        advice.append(f"统一口径下亮屏最长：{best.model} {_value(best)} {_cite(best)}")
    if not advice:
        advice.append("目前证据覆盖不全，建议再补充实测来源。")
    lines.extend(f"- {item}" for item in advice)
    lines.append("")
    return lines
