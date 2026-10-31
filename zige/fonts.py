"""Font discovery.

The cards need two things a default font cannot give us: Chinese glyphs and
pinyin tone marks (ǎ ě ǐ ǒ ǔ ǖ ...). macOS ships several usable faces, so we
probe them once, keep the first one that covers both, and cache by size.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional, Tuple

from PIL import ImageFont

# (path, preferred face name fragment)
_CANDIDATES: Tuple[Tuple[str, Optional[str]], ...] = (
    ("/System/Library/Fonts/PingFang.ttc", "PingFang SC"),
    ("/System/Library/Fonts/Hiragino Sans GB.ttc", "Hiragino Sans GB"),
    ("/System/Library/Fonts/Supplemental/Songti.ttc", "Songti SC"),
    ("/System/Library/Fonts/STHeiti Medium.ttc", "Heiti SC"),
    ("/Library/Fonts/Arial Unicode.ttf", "Arial Unicode"),
)

_PROBE = "汉字拼读ǎěǐǒǔǖǘǚǜü"

_cache: Dict[Tuple[str, int], ImageFont.FreeTypeFont] = {}
_resolved: Optional[Tuple[Path, int]] = None


def _covers(font: ImageFont.FreeTypeFont) -> bool:
    try:
        return font.getmask(_PROBE).getbbox() is not None
    except Exception:
        return False


def _resolve() -> Optional[Tuple[Path, int]]:
    """Find (file, face index) of a font covering hanzi + tone marks."""
    global _resolved
    if _resolved is not None:
        return _resolved
    for path_str, want in _CANDIDATES:
        path = Path(path_str)
        if not path.exists():
            continue
        for index in range(0, 12 if path.suffix.lower() == ".ttc" else 1):
            try:
                font = ImageFont.truetype(str(path), 40, index=index)
            except Exception:
                break
            name = " ".join(p for p in font.getname() if p)
            if not _covers(font):
                continue
            if want and want.lower() not in name.lower():
                continue
            _resolved = (path, index)
            return _resolved
        # Fall back to face 0 of this file when the preferred face is absent.
        try:
            font = ImageFont.truetype(str(path), 40, index=0)
            if _covers(font):
                _resolved = (path, 0)
                return _resolved
        except Exception:
            continue
    return None


def get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Return a font for the given pixel size."""
    key = ("bold" if bold else "regular", max(8, int(size)))
    cached = _cache.get(key)
    if cached is not None:
        return cached

    resolved = _resolve()
    if resolved is None:
        font = ImageFont.load_default()
        _cache[key] = font
        return font

    path, index = resolved
    try:
        font = ImageFont.truetype(str(path), key[1], index=index)
    except Exception:
        font = ImageFont.load_default()
        # Pillow's default font cannot do CJK; stroke data still renders fine,
        # only labels would be missing.
    _cache[key] = font
    return font


def font_name() -> str:
    resolved = _resolve()
    if resolved is None:
        return "PIL default (no CJK font found)"
    path, index = resolved
    try:
        return " ".join(p for p in ImageFont.truetype(str(path), 20, index=index).getname() if p)
    except Exception:
        return str(path)
