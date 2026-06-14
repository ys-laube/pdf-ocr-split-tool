"""PDF merge and bookmark-based split utilities (PyMuPDF-backed)."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import fitz


_ILLEGAL_FILENAME_CHARS = '<>:"/\\|?*'


def sanitize_filename(name: str, fallback: str = "untitled") -> str:
    cleaned = "".join("_" if c in _ILLEGAL_FILENAME_CHARS or ord(c) < 32 else c for c in name)
    cleaned = cleaned.strip().strip(".").strip()
    if not cleaned:
        return fallback
    if len(cleaned) > 150:
        cleaned = cleaned[:150].rstrip()
    return cleaned


def merge_pdfs(
    input_paths: list[Path],
    output_path: Path,
    progress_cb: Callable[[int, int], None] | None = None,
) -> int:
    if not input_paths:
        raise ValueError("병합할 PDF가 하나도 선택되지 않았습니다.")
    for p in input_paths:
        if not p.exists():
            raise FileNotFoundError(f"파일 없음: {p}")

    total = len(input_paths)
    merged = fitz.open()
    try:
        for idx, src_path in enumerate(input_paths, start=1):
            with fitz.open(src_path) as src:
                merged.insert_pdf(src)
            if progress_cb is not None:
                progress_cb(idx, total)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        merged.save(output_path, deflate=True, garbage=3)
    finally:
        merged.close()
    return total


def get_bookmark_tree(pdf_path: Path) -> list[tuple[int, str, int]]:
    with fitz.open(pdf_path) as doc:
        toc = doc.get_toc(simple=True)
    return [(int(lvl), str(title), int(page)) for lvl, title, page in toc]


def bookmarks_at_level(toc: list[tuple[int, str, int]], level: int) -> list[tuple[str, int]]:
    return [(title, page) for lvl, title, page in toc if lvl == level]


def split_by_bookmarks(
    pdf_path: Path,
    output_dir: Path,
    level: int,
    progress_cb: Callable[[int, int, str], None] | None = None,
    name_prefix_with_index: bool = True,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_paths: list[Path] = []

    with fitz.open(pdf_path) as doc:
        total_pages = doc.page_count
        toc = doc.get_toc(simple=True)
        selected = [(title, int(page)) for lvl, title, page in toc if int(lvl) == level]

        if not selected:
            raise ValueError(
                f"레벨 {level} 북마크가 없습니다. "
                f"PDF에 존재하는 북마크 레벨: {sorted({int(lvl) for lvl, _, _ in toc}) or '없음'}"
            )

        for i, (title, start_page) in enumerate(selected):
            if i + 1 < len(selected):
                end_page = selected[i + 1][1] - 1
            else:
                end_page = total_pages

            start_page = max(1, min(start_page, total_pages))
            end_page = max(start_page, min(end_page, total_pages))

            safe = sanitize_filename(title)
            if name_prefix_with_index:
                filename = f"{i + 1:03d}_{safe}.pdf"
            else:
                filename = f"{safe}.pdf"
            out_path = output_dir / filename

            counter = 2
            while out_path.exists():
                stem = out_path.stem
                out_path = output_dir / f"{stem}_{counter}.pdf"
                counter += 1

            sub_doc = fitz.open()
            try:
                sub_doc.insert_pdf(doc, from_page=start_page - 1, to_page=end_page - 1)
                sub_doc.save(out_path, deflate=True, garbage=3)
            finally:
                sub_doc.close()

            output_paths.append(out_path)
            if progress_cb is not None:
                progress_cb(i + 1, len(selected), title)

    return output_paths


def parse_dnd_paths(data: str) -> list[str]:
    paths: list[str] = []
    i = 0
    n = len(data)
    while i < n:
        c = data[i]
        if c.isspace():
            i += 1
            continue
        if c == "{":
            end = data.find("}", i + 1)
            if end == -1:
                paths.append(data[i + 1:])
                break
            paths.append(data[i + 1:end])
            i = end + 1
            continue
        end = i
        while end < n and not data[end].isspace():
            end += 1
        paths.append(data[i:end])
        i = end
    return paths
