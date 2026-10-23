import pytest

from phonelens.entities import detect_variant, match_tokens, normalize, resolve


def test_alias_without_spaces():
    res = resolve("redmik100")
    assert res.family == "Redmi K100"
    assert res.brand == "Redmi"
    assert res.needs_confirmation
    assert res.variants == ["标准版", "Pro", "Pro Max"]


def test_chinese_alias_and_variant():
    res = resolve("红米K100 Pro Max 深度评测")
    assert res.family == "Redmi K100"
    assert res.explicit_variant
    assert res.variants == ["Pro Max"]
    assert not res.needs_confirmation
    assert res.model_name("Pro Max") == "Redmi K100 Pro Max"


@pytest.mark.parametrize(
    "query,family,variant",
    [
        ("小米17", "Xiaomi 17", None),
        ("xiaomi 17 pro", "Xiaomi 17", "Pro"),
        ("iphone18promax", "Apple iPhone 18", "Pro Max"),
        ("iPhone 17 Air", "Apple iPhone 17", "Air"),
        ("iqoo16", "iQOO 16", None),
        ("一加16", "OnePlus 16", None),
        ("OPPO Find X9 Ultra", "OPPO Find X9", "Ultra"),
        ("华为mate80pro", "Huawei Mate 80", "Pro"),
        ("荣耀Magic8 Pro", "Honor Magic 8", "Pro"),
        ("vivo X300", "vivo X300", None),
    ],
)
def test_cross_brand_resolution(query, family, variant):
    res = resolve(query)
    assert res.family == family
    assert res.source == "catalog"
    if variant:
        assert res.variants == [variant]
        assert res.explicit_variant


def test_variant_is_not_duplicated():
    res = resolve("小米18 Pro")
    assert res.family == "Xiaomi 18"
    assert res.variants == ["Pro"]
    assert res.model_name("Pro") == "Xiaomi 18 Pro"

    res = resolve("小米18 Pro Max")
    assert res.family == "Xiaomi 18"
    assert res.model_name("Pro Max") == "Xiaomi 18 Pro Max"


def test_parser_for_unlisted_model():
    res = resolve("Redmi Note 15 Pro")
    assert res.family == "Redmi Note 15"
    assert res.brand == "Redmi"
    assert res.variants == ["Pro"]
    assert res.confidence == "medium"


def test_unknown_model_is_low_confidence_not_guessed():
    res = resolve("某品牌 X99")
    assert res.confidence == "low"
    assert res.family


def test_normalize_strips_noise():
    assert normalize("红米 K100 手机 深度评测") == "红米k100"


def test_variant_detection_order():
    assert detect_variant("redmi k100 pro max") == "Pro Max"
    assert detect_variant("redmi k100 pro") == "Pro"


def test_match_tokens_cover_alias_variants():
    tokens = match_tokens(resolve("iphone 18"))
    assert "iphone18" in tokens
    assert all(len(token) >= 4 for token in tokens)
