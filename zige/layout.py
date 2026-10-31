"""Turn raw OCR observations into a list of (pinyin, hanzi) word units.

A flash card is a small table with alternating rows:

    huà huà | dǎ gǔ | ...        <- pinyin row
    画画    | 打鼓   | ...        <- hanzi row

We therefore cluster observations into rows by their vertical centre, classify
each row as pinyin or hanzi by its content, and pair every hanzi row with the
pinyin row directly above it. Cells are then matched horizontally by centre
distance, which tolerates the small x-offsets that OCR bounding boxes carry.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence

from .ocr import TextLine

HANZI_RE = re.compile(r"^[\u3400-\u4dbf\u4e00-\u9fff]{1,4}$")
# Latin letters incl. the pinyin diacritic range, plus spaces / apostrophes.
PINYIN_RE = re.compile(r"^[A-Za-z\u00c0-\u024f\u0300-\u036f' ]+$")

# Words that are printed on a card but are not vocabulary to practise.
STOP_WORDS = {
    "我读了",
    "最快用时",
    "每日拼读",
    "小卡片",
    "练一练",
    "读一读",
}


class EmptyCardError(RuntimeError):
    """Raised when a card image yields no usable pinyin/hanzi cells."""


@dataclass
class Word:
    """One vocabulary item from the card."""

    hanzi: str
    pinyin_ocr: str = ""
    x: float = 0.0
    y: float = 0.0
    row: int = 0
    syllables: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def pinyin(self) -> str:
        return " ".join(self.syllables) if self.syllables else self.pinyin_ocr


@dataclass
class _Row:
    lines: List[TextLine]
    kind: str  # "hanzi" | "pinyin"
    index: int = 0

    @property
    def cy(self) -> float:
        return statistics.fmean(ln.cy for ln in self.lines)

    @property
    def height(self) -> float:
        return statistics.median(ln.height for ln in self.lines)

    def sorted_lines(self) -> List[TextLine]:
        return sorted(self.lines, key=lambda ln: ln.x0)


def _looks_like_pinyin(text: str) -> bool:
    if not PINYIN_RE.match(text) or not re.search(r"[A-Za-z\u00c0-\u024f]", text):
        return False
    letters = re.sub(r"[^A-Za-z\u00c0-\u024f]", "", text)
    # Require a vowel so stray letters such as "g" or "k h" are ignored.
    return bool(re.search(r"[aeiou\u00e0-\u00fc\u0100-\u024f]", letters, re.I))


def classify(text: str) -> Optional[str]:
    """Classify a cell as 'hanzi', 'pinyin' or None (title / footer / junk)."""
    stripped = text.strip()
    if HANZI_RE.match(stripped):
        return "hanzi"
    if _looks_like_pinyin(stripped):
        return "pinyin"
    return None


def _cluster_rows(lines: Sequence[TextLine]) -> List[_Row]:
    """Group observations into visual rows by vertical overlap."""
    rows: List[_Row] = []
    for line in sorted(lines, key=lambda ln: ln.cy):
        target = None
        for row in rows:
            tolerance = max(row.height, line.height) * 0.55
            if abs(row.cy - line.cy) <= tolerance:
                target = row
                break
        if target is None:
            rows.append(_Row(lines=[line], kind=""))
        else:
            target.lines.append(line)
    rows.sort(key=lambda r: r.cy)
    for idx, row in enumerate(rows):
        row.index = idx
    return rows


def _majority_kind(row: _Row) -> Optional[str]:
    kinds = [classify(ln.text) for ln in row.lines]
    kinds = [k for k in kinds if k]
    if not kinds:
        return None
    hanzi = kinds.count("hanzi")
    pinyin = kinds.count("pinyin")
    if hanzi == 0 and pinyin == 0:
        return None
    # A hanzi row wins when both appear, because single latin letters are often
    # part of an adjacent label.
    return "hanzi" if hanzi >= pinyin else "pinyin"


def _pair_cells(hanzi_row: _Row, pinyin_row: Optional[_Row]) -> List[Word]:
    hanzis = [ln for ln in hanzi_row.sorted_lines() if classify(ln.text) == "hanzi"]
    pinyins = (
        [ln for ln in pinyin_row.sorted_lines() if classify(ln.text) == "pinyin"]
        if pinyin_row is not None
        else []
    )

    words: List[Word] = []
    used = set()
    for cell in hanzis:
        text = cell.text.strip()
        if any(stop in text for stop in STOP_WORDS):
            continue
        match = None
        best = float("inf")
        for idx, probe in enumerate(pinyins):
            if idx in used:
                continue
            # Only consider candidates that overlap horizontally.
            if probe.x1 < cell.x0 - cell.width * 0.4 or probe.x0 > cell.x1 + cell.width * 0.4:
                continue
            distance = abs(probe.cx - cell.cx)
            if distance < best:
                best = distance
                match = idx
        pinyin_text = ""
        if match is not None:
            used.add(match)
            pinyin_text = pinyins[match].text.strip()
        words.append(
            Word(
                hanzi=text,
                pinyin_ocr=pinyin_text,
                x=cell.cx,
                y=cell.cy,
                row=hanzi_row.index,
            )
        )
    return words


def parse_card(lines: Iterable[TextLine]) -> List[Word]:
    """Parse OCR observations into ordered words."""
    rows = _cluster_rows(list(lines))
    for row in rows:
        row.kind = _majority_kind(row) or ""

    words: List[Word] = []
    for row in rows:
        if row.kind != "hanzi":
            continue
        # Find the pinyin row immediately above this hanzi row.
        above = [
            r
            for r in rows
            if r.kind == "pinyin" and 0 < row.cy - r.cy <= max(row.height, r.height) * 3.0
        ]
        above.sort(key=lambda r: row.cy - r.cy)
        pinyin_row = above[0] if above else None
        words.extend(_pair_cells(row, pinyin_row))

    # Sort top-to-bottom then left-to-right; drop duplicates produced by
    # overlapping OCR observations.
    words.sort(key=lambda w: (w.row, w.x))
    deduped: List[Word] = []
    seen = set()
    for word in words:
        key = (word.hanzi, round(word.y / 10), round(word.x / 10))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(word)
    return deduped


def describe_rows(lines: Iterable[TextLine]) -> str:
    """Human readable row dump used by --debug."""
    rows = _cluster_rows(list(lines))
    out = []
    for row in rows:
        kind = _majority_kind(row) or "ignored"
        cells = " | ".join(f"{ln.text}" for ln in row.sorted_lines())
        out.append(f"row {row.index:02d}  {kind:7s} y={row.cy:7.1f}  {cells}")
    return "\n".join(out) if out else "(no rows)"
