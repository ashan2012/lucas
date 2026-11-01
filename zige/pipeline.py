"""End-to-end pipeline: image (or JSON) -> rendered stroke-order material."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from . import export, layout, ocr, pinyin_util
from .config import PDF_LIBRARY_NAME
from .draw import PageRenderer, RenderOptions, Theme
from .layout import Word
from .strokes import StrokeData, StrokeError, get, prefetch

CJK_RE = re.compile(r"^[\u3400-\u4dbf\u4e00-\u9fff]+$")
CJK_ANY_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


@dataclass
class CardMeta:
    title: str = "拼读练习 · 笔顺卡片"
    subtitle: str = ""


@dataclass
class BuildOptions:
    out_dir: Path
    # Where the combined PDF goes. Defaults to a shared library folder next to
    # `out_dir` (out/<card>/ -> out/pdf仓库/).
    pdf_dir: Optional[Path] = None
    cell: int = 200
    scale: int = 2
    max_cols: int = 7
    practice_cells: int = 2
    make_pdf: bool = True
    make_char_pages: bool = True
    allow_network: bool = True
    workers: int = 8
    only: Optional[Sequence[int]] = None


@dataclass
class BuildResult:
    out_dir: Path
    pages: List[export.Page] = field(default_factory=list)
    words: List[Word] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    missing: Dict[str, str] = field(default_factory=dict)
    json_path: Optional[Path] = None
    pdf_path: Optional[Path] = None
    html_path: Optional[Path] = None


# --------------------------------------------------------------------------- #
# input
# --------------------------------------------------------------------------- #
def _detect_meta(lines: Sequence[ocr.TextLine], image_height: float) -> CardMeta:
    """Best-effort extraction of the printed title and the lesson hint."""
    meta = CardMeta()
    candidates = []
    for line in lines:
        text = line.text.strip()
        if len(text) < 4 or not CJK_ANY_RE.search(text):
            continue
        if line.cy > image_height * 0.18:
            continue
        candidates.append((line.width * line.height, text))
    if candidates:
        candidates.sort(reverse=True)
        meta.title = candidates[0][1]

    for line in lines:
        text = line.text.strip()
        if re.match(r"^\d+\s*[.、]", text) and line.cy < image_height * 0.30:
            meta.subtitle = text
            break
    return meta


def words_from_image(image_path: Path) -> Tuple[List[Word], CardMeta]:
    lines = ocr.read_image(image_path)
    image_height = ocr.image_size(image_path)[1]
    words = layout.parse_card(lines)
    if not words:
        raise layout.EmptyCardError(
            "no pinyin/hanzi cells found. Try --debug to inspect the OCR output, "
            "or write a JSON file and use --from-json."
        )
    return words, _detect_meta(lines, image_height)


def words_from_json(json_path: Path) -> Tuple[List[Word], CardMeta]:
    payload = export.load_json(json_path)
    items = payload.get("words") or []
    words: List[Word] = []
    for index, item in enumerate(items):
        hanzi = str(item.get("hanzi", "")).strip()
        if not hanzi:
            continue
        pinyin_text = str(item.get("pinyin", "") or item.get("pinyin_ocr", "")).strip()
        words.append(Word(hanzi=hanzi, pinyin_ocr=pinyin_text, row=index // 6, x=float(index)))
    if not words:
        raise layout.EmptyCardError(f"no words found in {json_path}")
    meta = CardMeta(
        title=str(payload.get("title") or "拼读练习 · 笔顺卡片"),
        subtitle=str(payload.get("subtitle") or ""),
    )
    return words, meta


def to_json_payload(words: Sequence[Word], meta: CardMeta, source: str = "") -> dict:
    return {
        "version": 1,
        "source": source,
        "title": meta.title,
        "subtitle": meta.subtitle,
        "words": [
            {
                "hanzi": word.hanzi,
                "pinyin": word.pinyin,
                "pinyin_ocr": word.pinyin_ocr,
            }
            for word in words
        ],
    }


def resolve_pinyin(words: Sequence[Word], use_json_pinyin: bool = False) -> List[str]:
    """Fill in `syllables` and `warnings` for each word; returns warnings."""
    warnings: List[str] = []
    for word in words:
        if use_json_pinyin and word.pinyin_ocr:
            syllables = [s for s in word.pinyin_ocr.split() if s]
            if len(syllables) != len(word.hanzi):
                syllables, extra = pinyin_util.analyse(word.hanzi, word.pinyin_ocr)
                warnings.extend(extra)
            word.syllables = syllables
            word.warnings = []
        else:
            syllables, extra = pinyin_util.analyse(word.hanzi, word.pinyin_ocr)
            warnings.extend(extra)
            word.syllables = syllables
            word.warnings = extra
    return warnings


# --------------------------------------------------------------------------- #
# build
# --------------------------------------------------------------------------- #
def _clean_outputs(out_dir: Path) -> None:
    """Remove pages from a previous run so numbering never goes stale."""
    for pattern in ("*.png", "*.pdf"):
        for stale in out_dir.glob(pattern):
            stale.unlink(missing_ok=True)
    for stale in (out_dir / "chars").glob("*.png"):
        stale.unlink(missing_ok=True)


def _unique_chars(words: Iterable[Word]) -> List[str]:
    seen: Dict[str, None] = {}
    for word in words:
        for char in word.hanzi:
            if CJK_RE.match(char) and char not in seen:
                seen[char] = None
    return list(seen)


def build(
    words: Sequence[Word],
    meta: CardMeta,
    options: BuildOptions,
    source: str = "",
    log: Callable[[str], None] = lambda msg: print(msg, file=sys.stderr),
) -> BuildResult:
    out_dir = Path(options.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    _clean_outputs(out_dir)
    result = BuildResult(out_dir=out_dir, words=list(words))

    selected = list(words)
    if options.only:
        wanted = {i for i in options.only}
        selected = [w for i, w in enumerate(words, start=1) if i in wanted]
        if not selected:
            raise ValueError(f"--only {sorted(wanted)} did not match any word")

    chars = _unique_chars(selected)
    log(f"[1/4] fetching stroke data for {len(chars)} characters ...")
    failures = prefetch(chars, allow_network=options.allow_network, workers=options.workers)

    data: Dict[str, StrokeData] = {}
    for char, error in failures.items():
        if error is None:
            try:
                data[char] = get(char, allow_network=False)
            except StrokeError as exc:  # pragma: no cover - cache read failure
                result.missing[char] = str(exc)
                result.warnings.append(f"“{char}” 笔顺读取失败：{exc}")
        else:
            result.missing[char] = str(error)
            result.warnings.append(f"“{char}” 缺少笔顺数据，卡片中将显示占位提示")

    json_path = out_dir / "parsed.json"
    export.save_json(to_json_payload(selected, meta, source), json_path)
    result.json_path = json_path

    log("[2/4] rendering word pages ...")
    renderer = PageRenderer(
        data,
        RenderOptions(
            cell=options.cell,
            scale=options.scale,
            max_cols=options.max_cols,
            practice_cells=options.practice_cells,
        ),
        Theme(),
        result.missing,
    )

    pages: List[export.Page] = []

    try:
        cover = renderer.render_cover([(w.hanzi, w.pinyin) for w in selected], meta.title)
        cover_path = export.save_png(cover, out_dir / "00_cover.png")
        pages.append(export.Page(path=cover_path, title="总览 / Overview", kind="cover"))
    except Exception as exc:  # pragma: no cover - cover is cosmetic
        log(f"    (skipped cover: {exc})")

    # Keep the numbering of the original card so a page can always be traced
    # back to its cell, even when --only renders a subset.
    original_index = {id(word): i for i, word in enumerate(words, start=1)}
    for word in selected:
        image = renderer.render_word(word.hanzi, word.syllables, meta.subtitle)
        path = export.save_png(
            image, out_dir / f"{original_index[id(word)]:02d}_{word.hanzi}.png"
        )
        pages.append(export.Page(path=path, title=f"{word.pinyin}  {word.hanzi}", kind="word"))
        log(f"    {path.name}")

    if options.make_char_pages and chars:
        log("[3/4] rendering single-character pages ...")
        syllables_by_char: Dict[str, str] = {}
        for word in selected:
            if len(word.syllables) == len(word.hanzi):
                for char, syllable in zip(word.hanzi, word.syllables):
                    syllables_by_char.setdefault(char, syllable)
        for char in chars:
            image = renderer.render_char(char, syllables_by_char.get(char, ""))
            path = export.save_png(image, out_dir / "chars" / f"{char}.png")
            pages.append(
                export.Page(path=path, title=f"{char}  {syllables_by_char.get(char, '')}", kind="char")
            )

    log("[4/4] writing index ...")
    result.html_path = export.save_html(
        pages,
        out_dir / "index.html",
        title=meta.title,
        subtitle=(meta.subtitle + "  ·  " if meta.subtitle else "") + f"{len(selected)} 个词语",
    )

    if options.make_pdf:
        pdf_dir = Path(options.pdf_dir) if options.pdf_dir else out_dir.parent / PDF_LIBRARY_NAME
        pdf_path = pdf_dir / f"{export.safe_name(meta.title, 'card')}.pdf"
        export.save_pdf([page.path for page in pages], pdf_path)
        result.pdf_path = pdf_path

    for word in selected:
        result.warnings.extend(word.warnings)
    result.pages = pages
    return result
