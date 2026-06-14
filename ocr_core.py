"""Per-page PDF analysis and selective OCR processing."""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import Callable, Optional

import fitz
import ocrmypdf


LARGE_IMAGE_AREA_RATIO = 0.60

INVISIBLE_TEXT_MARKER = b"3 Tr"


@dataclasses.dataclass
class PageInfo:
    page_num: int
    has_text: bool
    has_large_image: bool
    image_dpi: Optional[int]
    classification: str


def analyze_pdf(
    pdf_path: Path,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> list[PageInfo]:
    pages: list[PageInfo] = []
    with fitz.open(pdf_path) as doc:
        total = doc.page_count
        for idx in range(total):
            page = doc[idx]
            pages.append(_analyze_page(idx + 1, page))
            if progress_cb is not None:
                progress_cb(idx + 1, total)
    return pages


def _page_has_invisible_text_op(page: fitz.Page) -> bool:
    try:
        doc = page.parent
        for xref in page.get_contents():
            stream = doc.xref_stream(xref)
            if stream and INVISIBLE_TEXT_MARKER in stream:
                return True
    except Exception:
        pass
    return False


def _analyze_page(page_num: int, page: fitz.Page) -> PageInfo:
    has_text = bool(page.get_text("text").strip())
    if not has_text:
        has_text = _page_has_invisible_text_op(page)

    page_rect = page.rect
    page_area = page_rect.width * page_rect.height

    best_dpi: Optional[int] = None
    best_area_ratio = 0.0
    has_large_image = False

    try:
        images = page.get_images(full=True)
    except Exception:
        images = []

    for img_info in images:
        xref = img_info[0]
        img_w_px = img_info[2]
        img_h_px = img_info[3]
        if img_w_px <= 0 or img_h_px <= 0:
            continue

        try:
            rects = page.get_image_rects(xref, transform=False)
        except Exception:
            rects = []

        for rect in rects:
            rect_area = rect.width * rect.height
            if page_area <= 0:
                continue
            area_ratio = rect_area / page_area
            if area_ratio < LARGE_IMAGE_AREA_RATIO:
                continue

            disp_w_in = rect.width / 72.0
            disp_h_in = rect.height / 72.0
            if disp_w_in <= 0 or disp_h_in <= 0:
                continue
            dpi = int(round(min(img_w_px / disp_w_in, img_h_px / disp_h_in)))

            if area_ratio > best_area_ratio:
                best_area_ratio = area_ratio
                best_dpi = dpi
                has_large_image = True

    if has_large_image and has_text:
        classification = "ocr_scanned"
    elif has_large_image and not has_text:
        classification = "image_only"
    elif has_text:
        classification = "digital"
    else:
        classification = "blank"

    return PageInfo(
        page_num=page_num,
        has_text=has_text,
        has_large_image=has_large_image,
        image_dpi=best_dpi,
        classification=classification,
    )


def select_pages_to_ocr(pages: list[PageInfo], threshold_dpi: int) -> list[int]:
    selected: list[int] = []
    for p in pages:
        if p.classification == "image_only":
            selected.append(p.page_num)
        elif p.classification == "ocr_scanned":
            if p.image_dpi is None or p.image_dpi < threshold_dpi:
                selected.append(p.page_num)
    return selected


def pages_to_range_string(page_numbers: list[int]) -> str:
    if not page_numbers:
        return ""
    sorted_pages = sorted(set(page_numbers))
    parts: list[str] = []
    start = end = sorted_pages[0]
    for p in sorted_pages[1:]:
        if p == end + 1:
            end = p
            continue
        parts.append(str(start) if start == end else f"{start}-{end}")
        start = end = p
    parts.append(str(start) if start == end else f"{start}-{end}")
    return ",".join(parts)


def make_output_path(input_path: Path, output_dir: Path, target_dpi: int) -> Path:
    return output_dir / f"{input_path.stem}_ocr_{target_dpi}dpi.pdf"


def summarize(pages: list[PageInfo], threshold_dpi: int) -> dict:
    digital = sum(1 for p in pages if p.classification == "digital")
    ocr_scanned = sum(1 for p in pages if p.classification == "ocr_scanned")
    image_only = sum(1 for p in pages if p.classification == "image_only")
    blank = sum(1 for p in pages if p.classification == "blank")

    ocr_below = sum(
        1
        for p in pages
        if p.classification == "ocr_scanned"
        and (p.image_dpi is None or p.image_dpi < threshold_dpi)
    )
    ocr_above = ocr_scanned - ocr_below

    return {
        "total": len(pages),
        "digital": digital,
        "ocr_scanned": ocr_scanned,
        "ocr_below_threshold": ocr_below,
        "ocr_above_threshold": ocr_above,
        "image_only": image_only,
        "blank": blank,
        "needs_ocr": ocr_below + image_only,
    }


def base_estimate_seconds(
    num_pages_to_ocr: int,
    total_pages: int,
    total_size_bytes: int,
    target_dpi: int,
    cpu_count: int | None = None,
) -> float:
    if cpu_count is None:
        cpu_count = os.cpu_count() or 4

    if num_pages_to_ocr <= 0:
        ocr_seconds = 0.0
    else:
        base_per_page_at_300 = 3.5
        dpi_factor = (target_dpi / 300.0) ** 2
        parallel = min(cpu_count, num_pages_to_ocr)
        ocr_seconds = num_pages_to_ocr * base_per_page_at_300 * dpi_factor / max(1, parallel)

    size_mb = max(1.0, total_size_bytes / (1024.0 * 1024.0))
    POST_PROCESS_MB_PER_MIN = 100.0
    post_size_seconds = size_mb * 60.0 / POST_PROCESS_MB_PER_MIN

    page_overhead_seconds = max(0, total_pages) * 0.05

    return ocr_seconds + post_size_seconds + page_overhead_seconds


def estimate_ocr_seconds(
    num_pages_to_ocr: int,
    total_pages: int,
    total_size_bytes: int,
    target_dpi: int,
    cpu_count: int | None = None,
) -> float:
    import timing_stats
    base = base_estimate_seconds(
        num_pages_to_ocr=num_pages_to_ocr,
        total_pages=total_pages,
        total_size_bytes=total_size_bytes,
        target_dpi=target_dpi,
        cpu_count=cpu_count,
    )
    scale, _ = timing_stats.adaptive_scale()
    return base * scale


def run_ocr(
    input_path: Path,
    output_path: Path,
    pages_range: str,
    target_dpi: int,
    plugin_path: Path,
) -> int:
    return ocrmypdf.ocr(
        input_path,
        output_path,
        language=["eng", "kor"],
        redo_ocr=True,
        oversample=target_dpi,
        optimize=1,
        pages=pages_range or None,
        progress_bar=True,
        plugins=[plugin_path],
        output_type="pdf",
    )
