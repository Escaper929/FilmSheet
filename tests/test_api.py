"""Smoke tests for the FastAPI rendering endpoint."""

import io
import unittest

from fastapi.testclient import TestClient
from PIL import Image

from api.main import app


class TestRenderAPI(unittest.TestCase):

    def setUp(self):
        # The app enforces a per-client rate limit (20 requests / 60s by default),
        # which this module's request count would otherwise trip. Clear the
        # in-process ledger before every test so each one starts fresh.
        self._reset_rate_limit()
        self.client = TestClient(app)
        image = Image.new("RGB", (360, 240), (128, 64, 32))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        self.image_data = buffer.getvalue()

    def tearDown(self):
        self._reset_rate_limit()

    @staticmethod
    def _reset_rate_limit():
        import api.main as main_module
        main_module._request_times.clear()

    def _image_files(self):
        return [("images", ("frame.png", self.image_data, "image/png"))]

    def test_render_120_returns_an_image(self):
        response = self.client.post(
            "/render",
            files=self._image_files(),
            data={"film_format": "120", "sub_format": "67", "thumb_width": "300", "columns": "1"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "image/jpeg")

    def test_render_135_returns_an_image(self):
        response = self.client.post(
            "/render",
            files=self._image_files(),
            data={"film_format": "135", "sub_format": "标准 36×24", "thumb_width": "300", "columns": "1"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "image/jpeg")

    def test_blank_sub_format_falls_back_to_format_default(self):
        """Omitting sub_format must work for both formats.

        The legal sub-formats differ per format, so an empty value has to resolve
        against film_format — not against a single hardcoded default.
        """
        for film_format in ("135", "120"):
            with self.subTest(film_format=film_format):
                response = self.client.post(
                    "/render",
                    files=self._image_files(),
                    data={"film_format": film_format, "sub_format": "", "thumb_width": "300", "columns": "1"},
                )

                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.headers["content-type"], "image/jpeg")

    def test_rejects_unknown_120_sub_format(self):
        """An unknown 120 sub-format must be rejected, not silently rendered.

        Without the cross-field check the value falls through to a default ratio
        inside image_pipeline and returns a wrong-aspect sheet with HTTP 200.
        """
        response = self.client.post(
            "/render",
            files=self._image_files(),
            data={"film_format": "120", "sub_format": "not-a-format", "thumb_width": "300"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("sub_format", response.text)

    def test_rejects_135_sub_format_on_120(self):
        """A 135 sub-format name is meaningless for 120 and must not pass."""
        response = self.client.post(
            "/render",
            files=self._image_files(),
            data={"film_format": "120", "sub_format": "标准 36×24", "thumb_width": "300"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("sub_format", response.text)

    def test_rejects_120_sub_format_on_135(self):
        """And the reverse direction."""
        response = self.client.post(
            "/render",
            files=self._image_files(),
            data={"film_format": "135", "sub_format": "645", "thumb_width": "300"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("sub_format", response.text)

    def test_rejects_unknown_film_format(self):
        response = self.client.post(
            "/render",
            files=self._image_files(),
            data={"film_format": "999", "sub_format": "66", "thumb_width": "300"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("film_format", response.text)

    def test_accepts_every_declared_sub_format(self):
        """Every value the frontend offers must render, for both formats."""
        cases = [
            ("135", "标准 36×24"), ("135", "半格 18×24"),
            ("135", "方形 24×24"), ("135", "XPan 65×24"),
            ("120", "645"), ("120", "66"), ("120", "67"), ("120", "68"),
            ("120", "69"), ("120", "612"), ("120", "617"), ("120", "624"),
        ]
        for film_format, sub_format in cases:
            with self.subTest(film_format=film_format, sub_format=sub_format):
                response = self.client.post(
                    "/render",
                    files=self._image_files(),
                    data={
                        "film_format": film_format,
                        "sub_format": sub_format,
                        "thumb_width": "300",
                        "columns": "1",
                    },
                )

                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.headers["content-type"], "image/jpeg")

    def test_120_and_135_produce_different_geometry(self):
        """The 120 branch must be a genuinely different pipeline, not a copy."""
        sizes = {}
        for film_format, sub_format in (("135", "标准 36×24"), ("120", "66")):
            response = self.client.post(
                "/render",
                files=self._image_files(),
                data={
                    "film_format": film_format,
                    "sub_format": sub_format,
                    "thumb_width": "300",
                    "columns": "1",
                    "output_format": "PNG",
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
            sizes[film_format] = Image.open(io.BytesIO(response.content)).size

        self.assertNotEqual(sizes["135"], sizes["120"])

    def test_rejects_invalid_image_upload(self):
        response = self.client.post(
            "/render",
            files=[("images", ("not-an-image.txt", b"not an image", "text/plain"))],
        )

        self.assertEqual(response.status_code, 400)

    def test_rejects_server_pack_image_paths(self):
        response = self.client.post(
            "/render",
            files=self._image_files(),
            data={"pack_image_path": "C:/private/image.png"},
        )

        self.assertEqual(response.status_code, 400)

    def test_pwa_assets_are_served(self):
        for path, media_type in (
            ("/manifest.json", "application/json"),
            ("/sw.js", "application/javascript"),
            ("/icon-192.png", "image/png"),
            ("/icon-512.png", "image/png"),
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(media_type, response.headers["content-type"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
