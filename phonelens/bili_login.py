"""Bilibili QR login (passport web flow).

Credentials are written to ~/.phonelens/credential.json and never logged or
returned over HTTP. The QR itself is just a login URL, safe to display.
"""

from __future__ import annotations

import base64
import io
import json
import os
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Dict, Optional

import requests

from .config import APP_DIR, UA

GENERATE = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
POLL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
CREDENTIAL = APP_DIR / "credential.json"

STATUS = {
    86101: "waiting",
    86090: "scanned",
    86038: "expired",
    0: "confirmed",
}


@dataclass
class LoginSession:
    key: str
    url: str
    created: float = field(default_factory=time.time)
    status: str = "waiting"
    detail: str = ""

    @property
    def age(self) -> float:
        return time.time() - self.created


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Referer": "https://www.bilibili.com/"})
    return s


def create() -> LoginSession:
    with _session() as s:
        resp = s.get(GENERATE, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    payload = data.get("data") or {}
    if not payload.get("url") or not payload.get("qrcode_key"):
        raise RuntimeError(f"生成二维码失败: {data.get('message')}")
    return LoginSession(key=payload["qrcode_key"], url=payload["url"])


def svg(url: str) -> str:
    import qrcode
    import qrcode.image.svg

    img = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=10)
    buf = io.BytesIO()
    img.save(buf)
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return "data:image/svg+xml;base64," + encoded


def ascii_qr(url: str) -> str:
    import qrcode

    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make(fit=True)
    out = io.StringIO()
    qr.print_ascii(out=out, invert=True)
    return out.getvalue()


def poll_once(session: LoginSession) -> LoginSession:
    with _session() as s:
        resp = s.get(POLL, params={"qrcode_key": session.key}, timeout=10)
        payload = resp.json()
    data = payload.get("data") or {}
    code = int(data.get("code", -1))
    session.status = STATUS.get(code, "error")
    session.detail = str(data.get("message") or "")
    if session.status == "confirmed":
        cred = _collect_cookies(data.get("url", ""))
        if not cred.get("sessdata"):
            session.status = "error"
            session.detail = "登录成功但未取到 SESSDATA"
            return session
        save(cred)
        session.detail = "登录成功"
    return session


def _collect_cookies(cross_domain: str) -> Dict[str, str]:
    cookies: Dict[str, str] = {}
    if not cross_domain:
        return cookies
    with _session() as s:
        current = cross_domain
        for _ in range(8):
            resp = s.get(current, allow_redirects=False, timeout=15)
            headers = resp.raw.headers.getlist("Set-Cookie") if hasattr(resp.raw.headers, "getlist") else []
            for header in headers:
                name, _, rest = header.partition("=")
                cookies[name.strip()] = rest.split(";", 1)[0].strip()
            location = resp.headers.get("Location")
            if not location or resp.status_code not in (301, 302, 303, 307, 308):
                break
            current = urllib.parse.urljoin(current, location)
    return cookies


def save(cookies: Dict[str, str]) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "sessdata": cookies.get("SESSDATA", ""),
        "bili_jct": cookies.get("bili_jct", ""),
        "dedeuserid": cookies.get("DedeUserID", ""),
        "buvid3": cookies.get("buvid3", ""),
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    CREDENTIAL.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(CREDENTIAL, 0o600)
    except OSError:
        pass


def logged_in() -> bool:
    return CREDENTIAL.exists() and bool(load().get("sessdata"))


def load() -> Dict[str, str]:
    for path in (CREDENTIAL, APP_DIR / "credential.json"):
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
    return {}


def wait(session: LoginSession, timeout: int = 180, interval: float = 2.0) -> LoginSession:
    deadline = time.time() + timeout
    while time.time() < deadline:
        poll_once(session)
        if session.status in ("confirmed", "expired", "error"):
            return session
        time.sleep(interval)
    session.status = "expired"
    session.detail = "二维码超时"
    return session
