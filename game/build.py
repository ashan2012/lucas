"""Build a self-contained pinyin + stroke-order puzzle page from the parsed cards.

Every word of every parsed card becomes one level; the page is a single HTML file
(styles, game logic and stroke data inlined) so it can be opened by double-click,
no server needed.

Usage:
    .venv/bin/python game/build.py                    # 全部卡片 -> out/游戏/拼音笔顺拼图.html
    .venv/bin/python game/build.py --card 第3天        # 只做一张卡片
    .venv/bin/python game/build.py --word 画画         # 只做一个词语（调试用）
"""

from __future__ import annotations

import argparse
import json
import random
import re
import string
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from zige.strokes import StrokeError, get  # noqa: E402

HERE = Path(__file__).resolve().parent
DEFAULT_CARDS_DIR = ROOT / "out"
DEFAULT_OUT = DEFAULT_CARDS_DIR / "游戏" / "拼音笔顺拼图.html"

# accented vowel -> (base letter, tone number)
TONE_MARKS: Dict[str, tuple] = {}
for _base, _marks in {
    "a": "āáǎà",
    "e": "ēéěè",
    "i": "īíǐì",
    "o": "ōóǒò",
    "u": "ūúǔù",
    "ü": "ǖǘǚǜ",
}.items():
    for _tone, _char in enumerate(_marks, start=1):
        TONE_MARKS[_char] = (_base, _tone)
TONE_MARKS.update({"ń": ("n", 2), "ň": ("n", 3), "ǹ": ("n", 4)})


def split_syllable(syllable: str) -> dict:
    """'huà' -> {letters: ['h','u','a'], tone: 4, toneAt: 2}."""
    letters: List[str] = []
    tone, tone_at = 0, -1
    for char in syllable:
        base, mark = TONE_MARKS.get(char, (char, 0))
        if mark and not tone:
            tone, tone_at = mark, len(letters)
        letters.append(base)
    return {"letters": letters, "tone": tone, "toneAt": tone_at, "text": syllable}


# --------------------------------------------------------------------------- #
# cards
# --------------------------------------------------------------------------- #
def day_of(title: str) -> int:
    match = re.search(r"第\s*(\d+)\s*天", title or "")
    return int(match.group(1)) if match else 0


def load_cards(root: Path) -> List[Tuple[int, Path, dict]]:
    """Every `out/*/parsed.json`, de-duplicated by title + word list."""
    cards: List[Tuple[int, Path, dict]] = []
    seen = set()
    for path in sorted(root.glob("*/parsed.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        words = tuple(str(w.get("hanzi") or "") for w in payload.get("words") or [])
        key = (str(payload.get("title") or ""), words)
        if key in seen:  # 同一张卡片被解析过两次，只留第一份
            continue
        seen.add(key)
        cards.append((day_of(str(payload.get("title") or "")), path, payload))
    cards.sort(key=lambda item: (item[0], str(item[1])))
    return cards


def card_label(payload: dict, path: Path) -> str:
    day = day_of(str(payload.get("title") or ""))
    return f"第 {day} 天" if day else str(payload.get("title") or path.parent.name)


def word_specs(payload: dict) -> Iterable[Tuple[str, List[str]]]:
    """Yield (hanzi, syllables) only for words whose pinyin lines up."""
    for item in payload.get("words") or []:
        hanzi = re.sub(r"\s+", "", str(item.get("hanzi") or ""))
        syllables = str(item.get("pinyin") or "").split()
        if not hanzi or len(syllables) != len(hanzi):
            continue
        yield hanzi, syllables


def build_levels(payload: dict, card: str) -> List[dict]:
    return [
        {
            "hanzi": hanzi,
            "pinyin": " ".join(syllables),
            "card": card,
            "lesson": str(payload.get("subtitle") or "").strip(),
            "chars": [
                {"char": char, "syllable": split_syllable(syllable)}
                for char, syllable in zip(hanzi, syllables)
            ],
        }
        for hanzi, syllables in word_specs(payload)
    ]


# --------------------------------------------------------------------------- #
# data
# --------------------------------------------------------------------------- #
def load_strokes(levels: List[dict], allow_network: bool) -> Tuple[Dict[str, List[str]], List[str]]:
    """Stroke paths for every character, plus the words that had to be dropped."""
    strokes: Dict[str, List[str]] = {}
    failed: List[str] = []
    for level in levels:
        for spec in level["chars"]:
            char = spec["char"]
            if char in strokes:
                continue
            try:
                strokes[char] = list(get(char, allow_network=allow_network).strokes)
            except StrokeError:
                failed.append(char)
    return strokes, failed


def decoy_pool(levels: List[dict], strokes: Dict[str, List[str]], cap: int = 180) -> List[str]:
    """A stock of strokes from other characters, shuffled once and then shared."""
    wanted = {spec["char"] for level in levels for spec in level["chars"]}
    pool = list(dict.fromkeys(d for char in wanted for d in strokes.get(char, [])))
    random.Random(7).shuffle(pool)
    return pool[:cap]


def build_data(cards: List[Tuple[int, Path, dict]], allow_network: bool, decoys: int) -> Tuple[dict, List[str]]:
    levels: List[dict] = []
    all_levels: List[dict] = []
    for _day, path, payload in cards:
        all_levels.extend(build_levels(payload, card_label(payload, path)))

    strokes, failed = load_strokes(all_levels, allow_network)
    levels = [lv for lv in all_levels if all(spec["char"] in strokes for spec in lv["chars"])]

    pool = list(string.ascii_lowercase)
    for level in levels:
        for spec in level["chars"]:
            for letter in spec["syllable"]["letters"]:
                if letter not in pool:
                    pool.append(letter)

    data = {
        "levels": levels,
        "strokes": strokes,
        "decoys": decoy_pool(levels, strokes),
        "decoysPerChar": decoys,
        "poolLetters": pool,
    }
    return data, failed


def pieces_of(level: dict, strokes: Dict[str, Sequence[str]]) -> int:
    total = 0
    for spec in level["chars"]:
        total += len(spec["syllable"]["letters"]) + (1 if spec["syllable"]["tone"] else 0)
        total += len(strokes.get(spec["char"], []))
    return total


# --------------------------------------------------------------------------- #
# page
# --------------------------------------------------------------------------- #
PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{PAGE_TITLE}}</title>
<style>
{{CSS}}
</style>
</head>
<body>
<header class="topbar">
  <div class="brand">
    <span class="brand-mark">拼</span>
    <div>
      <h1 id="word-title">拼音笔顺拼图</h1>
      <p id="card-name">{{CARD_LIST}}</p>
    </div>
  </div>
  <div class="stats">
    <div class="stat"><span class="stat-label" id="level-label">关卡</span><span class="stat-value" id="level-index">1</span><span class="stat-total" id="level-total">/ 1</span></div>
    <div class="stat"><span class="stat-label">得分</span><span class="stat-value" id="score">0</span><span class="stat-total" id="score-total">/ 0</span></div>
    <div class="stat"><span class="stat-label">计时</span><span class="stat-value" id="timer">00:00</span></div>
    <button class="ghost-btn" id="hint-btn" type="button">提示</button>
    <button class="ghost-btn" id="sound-btn" type="button" aria-pressed="true">声音开</button>
  </div>
</header>

<nav class="level-bar">
  <label class="level-pick" for="level-select">跳到关卡</label>
  <select id="level-select"></select>
  <span class="card-timer" id="card-timer">本卡用时 00:00</span>
  <span class="level-tip">共 {{TOTAL}} 关 · 每拼对一块 +2 分 · 笔画要按笔顺写</span>
  <button class="review-btn" id="review-btn" type="button" disabled>错题重练 <b id="review-count">0</b></button>
  <button class="review-clear" id="review-clear" type="button" hidden>清空</button>
</nav>

<main class="board">
  <section class="panel panel-letters">
    <h2><span class="step">1</span>拼音口袋</h2>
    <p class="panel-note">拖字母到中间的方框里，声调放进圆圈</p>
    <div class="letter-pool" id="letter-pool"></div>
    <div class="tone-pool" id="tone-pool"></div>
  </section>

  <section class="panel panel-stage">
    <h2><span class="step">2</span>拼一拼</h2>
    <p class="panel-note">方框里自己拼拼音，再把笔画按顺序写进田字格</p>
    <div class="word" id="word"></div>
    <div class="demo-strip">
      <button class="demo-btn big" id="demo-btn" type="button"><span class="play">▶</span>看演示</button>
      <span class="demo-tip">小老师先拼一遍：<b>先拼拼音</b>，<b>再按笔顺写</b>，看完换你来</span>
    </div>
    <div class="stage-foot">
      <span>1 · 先拼拼音</span>
      <span>2 · 再按笔顺写笔画</span>
      <span>错号笔画拖进去会被退回</span>
    </div>
  </section>

  <section class="panel panel-strokes">
    <h2><span class="step">3</span>笔画口袋</h2>
    <p class="panel-note" id="stroke-note">按笔顺拖进田字格</p>
    <div class="stroke-pool" id="stroke-pool"></div>
    <p class="panel-foot">口袋里有几笔不是这个字的，拖错了要退回哦</p>
  </section>
</main>

<div class="toast" id="toast" aria-live="polite"></div>

<div class="overlay" id="overlay" hidden>
  <div class="overlay-card">
    <h3 id="overlay-title">这一关拼好啦！</h3>
    <p id="overlay-word"></p>
    <div class="overlay-stats">
      <div><span id="final-score">0</span><small>本关得分</small></div>
      <div><span id="final-time">00:00</span><small>本关用时</small></div>
      <div><span id="final-acc">100%</span><small>一次就对</small></div>
      <div><span id="final-card">00:00</span><small>本卡用时</small></div>
      <div><span id="total-score">0</span><small>累计</small></div>
    </div>
    <div class="overlay-actions">
      <button class="ghost-btn" id="replay" type="button">再玩一次</button>
      <button class="primary-btn" id="next-level" type="button">下一关</button>
    </div>
  </div>
</div>

<script>window.GAME_DATA = {{DATA}};</script>
<script>
{{JS}}
</script>
</body>
</html>
"""


def render(data: dict, css: str, js: str) -> str:
    cards = list(dict.fromkeys(level["card"] for level in data["levels"]))
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    replacements = {
        "{{PAGE_TITLE}}": f"拼音笔顺拼图 · {len(data['levels'])} 关",
        "{{CARD_LIST}}": "、".join(cards),
        "{{TOTAL}}": str(len(data["levels"])),
        "{{CSS}}": css,
        "{{JS}}": js,
        "{{DATA}}": payload,
    }
    page = PAGE
    for token, value in replacements.items():
        page = page.replace(token, value)
    return page


def default_name(level: dict) -> str:
    prefix = level["card"].replace(" ", "") or "拼图"
    return f"{prefix}-{level['hanzi']}.html"


def write_page(out_path: Path, data: dict) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        render(
            data,
            (HERE / "game.css").read_text(encoding="utf-8"),
            (HERE / "game.js").read_text(encoding="utf-8"),
        ),
        encoding="utf-8",
    )


def single_word_data(cards: List[Tuple[int, Path, dict]], word: str, decoys: int) -> dict:
    for _day, path, payload in cards:
        levels = build_levels(payload, card_label(payload, path))
        picked = [lv for lv in levels if lv["hanzi"] == word]
        if not picked:
            continue
        strokes, failed = load_strokes(picked, allow_network=True)
        if failed:
            raise SystemExit(f"缺少 “{'、'.join(failed)}” 的笔顺数据")
        data = {
            "levels": picked,
            "strokes": strokes,
            "decoys": decoy_pool(picked, strokes),
            "decoysPerChar": decoys,
            "poolLetters": sorted({letter for lv in picked for spec in lv["chars"] for letter in spec["syllable"]["letters"]} | set(string.ascii_lowercase)),
        }
        return data
    available = "、".join(lv["hanzi"] for _d, p, payload in cards for lv in build_levels(payload, card_label(payload, p)))
    raise SystemExit(f"词语 “{word}” 不在任何卡片里。可选：{available}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把卡片里的词语做成拼音 + 笔顺拼图页")
    parser.add_argument("--cards", default=str(DEFAULT_CARDS_DIR), help="解析结果所在目录，会扫描 */parsed.json")
    parser.add_argument("--card", help="只做标题里含该字样的卡片，例如 第3天")
    parser.add_argument("--word", help="只做一个词语，输出单独的页面（调试用）")
    parser.add_argument("-o", "--out", help="输出 html 路径")
    parser.add_argument("--decoys", type=int, default=3, help="每个字额外放几个错误笔画（默认 3）")
    parser.add_argument("--offline", action="store_true", help="只用本地笔顺缓存")
    args = parser.parse_args(argv)

    cards = load_cards(Path(args.cards))
    if args.card:
        cards = [c for c in cards if args.card in c[2].get("title", "") or args.card in c[1].parent.name]
    if not cards:
        raise SystemExit(f"在 {args.cards} 下没找到任何 */parsed.json")

    if args.word:
        data = single_word_data(cards, args.word, args.decoys)
        out_path = Path(args.out) if args.out else DEFAULT_OUT.parent / default_name(data["levels"][0])
    else:
        data, failed = build_data(cards, not args.offline, args.decoys)
        if failed:
            print(f"skip {len(set(failed))} 个字（无笔顺数据）：{''.join(sorted(set(failed)))}")
        out_path = Path(args.out) if args.out else DEFAULT_OUT

    write_page(out_path, data)

    total = sum(pieces_of(level, data["strokes"]) for level in data["levels"])
    cards = list(dict.fromkeys(level["card"] for level in data["levels"]))
    print(f"wrote {out_path}  ({out_path.stat().st_size // 1024} KB)")
    print(f"  cards : {len(cards)}（{'、'.join(cards)}）")
    print(f"  levels: {len(data['levels'])}")
    print(f"  pieces: {total}  (满分 {total * 2} 分)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
