"""Candidate discovery, ranking and the subtitle -> summary -> ASR chain."""

from __future__ import annotations

import math
import tempfile
from typing import List, Optional

from . import asr
from .bilibili import BiliClient, BiliError
from .entities import Resolution, match_tokens, tokens_for
from .models import CollectResult, Video

UPLOADER_AUTHORITY = {
    "大米评测": 3.0,
    "小白测评": 3.0,
    "凛光暗切": 3.0,
    "极客湾": 3.0,
    "充电头网评测室": 2.0,
    "吾知评测": 2.0,
    "离限玩家Online": 2.0,
    "老钱数码体验": 2.0,
    "生产线普工": 1.5,
    "低调的山": 1.5,
}

RUMOR_WORDS = ["爆料", "预测", "汇总", "曝光", "值得等吗", "会定价多少", "真机图"]
JUNK_WORDS = ["直播录像", "不关机不下播", "抽奖", "优惠", "好价", "叠券"]


def score(video: Video, resolution: Resolution, tokens: List[str]) -> float:
    blob = f"{video.title} {video.desc}".lower().replace(" ", "")
    if tokens and not any(token in blob for token in tokens):
        return -1.0
    value = 4.0
    value += UPLOADER_AUTHORITY.get(video.uploader, 0.5)
    value += 0.6 * math.log10(max(video.play, 1))
    if video.duration >= 180:
        value += 0.4
    if video.duration >= 600:
        value += 0.4
    if any(word in video.title for word in RUMOR_WORDS):
        value -= 1.5
    if any(word in video.title for word in JUNK_WORDS):
        value -= 3.0
    if video.duration > 3600:
        value -= 2.0
    if resolution.explicit_variant and len(resolution.variants) == 1:
        variant = resolution.variants[0].lower().replace(" ", "")
        if variant and variant not in ("标准版", "standard"):
            if variant not in blob:
                value -= 2.5
        elif "pro" in blob and "标准" not in blob:
            value -= 1.0
    return value


def discover(client: BiliClient, resolution: Resolution, limit: int = 5) -> List[Video]:
    tokens = match_tokens(resolution)
    seen: dict = {}
    for keyword in tokens_for(resolution.query, resolution.family)[:2]:
        try:
            videos = client.search(keyword)
        except BiliError:
            continue
        for video in videos:
            if not video.bvid or video.bvid in seen:
                continue
            value = score(video, resolution, tokens)
            if value <= 0:
                continue
            seen[video.bvid] = (value, video)
    ranked = sorted(seen.values(), key=lambda item: item[0], reverse=True)
    return [video for _, video in ranked[:limit]]


def fetch_text(
    client: BiliClient,
    video: Video,
    allow_asr: bool = True,
    workdir: Optional[str] = None,
) -> CollectResult:
    warnings: List[str] = []
    detail = client.video(video.bvid)
    try:
        text, lang = client.subtitle(detail)
        if text.strip():
            return CollectResult(video=detail, method=f"bilibili_subtitle:{lang or 'unknown'}", text=text)
        warnings.append("无字幕")
    except BiliError as exc:
        warnings.append(f"字幕获取失败: {exc}")

    try:
        summary = client.ai_summary(detail)
        if summary.strip():
            warnings.append("降级至 B站 AI 摘要（信息有损）")
            return CollectResult(video=detail, method="bilibili_ai_summary", text=summary, warnings=warnings)
        warnings.append("无 AI 摘要")
    except BiliError as exc:
        warnings.append(f"AI 摘要失败: {exc}")

    if allow_asr and asr.available():
        tmp = workdir or tempfile.mkdtemp(prefix="phonelens-asr-")
        try:
            text = asr.transcribe(client, detail, tmp)
            if text.strip():
                warnings.append("降级至 ASR（可能存在同音错字）")
                return CollectResult(video=detail, method="siliconflow_asr", text=text, warnings=warnings)
        except Exception as exc:  # noqa: BLE001 - degrade, never abort the run
            warnings.append(f"ASR 失败: {exc}")
        finally:
            asr.cleanup(tmp)
    else:
        warnings.append("ASR 不可用（缺 key 或 ffmpeg）")

    return CollectResult(video=detail, method="none", text="", warnings=warnings)
