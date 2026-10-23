"""The report agent.

A bounded tool loop rather than a fixed pipeline: it checks what it has, decides
whether the evidence is good enough, and records every decision. The LLM is used
for extraction and for one "is this enough?" judgement; everything else is
deterministic so a run can be reproduced.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import requests

from . import conflict as conflict_mod
from . import specs
from .bilibili import BiliClient
from .claims import Extractor, _finalize
from .collect import discover, fetch_text
from .config import LLMConfig, load_llm
from .entities import Resolution, resolve
from .models import Claim, CollectResult, Conflict, SourceRef
from .reconcile import apply_official
from .render_buyer import render as render_buyer
from .verify import Verification, verify

SPEC_KEYS = ["soc", "battery", "weight", "screen_size", "main_camera"]


@dataclass
class Step:
    thought: str
    action: str
    observation: str


@dataclass
class AgentResult:
    resolution: Resolution
    report: str
    claims: List[Claim]
    conflicts: List[Conflict]
    overrides: list
    results: List[CollectResult]
    trace: List[Step]
    verification: Verification


class ReportAgent:
    def __init__(
        self,
        query: str,
        top: int = 4,
        allow_asr: bool = True,
        max_steps: int = 12,
        on_event: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self.query = query
        self.top = max(1, top)
        self.allow_asr = allow_asr
        self.max_steps = max_steps
        self.on_event = on_event
        self.llm = load_llm()
        self.client = BiliClient()
        self.trace: List[Step] = []
        self.resolution: Resolution = resolve(query)
        self.video_claims: List[Claim] = []
        self.official_claims: List[Claim] = []
        self.results: List[CollectResult] = []
        self.overrides: list = []
        self.spec_status: List[str] = []
        self.candidates = []
        self.used: set = set()
        self.collected = 0

    def _log(self, thought: str, action: str, observation: str) -> None:
        self.trace.append(Step(thought, action, observation))
        if self.on_event:
            self.on_event(action, observation)

    # -- tools -------------------------------------------------------------
    def tool_official(self) -> None:
        found = specs.fetch(self.resolution.family, self.resolution.variants, self.llm)
        self.official_claims = [c for item in found for c in item.claims]
        pieces = [f"{i.model}:{i.status}" for i in found]
        self.spec_status = pieces
        self._log(
            "先拿官网参数做基线，避免把字幕错字当成规格",
            "official_specs",
            "；".join(pieces) or "无",
        )

    def tool_discover(self) -> None:
        self.candidates = discover(self.client, self.resolution, limit=self.top + 2)
        self._log(
            "按相关性和 UP 主专业度挑候选视频",
            "search_videos",
            f"{len(self.candidates)} 个候选",
        )

    def tool_collect(self, limit: int = 3) -> None:
        picked = [v for v in self.candidates if v.bvid not in self.used][:limit]
        for video in picked:
            self.used.add(video.bvid)
            result = fetch_text(self.client, video, allow_asr=self.allow_asr)
            self.results.append(result)
            self.collected += 1
        usable = sum(1 for r in self.results if r.usable)
        self._log(
            "补齐实测素材",
            "collect",
            f"新增 {len(picked)}，可用 {usable}/{len(self.results)}",
        )

    def tool_extract(self) -> None:
        variants = [self.resolution.model_name(v) for v in self.resolution.variants] or [self.resolution.family]
        extractor = Extractor(self.llm)
        dropped = 0
        for result in self.results:
            if not result.usable:
                continue
            source = SourceRef(
                bvid=result.video.bvid,
                title=result.video.title,
                uploader=result.video.uploader,
                url=result.video.url,
                method=result.method,
            )
            if self.llm.enabled:
                part, bad = extractor.extract(result, variants, source)
                dropped += bad
            else:
                from .claims import heuristic

                part = heuristic(result, variants, source)
            self.video_claims.extend(part)
        self._log(
            "只保留能在原文中找到的句子",
            "extract_claims",
            f"{len(self.video_claims)} 条；丢弃 {dropped} 条无出处/机型不符",
        )

    def tool_merge(self) -> None:
        strong = [c for c in self.official_claims if c.confidence in ("official", "spec_db")]
        priors = [c for c in self.official_claims if c.confidence == "ai_prior"]
        merged, self.overrides = apply_official(self.video_claims, strong)
        existing = {(c.model, c.metric) for c in merged}
        merged.extend([p for p in priors if (p.model, p.metric) not in existing])
        self.claims = _finalize(merged)
        self._log(
            "官网优先，冲突的字幕数值不再作为规格",
            "reconcile",
            f"{len(self.claims)} 条；官方校准 {len(self.overrides)} 项；模型知识 {len(priors)} 项",
        )

    def tool_conflicts(self) -> None:
        self.claims, self.conflicts = conflict_mod.detect(self.claims)
        self._log(
            "同条件才算分歧，条件不同只做并列",
            "detect_conflicts",
            f"{len(self.conflicts)} 组",
        )

    def _coverage(self) -> int:
        present = {c.metric for c in self.claims}
        return sum(1 for key in SPEC_KEYS if key in present)

    def _supervise(self) -> str:
        """One LLM judgement: collect more, or write now. Falls back to rules."""
        conflicts = len(getattr(self, "conflicts", []))
        usable = sum(1 for r in self.results if r.usable)
        if self.llm.enabled:
            try:
                body = {
                    "model": self.llm.model,
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "你在决定手机报告的证据是否足够。只输出 JSON："
                                '{"action":"collect_more"|"write","reason":"一句话"}。'
                                "如果关键规格缺失、可用视频少于 2 个、或同口径分歧超过 5 组，选 collect_more。"
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                f"机型 {self.resolution.label}；claim {len(self.claims)} 条；"
                                f"关键规格覆盖 {self._coverage()}/5；可用视频 {usable}；"
                                f"冲突 {conflicts} 组；官方参数 {len(self.official_claims)} 项。"
                            ),
                        },
                    ],
                }
                resp = requests.post(
                    f"{self.llm.base_url.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {self.llm.api_key}", "Content-Type": "application/json"},
                    json=body,
                    timeout=60,
                )
                resp.raise_for_status()
                content = re.sub(r"^```(?:json)?|```$", "", resp.json()["choices"][0]["message"]["content"].strip(), flags=re.M)
                decision = json.loads(content)
                action = decision.get("action")
                if action in ("collect_more", "write"):
                    self._log(f"模型判断：{decision.get('reason', '')}", "decide", action)
                    return action
            except Exception as exc:  # noqa: BLE001 - fall back to rules
                self._log(f"模型判断失败，用规则兜底：{exc}", "decide", "rule_fallback")

        if (self._coverage() < 3 or usable < 2 or conflicts > 5) and self.collected < self.top:
            return "collect_more"
        return "write"

    # -- loop --------------------------------------------------------------
    def run(self) -> AgentResult:
        self._log("先确认机型，别把 K100 和 K100 Pro Max 混在一起", "resolve", self.resolution.label)
        self.tool_official()
        self.tool_discover()
        self.tool_collect(3)
        self.tool_extract()
        self.tool_merge()
        self.tool_conflicts()

        guard = 0
        while guard < 3:
            guard += 1
            decision = self._supervise()
            if decision == "write" or self.collected >= self.top:
                break
            before = self.collected
            self.tool_collect(1)
            if self.collected == before:
                break
            self.tool_extract()
            self.tool_merge()
            self.tool_conflicts()

        self._log("写成购买参考：规格用官网，体验用实测", "write_report", "生成中")
        report = render_buyer(
            self.resolution.label,
            self.claims,
            self.conflicts,
            self.results,
            self.overrides,
            self.spec_status,
            self.query,
        )
        verification = verify(report, self.claims, self.conflicts, self.results)
        self._log(
            "逐条检查引用和维度覆盖",
            "verify",
            "通过" if verification.ok else "未通过：" + "；".join(c.detail for c in verification.checks if not c.ok),
        )
        return AgentResult(
            resolution=self.resolution,
            report=report,
            claims=self.claims,
            conflicts=self.conflicts,
            overrides=self.overrides,
            results=self.results,
            trace=self.trace,
            verification=verification,
        )
