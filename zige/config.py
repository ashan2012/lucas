"""Paths and shared constants."""

from __future__ import annotations

from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent

# Everything the tool downloads or compiles lives under the project so that the
# whole directory stays portable / inspectable.
CACHE_DIR = PROJECT_DIR / "cache"
STROKE_CACHE_DIR = CACHE_DIR / "strokes"

# hanzi-writer-data is a per-character dump of stroke outlines + stroke medians,
# derived from the Make Me a Hanzi project (MIT / Arphic public license).
STROKE_SOURCES = (
    "https://unpkg.com/hanzi-writer-data@2.0.1/{char}.json",
    "https://cdn.jsdelivr.net/npm/hanzi-writer-data@2.0.1/{char}.json",
)

# The stroke data uses a 1024x1024 design canvas with a bottom-up y axis that
# hanzi-writer flips with `translate(0,-900) scale(1,-1)`.
CANVAS = 1024.0
Y_FLIP = 900.0

# Calibrated over a sample of common characters: the design canvas is not
# perfectly centred, so we re-centre on the measured median glyph centre and
# scale a p90-sized glyph to ~86% of the cell.
GLYPH_CENTER_X = 520.0
GLYPH_CENTER_Y = 500.0
GLYPH_REFERENCE_EXTENT = 900.0

DEFAULT_CELL = 200
DEFAULT_DPI_SCALE = 2
