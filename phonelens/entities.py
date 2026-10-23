"""Entity resolution: turn "redmik100" / "iphone18promax" into a model family.

Resolution order:
  1. explicit catalog match (data file, easy to extend)
  2. rule-based parser for well-known naming schemes
  3. low-confidence fallback

A wrong model silently invalidates a whole report, so the resolver reports its
confidence and asks instead of guessing when several variants exist.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

VARIANTS: List[Tuple[str, str]] = [
    ("pro max", "Pro Max"),
    ("promax", "Pro Max"),
    ("pro+", "Pro+"),
    ("proplus", "Pro+"),
    ("pro", "Pro"),
    ("ultra", "Ultra"),
    ("至尊版", "至尊版"),
    ("标准版", "标准版"),
    ("ultimate", "Ultimate"),
    ("mini", "mini"),
    ("plus", "Plus"),
    ("air", "Air"),
    ("max", "Max"),
    ("rs", "RS"),
    ("fe", "FE"),
]

BRANDS: Dict[str, str] = {
    "redmi": "Redmi",
    "红米": "Redmi",
    "xiaomi": "Xiaomi",
    "小米": "Xiaomi",
    "honor": "Honor",
    "荣耀": "Honor",
    "iqoo": "iQOO",
    "爱酷": "iQOO",
    "vivo": "vivo",
    "oppo": "OPPO",
    "oneplus": "OnePlus",
    "一加": "OnePlus",
    "realme": "realme",
    "真我": "realme",
    "huawei": "Huawei",
    "华为": "Huawei",
    "samsung": "Samsung",
    "三星": "Samsung",
    "apple": "Apple",
    "iphone": "Apple",
    "苹果": "Apple",
}

IMPLIED_BRAND = {
    "mate": "Huawei",
    "pura": "Huawei",
    "nova": "Huawei",
    "find": "OPPO",
    "magic": "Honor",
    "iphone": "Apple",
}

SPECIAL_PATTERNS: List[Tuple[str, str, callable]] = [
    (r"iphone\s*(\d{1,2})", "Apple", lambda m: f"iPhone {m.group(1)}"),
    (r"find\s*x?\s*(\d{1,3})", "OPPO", lambda m: f"Find X{m.group(1)}"),
    (r"(mate|pura|nova)\s*(\d{1,3})", "Huawei", lambda m: f"{m.group(1).title()} {m.group(2)}"),
    (r"magic\s*(\d{1,2})", "Honor", lambda m: f"Magic {m.group(1)}"),
    (r"(?:redmi|红米)\s*(note|turbo|k)\s*(\d{1,3})", "Redmi", lambda m: f"{m.group(1).title()}{' ' + m.group(2) if m.group(1) != 'k' else m.group(2)}"),
    (r"(?:vivo\s*)?x\s*(\d{3})", "vivo", lambda m: f"X{m.group(1)}"),
]

GENERIC_RE = re.compile(r"([a-z]{2,8})\s*-?\s*(\d{2,4})")
NOISE_WORDS = ["手机", "系列", "评测", "测评", "深度", "报告", "怎么样", "值不值得", "对比"]


def normalize(text: str) -> str:
    text = str(text or "").strip().lower()
    for word in NOISE_WORDS:
        text = text.replace(word, "")
    text = text.replace("（", "(").replace("）", ")")
    text = re.sub(r"[\s\-_·]+", "", text)
    return text


@dataclass
class Resolution:
    query: str
    family: str
    brand: str
    variants: List[str] = field(default_factory=list)
    explicit_variant: bool = False
    confidence: str = "high"
    note: str = ""
    source: str = "catalog"

    @property
    def needs_confirmation(self) -> bool:
        return len(self.variants) > 1 and not self.explicit_variant

    @property
    def label(self) -> str:
        if len(self.variants) == 1:
            return self.model_name(self.variants[0])
        return f"{self.family} 系列" if self.family else "手机"

    def model_name(self, variant: str) -> str:
        if variant in ("", "标准版"):
            return self.family
        return f"{self.family} {variant}"


def detect_variant(text: str) -> Optional[str]:
    norm = normalize(text)
    for key, canonical in VARIANTS:
        if key in norm:
            return canonical
    return None


def _detect_brand(norm: str) -> Optional[str]:
    best = None
    best_len = 0
    for token, brand in BRANDS.items():
        if token in norm and len(token) > best_len:
            best, best_len = brand, len(token)
    return best


def _parse_unknown(query: str, variant: Optional[str]) -> Resolution:
    norm = normalize(query)
    brand = _detect_brand(norm)
    body = norm
    for token in BRANDS:
        if token in body:
            body = body.replace(token, "")

    series_name = ""
    for pattern, implied, builder in SPECIAL_PATTERNS:
        match = re.search(pattern, norm)
        if match:
            series_name = builder(match)
            brand = brand or implied
            break

    if not series_name:
        match = GENERIC_RE.search(body)
        if match:
            head, number = match.group(1), match.group(2)
            series_name = f"{head.upper() if len(head) <= 3 else head.title()}{number}"

    if not series_name:
        cleaned = re.sub(r"[^\w\u4e00-\u9fa5]+", " ", str(query)).strip()
        series_name = cleaned.title() if cleaned else str(query)

    family = f"{brand} {series_name}".strip() if brand else series_name
    if brand is None:
        family = series_name
        for key, implied in IMPLIED_BRAND.items():
            if key in norm:
                brand = implied
                family = f"{implied} {series_name}"
                break

    return Resolution(
        query=query,
        family=family,
        brand=brand or "Unknown",
        variants=[variant] if variant else [""],
        explicit_variant=bool(variant),
        confidence="medium" if brand else "low",
        note="" if brand else "品牌未识别，建议补充 aliases",
        source="parser",
    )


def resolve(query: str) -> Resolution:
    from .catalog import catalog

    variant = detect_variant(query)
    family, _ = catalog().match(query)

    if family is not None:
        if variant and family.variants and variant not in family.variants:
            return Resolution(
                query=query,
                family=family.name,
                brand=family.brand,
                variants=[variant],
                explicit_variant=True,
                confidence="medium",
                note=f"{variant} 不在目录版本 {family.variants} 中，请确认",
            )
        variants = [variant] if variant else (list(family.variants) or [""])
        return Resolution(
            query=query,
            family=family.name,
            brand=family.brand,
            variants=variants,
            explicit_variant=bool(variant),
        )

    return _parse_unknown(query, variant)


def match_tokens(resolution: Resolution) -> List[str]:
    """Strings that must appear in a candidate video for it to be relevant."""
    from .catalog import catalog

    tokens: List[str] = [normalize(resolution.query)]
    family = catalog().find(resolution.family)
    if family:
        tokens.extend(family.aliases)
    bare = resolution.family
    if resolution.brand and bare.lower().startswith(resolution.brand.lower()):
        bare = bare[len(resolution.brand):]
    tokens.append(normalize(bare))
    tokens.append(normalize(resolution.family))
    cleaned = [t for t in dict.fromkeys(tokens) if len(t) >= 4]
    return cleaned or [normalize(resolution.family)]


def tokens_for(query: str, family: str) -> List[str]:
    """Search keywords used to find relevant videos."""
    res = resolve(query)
    keywords = [query, family]
    if res.variants and len(res.variants) == 1:
        keywords.append(res.model_name(res.variants[0]))
    return list(dict.fromkeys(k for k in keywords if k.strip()))
