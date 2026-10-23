from phonelens.conflict import detect, explain
from phonelens.models import Claim, SourceRef

SOURCE = SourceRef("BV1", "t", "u", "https://b23.tv/1", "subtitle")


def _claim(cid, metric, value, condition=None, unit=""):
    return Claim(
        id=cid,
        model="Redmi K100 Pro",
        metric=metric,
        value=value,
        unit=unit,
        condition=condition or {},
        quote="q",
        source=SOURCE,
    )


def test_same_condition_creates_value_gap():
    claims = [
        _claim("C1", "fps", "59.9", {"game": "原神", "resolution": "880P", "duration_min": 20}),
        _claim("C2", "fps", "52.0", {"game": "原神", "resolution": "880P", "duration_min": 20}),
    ]
    _, conflicts = detect(claims)
    assert [c.reason for c in conflicts] == ["value_gap"]
    assert "真实分歧" in explain(conflicts[0])


def test_different_conditions_is_not_a_contradiction():
    claims = [
        _claim("C1", "battery_life", "9小时56分", {"brightness_nit": 400, "network": "5G"}),
        _claim("C2", "battery_life", "6小时10分", {"brightness_nit": 800, "network": "5G"}),
    ]
    _, conflicts = detect(claims)
    assert conflicts
    assert conflicts[0].reason == "condition_mismatch"
    assert "不可直接比较" in explain(conflicts[0])


def test_close_values_are_not_flagged():
    claims = [
        _claim("C1", "battery", "9070mAh"),
        _claim("C2", "battery", "9000mAh"),
    ]
    _, conflicts = detect(claims)
    assert conflicts == []


def test_text_conflict():
    claims = [
        _claim("C1", "soc", "骁龙8 Elite Gen5"),
        _claim("C2", "soc", "骁龙8 Elite Gen5 V"),
    ]
    _, conflicts = detect(claims)
    assert conflicts and conflicts[0].reason == "text_mismatch"
