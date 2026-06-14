"""PDF OCR Tool - drag-and-drop GUI for selective high-DPI OCR of textbooks."""

from __future__ import annotations

import queue
import sys
import threading
import time
import traceback
from pathlib import Path


if sys.platform == "darwin":
    KOREAN_FONT = "Apple SD Gothic Neo"
elif sys.platform == "win32":
    KOREAN_FONT = "Malgun Gothic"
else:
    KOREAN_FONT = "NanumGothic"


def _silence_subprocess_windows() -> None:
    if sys.platform != "win32":
        return
    import subprocess

    CREATE_NO_WINDOW = 0x08000000
    original_init = subprocess.Popen.__init__

    def patched(self, *args, **kwargs):
        flags = kwargs.get("creationflags", 0) or 0
        kwargs["creationflags"] = flags | CREATE_NO_WINDOW
        return original_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = patched


_silence_subprocess_windows()


_caffeinate_proc = None


def _set_sleep_prevention(enable: bool) -> None:
    global _caffeinate_proc
    if sys.platform == "win32":
        import ctypes

        ES_CONTINUOUS = 0x80000000
        ES_SYSTEM_REQUIRED = 0x00000001
        ES_AWAYMODE_REQUIRED = 0x00000040
        try:
            if enable:
                ctypes.windll.kernel32.SetThreadExecutionState(
                    ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED
                )
            else:
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        except Exception:
            pass
    elif sys.platform == "darwin":
        import subprocess
        try:
            if enable:
                if _caffeinate_proc is None:
                    _caffeinate_proc = subprocess.Popen(["caffeinate", "-i", "-s"])
            else:
                if _caffeinate_proc is not None:
                    _caffeinate_proc.terminate()
                    _caffeinate_proc = None
        except Exception:
            pass


import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

PROJECT_DIR = Path(__file__).resolve().parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import progress_bridge
import ocr_core
import timing_stats
import pdf_tools

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_AVAILABLE = True
except Exception:
    DND_AVAILABLE = False


PLUGIN_PATH = PROJECT_DIR / "progress_plugin.py"
DPI_CHOICES = [300, 450, 600]

PHASE_WEIGHTS = {
    "Scanning contents": 0.02,
    "OCR": 0.78,
    "PDF/A": 0.02,
    "Linearizing": 0.05,
    "Recompressing JPEGs": 0.07,
    "Deflating JPEGs": 0.04,
    "JBIG2": 0.02,
}

PHASE_LABELS = {
    "Scanning contents": "PDF 콘텐츠 스캔",
    "OCR": "OCR 처리",
    "PDF/A": "PDF/A 변환",
    "Linearizing": "PDF 최종 정리",
    "Recompressing JPEGs": "JPEG 재압축",
    "Deflating JPEGs": "JPEG 압축",
    "JBIG2": "JBIG2 압축",
    "Optimize": "용량 최적화",
}


def canonical_phase(desc: str) -> tuple[str, float, str]:
    for key, weight in PHASE_WEIGHTS.items():
        if key.lower() in desc.lower():
            return key, weight, PHASE_LABELS.get(key, key)
    return desc or "unknown", 0.0, desc or "처리 중"


def format_minsec(seconds: float | None) -> str:
    import math
    if seconds is None:
        return "계산 중..."
    try:
        if math.isnan(seconds) or math.isinf(seconds) or seconds < 0:
            return "계산 중..."
        seconds = int(seconds)
    except (TypeError, ValueError):
        return "계산 중..."
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}분 {sec:02d}초"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}시간 {minutes}분 {sec:02d}초"


def format_minutes(seconds: float) -> str:
    if seconds <= 0:
        return "0분"
    if seconds < 60:
        return "1분 미만"
    return f"약 {int(round(seconds / 60))}분"


def format_bytes(num_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024.0:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} TB"


class OverallProgress:
    def __init__(self) -> None:
        self.phase_done: dict[str, float] = {}
        self.start_time: float | None = None

    def start(self) -> None:
        self.start_time = time.time()
        self.phase_done.clear()

    def note_phase_start(self, desc: str, total: int | float | None) -> None:
        key, weight, _ = canonical_phase(desc)
        if weight <= 0:
            return
        if not total or total <= 0:
            self.phase_done[key] = 1.0
        else:
            self.phase_done.setdefault(key, 0.0)

    def update(self, desc: str, current: float, total: float) -> None:
        if not total or total <= 0:
            return
        key, weight, _ = canonical_phase(desc)
        if weight <= 0:
            return
        self.phase_done[key] = min(1.0, current / total)

    def finish_phase(self, desc: str) -> None:
        key, weight, _ = canonical_phase(desc)
        if weight > 0:
            self.phase_done[key] = 1.0

    def percent(self) -> float:
        return sum(self.phase_done.get(k, 0.0) * w for k, w in PHASE_WEIGHTS.items()) * 100.0

    def elapsed(self) -> float:
        return 0.0 if self.start_time is None else time.time() - self.start_time

    def estimated_total(self) -> float | None:
        pct = self.percent()
        if pct < 1.0:
            return None
        return self.elapsed() / (pct / 100.0)

    def estimated_remaining(self) -> float | None:
        tot = self.estimated_total()
        return None if tot is None else max(0.0, tot - self.elapsed())


class OCRApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("PDF OCR 도구 — OCR / 병합 / 분할 (한/영)")
        self.root.geometry("880x820")
        self.root.minsize(760, 680)

        self.input_path: Path | None = None
        self.output_dir: Path | None = None

        self.threshold_dpi = tk.IntVar(value=300)
        self.target_dpi = tk.IntVar(value=600)

        self.progress_queue: queue.Queue = queue.Queue()
        progress_bridge.progress_queue = self.progress_queue

        self.cached_pages: list[ocr_core.PageInfo] | None = None
        self.cached_for: tuple[Path, float] | None = None

        self.is_running = False
        self.overall = OverallProgress()
        self.current_phase_label = ""
        self.current_phase_progress = (0, 0, "")

        self.merge_files: list[Path] = []
        self.split_input_path: Path | None = None
        self.split_output_dir: Path | None = None
        self.split_bookmarks: list[tuple[int, str, int]] = []
        self.split_level = tk.IntVar(value=1)
        self.split_use_prefix = tk.BooleanVar(value=False)

        self._build_main_layout()
        self._tick()

    def _build_main_layout(self) -> None:
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=10)

        ocr_tab = ttk.Frame(self.notebook)
        merge_tab = ttk.Frame(self.notebook)
        split_tab = ttk.Frame(self.notebook)

        self.notebook.add(ocr_tab, text="  OCR  ")
        self.notebook.add(merge_tab, text="  📑 PDF 병합  ")
        self.notebook.add(split_tab, text="  ✂ PDF 분할 (북마크 기준)  ")

        self._build_ocr_tab(ocr_tab)
        self._build_merge_tab(merge_tab)
        self._build_split_tab(split_tab)

    def _build_ocr_tab(self, parent) -> None:
        pad = {"padx": 12, "pady": 6}

        drop_frame = tk.LabelFrame(
            parent, text=" PDF 파일 선택 ",
            font=(KOREAN_FONT, 10, "bold"), padx=10, pady=10,
        )
        drop_frame.pack(fill="x", **pad)

        self.drop_label = tk.Label(
            drop_frame,
            text=("이곳에 PDF 파일을 끌어다 놓으세요" if DND_AVAILABLE
                  else "아래 [파일 선택] 버튼으로 PDF를 선택하세요"),
            bg="#f0f4f8", fg="#444",
            font=(KOREAN_FONT, 12), height=3, relief="ridge", bd=2,
        )
        self.drop_label.pack(fill="x", pady=(0, 8))
        if DND_AVAILABLE:
            self.drop_label.drop_target_register(DND_FILES)
            self.drop_label.dnd_bind("<<Drop>>", self._on_drop)

        path_frame = tk.Frame(drop_frame)
        path_frame.pack(fill="x")
        tk.Label(path_frame, text="입력 파일:", width=10, anchor="w").pack(side="left")
        self.input_label = tk.Label(path_frame, text="(선택되지 않음)", fg="#888", anchor="w")
        self.input_label.pack(side="left", fill="x", expand=True)
        tk.Button(path_frame, text="파일 선택", command=self._browse_input, width=12).pack(side="right")

        out_frame = tk.Frame(drop_frame)
        out_frame.pack(fill="x", pady=(6, 0))
        tk.Label(out_frame, text="출력 폴더:", width=10, anchor="w").pack(side="left")
        self.output_label = tk.Label(out_frame, text="(선택되지 않음)", fg="#888", anchor="w")
        self.output_label.pack(side="left", fill="x", expand=True)
        tk.Button(out_frame, text="폴더 선택", command=self._browse_output, width=12).pack(side="right")

        settings_frame = tk.LabelFrame(
            parent, text=" OCR 설정 ",
            font=(KOREAN_FONT, 10, "bold"), padx=10, pady=10,
        )
        settings_frame.pack(fill="x", **pad)

        row1 = tk.Frame(settings_frame)
        row1.pack(fill="x", pady=4)
        tk.Label(row1, text="임계값 [a] DPI:", width=18, anchor="w",
                 font=(KOREAN_FONT, 10)).pack(side="left")
        for v in DPI_CHOICES:
            tk.Radiobutton(row1, text=f"{v}", variable=self.threshold_dpi, value=v).pack(side="left", padx=4)
        tk.Label(row1, text="  ← 이 값 미만으로 OCR된 페이지는 재처리",
                 fg="#666", font=(KOREAN_FONT, 9)).pack(side="left", padx=8)

        row2 = tk.Frame(settings_frame)
        row2.pack(fill="x", pady=4)
        tk.Label(row2, text="목표 [b] DPI:", width=18, anchor="w",
                 font=(KOREAN_FONT, 10)).pack(side="left")
        for v in DPI_CHOICES:
            tk.Radiobutton(row2, text=f"{v}", variable=self.target_dpi, value=v).pack(side="left", padx=4)
        tk.Label(row2, text="  ← 이 해상도로 재OCR 수행 (출력 파일명에 포함)",
                 fg="#666", font=(KOREAN_FONT, 9)).pack(side="left", padx=8)

        btn_frame = tk.Frame(parent)
        btn_frame.pack(fill="x", **pad)
        self.analyze_btn = tk.Button(
            btn_frame, text="📊 페이지 분석만", command=self._on_analyze_only,
            width=18, height=2,
        )
        self.analyze_btn.pack(side="left", padx=4)
        self.start_btn = tk.Button(
            btn_frame, text="▶ OCR 시작", command=self._on_start,
            width=18, height=2, bg="#1976d2", fg="white",
            font=(KOREAN_FONT, 10, "bold"),
        )
        self.start_btn.pack(side="left", padx=4)
        self.cancel_btn = tk.Button(
            btn_frame, text="⏹ 중단", command=self._on_cancel,
            width=12, height=2, state="disabled",
        )
        self.cancel_btn.pack(side="left", padx=4)

        prog_frame = tk.LabelFrame(
            parent, text=" 진행 상황 (전체) ",
            font=(KOREAN_FONT, 10, "bold"), padx=10, pady=10,
        )
        prog_frame.pack(fill="x", **pad)

        self.progress_bar = ttk.Progressbar(prog_frame, mode="determinate", length=600, maximum=100)
        self.progress_bar.pack(fill="x", pady=4)

        self.percent_label = tk.Label(
            prog_frame, text="대기 중",
            font=(KOREAN_FONT, 12, "bold"), fg="#1976d2",
        )
        self.percent_label.pack(anchor="w", pady=(4, 2))

        self.phase_label = tk.Label(
            prog_frame, text="단계: -",
            font=(KOREAN_FONT, 10), fg="#444",
        )
        self.phase_label.pack(anchor="w")

        self.time_label = tk.Label(
            prog_frame,
            text="경과: -    예상 총 시간: -    남은 시간: -",
            font=(KOREAN_FONT, 10), fg="#444",
        )
        self.time_label.pack(anchor="w", pady=(2, 0))

        log_frame = tk.LabelFrame(
            parent, text=" 로그 ",
            font=(KOREAN_FONT, 10, "bold"), padx=10, pady=10,
        )
        log_frame.pack(fill="both", expand=True, **pad)
        self.log_text = scrolledtext.ScrolledText(
            log_frame, height=10,
            font=("Consolas", 9), wrap="word", state="disabled",
        )
        self.log_text.pack(fill="both", expand=True)

    def _log(self, msg: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{timestamp}] {msg}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _on_drop(self, event) -> None:
        raw = event.data.strip()
        if raw.startswith("{") and raw.endswith("}"):
            raw = raw[1:-1]
        path = Path(raw)
        if path.suffix.lower() != ".pdf":
            messagebox.showerror("형식 오류", "PDF 파일만 처리할 수 있습니다.")
            return
        self._set_input(path)

    def _browse_input(self) -> None:
        path = filedialog.askopenfilename(
            title="OCR할 PDF 파일 선택",
            filetypes=[("PDF 파일", "*.pdf"), ("모든 파일", "*.*")],
        )
        if path:
            self._set_input(Path(path))

    def _browse_output(self) -> None:
        path = filedialog.askdirectory(title="출력 폴더 선택")
        if path:
            self.output_dir = Path(path)
            self.output_label.config(text=str(self.output_dir), fg="black")

    def _set_input(self, path: Path) -> None:
        if not path.exists():
            messagebox.showerror("파일 오류", f"파일을 찾을 수 없습니다:\n{path}")
            return
        self.input_path = path
        self.cached_pages = None
        self.cached_for = None
        try:
            size = path.stat().st_size
            self.input_label.config(text=f"{path}  ({format_bytes(size)})", fg="black")
        except Exception:
            self.input_label.config(text=str(path), fg="black")
        self._log(f"입력 파일 선택: {path.name}")
        if self.output_dir is None:
            self.output_dir = path.parent
            self.output_label.config(text=str(self.output_dir), fg="black")

    def _validate(self) -> bool:
        if self.input_path is None or not self.input_path.exists():
            messagebox.showwarning("입력 누락", "먼저 PDF 파일을 선택하세요.")
            return False
        if self.output_dir is None:
            messagebox.showwarning("출력 폴더", "출력 폴더를 선택하세요.")
            return False
        if not self.output_dir.exists():
            try:
                self.output_dir.mkdir(parents=True, exist_ok=True)
            except Exception as exc:
                messagebox.showerror("출력 폴더 오류", f"출력 폴더를 만들 수 없습니다:\n{exc}")
                return False
        out_path = ocr_core.make_output_path(self.input_path, self.output_dir, self.target_dpi.get())
        if out_path.resolve() == self.input_path.resolve():
            messagebox.showerror(
                "경로 충돌",
                "출력 파일이 원본 파일과 같은 경로입니다.\n"
                "출력 폴더 또는 목표 DPI를 변경하세요.",
            )
            return False
        return True

    def _on_analyze_only(self) -> None:
        if self.input_path is None or not self.input_path.exists():
            messagebox.showwarning("입력 누락", "먼저 PDF 파일을 선택하세요.")
            return
        if self.is_running:
            return
        self._set_running(True)
        threading.Thread(target=self._do_analyze_only, daemon=True).start()

    def _on_start(self) -> None:
        if not self._validate() or self.is_running:
            return
        self._set_running(True)
        threading.Thread(target=self._do_full_run, daemon=True).start()

    def _on_cancel(self) -> None:
        if not self.is_running:
            return
        if not messagebox.askyesno(
            "중단 확인",
            "작업을 강제 중단하시겠습니까?\n"
            "(이미 시작된 OCR 작업은 완전히 중단되기까지 시간이 걸릴 수 있고,\n"
            "임시 파일이 남을 수 있습니다.)",
        ):
            return
        self._log("⚠ 사용자 중단 요청 — 백그라운드 작업은 자체 종료까지 시간이 걸립니다.")
        self._set_running(False)

    def _set_running(self, running: bool) -> None:
        self.is_running = running
        state_busy = "disabled" if running else "normal"
        state_cancel = "normal" if running else "disabled"
        self.analyze_btn.config(state=state_busy)
        self.start_btn.config(state=state_busy)
        self.cancel_btn.config(state=state_cancel)

    def _ensure_analysis(self) -> list[ocr_core.PageInfo]:
        assert self.input_path is not None
        mtime = self.input_path.stat().st_mtime
        if self.cached_for == (self.input_path, mtime) and self.cached_pages is not None:
            self._post_log(f"이전 분석 결과 재사용 (페이지 {len(self.cached_pages)}개)")
            return self.cached_pages

        self._post_log(f"분석 시작: {self.input_path.name}")
        self._post_apply(lambda: self.percent_label.config(text="PDF 페이지 분석 중"))
        self._post_apply(lambda: self.phase_label.config(text="단계: PDF 페이지 분석"))
        self._post_apply(lambda: self.progress_bar.configure(value=0))
        analysis_start = time.time()

        def cb(done: int, total: int) -> None:
            pct = (done / total) * 100 if total else 0
            elapsed = time.time() - analysis_start
            self._post_apply(lambda: self.progress_bar.configure(value=pct))
            self._post_apply(lambda d=done, t=total: self.percent_label.config(
                text=f"PDF 페이지 분석 중  {d}/{t}"
            ))
            self._post_apply(lambda e=elapsed: self.time_label.config(
                text=f"경과: {format_minsec(e)}    예상 총 시간: -    남은 시간: -"
            ))

        pages = ocr_core.analyze_pdf(self.input_path, progress_cb=cb)
        self.cached_pages = pages
        self.cached_for = (self.input_path, mtime)
        return pages

    def _print_analysis_report(self, pages: list[ocr_core.PageInfo], threshold: int) -> dict:
        summary = ocr_core.summarize(pages, threshold)
        self._post_log("─" * 50)
        self._post_log(f"총 페이지: {summary['total']}")
        self._post_log(f"  디지털 텍스트 페이지: {summary['digital']}")
        self._post_log(f"  OCR 완료 (≥{threshold}dpi): {summary['ocr_above_threshold']}")
        self._post_log(
            f"  OCR 완료 (<{threshold}dpi, 재처리 대상): {summary['ocr_below_threshold']}"
        )
        self._post_log(f"  OCR 없음 (스캔만 됨, 처리 대상): {summary['image_only']}")
        self._post_log(f"  빈/벡터 페이지: {summary['blank']}")
        self._post_log(f"→ 재OCR 대상 페이지: {summary['needs_ocr']}개")

        ocr_below = [
            p for p in pages
            if p.classification == "ocr_scanned"
            and (p.image_dpi is None or p.image_dpi < threshold)
        ]
        dpi_values = [p.image_dpi for p in ocr_below if p.image_dpi]
        if dpi_values:
            self._post_log(
                f"  - 재처리 대상 페이지 이미지 DPI 범위: {min(dpi_values)} ~ {max(dpi_values)}"
            )
            self._post_log(
                "    ※ PDF 안 이미지의 DPI는 OCR 후에도 그대로 유지됩니다. "
                "목표 DPI는 OCR 처리 해상도만 의미합니다."
            )
        self._post_log("─" * 50)
        return summary

    def _do_analyze_only(self) -> None:
        try:
            pages = self._ensure_analysis()
            threshold = self.threshold_dpi.get()
            target = self.target_dpi.get()
            summary = self._print_analysis_report(pages, threshold)

            if summary["needs_ocr"] > 0:
                input_size = self.input_path.stat().st_size if self.input_path else 0
                est_sec = ocr_core.estimate_ocr_seconds(
                    num_pages_to_ocr=summary["needs_ocr"],
                    total_pages=summary["total"],
                    total_size_bytes=input_size,
                    target_dpi=target,
                )
                self._post_log(
                    f"💡 만약 지금 'OCR 시작'을 누르면 약 {summary['needs_ocr']}페이지를 "
                    f"{target}dpi로 처리합니다."
                )
                self._post_log(
                    f"   예상 OCR 소요 시간: {format_minutes(est_sec)} "
                    f"(±50% — 실제 하드웨어/PDF 내용에 따라 다름)"
                )
                self._post_log(
                    f"   ※ OCR 페이지가 적어도, 파일 전체({format_bytes(input_size)})에 "
                    "용량 최적화/재압축이 적용되므로 시간이 길어질 수 있습니다."
                )
                self._log_calibration_status()
            else:
                self._post_log("✓ 재OCR이 필요한 페이지가 없습니다.")

            self._post_apply(lambda: self.percent_label.config(text="분석 완료"))
            self._post_apply(lambda: self.phase_label.config(text="단계: 분석 완료"))
            self._post_apply(lambda: self.progress_bar.configure(value=100))
            self._post_log("✓ 분석 완료 (OCR은 실행하지 않음)")

        except Exception as exc:
            self._post_log("✗ 오류 발생:")
            self._post_log(traceback.format_exc())
            self._post_apply(lambda: self.percent_label.config(text="오류"))
            messagebox.showerror("오류", f"{exc}")
        finally:
            self._post_done()

    def _do_full_run(self) -> None:
        try:
            pages = self._ensure_analysis()
            threshold = self.threshold_dpi.get()
            target = self.target_dpi.get()
            summary = self._print_analysis_report(pages, threshold)

            if summary["needs_ocr"] == 0:
                self._post_apply(lambda: self.percent_label.config(text="재OCR 필요 없음"))
                self._post_apply(lambda: self.progress_bar.configure(value=100))
                self._post_log(
                    f"✓ 모든 스캔 페이지가 이미 {threshold}dpi 이상으로 OCR되어 있습니다. "
                    "작업을 건너뜁니다."
                )
                return

            pages_to_ocr = ocr_core.select_pages_to_ocr(pages, threshold)
            pages_range = ocr_core.pages_to_range_string(pages_to_ocr)
            input_size = self.input_path.stat().st_size if self.input_path else 0
            est_sec = ocr_core.estimate_ocr_seconds(
                num_pages_to_ocr=len(pages_to_ocr),
                total_pages=summary["total"],
                total_size_bytes=input_size,
                target_dpi=target,
            )
            self._post_log(f"OCR 대상 페이지 범위: {pages_range}")
            self._post_log(f"예상 소요 시간: {format_minutes(est_sec)} (±50%)")
            self._log_calibration_status()

            self._do_ocr(pages_to_ocr, pages_range, target, summary["total"], input_size)

        except Exception as exc:
            self._post_log("✗ 오류 발생:")
            self._post_log(traceback.format_exc())
            self._post_apply(lambda: self.percent_label.config(text="오류"))
            messagebox.showerror("오류", f"{exc}")
        finally:
            self._post_done()

    def _do_ocr(
        self,
        pages_to_ocr: list[int],
        pages_range: str,
        target_dpi: int,
        total_pages: int,
        input_size_bytes: int,
    ) -> None:
        assert self.input_path is not None and self.output_dir is not None
        output_path = ocr_core.make_output_path(self.input_path, self.output_dir, target_dpi)

        self._post_log(
            f"OCR 시작 → 목표 {target_dpi}dpi, "
            f"대상 {len(pages_to_ocr)}페이지, 출력: {output_path.name}"
        )

        while not self.progress_queue.empty():
            try:
                self.progress_queue.get_nowait()
            except queue.Empty:
                break

        self.overall.start()
        self._post_apply(lambda: self.progress_bar.configure(value=0))
        self._post_apply(lambda: self.percent_label.config(text="OCR 작업 진행 중  0.0%"))

        _set_sleep_prevention(True)
        self._post_log("💤 작업 중 시스템 절전/대기 모드 차단됨")
        try:
            exit_code = ocr_core.run_ocr(
                input_path=self.input_path,
                output_path=output_path,
                pages_range=pages_range,
                target_dpi=target_dpi,
                plugin_path=PLUGIN_PATH,
            )
        finally:
            _set_sleep_prevention(False)

        if exit_code == 0 and output_path.exists():
            in_size = self.input_path.stat().st_size
            out_size = output_path.stat().st_size
            elapsed = self.overall.elapsed()
            self._post_log(
                f"✓ 완료: {output_path.name}  "
                f"(원본 {format_bytes(in_size)} → 결과 {format_bytes(out_size)})"
            )
            self._post_log(f"  원본 파일 그대로 유지: {self.input_path}")
            self._post_log(f"  실제 소요 시간: {format_minsec(elapsed)}")
            try:
                import os as _os
                base_pred = ocr_core.base_estimate_seconds(
                    num_pages_to_ocr=len(pages_to_ocr),
                    total_pages=total_pages,
                    total_size_bytes=input_size_bytes,
                    target_dpi=target_dpi,
                )
                timing_stats.record(
                    pages_to_ocr=len(pages_to_ocr),
                    total_pages=total_pages,
                    total_bytes=input_size_bytes,
                    target_dpi=target_dpi,
                    base_predicted_seconds=base_pred,
                    actual_seconds=elapsed,
                    cpu_count=_os.cpu_count() or 4,
                )
                scale, n = timing_stats.adaptive_scale()
                self._post_log(
                    f"  📊 학습 기록 갱신됨 (시스템 보정 {scale:.2f}x, 표본 {n}개) "
                    "— 다음 추정에 반영"
                )
            except Exception:
                pass
            self._post_apply(lambda: self.percent_label.config(text="작업 완료  100.0%"))
            self._post_apply(lambda: self.progress_bar.configure(value=100))
            self._post_apply(lambda: self.phase_label.config(text=f"단계: 완료 — {output_path.name}"))
            self._post_apply(lambda e=elapsed: self.time_label.config(
                text=f"경과: {format_minsec(e)}    예상 총 시간: {format_minsec(e)}    남은 시간: 0초"
            ))
        else:
            self._post_log(f"✗ OCRmyPDF가 종료 코드 {exit_code} 반환")
            self._post_apply(lambda: self.percent_label.config(text=f"작업 실패 (종료 코드 {exit_code})"))

    def _log_calibration_status(self) -> None:
        scale, n = timing_stats.adaptive_scale()
        if n == 0:
            self._post_log(
                "   📊 학습 표본 없음 — 기본 상수로 추정 중. "
                "OCR 1회 실행 후부터 이 컴퓨터에 맞게 보정됩니다."
            )
        else:
            self._post_log(
                f"   📊 이 컴퓨터 학습 표본 {n}개 사용 (보정 {scale:.2f}x 적용됨)"
            )

    def _post_log(self, msg: str) -> None:
        self.root.after(0, self._log, msg)

    def _post_apply(self, fn) -> None:
        self.root.after(0, fn)

    def _post_done(self) -> None:
        self.root.after(0, lambda: self._set_running(False))

    def _tick(self) -> None:
        try:
            while True:
                evt = self.progress_queue.get_nowait()
                self._handle_progress_event(evt)
        except queue.Empty:
            pass

        if self.is_running and self.overall.start_time is not None:
            self._refresh_time_labels()

        self.root.after(150, self._tick)

    def _refresh_time_labels(self) -> None:
        elapsed = self.overall.elapsed()
        total_est = self.overall.estimated_total()
        remaining = self.overall.estimated_remaining()
        self.time_label.config(
            text=(
                f"경과: {format_minsec(elapsed)}    "
                f"예상 총 시간: {format_minsec(total_est)}    "
                f"남은 시간: {format_minsec(remaining)}"
            )
        )

    def _handle_progress_event(self, evt: dict) -> None:
        desc = evt.get("desc", "") or ""
        unit = evt.get("unit", "") or ""
        total = evt.get("total")
        current = evt.get("current", 0)
        kind = evt.get("kind")

        phase_key, phase_weight_val, phase_label = canonical_phase(desc)

        if kind == "start":
            self.overall.note_phase_start(desc, total)
            if total and total > 0:
                self._log(f"[{phase_label}] 시작 (총 {int(total)} {unit})")
            self._update_overall_ui(phase_label, current, total, unit)
            return

        if kind == "end":
            self.overall.finish_phase(desc)
            self._update_overall_ui(phase_label, current or total or 0, total or 0, unit)
            return

        if total and total > 0:
            self.overall.update(desc, current, total)
            self._update_overall_ui(phase_label, current, total, unit)

    def _update_overall_ui(self, phase_label: str, current: float, total: float, unit: str) -> None:
        overall_pct = self.overall.percent()
        self.progress_bar["value"] = overall_pct
        self.percent_label.config(text=f"전체 진행률  {overall_pct:.1f}%")

        if total and total > 0:
            self.phase_label.config(
                text=f"단계: {phase_label}  ({int(current)}/{int(total)} {unit})"
            )
        else:
            self.phase_label.config(text=f"단계: {phase_label}")

        self._refresh_time_labels()

    def _build_merge_tab(self, parent) -> None:
        pad = {"padx": 12, "pady": 6}

        drop_frame = tk.LabelFrame(
            parent, text=" 병합할 PDF 파일들 (위에서 아래로 순서대로 합쳐짐) ",
            font=(KOREAN_FONT, 10, "bold"), padx=10, pady=10,
        )
        drop_frame.pack(fill="both", expand=True, **pad)

        drop_zone = tk.Label(
            drop_frame,
            text=("이곳에 여러 PDF 파일을 끌어다 놓으세요 (한 번에 여러 개 가능)"
                  if DND_AVAILABLE else
                  "아래 [파일 추가] 버튼으로 PDF들을 선택하세요"),
            bg="#f0f4f8", fg="#444",
            font=(KOREAN_FONT, 11), height=2, relief="ridge", bd=2,
        )
        drop_zone.pack(fill="x", pady=(0, 8))
        if DND_AVAILABLE:
            drop_zone.drop_target_register(DND_FILES)
            drop_zone.dnd_bind("<<Drop>>", self._on_merge_drop)

        list_frame = tk.Frame(drop_frame)
        list_frame.pack(fill="both", expand=True)
        sb = tk.Scrollbar(list_frame, orient="vertical")
        sb.pack(side="right", fill="y")
        self.merge_listbox = tk.Listbox(
            list_frame, height=10, selectmode="extended",
            font=("Consolas", 10), yscrollcommand=sb.set,
        )
        self.merge_listbox.pack(side="left", fill="both", expand=True)
        sb.config(command=self.merge_listbox.yview)

        btns = tk.Frame(drop_frame)
        btns.pack(fill="x", pady=(8, 0))
        tk.Button(btns, text="+ 파일 추가", command=self._merge_add_files, width=14).pack(side="left", padx=2)
        tk.Button(btns, text="↑ 위로", command=lambda: self._merge_move(-1), width=10).pack(side="left", padx=2)
        tk.Button(btns, text="↓ 아래로", command=lambda: self._merge_move(1), width=10).pack(side="left", padx=2)
        tk.Button(btns, text="✕ 선택 제거", command=self._merge_remove_selected, width=12).pack(side="left", padx=2)
        tk.Button(btns, text="🗑 전체 비우기", command=self._merge_clear, width=12).pack(side="left", padx=2)

        out_frame = tk.LabelFrame(
            parent, text=" 출력 파일 ",
            font=(KOREAN_FONT, 10, "bold"), padx=10, pady=10,
        )
        out_frame.pack(fill="x", **pad)
        out_row = tk.Frame(out_frame)
        out_row.pack(fill="x")
        self.merge_output_label = tk.Label(out_row, text="(선택되지 않음)", fg="#888", anchor="w")
        self.merge_output_label.pack(side="left", fill="x", expand=True)
        tk.Button(out_row, text="저장 위치 선택", command=self._merge_pick_output, width=14).pack(side="right")
        self.merge_output_path: Path | None = None

        action = tk.Frame(parent)
        action.pack(fill="x", **pad)
        self.merge_run_btn = tk.Button(
            action, text="▶ 병합 시작", command=self._merge_run,
            width=20, height=2, bg="#1976d2", fg="white",
            font=(KOREAN_FONT, 10, "bold"),
        )
        self.merge_run_btn.pack(side="left", padx=4)
        self.merge_status = tk.Label(action, text="대기 중", font=(KOREAN_FONT, 10), fg="#444")
        self.merge_status.pack(side="left", padx=12)

    def _on_merge_drop(self, event) -> None:
        paths = [Path(p) for p in pdf_tools.parse_dnd_paths(event.data)]
        added = 0
        for p in paths:
            if p.suffix.lower() == ".pdf" and p.exists():
                self.merge_files.append(p)
                added += 1
        self._refresh_merge_list()
        if added == 0:
            messagebox.showwarning("형식 오류", "PDF 파일만 추가할 수 있습니다.")

    def _merge_add_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="병합할 PDF 파일들 선택 (여러 개 가능)",
            filetypes=[("PDF 파일", "*.pdf")],
        )
        for p in paths:
            self.merge_files.append(Path(p))
        self._refresh_merge_list()

    def _refresh_merge_list(self) -> None:
        self.merge_listbox.delete(0, "end")
        for idx, p in enumerate(self.merge_files, start=1):
            try:
                size = p.stat().st_size
                self.merge_listbox.insert("end", f"{idx:2d}. {p.name}   ({format_bytes(size)})")
            except Exception:
                self.merge_listbox.insert("end", f"{idx:2d}. {p.name}")

    def _merge_move(self, direction: int) -> None:
        sel = list(self.merge_listbox.curselection())
        if not sel:
            return
        if direction < 0:
            sel.sort()
            for i in sel:
                if i == 0:
                    break
                self.merge_files[i - 1], self.merge_files[i] = self.merge_files[i], self.merge_files[i - 1]
            new_sel = [max(0, i - 1) for i in sel]
        else:
            sel.sort(reverse=True)
            for i in sel:
                if i >= len(self.merge_files) - 1:
                    break
                self.merge_files[i + 1], self.merge_files[i] = self.merge_files[i], self.merge_files[i + 1]
            new_sel = [min(len(self.merge_files) - 1, i + 1) for i in sel]
        self._refresh_merge_list()
        self.merge_listbox.selection_clear(0, "end")
        for i in new_sel:
            self.merge_listbox.selection_set(i)

    def _merge_remove_selected(self) -> None:
        sel = sorted(self.merge_listbox.curselection(), reverse=True)
        for i in sel:
            del self.merge_files[i]
        self._refresh_merge_list()

    def _merge_clear(self) -> None:
        if self.merge_files and messagebox.askyesno("확인", "목록을 모두 비우시겠습니까?"):
            self.merge_files.clear()
            self._refresh_merge_list()

    def _merge_pick_output(self) -> None:
        default_name = "merged.pdf"
        if self.merge_files:
            default_name = f"{self.merge_files[0].stem}_merged.pdf"
        path = filedialog.asksaveasfilename(
            title="병합 결과 저장 위치",
            defaultextension=".pdf",
            initialfile=default_name,
            filetypes=[("PDF 파일", "*.pdf")],
        )
        if path:
            self.merge_output_path = Path(path)
            self.merge_output_label.config(text=str(self.merge_output_path), fg="black")

    def _merge_run(self) -> None:
        if len(self.merge_files) < 2:
            messagebox.showwarning("병합 불가", "최소 2개 이상의 PDF 파일이 필요합니다.")
            return
        if self.merge_output_path is None:
            messagebox.showwarning("출력 누락", "출력 파일 위치를 지정하세요.")
            return
        if any(self.merge_output_path.resolve() == p.resolve() for p in self.merge_files):
            messagebox.showerror("경로 충돌", "출력 파일이 입력 파일들 중 하나와 같습니다. 다른 이름을 선택하세요.")
            return

        self.merge_run_btn.config(state="disabled")
        threading.Thread(target=self._merge_worker, daemon=True).start()

    def _merge_worker(self) -> None:
        try:
            inputs = list(self.merge_files)
            output = self.merge_output_path
            assert output is not None
            start = time.time()
            self._post_apply(lambda: self.merge_status.config(text="병합 중...", fg="#1976d2"))

            def cb(done: int, total: int) -> None:
                self._post_apply(lambda d=done, t=total: self.merge_status.config(
                    text=f"병합 중... ({d}/{t})", fg="#1976d2"
                ))

            pdf_tools.merge_pdfs(inputs, output, progress_cb=cb)
            elapsed = time.time() - start
            out_size = output.stat().st_size
            self._post_apply(lambda: self.merge_status.config(
                text=f"✓ 완료: {output.name} ({format_bytes(out_size)}, {format_minsec(elapsed)})",
                fg="#2e7d32",
            ))
            self._post_log(f"[병합] {len(inputs)}개 PDF → {output.name} 완료 "
                           f"({format_bytes(out_size)}, {format_minsec(elapsed)})")
            messagebox.showinfo("병합 완료", f"{len(inputs)}개 PDF가 성공적으로 병합되었습니다.\n\n{output}")
        except Exception as exc:
            self._post_log(f"[병합 오류] {exc}")
            self._post_apply(lambda e=exc: self.merge_status.config(text=f"✗ 오류: {e}", fg="#c62828"))
            messagebox.showerror("병합 실패", str(exc))
        finally:
            self._post_apply(lambda: self.merge_run_btn.config(state="normal"))

    def _build_split_tab(self, parent) -> None:
        pad = {"padx": 12, "pady": 6}

        drop_frame = tk.LabelFrame(
            parent, text=" 분할할 PDF 파일 ",
            font=(KOREAN_FONT, 10, "bold"), padx=10, pady=10,
        )
        drop_frame.pack(fill="x", **pad)

        drop_zone = tk.Label(
            drop_frame,
            text=("이곳에 PDF 파일 1개를 끌어다 놓으세요"
                  if DND_AVAILABLE else
                  "[파일 선택] 버튼으로 PDF를 선택하세요"),
            bg="#f0f4f8", fg="#444",
            font=(KOREAN_FONT, 11), height=2, relief="ridge", bd=2,
        )
        drop_zone.pack(fill="x", pady=(0, 8))
        if DND_AVAILABLE:
            drop_zone.drop_target_register(DND_FILES)
            drop_zone.dnd_bind("<<Drop>>", self._on_split_drop)

        row = tk.Frame(drop_frame)
        row.pack(fill="x")
        tk.Label(row, text="입력 PDF:", width=10, anchor="w").pack(side="left")
        self.split_input_label = tk.Label(row, text="(선택되지 않음)", fg="#888", anchor="w")
        self.split_input_label.pack(side="left", fill="x", expand=True)
        tk.Button(row, text="파일 선택", command=self._split_pick_input, width=12).pack(side="right")

        bm_frame = tk.LabelFrame(
            parent, text=" 발견된 북마크 (분할 기준 레벨을 고르세요) ",
            font=(KOREAN_FONT, 10, "bold"), padx=10, pady=10,
        )
        bm_frame.pack(fill="both", expand=True, **pad)

        level_row = tk.Frame(bm_frame)
        level_row.pack(fill="x", pady=(0, 6))
        tk.Label(level_row, text="분할 기준 레벨:", width=18, anchor="w",
                 font=(KOREAN_FONT, 10)).pack(side="left")
        for lvl in (1, 2, 3):
            tk.Radiobutton(
                level_row, text=f"레벨 {lvl}", variable=self.split_level, value=lvl,
                command=self._refresh_bookmark_list,
            ).pack(side="left", padx=4)
        self.split_level_count_label = tk.Label(
            level_row, text="", fg="#1976d2", font=(KOREAN_FONT, 10, "bold"),
        )
        self.split_level_count_label.pack(side="left", padx=12)

        prefix_row = tk.Frame(bm_frame)
        prefix_row.pack(fill="x", pady=(0, 6))
        tk.Checkbutton(
            prefix_row,
            text="파일명 앞에 순번 붙이기 (예: 001_제목.pdf) — 북마크에 이미 번호가 있으면 끄세요",
            variable=self.split_use_prefix,
            font=(KOREAN_FONT, 9),
            fg="#444",
        ).pack(side="left")

        list_box_frame = tk.Frame(bm_frame)
        list_box_frame.pack(fill="both", expand=True)
        sb2 = tk.Scrollbar(list_box_frame, orient="vertical")
        sb2.pack(side="right", fill="y")
        self.bookmark_listbox = tk.Listbox(
            list_box_frame, height=10, font=("Consolas", 9), yscrollcommand=sb2.set,
        )
        self.bookmark_listbox.pack(side="left", fill="both", expand=True)
        sb2.config(command=self.bookmark_listbox.yview)

        out_frame = tk.LabelFrame(
            parent, text=" 출력 폴더 (각 분할 PDF가 북마크 이름으로 저장됨) ",
            font=(KOREAN_FONT, 10, "bold"), padx=10, pady=10,
        )
        out_frame.pack(fill="x", **pad)
        out_row = tk.Frame(out_frame)
        out_row.pack(fill="x")
        self.split_output_label = tk.Label(out_row, text="(선택되지 않음)", fg="#888", anchor="w")
        self.split_output_label.pack(side="left", fill="x", expand=True)
        tk.Button(out_row, text="폴더 선택", command=self._split_pick_output, width=12).pack(side="right")

        action = tk.Frame(parent)
        action.pack(fill="x", **pad)
        self.split_run_btn = tk.Button(
            action, text="▶ 분할 시작", command=self._split_run,
            width=20, height=2, bg="#1976d2", fg="white",
            font=(KOREAN_FONT, 10, "bold"),
        )
        self.split_run_btn.pack(side="left", padx=4)
        self.split_status = tk.Label(action, text="대기 중", font=(KOREAN_FONT, 10), fg="#444")
        self.split_status.pack(side="left", padx=12)

    def _on_split_drop(self, event) -> None:
        paths = pdf_tools.parse_dnd_paths(event.data)
        if not paths:
            return
        p = Path(paths[0])
        if p.suffix.lower() != ".pdf":
            messagebox.showerror("형식 오류", "PDF 파일만 분할할 수 있습니다.")
            return
        self._set_split_input(p)

    def _split_pick_input(self) -> None:
        path = filedialog.askopenfilename(
            title="분할할 PDF 선택",
            filetypes=[("PDF 파일", "*.pdf")],
        )
        if path:
            self._set_split_input(Path(path))

    def _set_split_input(self, path: Path) -> None:
        if not path.exists():
            messagebox.showerror("파일 오류", f"파일을 찾을 수 없습니다:\n{path}")
            return
        self.split_input_path = path
        self.split_input_label.config(text=str(path), fg="black")
        try:
            self.split_bookmarks = pdf_tools.get_bookmark_tree(path)
        except Exception as exc:
            messagebox.showerror("북마크 읽기 오류", str(exc))
            self.split_bookmarks = []
        if not self.split_bookmarks:
            messagebox.showwarning(
                "북마크 없음",
                "이 PDF에는 북마크가 존재하지 않습니다. 분할이 불가능합니다.",
            )
        self._refresh_bookmark_list()
        if self.split_output_dir is None:
            self.split_output_dir = path.parent / f"{path.stem}_split"
            self.split_output_label.config(text=str(self.split_output_dir), fg="black")

    def _refresh_bookmark_list(self) -> None:
        self.bookmark_listbox.delete(0, "end")
        level = self.split_level.get()
        level_count = 0
        for lvl, title, page in self.split_bookmarks:
            mark = " ← 분할 기준" if lvl == level else ""
            indent = "  " * max(0, lvl - 1)
            self.bookmark_listbox.insert(
                "end", f"{indent}[L{lvl}] {title}  (페이지 {page}){mark}"
            )
            if lvl == level:
                level_count += 1
        if self.split_bookmarks:
            available = sorted({lvl for lvl, _, _ in self.split_bookmarks})
            self.split_level_count_label.config(
                text=f"→ 레벨 {level}로 분할 시 {level_count}개 파일 생성 "
                     f"(존재하는 레벨: {available})"
            )
        else:
            self.split_level_count_label.config(text="")

    def _split_pick_output(self) -> None:
        path = filedialog.askdirectory(title="분할 출력 폴더 선택")
        if path:
            self.split_output_dir = Path(path)
            self.split_output_label.config(text=str(self.split_output_dir), fg="black")

    def _split_run(self) -> None:
        if self.split_input_path is None:
            messagebox.showwarning("입력 누락", "분할할 PDF를 먼저 선택하세요.")
            return
        if self.split_output_dir is None:
            messagebox.showwarning("출력 폴더", "출력 폴더를 지정하세요.")
            return
        level = self.split_level.get()
        count = sum(1 for lvl, _, _ in self.split_bookmarks if lvl == level)
        if count == 0:
            messagebox.showwarning(
                "북마크 없음",
                f"이 PDF에 레벨 {level} 북마크가 없습니다. 다른 레벨을 선택하세요.",
            )
            return

        self.split_run_btn.config(state="disabled")
        threading.Thread(target=self._split_worker, args=(level,), daemon=True).start()

    def _split_worker(self, level: int) -> None:
        try:
            assert self.split_input_path is not None and self.split_output_dir is not None
            start = time.time()
            self._post_apply(lambda: self.split_status.config(text="분할 중...", fg="#1976d2"))

            def cb(done: int, total: int, title: str) -> None:
                self._post_apply(lambda d=done, t=total, ti=title: self.split_status.config(
                    text=f"분할 중... ({d}/{t}) - {ti[:30]}", fg="#1976d2"
                ))

            outputs = pdf_tools.split_by_bookmarks(
                pdf_path=self.split_input_path,
                output_dir=self.split_output_dir,
                level=level,
                progress_cb=cb,
                name_prefix_with_index=self.split_use_prefix.get(),
            )
            elapsed = time.time() - start
            self._post_apply(lambda: self.split_status.config(
                text=f"✓ 완료: {len(outputs)}개 파일 ({format_minsec(elapsed)})",
                fg="#2e7d32",
            ))
            self._post_log(
                f"[분할] {self.split_input_path.name} → 레벨 {level}로 "
                f"{len(outputs)}개 파일 생성 (출력: {self.split_output_dir})"
            )
            messagebox.showinfo(
                "분할 완료",
                f"{len(outputs)}개 PDF가 생성되었습니다.\n\n출력 폴더:\n{self.split_output_dir}",
            )
        except Exception as exc:
            self._post_log(f"[분할 오류] {exc}")
            self._post_apply(lambda e=exc: self.split_status.config(text=f"✗ 오류: {e}", fg="#c62828"))
            messagebox.showerror("분할 실패", str(exc))
        finally:
            self._post_apply(lambda: self.split_run_btn.config(state="normal"))


def main() -> None:
    if DND_AVAILABLE:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
    OCRApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
