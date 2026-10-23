"""Claim extraction.

The extractor has one non-negotiable rule: a claim whose quote is not a literal
substring of the source text is dropped. That turns "不要编" from a prompt wish
into a checkable invariant.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional

import requests

from . import metrics
from .cache import Cache
from .config import LLMConfig
from .models import Claim, CollectResult, SourceRef
from .normalize import replace_cn_numbers

PUNCT = re.compile(r"[\s，。、；：！？,.;:!?\"'“”‘’()（）【】\[\]—\-…·]")

SYSTEM_PROMPT = """你是手机评测数据抽取器。规则：
1. 只抽取原文中明确出现的事实，禁止推断、换算、补充常识。
2. metric 必须从给定枚举中选择，不能自造。
3. value 保留原文写法（如 "9070mAh"、"9小时56分"、"119.9fps"）。
4. quote 必须逐字摘自原文，包含数字和单位，不得改写。
5. condition 只填原文提到的测试条件（亮度、网络、游戏、分辨率、时长、存储版本等），未提及留空字符串。
6. 原文没有的数字，宁可少抽，不要补。
只输出 JSON。"""

SCHEMA_HINT = """{"claims":[{"model":"机型全称","metric":"枚举键","value":"原文数值","unit":"单位","condition":{"key":"值"},"quote":"原文片段","confidence":"high|medium|low"}]}"""


def _squash(text: str) -> str:
    return PUNCT.sub("", str(text or "")).lower()


def quote_supported(quote: str, text: str) -> bool:
    q = _squash(quote)
    if len(q) < 4:
        return False
    return q in _squash(text)


def _chunks(text: str, size: int = 12000) -> List[str]:
    lines = text.splitlines()
    chunks: List[str] = []
    buf: List[str] = []
    length = 0
    for line in lines:
        if length + len(line) > size and buf:
            chunks.append("\n".join(buf))
            buf, length = [], 0
        buf.append(line)
        length += len(line) + 1
    if buf:
        chunks.append("\n".join(buf))
    return chunks or [text]


class Extractor:
    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self.cache = Cache()

    def _call(self, text: str, variants: List[str]) -> List[Dict[str, Any]]:
        cache_key = "llm:" + hashlib.sha1(
            f"{self.config.model}|{'/'.join(variants)}|{text}".encode("utf-8")
        ).hexdigest()
        cached = self.cache.get(cache_key, ttl=30 * 86400)
        if cached is not None:
            return list(cached)

        body = {
            "model": self.config.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"候选机型: {variants}\n"
                        f"metric 枚举: {sorted(metrics.METRICS)}\n"
                        f"输出格式: {SCHEMA_HINT}\n\n原文:\n{text}"
                    ),
                },
            ],
        }
        resp = requests.post(
            f"{self.config.base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=180,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.M).strip()
        data = json.loads(content)
        claims = list(data.get("claims") or [])
        self.cache.set(cache_key, claims)
        return claims

    def extract(
        self, result: CollectResult, variants: List[str], source: SourceRef
    ) -> tuple:
        accepted: List[Claim] = []
        rejected = 0
        for chunk in _chunks(result.text):
            for raw in self._call(chunk, variants):
                metric = metrics.canonical(raw.get("metric", ""))
                quote = str(raw.get("quote", "")).strip()
                if not metric or not quote:
                    rejected += 1
                    continue
                if not quote_supported(quote, result.text):
                    rejected += 1
                    continue
                if metric == "price" and not re.search(r"(元|块钱|售价|起售|¥|CNY)", quote):
                    rejected += 1
                    continue
                model = _match_model(raw.get("model", ""), variants)
                if not model:
                    rejected += 1
                    continue
                condition = raw.get("condition") or {}
                if not isinstance(condition, dict):
                    condition = {}
                condition = _clean_condition(condition, quote, metric)
                metric = metrics.reclassify(metric, condition)
                accepted.append(
                    Claim(
                        id="",
                        model=model,
                        metric=metric,
                        value=_clean_value(metric, raw.get("value", "")),
                        unit=str(raw.get("unit", "") or metrics.unit(metric)),
                        condition={k: v for k, v in condition.items() if v not in ("", None)},
                        quote=quote,
                        source=source,
                        confidence=str(raw.get("confidence", "medium")),
                    )
                )
        return _finalize(accepted), rejected


def heuristic(result: CollectResult, variants: List[str], source: SourceRef) -> List[Claim]:
    """No-LLM fallback: a line with a metric keyword and a number becomes a claim."""
    claims: List[Claim] = []
    for line in result.text.splitlines():
        if len(line) < 6:
            continue
        for alias, metric in metrics.ALIASES.items():
            if alias not in line:
                continue
            if not re.search(r"\d", line):
                continue
            model = _match_model(line, variants) or (variants[0] if variants else "")
            claims.append(
                Claim(
                    id="",
                    model=model,
                    metric=metric,
                    value=line,
                    unit=metrics.unit(metric),
                    condition={},
                    quote=line.strip(),
                    source=source,
                    confidence="low",
                )
            )
            break
    return _finalize(claims)


STORAGE_RE = re.compile(r"(?<!\d)(\d{1,2})\s*[+＋]\s*(\d{2,4})(?!\d)")
RESOLUTION_RE = re.compile(r"\d{3,4}\s*[pP]|\d{3,4}\s*[×x]\s*\d{3,4}")


def _minutes(value: object) -> object:
    text = replace_cn_numbers(str(value))
    if "半小时" in text:
        return 30
    match = re.search(r"(\d+(?:\.\d+)?)\s*(小时|分钟|min|h)", text)
    if not match:
        return value
    number = float(match.group(1))
    return int(number * 60) if match.group(2) in ("小时", "h") else int(number)


def _clean_condition(condition: dict, quote: str, metric: str) -> dict:
    out: Dict[str, Any] = {}
    for key, value in condition.items():
        canon = metrics.canon_condition_key(str(key))
        value = replace_cn_numbers(str(value)) if canon == "duration_min" else value
        if canon == "duration_min":
            value = _minutes(value)
        if canon == "quality" and RESOLUTION_RE.search(str(value)):
            canon = "resolution"
        out[canon] = value

    if metric == "charge_time":
        text = replace_cn_numbers(quote) + " " + " ".join(str(v) for v in condition.values())
        out.setdefault("from_soc", 0)
        if "前50%" in text:
            out["to_soc"] = 50
        elif re.search(r"充到\s*(\d{2})%", text):
            out["to_soc"] = int(re.search(r"充到\s*(\d{2})%", text).group(1))
        elif any(word in text for word in ("充满", "百分百", "100%")):
            out["to_soc"] = 100

    match = STORAGE_RE.search(quote)
    if match:
        storage = f"{match.group(1)}+{match.group(2)}"
        if metric == "price" or out.get("storage"):
            out["storage"] = storage
    return {k: v for k, v in out.items() if v not in ("", None)}


def _clean_value(metric: str, value: Any) -> Any:
    if metric != "soc":
        return value
    text = str(value)
    low = text.lower().replace(" ", "")
    if any(key in low for key in ("8e5v", "815v", "elitegen5v", "至尊版v", "81ttg5v")):
        return "骁龙8 Elite Gen5 V版"
    if any(key in low for key in ("8e5", "815", "elitegen5", "至尊版", "8elite", "81ttg5")):
        return "骁龙8 Elite Gen5"
    if "950" in low and ("天玑" in text or "dimensity" in low):
        return "天玑950"
    return text


def _match_model(text: str, variants: List[str]) -> Optional[str]:
    squashed = _squash(text)
    best = None
    for variant in variants:
        key = _squash(variant)
        if key and key in squashed and (best is None or len(key) > len(_squash(best))):
            best = variant
    return best


def _finalize(claims: List[Claim]) -> List[Claim]:
    deduped: Dict[tuple, Claim] = {}
    for claim in claims:
        key = (
            claim.model,
            claim.metric,
            _squash(claim.value),
            tuple(sorted((k, _squash(v)) for k, v in claim.condition.items())),
        )
        if key not in deduped:
            deduped[key] = claim
    ordered = sorted(deduped.values(), key=lambda c: (_metric_index(c.metric), c.model))
    for index, claim in enumerate(ordered, start=1):
        claim.id = f"C{index:03d}"
    return ordered


def _metric_index(metric: str) -> int:
    try:
        return metrics.PRIORITY.index(metric)
    except ValueError:
        return len(metrics.PRIORITY)
