"""Drawing: 田字格 grids, stroke-order strips and printable pages.

All geometry is computed in "final" pixels (what the user sees) and everything
is drawn on a canvas `scale` times larger, then downsampled with LANCZOS.
Pillow polygon fills are aliased, so supersampling is what makes the strokes
look smooth.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw

from . import pinyin_util
from .fonts import get_font
from .strokes import StrokeData, Transform, stroke_numbers, stroke_polygons


@dataclass(frozen=True)
class Theme:
    paper: Tuple[int, int, int] = (253, 252, 248)
    cell: Tuple[int, int, int] = (255, 255, 255)
    grid: Tuple[int, int, int] = (232, 150, 150)
    grid_dash: Tuple[int, int, int] = (244, 199, 199)
    ink: Tuple[int, int, int] = (38, 38, 38)
    ghost: Tuple[int, int, int] = (212, 212, 212)
    trace: Tuple[int, int, int] = (232, 232, 232)
    skeleton: Tuple[int, int, int] = (150, 147, 142)
    accent: Tuple[int, int, int] = (208, 62, 51)
    muted: Tuple[int, int, int] = (128, 124, 118)
    rule: Tuple[int, int, int] = (228, 222, 212)


@dataclass
class RenderOptions:
    cell: int = 200
    scale: int = 2
    max_cols: int = 7
    practice_cells: int = 2
    show_numbers: bool = True
    show_labels: bool = True


class _Canvas:
    """Supersampled canvas wrapper that exposes scaled drawing helpers."""

    def __init__(self, width: int, height: int, scale: int, background: Tuple[int, int, int]):
        self.scale = max(1, scale)
        self.image = Image.new("RGB", (width * self.scale, height * self.scale), background)
        self.draw = ImageDraw.Draw(self.image)
        self.width = width
        self.height = height

    def s(self, value: float) -> float:
        return value * self.scale

    def finish(self) -> Image.Image:
        return self.image.resize((self.width, self.height), Image.LANCZOS)


def _measure(text: str, size: int) -> float:
    """Width of `text` at `size`, in un-scaled (final) pixels."""
    return get_font(size).getlength(text)


def _fit_font_size(text: str, max_width: float, desired: float, minimum: int = 12) -> int:
    """Largest size <= desired whose rendered width fits in max_width."""
    size = max(minimum, int(desired))
    while size > minimum and _measure(text, size) > max_width:
        size -= 1
    return size


def _dashed_line(
    draw: ImageDraw.ImageDraw,
    start: Tuple[float, float],
    end: Tuple[float, float],
    dash: float,
    gap: float,
    color,
    width: float,
) -> None:
    x0, y0 = start
    x1, y1 = end
    length = math.hypot(x1 - x0, y1 - y0)
    if length <= 0:
        return
    ux, uy = (x1 - x0) / length, (y1 - y0) / length
    pos = 0.0
    while pos < length:
        seg = min(dash, length - pos)
        draw.line(
            [(x0 + ux * pos, y0 + uy * pos), (x0 + ux * (pos + seg), y0 + uy * (pos + seg))],
            fill=color,
            width=max(1, int(round(width))),
        )
        pos += dash + gap


class PageRenderer:
    """Renders word pages and single-character pages."""

    def __init__(
        self,
        data: Dict[str, StrokeData],
        options: Optional[RenderOptions] = None,
        theme: Optional[Theme] = None,
        missing: Optional[Dict[str, str]] = None,
    ):
        self.data = data
        self.options = options or RenderOptions()
        self.theme = theme or Theme()
        self.missing = missing or {}
        self._polys: Dict[str, List[List[list]]] = {}

    # ------------------------------------------------------------------ #
    # primitives
    # ------------------------------------------------------------------ #
    def _polygons(self, char: str) -> List[List[list]]:
        if char not in self._polys:
            self._polys[char] = stroke_polygons(self.data[char])
        return self._polys[char]

    def _grid(self, canvas: _Canvas, x: float, y: float, size: float, faint: bool = False) -> None:
        draw = canvas.draw
        theme = self.theme
        border = canvas.s(max(1.0, size * 0.012))
        box = [canvas.s(x), canvas.s(y), canvas.s(x + size), canvas.s(y + size)]
        draw.rectangle(box, fill=theme.cell)
        draw.rectangle(box, outline=theme.grid_dash if faint else theme.grid, width=int(round(border)))

        dash = canvas.s(size * 0.05)
        gap = canvas.s(size * 0.045)
        thin = canvas.s(max(0.8, size * 0.006))
        _dashed_line(draw, (box[0], canvas.s(y + size / 2)), (box[2], canvas.s(y + size / 2)), dash, gap, theme.grid_dash, thin)
        _dashed_line(draw, (canvas.s(x + size / 2), box[1]), (canvas.s(x + size / 2), box[3]), dash, gap, theme.grid_dash, thin)

    def _glyph(
        self,
        canvas: _Canvas,
        char: str,
        x: float,
        y: float,
        size: float,
        colors: Dict[int, Tuple[int, int, int]],
        default_color: Tuple[int, int, int],
        upto: Optional[int] = None,
        fill: float = 0.86,
    ) -> None:
        if char not in self.data:
            self._placeholder(canvas, char, x, y, size)
            return
        polygons = self._polygons(char)
        transform = Transform(x=canvas.s(x), y=canvas.s(y), size=canvas.s(size), fill=fill)
        last = len(polygons) if upto is None else min(upto, len(polygons))
        for index in range(last):
            color = colors.get(index + 1, default_color)
            for polygon in polygons[index]:
                points = transform.map_polygon(polygon)
                if len(points) >= 3:
                    canvas.draw.polygon(points, fill=color)

    def _placeholder(self, canvas: _Canvas, char: str, x: float, y: float, size: float) -> None:
        canvas.draw.text(
            (canvas.s(x + size / 2), canvas.s(y + size / 2)),
            char,
            font=get_font(int(canvas.s(size * 0.6))),
            fill=self.theme.ghost,
            anchor="mm",
        )

    def _numbers(self, canvas: _Canvas, char: str, x: float, y: float, size: float) -> None:
        """Number every stroke, spreading labels out to avoid collisions."""
        if char not in self.data or not self.options.show_numbers:
            return
        data = self.data[char]
        transform = Transform(x=canvas.s(x), y=canvas.s(y), size=canvas.s(size), fill=0.86)
        font = get_font(int(canvas.s(size * 0.125)))
        radius = max(1.0, canvas.s(size * 0.062))

        left, top = canvas.s(x), canvas.s(y)
        right, bottom = canvas.s(x + size), canvas.s(y + size)
        margin = radius * 1.2
        placed: List[Tuple[float, float]] = []

        for number, (px, py), (dx, dy) in stroke_numbers(data, transform):
            length = math.hypot(dx, dy) or 1.0
            tangent = (dx / length, dy / length)
            normal = (-tangent[1], tangent[0])
            spot: Optional[Tuple[float, float]] = None
            # Walk outwards along the stroke, then sideways, until the badge
            # no longer collides with an already placed number.
            for step in range(0, 30):
                distance = step * radius * 0.8
                for ox, oy in (tangent, normal, (-normal[0], -normal[1]), (-tangent[0], -tangent[1])):
                    candidate = (px + ox * distance, py + oy * distance)
                    if not (left + margin <= candidate[0] <= right - margin):
                        continue
                    if not (top + margin <= candidate[1] <= bottom - margin):
                        continue
                    if all(
                        math.hypot(candidate[0] - qx, candidate[1] - qy) >= radius * 2.05
                        for qx, qy in placed
                    ):
                        spot = candidate
                        break
                if spot is not None:
                    break
            if spot is None:
                spot = (
                    min(max(px, left + margin), right - margin),
                    min(max(py, top + margin), bottom - margin),
                )
            placed.append(spot)

            halo = radius * 1.22
            canvas.draw.ellipse(
                [spot[0] - halo, spot[1] - halo, spot[0] + halo, spot[1] + halo],
                fill=self.theme.cell,
            )
            canvas.draw.ellipse(
                [spot[0] - radius, spot[1] - radius, spot[0] + radius, spot[1] + radius],
                fill=self.theme.accent,
            )
            canvas.draw.text(spot, str(number), font=font, fill=self.theme.cell, anchor="mm")

    def _caption(self, canvas: _Canvas, x: float, y: float, width: float, text: str) -> None:
        if not text or not self.options.show_labels:
            return
        canvas.draw.text(
            (canvas.s(x + width / 2), canvas.s(y)),
            text,
            font=get_font(int(canvas.s(max(9, self.options.cell * 0.135)))),
            fill=self.theme.muted,
            anchor="ma",
        )

    # ------------------------------------------------------------------ #
    # strips
    # ------------------------------------------------------------------ #
    def _strip_rows(self, char: str, cell: float) -> List[List[Tuple[str, str]]]:
        """Build the cell sequence for one character.

        Each entry is (kind, label): 'progressive:N' shows strokes 1..N with the
        newest one highlighted, 'numbered' shows the full glyph with stroke
        numbers, 'blank' is an empty 田字格 for practice.
        """
        count = self.data[char].count if char in self.data else 0
        if not count:
            return []
        cols = max(1, int((self.options.max_cols * self.options.cell) // cell))
        cells: List[Tuple[str, str]] = [(f"progressive:{i}", f"{i}") for i in range(1, count + 1)]
        cells.append(("numbered", "笔顺"))

        # Practice cells top up the current row instead of spilling onto a new
        # one, which would leave a row with a single lonely cell.
        wanted = self.options.practice_cells
        added = 0
        while wanted > 0 and len(cells) % cols != 0:
            cells.append(("blank", "练习"))
            wanted -= 1
            added += 1
        if added == 0:
            cells.extend(("blank", "练习") for _ in range(min(wanted, cols)))

        # Close the rectangle when the final row is already mostly full.
        remainder = len(cells) % cols
        if remainder and remainder * 2 >= cols:
            cells.extend(("blank", "") for _ in range(cols - remainder))
        return [cells[i : i + cols] for i in range(0, len(cells), cols)]

    def _draw_cell(self, canvas: _Canvas, char: str, kind: str, x: float, y: float, cell: float) -> None:
        self._grid(canvas, x, y, cell)
        if kind == "numbered":
            # A grey skeleton keeps the stroke numbers readable on dense
            # characters while the shape of the character stays visible.
            self._glyph(canvas, char, x, y, cell, {}, self.theme.skeleton)
            self._numbers(canvas, char, x, y, cell)
        elif kind == "blank":
            return
        else:
            upto = int(kind.split(":", 1)[1])
            self._glyph(canvas, char, x, y, cell, {upto: self.theme.accent}, self.theme.ghost, upto=upto)

    # ------------------------------------------------------------------ #
    # word page
    # ------------------------------------------------------------------ #
    def render_word(self, hanzi: str, syllables: Sequence[str], title: str = "") -> Image.Image:
        opt = self.options
        cell = opt.cell
        gap = cell * 0.13
        pad = cell * 0.46
        content_w = opt.max_cols * cell + (opt.max_cols - 1) * gap
        width = int(round(content_w + pad * 2))

        # A repeated character only needs one block (画画 -> one 画 strip).
        order: List[str] = []
        for char in hanzi:
            if char not in order:
                order.append(char)
        blocks = [(char, self._strip_rows(char, cell)) for char in order]

        syllable_of: Dict[str, str] = {}
        if len(syllables) == len(hanzi):
            for char, syllable in zip(hanzi, syllables):
                syllable_of.setdefault(char, syllable)
        pinyin_text = pinyin_util.display(syllables)

        head_size = _fit_font_size(hanzi, content_w * 0.5, cell * 0.92, 24)
        title_size = int(cell * 0.22)
        header_h = max(cell * 1.5, head_size * 1.28)

        block_gap = cell * 0.34
        sub_h = cell * 0.34
        label_h = cell * 0.28 if opt.show_labels else 0.0
        footer_h = cell * 0.5

        height = pad + header_h
        for _char, rows in blocks:
            if not rows:
                height += sub_h + cell * 0.5 + block_gap
                continue
            height += sub_h + len(rows) * (cell + label_h) + (len(rows) - 1) * gap * 0.4 + block_gap
        height += footer_h
        height = int(round(height))

        canvas = _Canvas(width, height, opt.scale, self.theme.paper)
        draw = canvas.draw

        # ---- header ---------------------------------------------------- #
        y = pad
        head_w = _measure(hanzi, head_size)
        draw.text((canvas.s(pad), canvas.s(y)), hanzi, font=get_font(int(canvas.s(head_size))), fill=self.theme.ink, anchor="la")

        title_w = _measure(title, title_size) if title else 0.0
        pinyin_left = pad + head_w + cell * 0.32
        available = width - pad - title_w - (cell * 0.45 if title else 0.0) - pinyin_left
        pinyin_size = _fit_font_size(pinyin_text, max(cell * 1.2, available), cell * 0.4, 14)
        draw.text(
            (canvas.s(pinyin_left), canvas.s(y + (head_size - pinyin_size) * 0.55)),
            pinyin_text,
            font=get_font(int(canvas.s(pinyin_size))),
            fill=self.theme.muted,
            anchor="la",
        )
        if title:
            draw.text(
                (canvas.s(width - pad), canvas.s(y + cell * 0.08)),
                title,
                font=get_font(int(canvas.s(title_size))),
                fill=self.theme.muted,
                anchor="ra",
            )
        rule_y = y + header_h - cell * 0.26
        draw.line(
            [(canvas.s(pad), canvas.s(rule_y)), (canvas.s(width - pad), canvas.s(rule_y))],
            fill=self.theme.rule,
            width=max(1, int(canvas.s(1.2))),
        )

        # ---- stroke blocks --------------------------------------------- #
        y = pad + header_h
        sub_font = get_font(int(canvas.s(cell * 0.23)))
        for char, rows in blocks:
            stroke_count = self.data[char].count if char in self.data else 0
            label = f"{char}　共 {stroke_count} 笔" if stroke_count else f"{char}　笔顺数据缺失"
            draw.text((canvas.s(pad), canvas.s(y)), label, font=sub_font, fill=self.theme.accent, anchor="la")
            syllable = syllable_of.get(char, "")
            if syllable:
                label_w = _measure(label, cell * 0.23)
                draw.text(
                    (canvas.s(pad + label_w + cell * 0.16), canvas.s(y + cell * 0.02)),
                    syllable,
                    font=sub_font,
                    fill=self.theme.muted,
                    anchor="la",
                )
            y += sub_h

            if not rows:
                draw.text(
                    (canvas.s(pad), canvas.s(y)),
                    "未取得该字的笔顺数据：请确认字形正确，或检查网络后重试。",
                    font=get_font(int(canvas.s(cell * 0.18))),
                    fill=self.theme.muted,
                    anchor="la",
                )
                y += cell * 0.5 + block_gap
                continue

            for row in rows:
                for index, (kind, cell_label) in enumerate(row):
                    cx = pad + index * (cell + gap)
                    self._draw_cell(canvas, char, kind, cx, y, cell)
                    self._caption(canvas, cx, y + cell + cell * 0.07, cell, cell_label)
                y += cell + label_h + gap * 0.4
            y += block_gap - gap * 0.4

        # ---- footer ---------------------------------------------------- #
        draw.text(
            (canvas.s(pad), canvas.s(height - footer_h * 0.72)),
            "按笔顺编号一笔一笔地写，红色为当前笔画；最后在空白田字格里自己写一写。",
            font=get_font(int(canvas.s(cell * 0.155))),
            fill=self.theme.muted,
            anchor="la",
        )
        return canvas.finish()

    # ------------------------------------------------------------------ #
    # single character page
    # ------------------------------------------------------------------ #
    def render_char(self, char: str, syllable: str = "") -> Image.Image:
        opt = self.options
        cell = int(round(opt.cell * 1.25))
        gap = cell * 0.13
        pad = cell * 0.42
        cols = 5
        content_w = cols * cell + (cols - 1) * gap
        width = int(round(content_w + pad * 2))

        stroke_count = self.data[char].count if char in self.data else 0
        strip = self._strip_rows(char, cell)
        if not strip:
            strip = [[("blank", "")] * cols]

        header_h = cell * 0.95
        section_h = cell * 0.42
        label_h = cell * 0.26
        height = pad + header_h + section_h + len(strip) * (cell + label_h + gap * 0.35)
        height += section_h + cell + label_h + gap * 0.35  # 描红
        height += section_h + cell + label_h + gap * 0.35  # 自己写
        height += pad * 0.8
        height = int(round(height))

        canvas = _Canvas(width, height, opt.scale, self.theme.paper)
        draw = canvas.draw

        info = f"{syllable}    共 {stroke_count} 笔" if stroke_count else "笔顺数据缺失"
        head_size = _fit_font_size(char, content_w * 0.3, cell * 0.5, 20)
        draw.text((canvas.s(pad), canvas.s(pad)), char, font=get_font(int(canvas.s(head_size))), fill=self.theme.ink, anchor="la")
        info_size = _fit_font_size(f"{char} {info}", content_w * 0.6, cell * 0.2, 12)
        draw.text(
            (canvas.s(pad + _measure(char, head_size) + cell * 0.2), canvas.s(pad + cell * 0.2)),
            info.strip(),
            font=get_font(int(canvas.s(info_size))),
            fill=self.theme.muted,
            anchor="la",
        )

        y = pad + header_h
        section_font = get_font(int(canvas.s(cell * 0.19)))
        draw.text((canvas.s(pad), canvas.s(y)), "笔顺", font=section_font, fill=self.theme.accent, anchor="la")
        y += section_h
        for row in strip:
            for index, (kind, cell_label) in enumerate(row):
                cx = pad + index * (cell + gap)
                self._draw_cell(canvas, char, kind, cx, y, cell)
                self._caption(canvas, cx, y + cell + cell * 0.06, cell, cell_label)
            y += cell + label_h + gap * 0.35

        y += section_h * 0.2
        draw.text((canvas.s(pad), canvas.s(y)), "描红", font=section_font, fill=self.theme.accent, anchor="la")
        y += section_h
        for index in range(cols):
            cx = pad + index * (cell + gap)
            self._grid(canvas, cx, y, cell, faint=True)
            self._glyph(canvas, char, cx, y, cell, {}, self.theme.trace)
        y += cell + label_h + gap * 0.35

        draw.text((canvas.s(pad), canvas.s(y)), "自己写一写", font=section_font, fill=self.theme.accent, anchor="la")
        y += section_h
        for index in range(cols):
            cx = pad + index * (cell + gap)
            self._grid(canvas, cx, y, cell)
        return canvas.finish()

    # ------------------------------------------------------------------ #
    # cover
    # ------------------------------------------------------------------ #
    def render_cover(self, words: Sequence[Tuple[str, str]], title: str) -> Image.Image:
        """Overview page listing every word on the card."""
        opt = self.options
        cell = int(opt.cell * 0.8)
        pad = cell * 0.5
        gap = cell * 0.28
        cols = min(5, max(1, len(words)))
        word_size = cell * 0.5
        pinyin_size = cell * 0.17

        # Column width follows the widest entry so 3-character words never
        # bleed into the next column.
        col_w = max(
            max((_measure(hanzi, int(word_size)) for hanzi, _ in words), default=0.0),
            max((_measure(pinyin_text, int(pinyin_size)) for _, pinyin_text in words), default=0.0),
        ) + gap
        rows = max(1, math.ceil(len(words) / cols))
        width = int(pad * 2 + cols * col_w)
        height = int(pad * 2 + cell * 1.3 + rows * (cell + gap))

        canvas = _Canvas(width, height, opt.scale, self.theme.paper)
        draw = canvas.draw

        title_size = _fit_font_size(title, width - pad * 2, cell * 0.4, 16)
        draw.text((canvas.s(pad), canvas.s(pad)), title, font=get_font(int(canvas.s(title_size))), fill=self.theme.ink, anchor="la")
        draw.text(
            (canvas.s(pad), canvas.s(pad + title_size * 1.35)),
            "每个字都有一页田字格笔顺图，见后续页面",
            font=get_font(int(canvas.s(cell * 0.16))),
            fill=self.theme.muted,
            anchor="la",
        )

        y = pad + cell * 1.02
        for index, (hanzi, pinyin_text) in enumerate(words):
            col, row = index % cols, index // cols
            x = pad + col * col_w
            cy = y + row * (cell + gap)
            draw.text((canvas.s(x), canvas.s(cy)), hanzi, font=get_font(int(canvas.s(word_size))), fill=self.theme.ink, anchor="la")
            draw.text(
                (canvas.s(x), canvas.s(cy + cell * 0.6)),
                pinyin_text,
                font=get_font(int(canvas.s(pinyin_size))),
                fill=self.theme.muted,
                anchor="la",
            )
        return canvas.finish()


def placeholder_page(message: str, cell: int = 200, scale: int = 2) -> Image.Image:
    """Small image used when a character has no stroke data."""
    width, height = int(cell * 4.4), int(cell * 1.2)
    canvas = _Canvas(width, height, scale, Theme().paper)
    canvas.draw.text(
        (canvas.s(width / 2), canvas.s(height / 2)),
        message,
        font=get_font(int(canvas.s(cell * 0.18))),
        fill=Theme().muted,
        anchor="mm",
    )
    return canvas.finish()
