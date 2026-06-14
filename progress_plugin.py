"""OCRmyPDF plugin that mirrors progress events into ``progress_bridge.progress_queue``."""

from __future__ import annotations

from ocrmypdf import hookimpl
from ocrmypdf.pluginspec import ProgressBar

import progress_bridge


class QueueProgressBar(ProgressBar):
    def __init__(self, *, total=None, desc=None, unit=None, disable=False, **kwargs):
        self.total = total
        self.desc = desc or ""
        self.unit = unit or ""
        self.disable = disable
        self.current = 0
        self._emit("start")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, tb):
        self._emit("end")
        return False

    def update(self, n=1, *, completed=None):
        if completed is not None:
            self.current = completed
        else:
            self.current += n
        self._emit("update")

    def _emit(self, kind: str) -> None:
        q = progress_bridge.progress_queue
        if q is None:
            return
        try:
            q.put_nowait(
                {
                    "kind": kind,
                    "desc": self.desc,
                    "unit": self.unit,
                    "total": self.total,
                    "current": self.current,
                }
            )
        except Exception:
            pass


@hookimpl
def get_progressbar_class():
    return QueueProgressBar
