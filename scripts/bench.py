"""Benchmark: end-to-end runs + entity-resolution accuracy.

    python scripts/bench.py --queries "redmik100,小米17" --top 3

Writes out/benchmark.json and prints a table.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from phonelens.entities import resolve  # noqa: E402
from phonelens.pipeline import run  # noqa: E402

RESOLVE_CASES = [
    ("redmik100", "Redmi K100"),
    ("红米K100 Pro Max", "Redmi K100"),
    ("redmi k90 pro", "Redmi K90"),
    ("小米17", "Xiaomi 17"),
    ("xiaomi 17 ultra", "Xiaomi 17"),
    ("iphone18promax", "Apple iPhone 18"),
    ("苹果17", "Apple iPhone 17"),
    ("iqoo16", "iQOO 16"),
    ("爱酷15", "iQOO 15"),
    ("一加16", "OnePlus 16"),
    ("1+15", "OnePlus 15"),
    ("OPPO Find X9 Ultra", "OPPO Find X9"),
    ("findx9", "OPPO Find X9"),
    ("vivo X300", "vivo X300"),
    ("x300pro", "vivo X300"),
    ("华为mate80pro", "Huawei Mate 80"),
    ("pura90", "Huawei Pura 90"),
    ("荣耀Magic8 Pro", "Honor Magic 8"),
    ("magic8", "Honor Magic 8"),
    ("redmi turbo5", "Redmi Turbo 5"),
    ("redminote15pro", "Redmi Note 15"),
    ("红米 Note 14 Pro", "Redmi Note 14"),
    ("hONOR magic 7", "Honor Magic 7"),
    ("Redmi K100 标准版", "Redmi K100"),
]


def citation_coverage(report: str, claims: list) -> float:
    if not claims:
        return 0.0
    ids = {c.id for c in claims}
    used = set(re.findall(r"\bC\d{3}\b", report))
    return len(used & ids) / len(ids)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", default="redmik100,小米17")
    parser.add_argument("--top", type=int, default=3)
    parser.add_argument("--out", default=str(ROOT / "out" / "benchmark.json"))
    args = parser.parse_args()

    rows = []
    for query in [q.strip() for q in args.queries.split(",") if q.strip()]:
        started = time.perf_counter()
        result = run(query, top=args.top, use_llm=True, allow_asr=False, assume_family=True)
        elapsed = time.perf_counter() - started
        row = {
            "query": query,
            "family": result.resolution.family,
            "sources": len(result.results),
            "claims": len(result.claims),
            "conflicts": len(result.conflicts),
            "methods": sorted({r.method for r in result.results}),
            "citation_coverage": round(citation_coverage(result.report, result.claims), 4),
            "report_chars": len(result.report),
            "verification_ok": result.verification.ok,
            "elapsed_s": round(elapsed, 1),
        }
        rows.append(row)
        print(
            f"{query:<18} {row['sources']} 源  {row['claims']:>3} claim  "
            f"{row['conflicts']:>2} 冲突  覆盖率 {row['citation_coverage']:.0%}  "
            f"{row['elapsed_s']:>5.1f}s",
            file=sys.stderr,
        )

    correct = 0
    failures = []
    for query, expected in RESOLVE_CASES:
        got = resolve(query).family
        if got == expected:
            correct += 1
        else:
            failures.append({"query": query, "expected": expected, "got": got})

    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "resolve": {
            "total": len(RESOLVE_CASES),
            "correct": correct,
            "accuracy": round(correct / len(RESOLVE_CASES), 4),
            "failures": failures,
        },
        "runs": rows,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nentity resolution: {correct}/{len(RESOLVE_CASES)}")
    print(f"saved: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
