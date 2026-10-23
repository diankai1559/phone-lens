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
    os.environ.get("BILI_CREDENTIAL", APP_DIR / "credential.json")
)
LEGACY_BILI_CREDENTIAL = Path.home() / ".bilibili-cli" / "credential.json"

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


def _file_config() -> Dict[str, str]:
    path = APP_DIR / "config.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {str(k): str(v) for k, v in data.items() if isinstance(v, str)}


def load_llm() -> LLMConfig:
    cfg = _file_config()
    if _env("DEEPSEEK_API_KEY") or cfg.get("deepseek_api_key"):
        return LLMConfig(
            base_url=_env("DEEPSEEK_BASE_URL") or "https://api.deepseek.com/v1",
            api_key=_env("DEEPSEEK_API_KEY") or cfg.get("deepseek_api_key", ""),
            model=_env("PHONELENS_LLM_MODEL") or cfg.get("llm_model") or "deepseek-chat",
        )
    if _env("SILICONFLOW_API_KEY") or cfg.get("siliconflow_api_key"):
        return LLMConfig(
            base_url="https://api.siliconflow.cn/v1",
            api_key=_env("SILICONFLOW_API_KEY") or cfg.get("siliconflow_api_key", ""),
            model=_env("PHONELENS_LLM_MODEL") or cfg.get("llm_model") or "Qwen/Qwen2.5-72B-Instruct",
        )
    return LLMConfig(base_url="", api_key="", model="")


def load_asr_key() -> str:
    key = _env("SILICONFLOW_API_KEY", "ASR_API_KEY")
    if key:
        return key
    return _file_config().get("siliconflow_api_key", "").strip()


def load_bili_credential() -> Optional[Dict[str, str]]:
    for path in (BILI_CREDENTIAL, LEGACY_BILI_CREDENTIAL):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and data.get("sessdata"):
            return data
    return None


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
