"""Data model for the pipeline. Every claim must point at a source."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List


@dataclass
class Video:
    bvid: str
    title: str
    uploader: str
    mid: int
    duration: int
    play: int
    pubdate: int
    desc: str = ""
    aid: int = 0
    cid: int = 0

    @property
    def url(self) -> str:
        return f"https://www.bilibili.com/video/{self.bvid}"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SourceRef:
    bvid: str
    title: str
    uploader: str
    url: str
    method: str
    fetched_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Claim:
    id: str
    model: str
    metric: str
    value: Any
    unit: str
    condition: Dict[str, Any]
    quote: str
    source: SourceRef
    confidence: str = "medium"
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["source"] = self.source.to_dict()
        return d


@dataclass
class Conflict:
    model: str
    metric: str
    claims: List[Claim]
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model": self.model,
            "metric": self.metric,
            "reason": self.reason,
            "claims": [c.to_dict() for c in self.claims],
        }


@dataclass
class SourceStatus:
    name: str
    status: str
    detail: str = ""
    latency_ms: int = 0


@dataclass
class CollectResult:
    video: Video
    method: str
    text: str
    warnings: List[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return bool(self.text.strip())
