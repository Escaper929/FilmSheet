# -*- coding: utf-8 -*-
"""Tests for FilmSheet core rendering logic.

Tests pure functions and rendering pipelines without requiring
GUI, network, or actual image files.
"""

import io
import math
import os
import sys
import tempfile
import unittest
from unittest import mock

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image, ImageDraw

from processor.edge_text import generate_edge_text, BRAND_MAP, TYPE_MAP, CINEMA_KEYWORDS
from processor.filename_utils import generate_output_filename
from processor.image_pipeline import (
    process_135_image,
    process_120_image,
    cover_resize_crop,
    _crop_to_135_ratio,
)
from processor.config_schema import (
    validate_config,
    sanitize_config,
    FIELD_DEFS,
    SUB_FORMATS_135,
    SUB_FORMATS_120,
    RENDER_STYLES,
    FILM_FORMATS,
)
from utils.helpers import STYLE_COLORS, FILM_FORMAT_RATIOS, LABEL_MAP, INFO_LAYOUT, NO_COLON_FIELDS


# ====================================================================
# Edge text tests
# ====================================================================

class TestEdgeText(unittest.TestCase):
    """Test pure edge text generation."""

    def test_koda_portra_mapping(self):
        result = generate_edge_text("柯达 Portra 400", "")
        self.assertEqual(result["brand"], "KODAK")
        self.assertEqual(result["film_type"], "Portra 400")
        self.assertFalse(result["custom"])

    def test_fujifilm_superia_mapping(self):
        result = generate_edge_text("富士 Superia 400", "")
        self.assertEqual(result["brand"], "FUJIFILM")
        self.assertEqual(result["film_type"], "Superia 400")

    def test_cinema_vision3(self):
        result = generate_edge_text("柯达 Vision3 500T", "")
        self.assertEqual(result["brand"], "EASTMAN")
        # Brand prefix should be stripped
        self.assertNotIn("Vision3", result["brand"])

    def test_cinema_5207(self):
        result = generate_edge_text("5207", "")
        self.assertEqual(result["brand"], "EASTMAN")

    def test_custom_edge_text(self):
        result = generate_edge_text("", "My Custom Text")
        self.assertTrue(result["custom"])
        self.assertEqual(result["brand"], "My Custom Text")
        self.assertEqual(result["film_type"], "")

    def test_empty_input(self):
        result = generate_edge_text("", "")
        self.assertEqual(result["brand"], "")
        self.assertFalse(result["custom"])

    def test_ilford_black_white(self):
        result = generate_edge_text("伊尔福 Tri-X 400", "")
        self.assertEqual(result["brand"], "ILFORD")
        self.assertEqual(result["film_type"], "Tri-X 400")

    def test_consistent_lot_code(self):
        """Lot code should be deterministic for same brand+film."""
        r1 = generate_edge_text("柯达 Portra 400", "")
        r2 = generate_edge_text("柯达 Portra 400", "")
        self.assertEqual(r1["lot_code"], r2["lot_code"])


# ====================================================================
# Filename utils tests
# ====================================================================

class TestFilenameUtils(unittest.TestCase):

    def test_default_generates_from_info(self):
        result = generate_output_filename(
            "filmsheet_output.jpg",
            info_roll="卷1",
            info_camera="RZ67",
            info_film="Portra 400",
            info_shoot_date="2024-01-15",
        )
        self.assertIn("卷1", result)
        self.assertIn("RZ67", result)
        self.assertIn("Portra 400", result)

    def test_custom_filename_unchanged(self):
        result = generate_output_filename("my_custom_output.jpg")
        self.assertEqual(result, "my_custom_output.jpg")

    def test_special_chars_sanitized(self):
        result = generate_output_filename(
            "filmsheet_output.jpg",
            info_roll="卷/1",
            info_film="Portra 400",
        )
        # The slash in "卷/1" should be sanitized to underscore
        self.assertNotIn("卷/", result)

    def test_no_info_returns_original(self):
        result = generate_output_filename("filmsheet_output.jpg")
        self.assertEqual(result, "filmsheet_output.jpg")


# ====================================================================
# Image pipeline tests
# ====================================================================

class TestImagePipeline(unittest.TestCase):

    def _make_test_image(self, width, height, color=(255, 0, 0)):
        """Create a small solid-color test image."""
        img = Image.new("RGB", (width, height), color)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return buf

    def test_cover_resize_crop_square(self):
        img = Image.new("RGB", (100, 200), (255, 0, 0))
        result = cover_resize_crop(img, 100, 100)
        self.assertEqual(result.size, (100, 100))

    def test_cover_resize_crop_landscape(self):
        img = Image.new("RGB", (200, 100), (0, 255, 0))
        result = cover_resize_crop(img, 100, 50)
        self.assertEqual(result.size, (100, 50))

    def test_cover_resize_crop_preserves_aspect(self):
        """Cover mode should fill target, possibly cropping edges."""
        img = Image.new("RGB", (100, 100), (0, 0, 255))
        result = cover_resize_crop(img, 200, 100)
        self.assertEqual(result.size, (200, 100))

    def test_crop_to_135_ratio_wide(self):
        """Wide image should be cropped to 3:2."""
        img = Image.new("RGB", (300, 200), (255, 255, 0))
        result = _crop_to_135_ratio(img)
        # 300x200 → target ratio 1.5 → 300/200=1.5, already correct
        self.assertEqual(result.size, (300, 200))

    def test_crop_to_135_ratio_tall(self):
        """Tall image should be cropped to 3:2."""
        img = Image.new("RGB", (200, 300), (0, 255, 255))
        result = _crop_to_135_ratio(img)
        w, h = result.size
        self.assertAlmostEqual(w / h, 1.5, places=1)

    def test_process_135_image_positive(self):
        buf = self._make_test_image(360, 240)
        result = process_135_image(io.BytesIO(buf.getvalue()), 360, "positive", True)
        self.assertIsNotNone(result)
        self.assertEqual(result.size, (360, 240))

    def test_process_135_image_negative(self):
        buf = self._make_test_image(360, 240, color=(0, 0, 0))
        result = process_135_image(io.BytesIO(buf.getvalue()), 360, "negative", True)
        self.assertIsNotNone(result)
        # Inverted black should be white
        pixel = result.getpixel((0, 0))
        self.assertEqual(pixel, (255, 255, 255))

    def test_process_135_rotate_portrait(self):
        """Portrait image should be rotated when force_landscape=True."""
        buf = self._make_test_image(240, 360, color=(255, 128, 0))
        result = process_135_image(io.BytesIO(buf.getvalue()), 360, "positive", True)
        self.assertIsNotNone(result)
        w, h = result.size
        self.assertGreaterEqual(w, h)

    def test_process_120_image_66(self):
        buf = self._make_test_image(560, 560)
        result = process_120_image(io.BytesIO(buf.getvalue()), 1.0, 560, "positive", True)
        self.assertIsNotNone(result)
        self.assertEqual(result.size, (560, 560))

    def test_process_120_image_645(self):
        """645 ratio is 1.25:1."""
        buf = self._make_test_image(500, 400)
        result = process_120_image(io.BytesIO(buf.getvalue()), 1.25, 500, "positive", True)
        self.assertIsNotNone(result)


# ====================================================================
# Config schema tests
# ====================================================================

class TestConfigSchema(unittest.TestCase):

    def test_validate_valid_config(self):
        config = {
            "film_format": "135",
            "sub_format": "标准 36×24",
            "thumb_width": 400,
            "columns": 6,
            "render_style": "lightbox",
            "processing_mode": "positive",
            "output_format": "JPG",
            "quality": 95,
        }
        valid, errors = validate_config(config)
        self.assertTrue(valid)
        self.assertEqual(errors, [])

    def test_validate_invalid_thumb_width(self):
        # thumb_width range has been removed; no validation error expected
        config = {"thumb_width": 100}
        valid, errors = validate_config(config)
        self.assertTrue(valid)

    def test_validate_invalid_render_style(self):
        config = {"render_style": "invalid_style"}
        valid, errors = validate_config(config)
        self.assertFalse(valid)

    def test_sanitize_applies_defaults(self):
        config = {"film_format": "135"}
        result = sanitize_config(config)
        self.assertEqual(result["thumb_width"], 400)
        self.assertEqual(result["columns"], 6)
        self.assertEqual(result["render_style"], "lightbox")

    def test_sub_formats_135(self):
        self.assertEqual(SUB_FORMATS_135, ["标准 36×24", "半格 18×24", "方形 24×24", "XPan 65×24"])

    def test_sub_formats_120(self):
        self.assertEqual(SUB_FORMATS_120, ["645", "66", "67", "68", "69", "612", "617"])

    def test_film_formats(self):
        self.assertEqual(FILM_FORMATS, ["135", "120"])

    def test_render_styles(self):
        self.assertEqual(RENDER_STYLES, ["lightbox", "contact_sheet"])


# ====================================================================
# Constants tests
# ====================================================================

class TestConstants(unittest.TestCase):

    def test_style_colors_exist(self):
        self.assertIn("lightbox", STYLE_COLORS)
        self.assertIn("contact_sheet", STYLE_COLORS)

    def test_film_format_ratios(self):
        self.assertEqual(FILM_FORMAT_RATIOS["135"], 1.5)
        self.assertEqual(FILM_FORMAT_RATIOS["66"], 1.0)
        self.assertEqual(FILM_FORMAT_RATIOS["645"], 1.25)

    def test_label_map_complete(self):
        expected_keys = {"roll", "camera", "film", "shoot_date", "dev_date", "proc", "lab", "scanner"}
        self.assertEqual(set(LABEL_MAP.keys()), expected_keys)

    def test_info_layout_structure(self):
        self.assertEqual(len(INFO_LAYOUT), 3)
        for row in INFO_LAYOUT:
            for key in row:
                if key is not None:
                    self.assertIn(key, LABEL_MAP)

    def test_no_colon_fields(self):
        self.assertIn("roll", NO_COLON_FIELDS)


# ====================================================================
# Film engine tests
# ====================================================================

class TestFilmEngine(unittest.TestCase):

    def test_mm_to_px(self):
        from engine.film_engine import Strict135FilmEngine
        eng = Strict135FilmEngine(dpi=300)
        # 1 inch = 25.4mm, at 300dpi → 300px
        px = eng.mm_to_px(25.4)
        self.assertEqual(px, 300)

    def test_determine_perf_ks(self):
        from engine.film_engine import Strict135FilmEngine
        eng = Strict135FilmEngine()
        # Portra is not cinema → KS
        self.assertEqual(eng.determine_perf_type("Portra 400", "Auto"), "KS")

    def test_determine_perf_bh(self):
        from engine.film_engine import Strict135FilmEngine
        eng = Strict135FilmEngine()
        # Vision3 is cinema → BH
        self.assertEqual(eng.determine_perf_type("Vision3 500T", "Auto"), "BH")

    def test_determine_perf_manual_ks(self):
        from engine.film_engine import Strict135FilmEngine
        eng = Strict135FilmEngine()
        self.assertEqual(eng.determine_perf_type("", "KS (民用)"), "KS")

    def test_determine_perf_manual_bh(self):
        from engine.film_engine import Strict135FilmEngine
        eng = Strict135FilmEngine()
        self.assertEqual(eng.determine_perf_type("", "BH (电影)"), "BH")

    def test_perf_pitch_ks(self):
        from engine.film_engine import Strict135FilmEngine
        eng = Strict135FilmEngine()
        self.assertAlmostEqual(eng.get_perf_pitch("KS"), 4.750, places=3)

    def test_perf_pitch_bh(self):
        from engine.film_engine import Strict135FilmEngine
        eng = Strict135FilmEngine()
        self.assertAlmostEqual(eng.get_perf_pitch("BH"), 4.740, places=3)


# ====================================================================
# Integration: render layout consistency
# ====================================================================

class TestLayoutConsistency(unittest.TestCase):
    """Test that layout calculations are internally consistent."""

    def test_135_standard_ratio(self):
        """36x24 → 1.5 ratio matches FILM_FORMAT_RATIOS['135']."""
        self.assertAlmostEqual(36.0 / 24.0, FILM_FORMAT_RATIOS["135"], places=3)

    def test_120_66_is_square(self):
        """6x6 → 1.0 ratio."""
        self.assertAlmostEqual(FILM_FORMAT_RATIOS["66"], 1.0, places=3)

    def test_info_layout_has_three_rows(self):
        self.assertEqual(len(INFO_LAYOUT), 3)

    def test_all_info_keys_in_label_map(self):
        for row in INFO_LAYOUT:
            for key in row:
                if key is not None:
                    self.assertIn(key, LABEL_MAP, f"{key} not in LABEL_MAP")


# ====================================================================
# Run
# ====================================================================

if __name__ == "__main__":
    unittest.main(verbosity=2)
