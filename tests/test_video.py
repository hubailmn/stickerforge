import os
import subprocess
import tempfile
import unittest

from stickerforge import config, video
from stickerforge.converter import convert


def make_clip(seconds=3, fps=15, size="320x240"):
    output = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False).name
    try:
        subprocess.run(
            [
                video.ffmpeg_exe(),
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"testsrc=size={size}:rate={fps}:duration={seconds}",
                "-pix_fmt",
                "yuv420p",
                output,
            ],
            check=True,
        )
        with open(output, "rb") as handle:
            return handle.read()
    finally:
        if os.path.exists(output):
            os.unlink(output)


@unittest.skipUnless(video.ffmpeg_available(), "ffmpeg not available")
class VideoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.clip = make_clip(seconds=3, fps=15)

    def test_probe_reads_metadata(self):
        path = video.write_temp(self.clip)
        try:
            meta = video.probe(path)
        finally:
            os.unlink(path)
        self.assertEqual((meta["width"], meta["height"]), (320, 240))
        self.assertGreater(meta["duration"], 2.5)
        self.assertAlmostEqual(meta["fps"], 15, delta=1.5)

    def test_trim_produces_animation(self):
        result = convert(self.clip, trim_start=0.0, trim_end=1.0, fps=8)
        self.assertTrue(result.animated)
        self.assertEqual(result.source_kind, "video")
        self.assertEqual((result.width, result.height), (512, 512))
        self.assertLessEqual(result.frames, 10)
        self.assertLessEqual(result.size, config.ANIMATED_MAX_BYTES)
        self.assertTrue(result.fits)

    def test_static_sticker_from_video(self):
        result = convert(self.clip, keep_animation=False, trim_start=0.5, trim_end=0.6)
        self.assertFalse(result.animated)
        self.assertEqual(result.source_kind, "video")
        self.assertLessEqual(result.size, config.STATIC_MAX_BYTES)

    def test_long_clip_is_capped_to_ten_seconds(self):
        long_clip = make_clip(seconds=12, fps=10)
        result = convert(long_clip, fps=2, max_frames=6)
        self.assertTrue(any("trimmed" in warning for warning in result.warnings))
        self.assertLessEqual(result.frames, 6)


if __name__ == "__main__":
    unittest.main()
