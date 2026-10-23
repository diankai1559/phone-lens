"""Reverse verification: does every sentence in the report have a claim behind it?

This is the audit step that makes the anti-hallucination claim testable instead
of rhetorical.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Sequence

from . import metrics
from .claims import quote_supported
from .models import Claim, CollectResult, Conflict

ID_RE = re.compile(r"\bC\d{3}\b")

REQUIRED = ["soc", "battery", "battery_life", "fps", "main_camera"]
CRITICAL = {"soc", "battery"}


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


@dataclass
class Verification:
    checks: List[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks if c.name in CRITICAL_CHECKS)

    def add(self, name: str, ok: bool, detail: str) -> None:
        self.checks.append(Check(name=name, ok=ok, detail=detail))

    def to_dict(self) -> Dict[str, object]:
        return {
            "ok": self.ok,
            "checks": [c.__dict__ for c in self.checks],
        }


CRITICAL_CHECKS = {
    "claims_present", "quote_integrity", "citation_coverage", "conflicts_rendered",
}


def verify(
    report: str,
    claims: Sequence[Claim],
    conflicts: Sequence[Conflict],
    results: Sequence[CollectResult],
) -> Verification:
    result = Verification()

    result.add("claims_present", len(claims) > 0, f"{len(claims)} claims")

    ids = {c.id for c in claims}
    referenced = set(ID_RE.findall(report))
    missing_ids = sorted(referenced - ids)
    result.add(
        "citation_integrity",
        not missing_ids,
        "全部引用都有对应 claim" if not missing_ids else f"悬空引用: {missing_ids}",
    )

    coverage = len(referenced & ids) / len(ids) if ids else 0.0
    minimum = min(10, len(ids))
    result.add(
        "citation_coverage",
        len(referenced & ids) >= minimum,
        f"报告引用 {len(referenced & ids)}/{len(ids)} 条 claim（最低要求 {minimum}）",
    )

    corpus = "\n".join(r.text for r in results if r.text)
    video_claims = [c for c in claims if c.source.method not in ("official_specs", "spec_db", "llm_prior")]
    bad = [c.id for c in video_claims if not quote_supported(c.quote, corpus)] if corpus else []
    result.add(
        "quote_integrity",
        not bad,
        "视频 claim 的 quote 均命中原文" if not bad else f"quote 无法回溯: {bad}",
    )

    present = {c.metric for c in claims}
    missing = [m for m in REQUIRED if m not in present]
    result.add(
        "dimension_coverage",
        not (set(missing) & CRITICAL),
        "缺失维度: " + (", ".join(metrics.label(m) for m in missing) if missing else "无"),
    )

    rendered = ("数据分歧" in report) or ("需要注意" in report)
    conflict_ok = True
    detail = "无冲突"
    if conflicts:
        detail = f"检测到 {len(conflicts)} 组冲突"
        if not rendered:
            conflict_ok = False
            detail += "，但报告缺少分歧章节"
        for conflict in conflicts:
            if metrics.label(conflict.metric) not in report:
                conflict_ok = False
                detail += f"；{metrics.label(conflict.metric)} 未呈现"
    result.add("conflicts_rendered", conflict_ok, detail)

    result.add(
        "sources_attributed",
        all(c.source.url or c.source.method == "llm_prior" for c in claims),
        "每条 claim 都有可访问出处或明确标注为模型知识",
    )
    return result
