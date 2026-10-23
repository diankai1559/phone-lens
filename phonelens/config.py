"""Configuration and credential discovery.

Nothing is written to disk here; secrets stay in env vars or user config.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

APP_DIR = Path(os.environ.get("PHONELENS_HOME", Path.home() / ".phonelens"))
CACHE_DIR = APP_DIR / "cache"
BILI_CREDENTIAL = Path(
    os.environ.get("BILI_CREDENTIAL", Path.home() / ".bilibili-cli" / "credential.json")
)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


@dataclass
class LLMConfig:
    base_url: str
    api_key: str
    model: str

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)


def _env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def load_llm() -> LLMConfig:
    if _env("DEEPSEEK_API_KEY"):
        return LLMConfig(
            base_url=_env("DEEPSEEK_BASE_URL") or "https://api.deepseek.com/v1",
            api_key=_env("DEEPSEEK_API_KEY"),
            model=_env("PHONELENS_LLM_MODEL") or "deepseek-chat",
        )
    if _env("SILICONFLOW_API_KEY"):
        return LLMConfig(
            base_url="https://api.siliconflow.cn/v1",
            api_key=_env("SILICONFLOW_API_KEY"),
            model=_env("PHONELENS_LLM_MODEL") or "Qwen/Qwen2.5-72B-Instruct",
        )
    return LLMConfig(base_url="", api_key="", model="")


def load_asr_key() -> str:
    key = _env("SILICONFLOW_API_KEY", "ASR_API_KEY")
    if key:
        return key
    for path in (APP_DIR / "config.json", Path.home() / ".claude" / "skills" / "bilibili-knowledge" / "config.json"):
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            value = str(data.get("siliconflow_api_key", "")).strip()
            if value:
                return value
    return ""


def load_bili_credential() -> Optional[Dict[str, str]]:
    if not BILI_CREDENTIAL.exists():
        return None
    try:
        data = json.loads(BILI_CREDENTIAL.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def bili_cookies() -> Dict[str, str]:
    cred = load_bili_credential() or {}
    cookies = {}
    mapping = {
        "sessdata": "SESSDATA",
        "bili_jct": "bili_jct",
        "dedeuserid": "DedeUserID",
        "buvid3": "buvid3",
    }
    for src, dst in mapping.items():
        value = str(cred.get(src, "") or "").strip()
        if value:
            cookies[dst] = value
    return cookies
