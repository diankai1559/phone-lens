"""End-to-end pipeline: resolve -> probe -> collect -> extract -> normalise ->
detect conflicts -> render -> verify."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional

from . import conflict as conflict_mod
from . import sources
from .bilibili import BiliClient
from .claims import Extractor, heuristic
from .collect import discover, fetch_text
from .config import load_llm
from .entities import Resolution, resolve
from .models import Claim, CollectResult, Conflict, SourceRef, SourceStatus
from .render import render
from .verify import Verification, verify


@dataclass
class RunResult:
    resolution: Resolution
    claims: List[Claim]
    conflicts: List[Conflict]
    results: List[CollectResult]
    statuses: List[SourceStatus]
    report: str
    verification: Verification


def run(
    query: str,
    top: int = 5,
    use_llm: bool = True,
    allow_asr: bool = True,
    assume_family: bool = True,
    ask: Optional[Callable[[Resolution], List[str]]] = None,
    client: Optional[BiliClient] = None,
    on_stage: Optional[Callable[[str, str], None]] = None,
) -> RunResult:
    def emit(stage: str, detail: str = "") -> None:
        if on_stage:
            on_stage(stage, detail)

    emit("resolve", "机型对齐")
    resolution = resolve(query)
    if resolution.needs_confirmation and ask is not None:
        chosen = ask(resolution)
        if chosen:
            resolution.variants = chosen
            if len(chosen) == 1:
                resolution.explicit_variant = True
    emit("resolve", f"{resolution.label}（{resolution.confidence}）")

    client = client or BiliClient()
    emit("probe", "探测信源")
    statuses = sources.probe_all(client)
    strategy = sources.strategy(statuses)

    emit("discover", "筛选候选视频")
    videos = discover(client, resolution, limit=top)
    results: List[CollectResult] = []
    for index, video in enumerate(videos, start=1):
        emit("collect", f"[{index}/{len(videos)}] {video.title[:30]}")
        results.append(fetch_text(client, video, allow_asr=allow_asr and strategy["asr"]))

    variants = [resolution.model_name(v) for v in resolution.variants] or [resolution.family]
    extractor = Extractor(load_llm())
    claims: List[Claim] = []
    rejected = 0
    for index, result in enumerate(results, start=1):
        if not result.usable:
            continue
        emit("extract", f"[{index}/{len(results)}] {result.video.uploader}")
        source = SourceRef(
            bvid=result.video.bvid,
            title=result.video.title,
            uploader=result.video.uploader,
            url=result.video.url,
            method=result.method,
        )
        if use_llm and strategy["llm"]:
            part, dropped = extractor.extract(result, variants, source)
            rejected += dropped
        else:
            part = heuristic(result, variants, source)
        claims.extend(part)

    emit("normalize", f"{len(claims)} 条 claim")
    claims, conflicts = conflict_mod.detect(claims)
    emit("render", "生成报告")
    report = render(resolution.label, claims, conflicts, results, statuses, query)
    emit("verify", "反向校验")
    verification = verify(report, claims, conflicts, results)
    emit("done", f"claims={len(claims)} conflicts={len(conflicts)}")
    return RunResult(
        resolution=resolution,
        claims=claims,
        conflicts=conflicts,
        results=results,
        statuses=statuses,
        report=report,
        verification=verification,
    )
