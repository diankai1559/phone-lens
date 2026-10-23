"""Minimal Bilibili web client: WBI signing, search, subtitles, audio.

Only public read endpoints are used. No write action (like/coin/follow) exists
in this module by design.
"""

from __future__ import annotations

import hashlib
import html
import re
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

import requests

from .cache import Cache, retry
from .config import UA, bili_cookies
from .models import Video

MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62,
    11, 36, 20, 34, 44, 52,
]

TAG_RE = re.compile(r"<[^>]+>")


class BiliError(RuntimeError):
    pass


class BiliClient:
    def __init__(self, cache: Optional[Cache] = None, timeout: int = 15) -> None:
        self.cache = cache or Cache()
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": UA, "Referer": "https://www.bilibili.com/"}
        )
        self.session.cookies.update(bili_cookies())
        self._mixin_key = ""
        self._mixin_at = 0.0

    # -- wbi ---------------------------------------------------------------
    def _key(self) -> str:
        if self._mixin_key and time.time() - self._mixin_at < 3600:
            return self._mixin_key
        resp = self.session.get(
            "https://api.bilibili.com/x/web-interface/nav", timeout=self.timeout
        )
        data = resp.json()
        wbi = (data.get("data") or {}).get("wbi_img") or {}
        if not wbi:
            raise BiliError("nav 接口未返回 wbi_img")
        img = wbi["img_url"].rsplit("/", 1)[-1].split(".")[0]
        sub = wbi["sub_url"].rsplit("/", 1)[-1].split(".")[0]
        raw = img + sub
        self._mixin_key = "".join(raw[i] for i in MIXIN_KEY_ENC_TAB)[:32]
        self._mixin_at = time.time()
        return self._mixin_key

    def _sign(self, params: Dict[str, Any]) -> Dict[str, Any]:
        signed = dict(params)
        signed["wts"] = int(time.time())
        signed = dict(sorted(signed.items()))
        signed = {
            k: "".join(ch for ch in str(v) if ch not in "!'()*")
            for k, v in signed.items()
        }
        query = urllib.parse.urlencode(signed)
        signed["w_rid"] = hashlib.md5(
            (query + self._key()).encode("utf-8")
        ).hexdigest()
        return signed

    def _get(self, url: str, params: Dict[str, Any], signed: bool = True) -> Dict[str, Any]:
        query = self._sign(params) if signed else params
        cache_key = url + "?" + urllib.parse.urlencode(sorted(query.items()))
        cached = self.cache.get(cache_key, ttl=3600)
        if cached is not None:
            return cached

        def call() -> Dict[str, Any]:
            resp = self.session.get(url, params=query, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()

        payload = retry(call)
        if payload.get("code") != 0:
            raise BiliError(f"{url} 返回 {payload.get('code')}: {payload.get('message')}")
        self.cache.set(cache_key, payload)
        return payload

    # -- endpoints ---------------------------------------------------------
    def health(self) -> Tuple[bool, str]:
        try:
            payload = self.session.get(
                "https://api.bilibili.com/x/web-interface/nav", timeout=8
            ).json()
        except Exception as exc:  # noqa: BLE001 - surfaced to the health matrix
            return False, f"网络不可达: {exc}"
        data = payload.get("data") or {}
        if payload.get("code") != 0:
            return False, f"nav 失败: {payload.get('message')}"
        if not data.get("isLogin"):
            return True, "可匿名访问（无字幕权限）"
        return True, f"已登录: {data.get('uname', '')}"

    def search(self, keyword: str, page: int = 1) -> List[Video]:
        payload = self._get(
            "https://api.bilibili.com/x/web-interface/wbi/search/type",
            {"search_type": "video", "keyword": keyword, "page": page},
        )
        items = (payload.get("data") or {}).get("result") or []
        videos: List[Video] = []
        for item in items:
            title = html.unescape(TAG_RE.sub("", item.get("title", "")))
            videos.append(
                Video(
                    bvid=item.get("bvid", ""),
                    title=title,
                    uploader=item.get("author", ""),
                    mid=int(item.get("mid") or 0),
                    duration=_duration_seconds(item.get("duration", "")),
                    play=int(item.get("play") or 0),
                    pubdate=int(item.get("pubdate") or 0),
                    desc=item.get("description", "") or "",
                )
            )
        return videos

    def video(self, bvid: str) -> Video:
        payload = self._get(
            "https://api.bilibili.com/x/web-interface/view", {"bvid": bvid}
        )
        data = payload["data"]
        return Video(
            bvid=data["bvid"],
            title=data["title"],
            uploader=(data.get("owner") or {}).get("name", ""),
            mid=int((data.get("owner") or {}).get("mid") or 0),
            duration=int(data.get("duration") or 0),
            play=int((data.get("stat") or {}).get("view") or 0),
            pubdate=int(data.get("pubdate") or 0),
            desc=data.get("desc", "") or "",
            aid=int(data["aid"]),
            cid=int(data["cid"]),
        )

    def subtitle(self, video: Video) -> Tuple[str, str]:
        payload = self._get(
            "https://api.bilibili.com/x/player/wbi/v2",
            {"aid": video.aid, "cid": video.cid},
        )
        subs = ((payload.get("data") or {}).get("subtitle") or {}).get("subtitles") or []
        if not subs:
            return "", ""
        preferred = next((s for s in subs if s.get("lan") == "ai-zh"), subs[0])
        url = preferred.get("subtitle_url", "")
        if url.startswith("//"):
            url = "https:" + url
        if not url:
            return "", ""
        resp = self.session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        body = resp.json().get("body") or []
        text = "\n".join(str(item.get("content", "")).strip() for item in body)
        return text.strip(), preferred.get("lan", "")

    def ai_summary(self, video: Video) -> str:
        payload = self._get(
            "https://api.bilibili.com/x/web-interface/view/conclusion/get",
            {"bvid": video.bvid, "cid": video.cid, "up_mid": video.mid},
        )
        model = (payload.get("data") or {}).get("model_result") or {}
        return str(model.get("summary") or "").strip()

    def audio_url(self, video: Video) -> str:
        payload = self._get(
            "https://api.bilibili.com/x/player/wbi/playurl",
            {"bvid": video.bvid, "cid": video.cid, "fnval": 16, "fourk": 0},
        )
        audios = ((payload.get("data") or {}).get("dash") or {}).get("audio") or []
        if not audios:
            raise BiliError("无音频流（可能需要登录或该视频受限）")
        best = max(audios, key=lambda a: a.get("bandwidth") or 0)
        return best.get("baseUrl") or best.get("base_url") or ""

    def download(self, url: str, dest: str) -> str:
        merged: Dict[str, str] = {}
        merged.update(self.session.headers)
        merged["Referer"] = "https://www.bilibili.com/"
        with self.session.get(url, headers=merged, stream=True, timeout=60) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=1 << 16):
                    if chunk:
                        fh.write(chunk)
        return dest


def _duration_seconds(text: str) -> int:
    parts = [p for p in str(text).split(":") if p != ""]
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return 0
    seconds = 0
    for num in nums:
        seconds = seconds * 60 + num
    return seconds
