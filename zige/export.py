"""File outputs: PNG pages, a printable PDF, an HTML index and the JSON
round-trip file that lets a user fix OCR mistakes by hand.
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from PIL import Image


@dataclass
class Page:
    """One rendered page on disk."""

    path: Path
    title: str
    kind: str  # "word" | "char" | "cover"


def save_png(image: Image.Image, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG", optimize=True)
    return path


def save_pdf(pages: Sequence[Path], path: Path, dpi: int = 150) -> Path:
    images = []
    try:
        for page in pages:
            with Image.open(page) as img:
                images.append(img.convert("RGB").copy())
        if not images:
            raise ValueError("no pages to write")
        path.parent.mkdir(parents=True, exist_ok=True)
        first, rest = images[0], images[1:]
        first.save(path, "PDF", resolution=dpi, save_all=True, append_images=rest)
    finally:
        for img in images:
            img.close()
    return path


def save_json(data: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_html(pages: Sequence[Page], path: Path, title: str, subtitle: str = "") -> Path:
    cards = []
    for page in pages:
        rel = page.path.name if page.path.parent == path.parent else page.path.relative_to(path.parent).as_posix()
        label = html.escape(page.title)
        cards.append(
            f'    <figure class="page">\n'
            f'      <img src="{html.escape(rel)}" alt="{label}">\n'
            f'      <figcaption>{label}</figcaption>\n'
            f'    </figure>'
        )
    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
  :root {{ color-scheme: light; }}
  body {{
    margin: 0; padding: 28px 18px 64px;
    background: #f4f1ea; color: #2b2b2b;
    font-family: -apple-system, "PingFang SC", "Hiragino Sans GB", sans-serif;
  }}
  header {{ max-width: 980px; margin: 0 auto 22px; }}
  h1 {{ font-size: 22px; margin: 0 0 6px; }}
  p.sub {{ margin: 0; color: #7a756d; font-size: 13px; }}
  main {{ max-width: 980px; margin: 0 auto; display: grid; gap: 18px; }}
  figure.page {{ margin: 0; background: #fff; border-radius: 10px; padding: 10px;
                 box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
  figure.page img {{ width: 100%; height: auto; display: block; border-radius: 6px; }}
  figcaption {{ font-size: 12px; color: #7a756d; padding: 8px 4px 2px; }}
  @media print {{
    body {{ background: #fff; padding: 0; }}
    header, figcaption {{ display: none; }}
    figure.page {{ break-inside: avoid; box-shadow: none; padding: 0; page-break-after: always; }}
  }}
</style>
</head>
<body>
<header>
  <h1>{html.escape(title)}</h1>
  <p class="sub">{html.escape(subtitle)}</p>
</header>
<main>
{chr(10).join(cards)}
</main>
</body>
</html>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document, encoding="utf-8")
    return path


def safe_name(text: str, fallback: str = "page") -> str:
    cleaned = "".join(ch for ch in text if ch.isalnum() or ch in "-_")
    return cleaned or fallback
