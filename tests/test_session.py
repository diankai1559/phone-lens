import pytest

pytest.importorskip("fastapi")

from phonelens.models import Claim, SourceRef
from phonelens.server import _merge_session


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
    session_id = "test-session"
    sess, models = _merge_session(session_id, [_claim("C001", "Xiaomi 18 Pro", "7000")])
    assert models == ["Xiaomi 18 Pro"]
    assert sess["claims"][0].id == "A001"

    sess, models = _merge_session(session_id, [_claim("C001", "Redmi K60 至尊版", "5000")])
    assert models == ["Redmi K60 至尊版", "Xiaomi 18 Pro"]
    ids = sorted(claim.id for claim in sess["claims"])
    assert ids == ["A001", "B001"]


def test_session_deduplicates_same_claim():
    session_id = "test-session-dup"
    _merge_session(session_id, [_claim("C001", "Xiaomi 18 Pro", "7000")])
    sess, _ = _merge_session(session_id, [_claim("C001", "Xiaomi 18 Pro", "7000")])
    assert len(sess["claims"]) == 1
