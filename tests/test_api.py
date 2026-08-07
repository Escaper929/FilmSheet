"""Smoke tests for the FastAPI rendering endpoint."""

import io
import unittest

from fastapi.testclient import TestClient
from PIL import Image

from api.main import app


class TestRenderAPI(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(app)
        image = Image.new("RGB", (360, 240), (128, 64, 32))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        self.image_data = buffer.getvalue()

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
