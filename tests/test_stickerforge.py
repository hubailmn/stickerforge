import io
import unittest
import zipfile

from PIL import Image, ImageDraw

from stickerforge import config
from stickerforge.converter import convert
from stickerforge.pack import build_archive, build_cover, sticker_filename


def make_gif(frame_count=10, size=(240, 180), animated=True):
    frames = []
    for index in range(frame_count):
        image = Image.new("RGBA", size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        span = max(size[0] - 60, 1)
        x = int(span * index / max(frame_count - 1, 1))
        draw.rectangle([x, 50, x + 60, size[1] - 40], fill=(255, 0, 0, 255))
        frames.append(image)
    buffer = io.BytesIO()
    if animated and frame_count > 1:
        frames[0].save(
            buffer,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=80,
            loop=0,
            disposal=2,
        )
    else:
        frames[0].save(buffer, format="PNG")
    return buffer.getvalue()


class ConverterTests(unittest.TestCase):
    def test_animated_gif_becomes_animated_sticker(self):
        result = convert(make_gif())
        self.assertEqual((result.width, result.height), (512, 512))
        self.assertTrue(result.animated)
        self.assertGreaterEqual(result.frames, 2)
        self.assertLessEqual(result.size, config.ANIMATED_MAX_BYTES)
        self.assertTrue(result.fits)

    def test_static_image_becomes_static_sticker(self):
        result = convert(make_gif(frame_count=1, animated=False))
        self.assertEqual((result.width, result.height), (512, 512))
        self.assertFalse(result.animated)
        self.assertLessEqual(result.size, config.STATIC_MAX_BYTES)
        self.assertTrue(result.fits)

    def test_animation_can_be_disabled(self):
        result = convert(make_gif(), keep_animation=False)
        self.assertFalse(result.animated)
        self.assertLessEqual(result.size, config.STATIC_MAX_BYTES)

    def test_frame_cap_adds_warning(self):
        result = convert(make_gif(frame_count=40), max_frames=10)
        self.assertLessEqual(result.frames, 10)
        self.assertTrue(any("frames reduced" in w for w in result.warnings))

    def test_corrupt_input_raises(self):
        with self.assertRaises(ValueError):
            convert(b"not an image")


class PackTests(unittest.TestCase):
    def test_cover_is_square_png(self):
        cover = build_cover(make_gif(frame_count=1, animated=False), size=96)
        image = Image.open(io.BytesIO(cover))
        self.assertEqual(image.size, (96, 96))
        self.assertEqual(image.format, "PNG")

    def test_archive_contains_expected_members(self):
        sticker = convert(make_gif(frame_count=4))
        archive = build_archive(
            "My Pack",
            "Tester",
            [("001.webp", sticker.data)],
            build_cover(sticker.data),
        )
        with zipfile.ZipFile(io.BytesIO(archive)) as opened:
            names = set(opened.namelist())
            self.assertIn("author.txt", names)
            self.assertIn("title.txt", names)
            self.assertIn("cover.png", names)
            self.assertIn("001.webp", names)
            self.assertEqual(opened.read("title.txt"), b"My Pack")
            self.assertEqual(opened.read("author.txt"), b"Tester")

    def test_filename_is_zero_padded(self):
        self.assertEqual(sticker_filename(7), "007.webp")
        self.assertLess(sticker_filename(2), sticker_filename(10))


if __name__ == "__main__":
    unittest.main()
