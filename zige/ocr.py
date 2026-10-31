"""OCR front-end.

Uses macOS' built-in Vision framework (via a tiny Swift helper compiled on first
use) so that no heavyweight model download is required on Apple hardware.

The result is a flat list of text observations with pixel bounding boxes in the
usual top-left origin image space.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

from PIL import Image

from .config import CACHE_DIR

SWIFT_SRC = Path(__file__).resolve().parent / "swift" / "vision_ocr.swift"


class OcrError(RuntimeError):
    """Raised when the OCR backend is unavailable or fails."""


@dataclass
class TextLine:
    """One recognized text observation in image pixel coordinates."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2.0

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2.0


def _helper_path() -> Path:
    """Compile the Swift helper once and return the executable path."""
    binary = CACHE_DIR / "bin" / "vision_ocr"
    if binary.exists() and binary.stat().st_mtime >= SWIFT_SRC.stat().st_mtime:
        return binary

    for tool in ("swiftc", "xcrun"):
        if not shutil.which(tool):
            continue

        build_dir = CACHE_DIR / "swift"
        build_dir.mkdir(parents=True, exist_ok=True)
        binary.parent.mkdir(parents=True, exist_ok=True)

        cmd = [tool] + (["swiftc"] if tool == "xcrun" else []) + [
            "-O",
            "-module-cache-path",
            str(build_dir / "modules"),
            str(SWIFT_SRC),
            "-o",
            str(binary),
        ]
        # Sandboxes sometimes block swiftc from using the system temp dir or
        # ~/Library caches, so keep all build state inside the project.
        env = dict(os.environ, TMPDIR=str(build_dir), TMP=str(build_dir), TEMP=str(build_dir))
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180, env=env)
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise OcrError(f"failed to build Vision OCR helper: {exc}") from exc

        if proc.returncode == 0 and binary.exists():
            return binary
        raise OcrError("failed to build Vision OCR helper:\n" + (proc.stderr or proc.stdout))

    raise OcrError(
        "no Swift compiler found (swiftc). The OCR backend needs macOS + Xcode "
        "Command Line Tools. Use --from-json to render from a hand-written word list."
    )


def available() -> bool:
    """Whether the Vision backend can be used on this machine."""
    return SWIFT_SRC.exists() and (shutil.which("swiftc") or shutil.which("xcrun")) is not None


def image_size(path: os.PathLike | str) -> Tuple[int, int]:
    """Pixel size of an image, falling back to `sips` for HEIC and friends."""
    try:
        with Image.open(path) as img:
            return img.size
    except Exception:
        pass
    if shutil.which("sips"):
        proc = subprocess.run(
            ["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        width = height = 0
        for line in proc.stdout.splitlines():
            if "pixelWidth:" in line:
                width = int(float(line.split(":")[1]))
            elif "pixelHeight:" in line:
                height = int(float(line.split(":")[1]))
        if width and height:
            return width, height
    raise OcrError(f"cannot read image: {path}")


def read_image(image_path: os.PathLike | str, languages: str = "zh-Hans,en-US") -> List[TextLine]:
    """Run OCR and return text observations ordered top-to-bottom, left-to-right."""
    path = Path(image_path)
    if not path.exists():
        raise OcrError(f"input image not found: {path}")

    img_w, img_h = image_size(path)

    helper = _helper_path()
    proc = None
    last_error = ""
    for attempt in range(2):
        try:
            proc = subprocess.run(
                [str(helper), str(path), "--lang", languages],
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired as exc:
            raise OcrError("Vision OCR timed out") from exc
        if proc.returncode == 0:
            break
        last_error = proc.stderr.strip() or f"exit code {proc.returncode}"
        # The Chinese recognition model occasionally fails to load on the first
        # request of a process ("Error loading network -5"); a retry clears it.
        time.sleep(0.5 * (attempt + 1))
    if proc is None or proc.returncode != 0:
        raise OcrError(f"Vision OCR failed: {last_error}")

    lines: List[TextLine] = []
    for row in proc.stdout.splitlines():
        parts = row.split("\t")
        if len(parts) < 5:
            continue
        try:
            nx, ny, nw, nh = (float(v) for v in parts[:4])
        except ValueError:
            continue
        text = parts[4].strip()
        if not text:
            continue
        # Vision uses a bottom-left origin; flip to image space.
        x0 = nx * img_w
        x1 = (nx + nw) * img_w
        y0 = (1.0 - (ny + nh)) * img_h
        y1 = (1.0 - ny) * img_h
        lines.append(TextLine(text=text, x0=x0, y0=y0, x1=x1, y1=y1))

    lines.sort(key=lambda ln: (round(ln.cy, 1), ln.x0))
    return lines


def read_text_debug(image_path: str) -> str:
    """Return a human readable dump, handy while tuning the layout parser."""
    rows = [
        f"{ln.x0:7.1f} {ln.y0:7.1f} {ln.width:6.1f} {ln.height:6.1f}  {ln.text}"
        for ln in read_image(image_path)
    ]
    return "\n".join(rows) if rows else "(no text recognized)"
    return lines


def read_text_debug(image_path: str) -> str:
    """Return a human readable dump, handy while tuning the layout parser."""
    rows = [f"{ln.x0:7.1f} {ln.y0:7.1f} {ln.width:6.1f} {ln.height:6.1f}  {ln.text}" for ln in read_image(image_path)]
    return "\n".join(rows) if rows else "(no text recognized)"
