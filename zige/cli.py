"""Command line interface."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from . import ocr, pipeline
from .config import DEFAULT_CELL, DEFAULT_DPI_SCALE
from .layout import EmptyCardError
from .strokes import StrokeError

DESCRIPTION = """\
Turn a photo of a 拼读 (pinyin reading) flash card into 田字格 stroke-order sheets.

The card is read with macOS Vision OCR: hanzi cells are extracted, pinyin is
regenerated from the hanzi (so tone marks are always correct), and every
character gets a 田字格 strip showing the strokes in writing order.
"""


def _parse_only(value: str) -> List[int]:
    numbers = []
    for chunk in value.replace(" ", ",").split(","):
        if not chunk:
            continue
        try:
            numbers.append(int(chunk))
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"invalid index: {chunk}") from exc
    return numbers


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zige",
        description=DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("image", nargs="?", help="photo of the flash card (png/jpg/heic ...)")
    parser.add_argument(
        "--from-json",
        metavar="PATH",
        help="render from a parsed.json (use it after fixing OCR mistakes)",
    )
    parser.add_argument("-o", "--out", metavar="DIR", help="output directory (default: out/<card name>)")
    parser.add_argument("--cell", type=int, default=DEFAULT_CELL, help=f"田字格 size in px (default {DEFAULT_CELL})")
    parser.add_argument(
        "--scale",
        type=int,
        default=DEFAULT_DPI_SCALE,
        help=f"supersampling factor for smooth strokes (default {DEFAULT_DPI_SCALE})",
    )
    parser.add_argument("--max-cols", type=int, default=7, help="max cells per row (default 7)")
    parser.add_argument("--practice", type=int, default=2, help="blank practice cells per character (default 2)")
    parser.add_argument("--only", type=_parse_only, metavar="1,3", help="render only these word numbers")
    parser.add_argument("--no-pdf", action="store_true", help="skip the combined PDF")
    parser.add_argument("--no-char-pages", action="store_true", help="skip the per-character pages")
    parser.add_argument("--offline", action="store_true", help="use only cached stroke data")
    parser.add_argument("--workers", type=int, default=8, help="parallel downloads (default 8)")
    parser.add_argument("--dump-ocr", action="store_true", help="print raw OCR lines and exit")
    parser.add_argument("--debug", action="store_true", help="print the detected row structure")
    parser.add_argument("--version", action="version", version="zige 1.0")
    return parser


def _print_table(words: Sequence) -> None:
    if not words:
        return
    width = max(len(w.hanzi) for w in words)
    print(f"\nDetected {len(words)} words:")
    for index, word in enumerate(words, start=1):
        ocr_note = f"   (ocr: {word.pinyin_ocr})" if word.pinyin_ocr else ""
        print(f"  {index:02d}. {word.hanzi:{width}s}  {word.pinyin}{ocr_note}")
    print()


def _default_out(image: Optional[Path], from_json: Optional[Path]) -> Path:
    stem = "card"
    if image is not None:
        stem = image.stem or "card"
    elif from_json is not None:
        stem = from_json.parent.name or "card"
    cleaned = "".join(ch for ch in stem if ch.isalnum() or ch in "-_") or "card"
    return Path("out") / cleaned


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.dump_ocr:
        if not args.image:
            parser.error("--dump-ocr needs an image")
        try:
            print(ocr.read_text_debug(args.image))
        except ocr.OcrError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        return 0

    image = Path(args.image).expanduser() if args.image else None
    from_json = Path(args.from_json).expanduser() if args.from_json else None

    if image is None and from_json is None:
        parser.error("provide a card image, or --from-json PATH")
    if image is not None and from_json is not None:
        parser.error("use either an image or --from-json, not both")
    if image is not None and not image.exists():
        print(f"error: image not found: {image}", file=sys.stderr)
        return 2

    try:
        if from_json is not None:
            if not from_json.exists():
                print(f"error: json not found: {from_json}", file=sys.stderr)
                return 2
            words, meta = pipeline.words_from_json(from_json)
            warnings = pipeline.resolve_pinyin(words, use_json_pinyin=True)
        else:
            if args.debug:
                print(ocr.read_text_debug(str(image)))
                print()
                from . import layout as layout_mod

                print(layout_mod.describe_rows(ocr.read_image(image)))
                print()
            words, meta = pipeline.words_from_image(image)
            warnings = pipeline.resolve_pinyin(words, use_json_pinyin=False)
    except ocr.OcrError as exc:
        print(f"error: OCR failed: {exc}", file=sys.stderr)
        return 2
    except EmptyCardError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    _print_table(words)
    for warning in warnings:
        print(f"  ! {warning}", file=sys.stderr)

    out_dir = Path(args.out).expanduser() if args.out else _default_out(image, from_json)
    options = pipeline.BuildOptions(
        out_dir=out_dir,
        cell=args.cell,
        scale=args.scale,
        max_cols=max(2, args.max_cols),
        practice_cells=max(0, args.practice),
        make_pdf=not args.no_pdf,
        make_char_pages=not args.no_char_pages,
        allow_network=not args.offline,
        workers=max(1, args.workers),
        only=args.only,
    )

    try:
        result = pipeline.build(
            words,
            meta,
            options,
            source=str(image) if image else str(from_json),
        )
    except StrokeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 4
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"\nDone. {len(result.pages)} pages written to {result.out_dir}")
    if result.html_path:
        print(f"  index : {result.html_path}")
    if result.pdf_path:
        print(f"  pdf   : {result.pdf_path}")
    if result.json_path:
        print(f"  json  : {result.json_path}   (edit and re-run with --from-json)")
    if result.missing:
        print(f"  note  : {len(result.missing)} character(s) had no stroke data", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
