"""Shared state between the GUI process and the OCRmyPDF progress plugin.

The GUI assigns a ``queue.Queue`` to ``progress_queue`` before invoking
ocrmypdf.ocr().  The progress plugin (loaded by OCRmyPDF as a file path)
imports this module at runtime and pushes events to that queue.

This indirection is needed because OCRmyPDF reloads file-path plugins via
importlib.util.spec_from_file_location, which would otherwise reset any
module-level globals defined inside the plugin file itself.
"""

from __future__ import annotations

import queue
from typing import Optional

# Set by the GUI before each OCR run.
progress_queue: Optional[queue.Queue] = None
