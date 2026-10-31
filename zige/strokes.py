"""Stroke data: fetching, caching and geometry.

Stroke outlines come from `hanzi-writer-data` (per character JSON with a list of
SVG path strings plus the "median" - the pen trajectory). Each path is the
*outline* of a brush stroke, so drawing it means filling the polygon, not
stroking a line.

Everything is expressed on the 1024x1024 design canvas; `Transform` maps that
canvas onto a square on the output image.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .config import (
    CANVAS,
    GLYPH_CENTER_X,
    GLYPH_CENTER_Y,
    GLYPH_REFERENCE_EXTENT,
    STROKE_CACHE_DIR,
    STROKE_SOURCES,
    Y_FLIP,
)

Point = Tuple[float, float]
Polygon = List[Point]

_USER_AGENT = "zige/1.0 (+stroke order card generator)"
_lock = threading.Lock()


class StrokeError(RuntimeError):
    """Raised when stroke data for a character cannot be obtained."""


@dataclass(frozen=True)
class StrokeData:
    char: str
    strokes: Tuple[str, ...]
    medians: Tuple[Tuple[Point, ...], ...]

    @property
    def count(self) -> int:
        return len(self.strokes)


# --------------------------------------------------------------------------- #
# cache / download
# --------------------------------------------------------------------------- #
def _cache_path(char: str) -> Path:
    return STROKE_CACHE_DIR / f"{ord(char):05x}.json"


def _load_cached(char: str) -> Optional[dict]:
    path = _cache_path(char)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def _store(char: str, payload: dict) -> None:
    STROKE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(char)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    os.replace(tmp, path)


def _download(char: str, timeout: float = 25.0) -> dict:
    quoted = urllib.parse.quote(char)
    errors = []
    for template in STROKE_SOURCES:
        url = template.format(char=quoted)
        request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            errors.append(f"{url} -> {exc}")
            continue
        if "strokes" not in payload:
            errors.append(f"{url} -> unexpected payload")
            continue
        return payload
    raise StrokeError(
        f"无法获取 “{char}” 的笔顺数据（已尝试 {len(STROKE_SOURCES)} 个源）：\n  "
        + "\n  ".join(errors)
    )


def get(char: str, allow_network: bool = True) -> StrokeData:
    """Load stroke data for a character, from cache when possible."""
    with _lock:
        payload = _load_cached(char)
    if payload is None:
        if not allow_network:
            raise StrokeError(f"“{char}” 不在本地笔顺缓存中，且已禁用联网")
        payload = _download(char)
        with _lock:
            _store(char, payload)

    strokes = payload.get("strokes") or []
    medians = payload.get("medians") or []
    if not strokes:
        raise StrokeError(f"“{char}” 的笔顺数据为空")
    return StrokeData(
        char=char,
        strokes=tuple(strokes),
        medians=tuple(tuple((float(p[0]), float(p[1])) for p in median) for median in medians),
    )


def prefetch(
    chars: Iterable[str],
    allow_network: bool = True,
    workers: int = 8,
    progress: Optional[Callable[[str, Optional[StrokeError]], None]] = None,
) -> Dict[str, Optional[StrokeError]]:
    """Warm the cache for many characters; returns {char: error_or_None}."""
    unique = list(dict.fromkeys(ch for ch in chars if ch.strip()))
    results: Dict[str, Optional[StrokeError]] = {}

    def work(ch: str) -> Tuple[str, Optional[StrokeError]]:
        try:
            get(ch, allow_network=allow_network)
            return ch, None
        except StrokeError as exc:
            return ch, exc

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for ch, err in pool.map(work, unique):
            results[ch] = err
            if progress:
                progress(ch, err)
    return results


# --------------------------------------------------------------------------- #
# path parsing
# --------------------------------------------------------------------------- #
_TOKEN_RE = re.compile(r"[MLQCZTmlqczt]|[-+]?(?:\d*\.\d+|\d+)")


def _flatten_quad(p0: Point, p1: Point, p2: Point, steps: int) -> List[Point]:
    out = []
    for i in range(1, steps + 1):
        t = i / steps
        mt = 1.0 - t
        out.append(
            (
                mt * mt * p0[0] + 2 * mt * t * p1[0] + t * t * p2[0],
                mt * mt * p0[1] + 2 * mt * t * p1[1] + t * t * p2[1],
            )
        )
    return out


def _flatten_cubic(p0: Point, p1: Point, p2: Point, p3: Point, steps: int) -> List[Point]:
    out = []
    for i in range(1, steps + 1):
        t = i / steps
        mt = 1.0 - t
        out.append(
            (
                mt ** 3 * p0[0] + 3 * mt * mt * t * p1[0] + 3 * mt * t * t * p2[0] + t ** 3 * p3[0],
                mt ** 3 * p0[1] + 3 * mt * mt * t * p1[1] + 3 * mt * t * t * p2[1] + t ** 3 * p3[1],
            )
        )
    return out


def flatten_path(path: str, curve_steps: int = 14) -> List[Polygon]:
    """Convert an SVG path string into one or more closed polygons.

    hanzi-writer paths only use M/L/Q/C/Z (absolute or relative), so a compact
    parser is enough - no general SVG renderer dependency.
    """
    tokens = _TOKEN_RE.findall(path)
    polygons: List[Polygon] = []
    current: Polygon = []
    pos: Point = (0.0, 0.0)
    start: Point = (0.0, 0.0)
    cmd = ""
    i = 0

    def take(count: int) -> Optional[List[float]]:
        nonlocal i
        if i + count > len(tokens):
            return None
        chunk = tokens[i : i + count]
        if any(re.match(r"[A-Za-z]", c) for c in chunk):
            return None
        i += count
        return [float(c) for c in chunk]

    while i < len(tokens):
        token = tokens[i]
        if re.match(r"[A-Za-z]", token):
            cmd = token
            i += 1
            if cmd in "Zz":
                if current:
                    current.append(start)
                    polygons.append(current)
                    current = []
                pos = start
                continue
        if not cmd:
            i += 1
            continue

        lower = cmd.lower()
        relative = cmd.islower()

        if lower in ("m", "l"):
            values = take(2)
            if values is None:
                break
            x, y = values
            if relative:
                x += pos[0]
                y += pos[1]
            if lower == "m":
                if current:
                    polygons.append(current)
                    current = [(x, y)]
                else:
                    current = [(x, y)]
                start = (x, y)
                # Subsequent coordinate pairs of an M are implicit line-tos.
                cmd = "l" if relative else "L"
            else:
                current.append((x, y))
            pos = (x, y)

        elif lower == "q":
            values = take(4)
            if values is None:
                break
            x1, y1, x, y = values
            if relative:
                x1 += pos[0]; y1 += pos[1]
                x += pos[0]; y += pos[1]
            if not current:
                current = [pos]
            current.extend(_flatten_quad(pos, (x1, y1), (x, y), curve_steps))
            pos = (x, y)

        elif lower == "c":
            values = take(6)
            if values is None:
                break
            x1, y1, x2, y2, x, y = values
            if relative:
                x1 += pos[0]; y1 += pos[1]
                x2 += pos[0]; y2 += pos[1]
                x += pos[0]; y += pos[1]
            if not current:
                current = [pos]
            current.extend(_flatten_cubic(pos, (x1, y1), (x2, y2), (x, y), curve_steps))
            pos = (x, y)

        elif lower == "t":
            # Smooth quadratic: reflect the previous control point.
            values = take(2)
            if values is None:
                break
            x, y = values
            if relative:
                x += pos[0]; y += pos[1]
            if not current:
                current = [pos]
            current.extend(_flatten_quad(pos, pos, (x, y), curve_steps))
            pos = (x, y)

        else:  # pragma: no cover - unknown command, skip token
            i += 1

    if current:
        polygons.append(current)
    return [poly for poly in polygons if len(poly) >= 3]


# --------------------------------------------------------------------------- #
# geometry
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Transform:
    """Maps the design canvas onto a square cell of side `size` at (x, y)."""

    x: float
    y: float
    size: float
    fill: float = 0.86  # fraction of the cell a reference-sized glyph occupies

    @property
    def scale(self) -> float:
        return self.size * self.fill / GLYPH_REFERENCE_EXTENT

    def map(self, point: Point) -> Point:
        px, py = point
        sx = self.scale
        return (
            self.x + self.size / 2.0 + (px - GLYPH_CENTER_X) * sx,
            self.y + self.size / 2.0 + ((Y_FLIP - py) - GLYPH_CENTER_Y) * sx,
        )

    def map_polygon(self, polygon: Sequence[Point]) -> List[Point]:
        return [self.map(p) for p in polygon]


def stroke_polygons(data: StrokeData, curve_steps: int = 14) -> List[List[Polygon]]:
    """Flatten every stroke once so repeated rendering stays cheap."""
    return [flatten_path(path, curve_steps) for path in data.strokes]


def stroke_numbers(data: StrokeData, transform: Transform) -> List[Tuple[int, Point, Point]]:
    """Return (index, label_point, direction) for numbering the strokes."""
    out: List[Tuple[int, Point, Point]] = []
    center = transform.map((GLYPH_CENTER_X, Y_FLIP - GLYPH_CENTER_Y))
    for idx, median in enumerate(data.medians):
        if not median:
            continue
        head = transform.map(median[0])
        tail = transform.map(median[min(len(median) - 1, 3)])
        # Push the label slightly away from the glyph centre so it does not sit
        # on top of the stroke body.
        dx, dy = head[0] - center[0], head[1] - center[1]
        norm = math.hypot(dx, dy) or 1.0
        offset = transform.size * 0.085
        label_point = (head[0] + dx / norm * offset, head[1] + dy / norm * offset)
        out.append((idx + 1, label_point, (tail[0] - head[0], tail[1] - head[1])))
    return out


