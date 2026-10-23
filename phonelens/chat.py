"""Multi-turn conversation over an already-built evidence set.

Design: the chat never re-reads the 45k-character transcript. It answers from a
compact "evidence pack" of claims + conflicts, keeps short-term memory, and
checks every citation it emits. Ungrounded answers are flagged, not hidden.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import requests

from . import metrics
from .config import LLMConfig
from .models import Claim, Conflict

ID_RE = re.compile(r"\bC\d{3}\b")

CHITCHAT = ("你好", "谢谢", "在吗", "hi", "hello", "你是谁", "能做什么")

SYSTEM_PROMPT = """你是手机评测助手，只能依据【证据包】回答。

规则：
1. 每条事实性结论后面必须附上 claim id，格式 [C012]；可以一条句子里放多个。
2. 证据包里没有的信息，直接说「素材中没有提到」，不要用常识或记忆补充。
3. 允许比较、归纳、做购买建议，但必须给出依据；推断部分要标注「推断」。
4. 数据口径不同（例如亮度/网络/测试时长不同）时，先说不可直接比较，再分别陈述。
5. 回答用中文，简洁，不要复述整份报告。"""


@dataclass
class Answer:
    text: str
    citations: List[str] = field(default_factory=list)
    dropped: List[str] = field(default_factory=list)
    mismatched: List[str] = field(default_factory=list)
    grounded: bool = False


class Conversation:
    def __init__(
        self,
        llm: LLMConfig,
        claims: Sequence[Claim],
        conflicts: Sequence[Conflict],
        family: str,
        max_turns: int = 6,
        evidence_budget: int = 6000,
    ) -> None:
        self.llm = llm
        self.claims = list(claims)
        self.conflicts = list(conflicts)
        self.family = family
        self.max_turns = max_turns
        self.evidence_budget = evidence_budget
        self.history: List[Dict[str, str]] = []
        self.by_id = {c.id: c for c in claims}

    # -- evidence ----------------------------------------------------------
    def _conflict_metrics(self) -> set:
        return {(c.model, c.metric) for c in self.conflicts}

    def evidence(self, question: str) -> str:
        ranked = self._rank(question)
        lines = [f"机型：{self.family}", "claim 列表（id | 机型 | 指标 | 数值 | 条件 | 出处BV）："]
        used = 0
        picked = []
        for claim in ranked:
            row = (
                f"{claim.id} | {claim.model} | {metrics.label(claim.metric)} | "
                f"{claim.value}{claim.unit} | "
                f"{json.dumps(claim.condition, ensure_ascii=False) if claim.condition else '-'} | "
                f"{claim.source.bvid}"
            )
            if used + len(row) > self.evidence_budget and picked:
                break
            picked.append(claim)
            lines.append(row)
            used += len(row) + 1

        if self.conflicts:
            lines.append("")
            lines.append("已知分歧：")
            for conflict in self.conflicts:
                ids = ", ".join(c.id for c in conflict.claims)
                lines.append(
                    f"- {conflict.model} {metrics.label(conflict.metric)}: "
                    f"{conflict.reason}（{ids}）"
                )
        return "\n".join(lines)

    def _rank(self, question: str) -> List[Claim]:
        def score(claim: Claim) -> float:
            blob = (
                f"{claim.model}{metrics.label(claim.metric)}{claim.value}"
                f"{claim.quote}{claim.source.uploader}"
            ).lower()
            value = sum(1 for ch in set(question.lower()) if ch.strip() and ch in blob)
            if claim.metric in {"battery_life", "fps", "price", "soc", "main_camera"}:
                value += 3
            return value

        return sorted(self.claims, key=score, reverse=True)

    # -- chat --------------------------------------------------------------
    def _messages(self, question: str) -> List[Dict[str, str]]:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.append({"role": "system", "content": self.evidence(question)})
        messages.extend(self.history[-(self.max_turns * 2):])
        messages.append({"role": "user", "content": question})
        return messages

    def ask(self, question: str) -> Answer:
        if not self.llm.enabled:
            raise RuntimeError("对话需要 LLM key：设置 DEEPSEEK_API_KEY 或 SILICONFLOW_API_KEY")
        payload = {
            "model": self.llm.model,
            "temperature": 0.2,
            "messages": self._messages(question),
        }
        resp = requests.post(
            f"{self.llm.base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.llm.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=120,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()

        citations, dropped = self._check(raw)
        mismatched = self._semantic_check(raw)
        answer = Answer(
            text=self._flag(raw, dropped, mismatched, question),
            citations=citations,
            dropped=dropped,
            mismatched=mismatched,
            grounded=bool(citations) or _is_chitchat(question),
        )
        self.history.append({"role": "user", "content": question})
        self.history.append({"role": "assistant", "content": answer.text})
        self.history = self.history[-(self.max_turns * 2):]
        return answer

    def _check(self, text: str) -> tuple:
        found = ID_RE.findall(text)
        valid = [cid for cid in dict.fromkeys(found) if cid in self.by_id]
        dropped = [cid for cid in dict.fromkeys(found) if cid not in self.by_id]
        return valid, dropped

    def _flag(self, text: str, dropped: List[str], mismatched: List[str], question: str) -> str:
        if dropped:
            text += f"\n\n> 已移除无效引用：{', '.join(dropped)}"
        if mismatched:
            text += f"\n\n> ⚠ 引用与本句数值可能错位：{', '.join(mismatched)}"
        if not ID_RE.search(text) and not _is_chitchat(question):
            text += "\n\n> ⚠ 本条回答未引用任何 claim，请谨慎采信。"
        return text

    def _semantic_check(self, text: str) -> List[str]:
        """Weak alignment check: a cited claim's value should appear in the answer."""
        numbers = re.findall(r"\d+(?:\.\d+)?", text)
        if not numbers:
            return []
        suspicious = []
        for cid in dict.fromkeys(ID_RE.findall(text)):
            claim = self.by_id.get(cid)
            if claim is None:
                continue
            try:
                value = f"{float(claim.value):g}"
            except (TypeError, ValueError):
                continue
            if not any(num == value or value.startswith(num) or num.startswith(value) for num in numbers):
                suspicious.append(cid)
        return sorted(set(suspicious))

    # -- persistence -------------------------------------------------------
    def save(self, path: str) -> None:
        payload = {
            "family": self.family,
            "history": self.history,
            "claim_ids": list(self.by_id),
        }
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_history(self, path: str) -> None:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.history = list(data.get("history") or [])[-(self.max_turns * 2):]


def _is_chitchat(question: str) -> bool:
    text = question.strip().lower()
    return any(text.startswith(word) for word in CHITCHAT) and len(text) <= 12


def load_result(path: str):
    """Rebuild claims/conflicts from a report JSON produced by `phonelens report --json`."""
    from .models import SourceRef

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    claims = []
    for raw in data.get("claims") or []:
        source = SourceRef(**raw["source"])
        claims.append(
            Claim(
                id=raw["id"],
                model=raw["model"],
                metric=raw["metric"],
                value=raw["value"],
                unit=raw.get("unit", ""),
                condition=raw.get("condition") or {},
                quote=raw.get("quote", ""),
                source=source,
                confidence=raw.get("confidence", "medium"),
            )
        )
    conflicts = []
    for raw in data.get("conflicts") or []:
        members = [c for c in claims if c.id in {m["id"] for m in raw.get("claims", [])}]
        from .models import Conflict

        conflicts.append(
            Conflict(
                model=raw["model"],
                metric=raw["metric"],
                claims=members,
                reason=raw.get("reason", ""),
            )
        )
    family = ((data.get("resolution") or {}).get("family") or "手机")
    return family, claims, conflicts


def repl(conversation: Conversation, session_path: Optional[str] = None) -> None:
    print(f"已进入对话（机型：{conversation.family}）。命令：/claims /conflicts /save /exit")
    while True:
        try:
            question = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not question:
            continue
        if question in ("/exit", "/quit"):
            break
        if question == "/claims":
            for claim in conversation.claims[:40]:
                print(f"  {claim.id} {claim.model} {metrics.label(claim.metric)} {claim.value}{claim.unit}")
            continue
        if question == "/conflicts":
            for conflict in conversation.conflicts:
                print(f"  {conflict.model} {metrics.label(conflict.metric)} {conflict.reason}")
            continue
        if question.startswith("/save"):
            target = question.split(maxsplit=1)[1] if " " in question else session_path or "session.json"
            conversation.save(target)
            print(f"  saved: {target}")
            continue
        try:
            answer = conversation.ask(question)
        except Exception as exc:  # noqa: BLE001 - keep the REPL alive
            print(f"  出错: {exc}")
            continue
        print(f"\n助手> {answer.text}")
        if session_path:
            conversation.save(session_path)
