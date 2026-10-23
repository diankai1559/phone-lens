"""Device catalog: a data file, not code.

Adding a new model should be a one-line JSON change. The bundled catalog ships
with common 2025-2026 flagships; user entries in ~/.phonelens/devices.json are
merged on top.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from typing import Dict, List, Optional, Tuple

from .config import APP_DIR
from .entities import normalize


@dataclass
class Family:
    name: str
    brand: str
    aliases: List[str]
    variants: List[str]


class Catalog:
    def __init__(self) -> None:
        self.families: Dict[str, Family] = {}
        self._load(resources.files("phonelens").joinpath("data/devices.json"))
        user_path = APP_DIR / "devices.json"
        if user_path.exists():
            self._load(user_path)

    def _load(self, path) -> None:
        try:
            text = path.read_text(encoding="utf-8") if hasattr(path, "read_text") else str(path)
            data = json.loads(text)
        except (OSError, json.JSONDecodeError):
            return
        for name, raw in (data.get("families") or {}).items():
            self.families[name] = Family(
                name=name,
                brand=str(raw.get("brand", name.split(" ")[0])),
                aliases=[normalize(a) for a in raw.get("aliases") or []],
                variants=[str(v) for v in raw.get("variants") or []],
            )

    def match(self, text: str) -> Tuple[Optional[Family], int]:
        norm = normalize(text)
        best: Optional[Family] = None
        best_len = 0
        for family in self.families.values():
            for alias in family.aliases:
                if alias and alias in norm and len(alias) > best_len:
                    best, best_len = family, len(alias)
        return best, best_len

    def find(self, name: str) -> Optional[Family]:
        return self.families.get(name)

    def add(self, name: str, brand: str, aliases: List[str], variants: List[str]) -> Family:
        family = Family(name, brand, [normalize(a) for a in aliases], variants)
        self.families[name] = family
        self._save_user()
        return family

    def _save_user(self) -> None:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        payload = {"families": {}}
        for name, family in self.families.items():
            payload["families"][name] = {
                "brand": family.brand,
                "aliases": family.aliases,
                "variants": family.variants,
            }
        (APP_DIR / "devices.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )


_CATALOG: Optional[Catalog] = None


def catalog() -> Catalog:
    global _CATALOG
    if _CATALOG is None:
        _CATALOG = Catalog()
    return _CATALOG
