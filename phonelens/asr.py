"""Audio download + SiliconFlow ASR fallback for videos without subtitles."""

from __future__ import annotations

import math
import os
import shutil
import subprocess
from typing import List

import requests

from .config import load_asr_key
from .models import Video

ASR_URL = "https://api.siliconflow.cn/v1/audio/transcriptions"
ASR_MODEL = "FunAudioLLM/SenseVoiceSmall"
CHUNK_SECONDS = 1500


def available() -> bool:
    return bool(shutil.which("ffmpeg") and load_asr_key())


def _run(cmd: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def _probe_duration(path: str) -> float:
    exe = shutil.which("ffprobe")
    if not exe:
        return 0.0
    result = _run([exe, "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", path])
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def transcribe(client, video: Video, workdir: str) -> str:
    api_key = load_asr_key()
    if not api_key:
        raise RuntimeError("未配置 SILICONFLOW_API_KEY，无法走 ASR 兜底")
    if not shutil.which("ffmpeg"):
        raise RuntimeError("缺少 ffmpeg，无法处理音频")

    os.makedirs(workdir, exist_ok=True)
    url = client.audio_url(video)
    raw = client.download(url, os.path.join(workdir, f"{video.bvid}.m4s"))
    mono = os.path.join(workdir, f"{video.bvid}.mp3")
    _run([shutil.which("ffmpeg"), "-y", "-i", raw, "-b:a", "64k", "-ac", "1", mono])

    duration = _probe_duration(mono)
    count = max(1, math.ceil(duration / CHUNK_SECONDS)) if duration else 1
    texts: List[str] = []
    for index in range(count):
        chunk = os.path.join(workdir, f"{video.bvid}_{index}.mp3")
        _run([
            shutil.which("ffmpeg"), "-y", "-i", mono,
            "-ss", str(index * CHUNK_SECONDS), "-t", str(CHUNK_SECONDS),
            "-c", "copy", chunk,
        ])
        with open(chunk, "rb") as fh:
            resp = requests.post(
                ASR_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": (os.path.basename(chunk), fh, "audio/mpeg")},
                data={"model": ASR_MODEL},
                timeout=600,
            )
        resp.raise_for_status()
        texts.append(str(resp.json().get("text", "")).strip())
    return "\n".join(t for t in texts if t)


def cleanup(workdir: str) -> None:
    shutil.rmtree(workdir, ignore_errors=True)
