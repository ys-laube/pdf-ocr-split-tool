"""Persistent OCR timing history for machine-adaptive estimates.

Records (base_predicted, actual) pairs in ``~/.pdf-ocr-tool/timing_history.json``.
On any machine, the first run uses default model constants; subsequent runs
multiply the base estimate by the median scale factor from past runs.
"""

from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

STATS_DIR = Path.home() / ".pdf-ocr-tool"
STATS_PATH = STATS_DIR / "timing_history.json"

MAX_HISTORY = 10
USE_LAST_N = 5

MIN_ACCEPTABLE_RATIO = 0.3
MAX_ACCEPTABLE_RATIO = 5.0


def _load_runs() -> list[dict]:
    if not STATS_PATH.exists():
        return []
    try:
        data = json.loads(STATS_PATH.read_text(encoding="utf-8"))
        runs = data.get("runs", [])
        return runs if isinstance(runs, list) else []
    except Exception:
        return []


def _save_runs(runs: list[dict]) -> None:
    try:
        STATS_DIR.mkdir(parents=True, exist_ok=True)
        STATS_PATH.write_text(
            json.dumps({"runs": runs}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def record(
    pages_to_ocr: int,
    total_pages: int,
    total_bytes: int,
    target_dpi: int,
    base_predicted_seconds: float,
    actual_seconds: float,
    cpu_count: int,
) -> None:
    runs = _load_runs()
    runs.append(
        {
            "timestamp": time.time(),
            "pages_to_ocr": pages_to_ocr,
            "total_pages": total_pages,
            "total_bytes": total_bytes,
            "target_dpi": target_dpi,
            "base_predicted_seconds": round(base_predicted_seconds, 2),
            "actual_seconds": round(actual_seconds, 2),
            "cpu_count": cpu_count,
        }
    )
    runs = runs[-MAX_HISTORY:]
    _save_runs(runs)


def adaptive_scale() -> tuple[float, int]:
    runs = _load_runs()[-USE_LAST_N:]
    ratios: list[float] = []
    for r in runs:
        base = r.get("base_predicted_seconds", 0)
        actual = r.get("actual_seconds", 0)
        if base > 1 and actual > 1:
            ratio = actual / base
            if MIN_ACCEPTABLE_RATIO <= ratio <= MAX_ACCEPTABLE_RATIO:
                ratios.append(ratio)
    if not ratios:
        return 1.0, 0
    return statistics.median(ratios), len(ratios)
