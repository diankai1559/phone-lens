import pytest

pytest.importorskip("fastapi")

import phonelens.server as server
from phonelens.models import Claim, SourceRef


@pytest.fixture(autouse=True)
def isolated_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "SESS_DIR", tmp_path)
    server.SESSIONS.clear()
    yield
    server.SESSIONS.clear()


def _claim(cid, model, value):
    return Claim(
        id=cid,
        model=model,
        metric="battery",
        value=value,
        unit="mAh",
        condition={},
        quote="q",
        source=SourceRef("BV1", "t", "u", "https://b23.tv/1", "subtitle"),
    )


def test_session_merges_models_with_prefixes():
    sess, models = server._merge_session("s1", [_claim("C001", "Xiaomi 18 Pro", "7000")])
    assert models == ["Xiaomi 18 Pro"]
    assert sess["claims"][0].id == "A001"

    sess, models = server._merge_session("s1", [_claim("C001", "Redmi K60 至尊版", "5000")])
    assert models == ["Redmi K60 至尊版", "Xiaomi 18 Pro"]
    ids = sorted(claim.id for claim in sess["claims"])
    assert ids == ["A001", "B001"]


def test_session_deduplicates_same_claim():
    server._merge_session("s2", [_claim("C001", "Xiaomi 18 Pro", "7000")])
    sess, _ = server._merge_session("s2", [_claim("C001", "Xiaomi 18 Pro", "7000")])
    assert len(sess["claims"]) == 1


def test_session_survives_memory_reset():
    server._merge_session("s3", [_claim("C001", "Xiaomi 18 Pro", "7000")])
    server.SESSIONS.clear()
    sess, models = server._merge_session("s3", [_claim("C001", "Redmi K60 至尊版", "5000")])
    assert models == ["Redmi K60 至尊版", "Xiaomi 18 Pro"]
