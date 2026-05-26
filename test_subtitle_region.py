import os
import tempfile
import unittest

from PIL import Image, ImageDraw

from src.subtitle_region import SubtitleRegionDetector


class TestSubtitleRegionDetector(unittest.TestCase):
    def test_detects_lower_caption_band(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "frame.jpg")
            image = Image.new("RGB", (640, 360), color=(20, 20, 28))
            draw = ImageDraw.Draw(image)

            # Simulate large white subtitle strokes with dark outline.
            for y in range(265, 305, 10):
                draw.rectangle((150, y, 490, y + 5), fill=(245, 245, 245))
            image.save(path)

            region = SubtitleRegionDetector().detect_frame(path)

            self.assertIsNotNone(region)
            left, top, right, bottom = region.box
            self.assertEqual(left, 0)
            self.assertEqual(right, 640)
            self.assertLessEqual(top, 285)
            self.assertGreaterEqual(bottom, 305)
            self.assertGreater(bottom - top, 30)


if __name__ == "__main__":
    unittest.main()
