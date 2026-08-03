import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from image_utils import image_mime_type, image_type  # noqa: E402


class ImageTypeTests(unittest.TestCase):
    def test_detects_supported_signatures(self):
        samples = {
            "jpeg": b"\xff\xd8\xff\xe0" + b"x" * 28,
            "png": b"\x89PNG\r\n\x1a\n" + b"x" * 24,
            "gif": b"GIF89a" + b"x" * 26,
            "bmp": b"BM" + b"x" * 30,
            "tiff": b"II*\x00" + b"x" * 28,
            "webp": b"RIFF" + b"x" * 4 + b"WEBP" + b"x" * 20,
        }
        with tempfile.TemporaryDirectory() as directory:
            for expected, content in samples.items():
                path = os.path.join(directory, expected)
                with open(path, "wb") as stream:
                    stream.write(content)
                self.assertEqual(image_type(path), expected)

    def test_mime_type_uses_signature_not_extension(self):
        with tempfile.NamedTemporaryFile(suffix=".png") as stream:
            stream.write(b"\xff\xd8\xff\xe0" + b"x" * 28)
            stream.flush()
            self.assertEqual(image_mime_type(stream.name), "image/jpeg")

    def test_rejects_unknown_file(self):
        with tempfile.NamedTemporaryFile() as stream:
            stream.write(b"not an image")
            stream.flush()
            self.assertIsNone(image_type(stream.name))


if __name__ == "__main__":
    unittest.main()
