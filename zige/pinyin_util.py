"""Pinyin generation.

OCR is good at hanzi but lossy on tone marks ("huà huà" comes back as
"hud hud"). We therefore derive the pinyin from the hanzi with pypinyin (which
knows phrase context, so 贺卡 -> hè kǎ, 可乐 -> kě lè) and use the OCR text only
as a *hint* for heteronyms: a reading is swapped in only when the OCR syllable
matches one of that character's real readings once tone marks are ignored.
"""

from __future__ import annotations

import re
import unicodedata
from typing import List, Sequence

try:  # pragma: no cover - import guard for a clearer error message
    from pypinyin import Style, pinyin as _pinyin
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "pypinyin is required. Install it with: pip install -r requirements.txt"
    ) from exc

_TONE_MARKS = {
    "ā": "a", "á": "a", "ǎ": "a", "à": "a",
    "ē": "e", "é": "e", "ě": "e", "è": "e",
    "ī": "i", "í": "i", "ǐ": "i", "ì": "i",
    "ō": "o", "ó": "o", "ǒ": "o", "ò": "o",
    "ū": "u", "ú": "u", "ǔ": "u", "ù": "u",
    "ǖ": "v", "ǘ": "v", "ǚ": "v", "ǜ": "v",
    "ü": "v", "ń": "n", "ň": "n", "ǹ": "n", "ḿ": "m",
}


def _normalise(syllable: str) -> str:
    """Reduce a syllable to its tone-less, comparison-friendly skeleton."""
    text = unicodedata.normalize("NFC", syllable).lower()
    text = "".join(_TONE_MARKS.get(ch, ch) for ch in text)
    text = text.replace("u:", "v").replace("ü", "v")
    return re.sub(r"[^a-zv]", "", text)


def _readings(char: str) -> List[str]:
    """All dictionary readings of a single character, tone marks included."""
    try:
        groups = _pinyin(char, style=Style.TONE, heteronym=True, errors="default")
    except Exception:
        return []
    return [r for group in groups for r in group]


def syllables_for(word: str) -> List[str]:
    """Tone-marked syllables for a word, using phrase context for heteronyms."""
    if not word:
        return []
    try:
        groups = _pinyin(word, style=Style.TONE, heteronym=False, errors="default")
    except Exception:
        return list(word)
    return [group[0] if group else "" for group in groups]


def analyse(word: str, ocr_pinyin: str = "") -> tuple[List[str], List[str]]:
    """Return (syllables, warnings) for a word.

    OCR pinyin is used to pick between heteronym readings when it is
    unambiguous; all other disagreements are reported as warnings so the user
    can review the generated card.
    """
    syllables = syllables_for(word)
    warnings: List[str] = []
    if not syllables:
        return [], [f"无法为 “{word}” 生成拼音"]

    candidates = [_readings(ch) for ch in word]
    tokens = [t for t in re.split(r"[\s']+", ocr_pinyin.strip()) if t]

    if len(tokens) == len(word):
        for idx, token in enumerate(tokens):
            skeleton = _normalise(token)
            if not skeleton:
                continue
            valid = {_normalise(r) for r in candidates[idx]}
            current = _normalise(syllables[idx])
            if skeleton in valid and skeleton != current:
                # OCR saw a real reading that differs from the phrase guess.
                for reading in candidates[idx]:
                    if _normalise(reading) == skeleton:
                        warnings.append(
                            f"“{word}”第{idx + 1}字按卡片采用 {reading}（词典默认 {syllables[idx]}）"
                        )
                        syllables[idx] = reading
                        break
            elif skeleton not in valid and skeleton != current:
                warnings.append(
                    f"“{word}”拼音 OCR 为 “{token}”，与字典结果 “{syllables[idx]}” 不一致，已采用字典"
                )
    elif tokens and len(tokens) != len(word):
        warnings.append(
            f"“{word}”拼音格子数({len(tokens)})与字数({len(word)})不一致，已按字数重新生成"
        )

    return syllables, warnings


def display(syllables: Sequence[str]) -> str:
    """Render syllables as the space separated form used on cards."""
    return " ".join(syllables)
