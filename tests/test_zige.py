"""Unit tests for the zige pipeline.

Run with:  .venv/bin/python -m unittest discover -s tests -v

The OCR side is mocked with a synthetic card so the tests need neither an image
nor the Swift helper. Rendering tests skip themselves when the stroke cache is
cold, because fetching stroke data would require network access.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from zige import config, export, layout, pipeline, pinyin_util, strokes  # noqa: E402
from zige.draw import PageRenderer, RenderOptions  # noqa: E402
from zige.ocr import TextLine  # noqa: E402


def line(text: str, x0: float, y0: float, x1: float, y1: float) -> TextLine:
    return TextLine(text=text, x0=x0, y0=y0, x1=x1, y1=y1)


def card_lines() -> list:
    """A miniature card with the same structure as a real 拼读小卡片."""
    return [
        line("每日拼读练习小卡片（第3天）", 100, 30, 700, 70),
        line("3.g k h", 60, 92, 160, 112),
        line("huà huà", 100, 140, 230, 170),
        line("dǎ gǔ", 420, 140, 520, 170),
        line("画画", 100, 200, 230, 270),
        line("打鼓", 420, 200, 520, 270),
        line("hú li", 100, 340, 210, 370),
        line("kě lè", 420, 340, 520, 370),
        line("狐狸", 100, 400, 230, 470),
        line("可乐", 420, 400, 520, 470),
        line("我读了（ ）遍，最快用时（ ）分（ ）秒。", 100, 700, 700, 730),
    ]


class LayoutTests(unittest.TestCase):
    def test_pairs_each_hanzi_cell_with_the_pinyin_above_it(self):
        words = layout.parse_card(card_lines())
        self.assertEqual([w.hanzi for w in words], ["画画", "打鼓", "狐狸", "可乐"])
        self.assertEqual(
            [w.pinyin_ocr for w in words],
            ["huà huà", "dǎ gǔ", "hú li", "kě lè"],
        )

    def test_words_are_ordered_top_to_bottom_then_left_to_right(self):
        words = layout.parse_card(card_lines())
        keys = [(w.row, w.x) for w in words]
        self.assertEqual(keys, sorted(keys))

    def test_title_and_footer_are_not_treated_as_vocabulary(self):
        words = layout.parse_card(card_lines())
        joined = "".join(w.hanzi for w in words)
        self.assertNotIn("每日", joined)
        self.assertNotIn("我读了", joined)
        self.assertNotIn("卡片", joined)

    def test_classify(self):
        self.assertEqual(layout.classify("画画"), "hanzi")
        self.assertEqual(layout.classify("huà huà"), "pinyin")
        self.assertEqual(layout.classify("hā mì guā"), "pinyin")
        # Labels and prose must be ignored.
        self.assertIsNone(layout.classify("3.g k h"))
        self.assertIsNone(layout.classify("我读了（ ）遍"))
        self.assertIsNone(layout.classify("g k h"))

    def test_cells_are_paired_by_horizontal_centre_not_by_document_order(self):
        lines = [
            line("mó gu", 400, 100, 500, 130),
            line("gū gu", 100, 100, 200, 130),
            line("蘑菇", 395, 160, 505, 230),
            line("姑姑", 100, 160, 200, 230),
        ]
        words = layout.parse_card(lines)
        mapping = {w.hanzi: w.pinyin_ocr for w in words}
        self.assertEqual(mapping["蘑菇"], "mó gu")
        self.assertEqual(mapping["姑姑"], "gū gu")

    def test_unmatched_hanzi_cell_still_becomes_a_word(self):
        words = layout.parse_card([line("狐狸", 100, 200, 230, 270)])
        self.assertEqual([w.hanzi for w in words], ["狐狸"])
        self.assertEqual(words[0].pinyin_ocr, "")


class PinyinTests(unittest.TestCase):
    def test_phrase_context_fixes_heteronyms(self):
        self.assertEqual(pinyin_util.analyse("贺卡", "")[0], ["hè", "kǎ"])
        self.assertEqual(pinyin_util.analyse("可乐", "")[0], ["kě", "lè"])
        self.assertEqual(pinyin_util.analyse("花朵", "")[0], ["huā", "duǒ"])
        self.assertEqual(pinyin_util.analyse("刻苦", "")[0], ["kè", "kǔ"])
        self.assertEqual(pinyin_util.analyse("哈密瓜", "")[0], ["hā", "mì", "guā"])

    def test_matching_ocr_text_produces_no_warning(self):
        syllables, warnings = pinyin_util.analyse("贺卡", "hè kǎ")
        self.assertEqual(syllables, ["hè", "kǎ"])
        self.assertEqual(warnings, [])

    def test_ocr_reading_wins_when_it_is_a_real_reading(self):
        # The card is the ground truth; 乐 does read yuè, so the hint is taken
        # and the change is reported for review.
        syllables, warnings = pinyin_util.analyse("可乐", "kě yuè")
        self.assertEqual(syllables, ["kě", "yuè"])
        self.assertTrue(warnings)
        self.assertIn("按卡片采用", warnings[0])

    def test_garbled_tone_marks_fall_back_to_the_dictionary(self):
        # Vision returns "hud hud" for "huà huà".
        syllables, warnings = pinyin_util.analyse("画画", "hud hud")
        self.assertEqual(syllables, ["huà", "huà"])
        self.assertTrue(warnings)
        self.assertIn("不一致", warnings[0])

    def test_syllable_count_mismatch_is_reported(self):
        syllables, warnings = pinyin_util.analyse("哈密瓜", "ha mi")
        self.assertEqual(len(syllables), 3)
        self.assertTrue(any("不一致" in w for w in warnings))

    def test_normalise_ignores_tones_and_umlauts(self):
        self.assertEqual(pinyin_util._normalise("lǜ"), "lv")
        self.assertEqual(pinyin_util._normalise("huà"), "hua")


def _cached(char: str) -> bool:
    """True when stroke data for `char` is already on disk."""
    return strokes._cache_path(char).exists()


class StrokeTests(unittest.TestCase):
    def test_flatten_path_builds_a_closed_polygon(self):
        polygons = strokes.flatten_path("M0,0 L10,0 L10,10 Z")
        self.assertEqual(len(polygons), 1)
        self.assertEqual(len(polygons[0]), 4)  # Z appends the start point
        self.assertEqual(polygons[0][-1], polygons[0][0])

    def test_relative_commands_match_absolute_ones(self):
        absolute = strokes.flatten_path("M0,0 L10,0 L10,10 Z")
        relative = strokes.flatten_path("M0,0 l10,0 l0,10 z")
        self.assertEqual(absolute, relative)

    def test_curves_are_subdivided_into_points(self):
        polygons = strokes.flatten_path("M0,0 Q10,0 10,10 Z", curve_steps=4)
        # start + 4 curve samples + closing point
        self.assertEqual(len(polygons[0]), 6)

    def test_transform_maps_the_design_centre_to_the_cell_centre(self):
        transform = strokes.Transform(x=100, y=200, size=100)
        centre = transform.map(
            (config.GLYPH_CENTER_X, config.Y_FLIP - config.GLYPH_CENTER_Y)
        )
        self.assertAlmostEqual(centre[0], 150.0)
        self.assertAlmostEqual(centre[1], 250.0)

    def test_design_canvas_is_flipped_upright(self):
        transform = strokes.Transform(x=0, y=0, size=100)
        top = transform.map((config.GLYPH_CENTER_X, config.Y_FLIP))
        bottom = transform.map((config.GLYPH_CENTER_X, 0.0))
        self.assertLess(top[1], bottom[1])

    def test_glyph_occupies_the_requested_fraction_of_the_cell(self):
        transform = strokes.Transform(x=0, y=0, size=200, fill=1.0)
        self.assertAlmostEqual(transform.scale * config.GLYPH_REFERENCE_EXTENT, 200.0)

    def test_uncached_character_fails_cleanly_when_offline(self):
        with self.assertRaises(strokes.StrokeError):
            strokes.get("龘", allow_network=False)


@unittest.skipUnless(_cached("画"), "stroke cache is cold; run the CLI once first")
class StrokeDataTests(unittest.TestCase):
    def test_strokes_and_medians_line_up(self):
        data = strokes.get("画", allow_network=False)
        self.assertEqual(data.count, 8)
        self.assertEqual(len(data.medians), data.count)

    def test_every_stroke_flattens_to_polygons(self):
        data = strokes.get("画", allow_network=False)
        for polygons in strokes.stroke_polygons(data):
            self.assertGreaterEqual(len(polygons), 1)
            self.assertGreaterEqual(len(polygons[0]), 3)

    def test_stroke_numbers_are_one_based_and_inside_the_cell(self):
        data = strokes.get("画", allow_network=False)
        numbers = strokes.stroke_numbers(data, strokes.Transform(x=0, y=0, size=200))
        self.assertEqual([n for n, _, _ in numbers], list(range(1, len(data.medians) + 1)))
        for _, (px, py), _ in numbers:
            self.assertTrue(-30 <= px <= 230, f"x={px}")
            self.assertTrue(-30 <= py <= 230, f"y={py}")


@unittest.skipUnless(_cached("画"), "stroke cache is cold; run the CLI once first")
class RenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.renderer = PageRenderer(
            {"画": strokes.get("画", allow_network=False)},
            RenderOptions(cell=80, scale=1),
        )

    def test_word_page_geometry(self):
        image = self.renderer.render_word("画", ["huà"])
        self.assertIsInstance(image, Image.Image)
        opt = self.renderer.options
        expected_w = round(
            opt.max_cols * opt.cell
            + (opt.max_cols - 1) * opt.cell * 0.13
            + 2 * opt.cell * 0.46
        )
        self.assertEqual(image.width, expected_w)
        self.assertTrue(300 < image.height < 1600, image.height)

    def test_repeated_character_gets_a_single_stroke_block(self):
        single = self.renderer.render_word("画", ["huà"])
        double = self.renderer.render_word("画画", ["huà", "huà"])
        self.assertEqual((single.width, single.height), (double.width, double.height))

    def test_missing_stroke_data_degrades_to_a_hint(self):
        renderer = PageRenderer({}, RenderOptions(cell=80, scale=1))
        image = renderer.render_word("鑫", ["xīn"])
        self.assertGreater(image.height, 0)

    def test_character_page_stacks_three_sections(self):
        image = self.renderer.render_char("画", "huà")
        self.assertGreater(image.height, image.width)

    def test_cover_page_lists_words_of_different_lengths(self):
        image = self.renderer.render_cover(
            [("画画", "huà huà"), ("哈密瓜", "hā mì guā")], "每日拼读练习小卡片"
        )
        self.assertGreater(image.width, 0)
        self.assertGreater(image.height, 0)


class ExportTests(unittest.TestCase):
    def test_json_round_trip(self):
        payload = {
            "title": "每日拼读练习小卡片（第3天）",
            "words": [{"hanzi": "画画", "pinyin": "huà huà"}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "parsed.json"
            export.save_json(payload, path)
            self.assertEqual(export.load_json(path), payload)

    def test_html_references_pages_relatively(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "chars").mkdir()
            Image.new("RGB", (8, 8), "white").save(root / "01_画画.png")
            Image.new("RGB", (8, 8), "white").save(root / "chars" / "画.png")
            pages = [
                export.Page(path=root / "01_画画.png", title="huà huà  画画", kind="word"),
                export.Page(path=root / "chars" / "画.png", title="画  huà", kind="char"),
            ]
            index = export.save_html(pages, root / "index.html", title="卡片", subtitle="1 个词语")
            text = index.read_text(encoding="utf-8")
            self.assertIn('src="01_画画.png"', text)
            self.assertIn('src="chars/画.png"', text)
            self.assertNotIn(tmp, text)
            self.assertEqual(text.count("<img"), 2)

    def test_pdf_is_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pages = []
            for index in range(3):
                page = root / f"{index}.png"
                Image.new("RGB", (60, 80), "white").save(page)
                pages.append(page)
            pdf = export.save_pdf(pages, root / "card.pdf")
            self.assertTrue(pdf.read_bytes().startswith(b"%PDF"))

    def test_safe_name_strips_punctuation(self):
        self.assertEqual(export.safe_name("每日拼读练习小卡片（第3天）"), "每日拼读练习小卡片第3天")
        self.assertEqual(export.safe_name("（ ）"), "page")


@unittest.skipUnless(
    all(_cached(ch) for ch in "画打鼓"), "stroke cache is cold; run the CLI once first"
)
class PipelineTests(unittest.TestCase):
    def test_build_writes_cover_words_chars_and_json(self):
        words = [layout.Word(hanzi="画画"), layout.Word(hanzi="打鼓")]
        meta = pipeline.CardMeta(title="测试卡片", subtitle="3.g k h")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            options = pipeline.BuildOptions(
                out_dir=out, cell=80, scale=1, make_pdf=False, allow_network=False
            )
            pipeline.resolve_pinyin(words)
            result = pipeline.build(words, meta, options, log=lambda msg: None)

            names = sorted(p.name for p in out.glob("*.png"))
            self.assertEqual(names, ["00_cover.png", "01_画画.png", "02_打鼓.png"])
            self.assertEqual(len(result.pages), 1 + 2 + 3)  # cover, words, 画/打/鼓
            self.assertTrue((out / "parsed.json").exists())
            self.assertTrue((out / "index.html").exists())
            self.assertEqual(result.warnings, [])
            self.assertEqual(result.missing, {})

    def test_pdf_lands_in_the_shared_library_folder(self):
        words = [layout.Word(hanzi="画画")]
        meta = pipeline.CardMeta(title="测试卡片")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "卡片"
            pipeline.resolve_pinyin(words)
            result = pipeline.build(
                words,
                meta,
                pipeline.BuildOptions(out_dir=out, cell=80, scale=1, allow_network=False),
                log=lambda msg: None,
            )
            library = Path(tmp) / config.PDF_LIBRARY_NAME
            self.assertEqual(result.pdf_path, library / "测试卡片.pdf")
            self.assertTrue(result.pdf_path.read_bytes().startswith(b"%PDF"))
            self.assertEqual(list(out.glob("*.pdf")), [])
