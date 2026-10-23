"""Authority reconciliation.

Official specs win on spec-type metrics. Video claims that contradict them are
dropped and recorded, so the report does not present a subtitle typo as a
"conflict" the reader has to resolve.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from .models import Claim

SPEC_METRICS = {
    "soc", "screen_size", "screen_refresh", "screen_peak_brightness",
    "screen_brightness", "battery", "charge_power", "charge_wireless_power",
    "weight", "thickness", "main_camera", "ultrawide", "telephoto",
    "speaker", "usb", "ip_rating", "fingerprint",
}


def apply_official(
    video_claims: List[Claim], official_claims: List[Claim]
) -> Tuple[List[Claim], List[Tuple[Claim, Claim]]]:
    if not official_claims:
        return video_claims, []

    index: Dict[Tuple[str, str], Claim] = {}
    for claim in official_claims:
        index[(claim.model, claim.metric)] = claim

    kept: List[Claim] = []
    overrides: List[Tuple[Claim, Claim]] = []
    for claim in video_claims:
        official = index.get((claim.model, claim.metric))
        if claim.metric in SPEC_METRICS and official is not None:
            overrides.append((claim, official))
            continue
        kept.append(claim)

    return official_claims + kept, overrides
