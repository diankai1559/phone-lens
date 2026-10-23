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
from typing import Dict, List, Optional

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


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Referer": "https://www.bilibili.com/"})
    return s


@dataclass
class LoginSession:
    key: str
    url: str
    http: requests.Session = field(default_factory=_session, repr=False, compare=False)
    created: float = field(default_factory=time.time)
    status: str = "waiting"
    detail: str = ""

    @property
    def age(self) -> float:
        return time.time() - self.created


def create() -> LoginSession:
    s = _session()
    resp = s.get(GENERATE, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    payload = data.get("data") or {}
    if not payload.get("url") or not payload.get("qrcode_key"):
        raise RuntimeError(f"生成二维码失败: {data.get('message')}")
    return LoginSession(key=payload["qrcode_key"], url=payload["url"], http=s)


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
    resp = session.http.get(POLL, params={"qrcode_key": session.key}, timeout=10)
    payload = resp.json()
    data = payload.get("data") or {}
    code = int(data.get("code", -1))

    if code != 0:
        session.status = STATUS.get(code, "error")
        session.detail = str(data.get("message") or "")
        return session

    session.status = "finalizing"
    session.detail = "正在写入凭证"

    cookies = _collect_cookies(session.http, str(data.get("url") or ""))
    for name, value in resp.cookies.get_dict().items():
        cookies.setdefault(name, value)
    if "SESSDATA" not in cookies:
        try:
            session.http.get("https://www.bilibili.com/", timeout=10)
        except requests.RequestException:
            pass
        for name, value in session.http.cookies.get_dict().items():
            cookies.setdefault(name, value)

    if not cookies.get("SESSDATA"):
        names = ",".join(list(cookies)[:6]) or "无"
        session.status = "error"
        session.detail = f"未取到 SESSDATA（收到 {len(cookies)} 个 cookie：{names}）"
        return session

    ok, message = _validate(cookies)
    if not ok:
        session.status = "error"
        session.detail = f"cookie 校验失败：{message or 'nav 未登录'}"
        return session

    save(cookies)
    session.status = "confirmed"
    session.detail = "登录成功"
    return session


def _parse_set_cookie(headers: List[str]) -> Dict[str, str]:
    cookies: Dict[str, str] = {}
    for header in headers:
        name, _, rest = header.partition("=")
        if not name or not rest:
            continue
        cookies[name.strip()] = rest.split(";", 1)[0].strip()
    return cookies


def _collect_cookies(http: requests.Session, cross_domain: str) -> Dict[str, str]:
    cookies: Dict[str, str] = {}
    if cross_domain:
        current = cross_domain
        for _ in range(8):
            resp = http.get(current, allow_redirects=False, timeout=15)
            headers = resp.raw.headers.getlist("Set-Cookie") if hasattr(resp.raw.headers, "getlist") else []
            cookies.update(_parse_set_cookie(headers))
            cookies.update(resp.cookies.get_dict())
            location = resp.headers.get("Location")
            if not location or resp.status_code not in (301, 302, 303, 307, 308):
                break
            current = urllib.parse.urljoin(current, location)
    for name, value in http.cookies.get_dict().items():
        cookies.setdefault(name, value)
    return cookies


def _validate(cookies: Dict[str, str]) -> tuple:
    try:
        resp = requests.get(
            "https://api.bilibili.com/x/web-interface/nav",
            headers={"User-Agent": UA},
            cookies=cookies,
            timeout=10,
        )
        payload = resp.json()
    except (requests.RequestException, ValueError) as exc:
        return False, str(exc)[:80]
    if payload.get("code") != 0:
        return False, str(payload.get("message") or "")
    return bool((payload.get("data") or {}).get("isLogin")), "nav isLogin=false"


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
