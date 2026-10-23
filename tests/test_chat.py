from phonelens.chat import Conversation
from phonelens.config import LLMConfig
from phonelens.models import Claim, Conflict, SourceRef

SOURCE = SourceRef("BV1y58q6pEJ1", "K100 使用报告", "大米评测", "https://www.bilibili.com/video/BV1y58q6pEJ1", "subtitle")


def _claim(cid, metric, value, unit="", condition=None):
    return Claim(
        id=cid,
        model="Redmi K100 Pro Max",
        metric=metric,
        value=value,
        unit=unit,
        condition=condition or {},
        quote="q",
        source=SOURCE,
    )


def _conversation():
    claims = [
        _claim("C001", "battery", "9070", "mAh"),
        _claim("C002", "battery_life", "10.33", "h", {"brightness_nit": 400, "network": "5G"}),
        _claim("C003", "charge_time", "74", "min", {"from_soc": 0, "to_soc": 100}),
    ]
    conflicts = [Conflict("Redmi K100 Pro Max", "battery_life", [claims[1]], "condition_mismatch")]
    return Conversation(LLMConfig("http://x", "k", "m"), claims, conflicts, "Redmi K100 Pro Max")


def test_evidence_contains_claims_and_conflicts():
    text = _conversation().evidence("续航怎么样")
    assert "C001" in text
    assert "已知分歧" in text
    assert "Redmi K100 Pro Max" in text


def test_invalid_citation_is_dropped():
    conv = _conversation()
    valid, dropped = conv._check("电池 9070mAh [C001]，但 C999 是编的")
    assert valid == ["C001"]
    assert dropped == ["C999"]


def test_ungrounded_answer_is_flagged():
    conv = _conversation()
    text = conv._flag("这台手机能用三天。", [], [], "续航多久")
    assert "未引用任何 claim" in text


def test_chitchat_is_not_flagged():
    conv = _conversation()
    text = conv._flag("你好，我可以帮你分析。", [], [], "你好")
    assert "未引用任何 claim" not in text


def test_history_is_bounded():
    conv = _conversation()
    conv.max_turns = 2
    for i in range(5):
        conv.history.append({"role": "user", "content": str(i)})
        conv.history.append({"role": "assistant", "content": str(i)})
    conv.history = conv.history[-(conv.max_turns * 2):]
    assert len(conv.history) == 4
    assert conv.history[-1]["content"] == "4"


def test_grounding_requires_existing_ids():
    conv = _conversation()
    valid, _ = conv._check("[C001] 和 [C002]")
    assert set(valid) == {"C001", "C002"}


def test_semantic_mismatch_is_detected():
    conv = _conversation()
    assert conv._semantic_check("电池是 9070mAh [C001]") == []
    assert conv._semantic_check("续航 13.8 小时 [C001]") == ["C001"]
