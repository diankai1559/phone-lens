"""Local web UI: FastAPI backend + a single static page.

Run:  phonelens serve --port 8000
The report pipeline stays in the CLI; the server only orchestrates jobs.
"""

from __future__ import annotations

import threading
import time
import uuid
from importlib import resources
from typing import Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from . import bili_login
from .chat import Conversation
from .config import load_llm
from .sources import probe_all
from .bilibili import BiliClient

app = FastAPI(title="phonelens", version="0.1.0")

JOBS: Dict[str, dict] = {}
LOGIN: Dict[str, bili_login.LoginSession] = {}
SESSIONS: Dict[str, dict] = {}
LOCK = threading.Lock()


class ReportIn(BaseModel):
    query: str
    top: int = 5
    family: bool = True
    allow_asr: bool = True
    session_id: Optional[str] = None


class ChatIn(BaseModel):
    job_id: Optional[str] = None
    session_id: Optional[str] = None
    question: str


def _update(job_id: str, **fields) -> None:
    with LOCK:
        job = JOBS.get(job_id)
        if job:
            job.update(fields)


def _merge_session(session_id: str, claims: list) -> tuple:
    from .conflict import detect, normalize_claim

    sess = SESSIONS.setdefault(
        session_id, {"prefix": 0, "claims": [], "conflicts": [], "conversation": None}
    )
    index = sess["prefix"]
    prefix = chr(65 + index) if index < 26 else f"Z{index}"
    sess["prefix"] = index + 1

    seen = {
        (c.model, c.metric, str(c.value), tuple(sorted((k, str(v)) for k, v in c.condition.items())), c.source.bvid)
        for c in sess["claims"]
    }
    for claim in claims:
        claim = normalize_claim(claim)
        key = (claim.model, claim.metric, str(claim.value), tuple(sorted((k, str(v)) for k, v in claim.condition.items())), claim.source.bvid)
        if key in seen:
            continue
        seen.add(key)
        claim.id = f"{prefix}{claim.id[-3:]}"
        sess["claims"].append(claim)

    merged, conflicts = detect(sess["claims"])
    sess["claims"], sess["conflicts"] = merged, conflicts
    models = sorted({c.model for c in merged})

    previous = sess.get("conversation")
    conversation = Conversation(load_llm(), merged, conflicts, "、".join(models))
    if previous is not None:
        conversation.history = previous.history
    sess["conversation"] = conversation
    return sess, models


def _worker(job_id: str, req: ReportIn) -> None:
    from .agent import ReportAgent

    stage_map = {
        "official_specs": "probe",
        "search_videos": "discover",
        "collect": "collect",
        "extract_claims": "extract",
        "reconcile": "normalize",
        "detect_conflicts": "normalize",
        "decision": "normalize",
        "decide": "normalize",
        "write_report": "render",
        "verify": "verify",
    }

    def on_event(action: str, detail: str) -> None:
        _update(job_id, stage=stage_map.get(action, action), detail=detail, updated=time.time())

    try:
        agent = ReportAgent(
            req.query,
            top=max(1, min(req.top, 10)),
            allow_asr=req.allow_asr,
            on_event=on_event,
        )
        result = agent.run()
        conversation = None
        session_models: list = []
        if req.session_id:
            sess, session_models = _merge_session(req.session_id, result.claims)
            conversation = sess["conversation"]
        elif load_llm().enabled:
            conversation = Conversation(
                load_llm(), result.claims, result.conflicts, result.resolution.label
            )
        _update(
            job_id,
            status="done",
            stage="done",
            detail=f"{len(result.claims)} claims / {len(result.conflicts)} conflicts",
            report=result.report,
            claims=[c.to_dict() for c in result.claims],
            conflicts=[c.to_dict() for c in result.conflicts],
            trace=[s.__dict__ for s in result.trace],
            verification=result.verification.to_dict(),
            resolution=result.resolution.__dict__,
            session_models=session_models,
            conversation=conversation,
            updated=time.time(),
        )
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI job
        _update(job_id, status="error", error=str(exc), updated=time.time())


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return resources.files("phonelens").joinpath("webui/index.html").read_text(encoding="utf-8")


@app.get("/api/probe")
def probe() -> dict:
    statuses = probe_all(BiliClient())
    return {"sources": [s.__dict__ for s in statuses]}


@app.post("/api/report")
def create_report(req: ReportIn) -> dict:
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="机型不能为空")
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {
        "id": job_id,
        "status": "running",
        "stage": "queued",
        "detail": "排队中",
        "query": req.query,
        "started": time.time(),
        "updated": time.time(),
    }
    threading.Thread(target=_worker, args=(job_id, req), daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/report/{job_id}")
def get_report(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    payload = {k: v for k, v in job.items() if k != "conversation"}
    if job.get("status") == "running" and time.time() - job["started"] > 900:
        payload["status"] = "error"
        payload["error"] = "超时（>15 分钟）"
    return payload


@app.post("/api/chat")
def chat(req: ChatIn) -> dict:
    conversation: Optional[Conversation] = None
    if req.session_id:
        sess = SESSIONS.get(req.session_id)
        if sess:
            conversation = sess.get("conversation")
    if conversation is None and req.job_id:
        job = JOBS.get(req.job_id)
        if job:
            conversation = job.get("conversation")
    if conversation is None:
        raise HTTPException(status_code=400, detail="没有可用的会话，请先生成报告")
    answer = conversation.ask(req.question)
    return {
        "text": answer.text,
        "citations": answer.citations,
        "dropped": answer.dropped,
        "mismatched": answer.mismatched,
        "grounded": answer.grounded,
    }


@app.post("/api/login/qr")
def login_qr() -> dict:
    session = bili_login.create()
    sid = uuid.uuid4().hex[:10]
    LOGIN[sid] = session

    def worker() -> None:
        while session.age < 180:
            bili_login.poll_once(session)
            if session.status in ("confirmed", "expired", "error"):
                return
            time.sleep(2)

    threading.Thread(target=worker, daemon=True).start()
    return {"id": sid, "qr": bili_login.svg(session.url), "status": session.status}


@app.get("/api/login/qr/{sid}")
def login_status(sid: str) -> dict:
    session = LOGIN.get(sid)
    if not session:
        raise HTTPException(status_code=404, detail="二维码已失效，请刷新")
    return {"status": session.status, "detail": session.detail, "age": int(session.age)}


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    import uvicorn

    print(f"phonelens UI → http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="warning")
