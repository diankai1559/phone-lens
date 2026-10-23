"""Deterministic markdown renderer.

The model never writes this file. It only fills claims, so a sentence without a
claim id cannot exist in the output.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Dict, List

from . import metrics
from .conflict import explain
from .models import Claim, Conflict, CollectResult

SECTIONS = [
    ("二、核心规格", ["price", "soc", "screen_size", "screen_refresh", "screen_brightness",
                     "screen_manual_brightness", "weight", "thickness"]),
    ("三、性能与散热", ["fps", "fps_1pct_low", "fps_peak", "power", "temp", "screen_brightness_ingame"]),
    ("四、续航与充电", ["battery", "charge_power", "charge_wireless_power", "charge_time", "battery_life"]),
    ("五、影像", ["main_camera", "ultrawide", "telephoto"]),
    ("六、外设与体验", ["speaker", "usb", "ip_rating", "fingerprint", "weight_note"]),
]

HEADLINE_METRICS = [
    "price", "soc", "battery", "charge_time", "battery_life", "screen_refresh",
    "screen_brightness", "main_camera", "speaker", "weight",
]


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _unit(claim: Claim) -> str:
    unit = claim.unit or metrics.unit(claim.metric)
    if unit == "min":
        return "min"
    return unit


def _condition(claim: Claim) -> str:
    if not claim.condition:
        return "—"
    return "，".join(f"{k}={v}" for k, v in sorted(claim.condition.items()))


def _cite(claim: Claim) -> str:
    return f"[{claim.id}]({claim.source.url})"


def _conflict_metrics(conflicts: List[Conflict]) -> Dict[str, set]:
    table: Dict[str, set] = defaultdict(set)
    for conflict in conflicts:
        table[conflict.metric].add(conflict.model)
    return table


def _representative(metric: str, claims: List[Claim]) -> Claim:
    """Pick one headline value: mode for specs, lowest for price."""
    numeric = [c for c in claims if isinstance(c.value, (int, float))]
    if not numeric:
        return claims[0]
    if metric == "price":
        import statistics

        return min(numeric, key=lambda c: abs(float(c.value) - statistics.median(float(x.value) for x in numeric)))
    counts: Dict[float, int] = defaultdict(int)
    for claim in numeric:
        counts[float(claim.value)] += 1
    top = max(counts.values())
    tied = [c for c in numeric if counts[float(c.value)] == top]
    return tied[0]


def render(
    family: str,
    claims: List[Claim],
    conflicts: List[Conflict],
    results: List[CollectResult],
    statuses: List,
    query: str,
) -> str:
    lines: List[str] = []
    lines.append(f"# {family} 深度分析报告")
    lines.append("")
    lines.append(
        f"> 查询：`{query}`　｜　信源 {len(results)} 个　｜　claim {len(claims)} 条　｜　"
        f"生成 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
    lines.append("> 所有数字均来自下方视频，非实验室复测；带 `[Cxxx]` 标记的可跳转原视频。")
    lines.append("")

    lines.append("## 一、结论速览")
    lines.append("")
    conflicted = _conflict_metrics(conflicts)
    seen_metric = set()
    prefix = family.replace(" 系列", "")
    for metric in HEADLINE_METRICS:
        candidates = [c for c in claims if c.metric == metric]
        if not candidates:
            continue
        candidates.sort(key=lambda c: ({"high": 0, "medium": 1, "low": 2}.get(c.confidence, 1)))
        grouped: Dict[str, List[Claim]] = defaultdict(list)
        for claim in candidates:
            grouped[claim.model].append(claim)
        for model, items in grouped.items():
            best = _representative(metric, items)
            short = model.replace(prefix, "").strip() or model
            suffix = "　⚠ 存在分歧，见第七节" if metric in conflicted else ""
            tag = f"（{short}）" if len(grouped) > 1 else ""
            lines.append(
                f"- **{metrics.label(metric)}{tag}**：{_cell(best.value)}{_unit(best)}"
                f"（{_cite(best)}）{suffix}"
            )
        seen_metric.add(metric)
    if not seen_metric:
        lines.append("- 未抽取到可用 claim，请检查信源或降级情况。")
    lines.append("")

    for title, metric_keys in SECTIONS:
        rows = [c for c in claims if c.metric in metric_keys]
        if not rows:
            continue
        lines.append(f"## {title}")
        lines.append("")
        lines.append("| 机型 | 指标 | 数值 | 测试条件 | 出处 |")
        lines.append("|---|---|---|---|---|")
        for claim in rows:
            lines.append(
                f"| {_cell(claim.model)} | {metrics.label(claim.metric)} | "
                f"{_cell(claim.value)}{_unit(claim)} | {_cell(_condition(claim))} | {_cite(claim)} |"
            )
        lines.append("")

    rendered_metrics = {metric for _, keys in SECTIONS for metric in keys}
    leftovers = [c for c in claims if c.metric not in rendered_metrics]
    if leftovers:
        lines.append("## 七、其他指标")
        lines.append("")
        lines.append("| 机型 | 指标 | 数值 | 测试条件 | 出处 |")
        lines.append("|---|---|---|---|---|")
        for claim in leftovers:
            lines.append(
                f"| {_cell(claim.model)} | {metrics.label(claim.metric)} | "
                f"{_cell(claim.value)}{_unit(claim)} | {_cell(_condition(claim))} | {_cite(claim)} |"
            )
        lines.append("")

    lines.append("## 八、数据分歧")
    lines.append("")
    if not conflicts:
        lines.append("未检测到显著分歧。")
    else:
        for conflict in conflicts:
            lines.append(f"### {conflict.model} · {metrics.label(conflict.metric)}")
            lines.append("")
            lines.append(f"**判定：{explain(conflict)}**")
            lines.append("")
            lines.append("| 数值 | 测试条件 | 出处 | 原话 |")
            lines.append("|---|---|---|---|")
            for claim in conflict.claims:
                lines.append(
                    f"| {_cell(claim.value)}{_unit(claim)} | {_cell(_condition(claim))} | "
                    f"{_cite(claim)} | {_cell(claim.quote)[:60]} |"
                )
            lines.append("")
    lines.append("")

    lines.append("## 九、信源与降级声明")
    lines.append("")
    lines.append("| 视频 | UP 主 | 时长 | 获取方式 | 备注 |")
    lines.append("|---|---|---|---|---|")
    for result in results:
        minutes = result.video.duration // 60
        remark = "；".join(result.warnings) if result.warnings else "—"
        lines.append(
            f"| [{_cell(result.video.title[:40])}]({result.video.url}) | "
            f"{_cell(result.video.uploader)} | {minutes}min | {result.method} | {_cell(remark)} |"
        )
    if not results:
        lines.append("| — | — | — | — | 无可用信源 |")
    lines.append("")

    if statuses:
        lines.append("**源健康探测**")
        lines.append("")
        lines.append("| 源 | 状态 | 说明 |")
        lines.append("|---|---|---|")
        for status in statuses:
            lines.append(f"| {status.name} | {status.status} | {_cell(status.detail)} |")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("本报告由 phonelens 生成：抽取阶段强制 quote 必须命中原文，未命中的 claim 直接丢弃；"
                 "口径不同的数据不取平均，而是单列。")
    lines.append("")
    return "\n".join(lines)
