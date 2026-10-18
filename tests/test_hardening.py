import unittest
from unittest import mock


from stickerforge import fetcher
from stickerforge.fetcher import FetchError, fetch
from stickerforge.web import ExpiringStore, app


class FakeResponse:
    def __init__(self, status=200, location=None, body=b"x"):
        self.status_code = status
        self.headers = {"Location": location} if location else {}
        self._body = body

    @property
    def is_redirect(self):
        return self.status_code in (301, 302, 303, 307, 308) and "Location" in self.headers

    def iter_content(self, _size):
        yield self._body

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.urls = []

    def get(self, url, **_kwargs):
        self.urls.append(url)
        return self._responses.pop(0)


class FetchGuardTests(unittest.TestCase):
    def test_redirect_into_private_address_is_blocked(self):
        # 93.184.216.34 is a public literal, so the first hop passes the guard.
        session = FakeSession([FakeResponse(302, location="http://127.0.0.1/secret")])
        with mock.patch.object(fetcher, "_session", return_value=session):
            with self.assertRaises(FetchError):
                fetch("http://93.184.216.34/start.gif")
        self.assertEqual(session.urls, ["http://93.184.216.34/start.gif"])

    def test_redirect_loop_is_capped(self):
        responses = [FakeResponse(302, location="http://93.184.216.34/again")] * (
            fetcher.MAX_REDIRECTS + 2
        )
        session = FakeSession(responses)
        with mock.patch.object(fetcher, "_session", return_value=session):
            with self.assertRaisesRegex(FetchError, "too many redirects"):
                fetch("http://93.184.216.34/loop")

    def test_public_redirect_is_followed(self):
        session = FakeSession(
            [
                FakeResponse(301, location="http://93.184.216.34/final.gif"),
                FakeResponse(200, body=b"gifdata"),
            ]
        )
        with mock.patch.object(fetcher, "_session", return_value=session):
            self.assertEqual(fetch("http://93.184.216.34/start.gif"), b"gifdata")

    def test_local_paths_rejected_when_not_allowed(self):
        with self.assertRaises(FetchError):
            fetch("file:///etc/hostname", allow_local=False)
        with self.assertRaises(FetchError):
            fetch("/etc/hostname", allow_local=False)

    def test_web_analyze_refuses_local_file_urls(self):
        response = app.test_client().post(
            "/api/analyze",
            data={"urls": "file:///etc/hostname"},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"only http(s) URLs are accepted", response.data)


class ExpiringStoreTests(unittest.TestCase):
    def test_byte_cap_evicts_oldest(self):
        store = ExpiringStore(max_items=100, max_bytes=100)
        store.put("a", {"v": 1}, nbytes=60)
        store.put("b", {"v": 2}, nbytes=60)
        self.assertIsNone(store.get("a"))
        self.assertEqual(store.get("b"), {"v": 2})

    def test_item_cap_evicts_oldest(self):
        store = ExpiringStore(max_items=2, max_bytes=10**9)
        for key in ("a", "b", "c"):
            store.put(key, {"k": key})
        self.assertIsNone(store.get("a"))
        self.assertIsNotNone(store.get("c"))

    def test_expired_entries_are_dropped_on_get(self):
        store = ExpiringStore(ttl=0)
        store.put("a", {"v": 1}, nbytes=5)
        self.assertIsNone(store.get("a"))
        self.assertEqual(store._total_bytes, 0)

    def test_reput_updates_byte_accounting(self):
        store = ExpiringStore(max_bytes=10**9)
        store.put("a", {"v": 1}, nbytes=50)
        store.put("a", {"v": 2}, nbytes=20)
        self.assertEqual(store._total_bytes, 20)


if __name__ == "__main__":
    unittest.main()


class PackPlanTests(unittest.TestCase):
    def test_sizes_respect_whatsapp_limits(self):
        from stickerforge.pack import plan_pack_sizes

        self.assertEqual(plan_pack_sizes(0), [])
        self.assertEqual(plan_pack_sizes(1), [1])
        self.assertEqual(plan_pack_sizes(30), [30])
        self.assertEqual(plan_pack_sizes(31), [16, 15])
        self.assertEqual(plan_pack_sizes(61), [21, 20, 20])
        for count in range(1, 200):
            sizes = plan_pack_sizes(count)
            self.assertEqual(sum(sizes), count)
            self.assertTrue(all(size <= 30 for size in sizes))
            if len(sizes) > 1:
                self.assertGreaterEqual(min(sizes), 15)


def _unique_gif(index, frames=2):
    import io

    from PIL import Image, ImageDraw

    images = []
    for frame in range(frames):
        image = Image.new("RGBA", (120, 120), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rectangle([index, 10 + frame * 5, index + 40, 60], fill=(index * 7 % 256, 90, 200, 255))
        images.append(image)
    buffer = io.BytesIO()
    images[0].save(buffer, format="GIF", save_all=True, append_images=images[1:], duration=100, loop=0)
    return buffer.getvalue()


def _unique_png(index):
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGBA", (64, 64), (index * 9 % 256, 30, 30, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


class PackSplitTests(unittest.TestCase):
    def build(self, files, title="Split"):
        import io

        data = {"title": title, "files": [(io.BytesIO(blob), name) for name, blob in files]}
        response = app.test_client().post("/api/build", data=data, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200, response.data)
        return response.get_json()

    def test_31_animated_stickers_split_into_two_packs(self):
        files = [(f"a{i}.gif", _unique_gif(i)) for i in range(31)]
        result = self.build(files)
        self.assertEqual([p["count"] for p in result["packs"]], [16, 15])
        self.assertEqual(result["total_stickers"], 31)
        self.assertEqual([p["title"] for p in result["packs"]], ["Split (1/2)", "Split (2/2)"])
        self.assertTrue(all(p["animated"] for p in result["packs"]))

    def test_static_and_animated_are_never_mixed_in_a_pack(self):
        # Two statics next to two animations: the statics join the animated pack.
        files = [
            ("s1.png", _unique_png(1)),
            ("s2.png", _unique_png(2)),
            ("a1.gif", _unique_gif(1)),
            ("a2.gif", _unique_gif(2)),
        ]
        result = self.build(files)
        self.assertEqual([(p["animated"], p["count"]) for p in result["packs"]], [(True, 4)])

    def test_mixed_packs_only_when_both_kinds_are_big_enough(self):
        files = [(f"a{i}.gif", _unique_gif(i)) for i in range(3)] + [
            (f"s{i}.png", _unique_png(i)) for i in range(3)
        ]
        result = self.build(files)
        self.assertEqual(sorted((p["animated"], p["count"]) for p in result["packs"]), [(False, 3), (True, 3)])

    def test_identical_content_is_converted_once(self):
        blob = _unique_gif(5)
        result = self.build([("one.gif", blob), ("two.gif", blob)])
        self.assertEqual(result["total_stickers"], 1)
        self.assertTrue(any("duplicate" in w for w in result["warnings"]))


def webp_frame_durations(data):
    """Read per-frame durations (ms) straight from the WebP container.

    Each ANMF chunk holds a 3-byte duration after 12 bytes of frame position
    and size, so this does not depend on how Pillow exposes them.
    """
    import struct

    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP", "not a WebP file"
    durations = []
    offset = 12
    while offset + 8 <= len(data):
        tag = data[offset : offset + 4]
        size = struct.unpack("<I", data[offset + 4 : offset + 8])[0]
        body = offset + 8
        if tag == b"ANMF":
            durations.append(int.from_bytes(data[body + 12 : body + 15], "little"))
        offset = body + size + (size & 1)
    return durations


class AnimationDurationTests(unittest.TestCase):
    def test_frame_durations_sum_exactly(self):
        from stickerforge.converter import _frame_durations

        for fps in (5, 7, 8, 12, 15, 24, 29.97, 30):
            for count in (1, 10, 70, 90):
                total = sum(_frame_durations(count, fps))
                self.assertEqual(total, round(count * 1000 / fps))

    def test_long_gif_is_trimmed_under_ten_seconds(self):
        import io

        from PIL import Image

        from stickerforge.converter import convert

        frames = [Image.new("RGBA", (120, 120), (i * 3, 80, 200, 255)) for i in range(60)]
        buffer = io.BytesIO()
        # 60 frames x 400 ms = 24 s, well over WhatsApp's 10 s limit.
        frames[0].save(buffer, format="GIF", save_all=True, append_images=frames[1:], duration=400, loop=0)
        result = convert(buffer.getvalue())
        self.assertTrue(result.animated)
        durations = webp_frame_durations(result.data)
        self.assertGreater(len(durations), 1)
        self.assertLessEqual(sum(durations), 10000)
        self.assertTrue(all(d >= 8 for d in durations))
        self.assertTrue(any("trimmed" in w for w in result.warnings))

    def test_video_at_awkward_fps_stays_under_ten_seconds(self):
        import os
        import tempfile

        from stickerforge import video
        from stickerforge.converter import convert

        if not video.ffmpeg_available():
            self.skipTest("ffmpeg unavailable")
        fd, path = tempfile.mkstemp(suffix=".mp4")
        os.close(fd)
        try:
            import subprocess

            subprocess.run(
                [video.ffmpeg_exe(), "-v", "error", "-y", "-f", "lavfi", "-i",
                 "testsrc2=size=160x120:rate=30:duration=10", "-c:v", "libx264",
                 "-pix_fmt", "yuv420p", path],
                check=True,
            )
            with open(path, "rb") as handle:
                data = handle.read()
        finally:
            os.unlink(path)
        result = convert(data, trim_start=0, trim_end=10, fps=7)
        durations = webp_frame_durations(result.data)
        self.assertLessEqual(sum(durations), 10000)


class ResolveKindsTests(unittest.TestCase):
    def test_lone_static_joins_animated_pack(self):
        from stickerforge.pack import resolve_kinds

        flags, changed, notes = resolve_kinds([True] * 30 + [False])
        self.assertTrue(all(flags))
        self.assertEqual(changed, [30])
        self.assertTrue(notes)

    def test_two_statics_join_animated_pack(self):
        from stickerforge.pack import resolve_kinds

        flags, changed, _ = resolve_kinds([True, True, True, False, False])
        self.assertEqual(flags, [True] * 5)
        self.assertEqual(changed, [3, 4])

    def test_lone_animated_flattens_only_when_static_pack_needs_it(self):
        from stickerforge.pack import resolve_kinds

        flags, changed, notes = resolve_kinds([True, False, False, False])
        self.assertEqual(flags, [False] * 4)
        self.assertEqual(changed, [0])
        self.assertTrue(notes)

    def test_valid_mixes_are_left_alone(self):
        from stickerforge.pack import resolve_kinds

        flags, changed, notes = resolve_kinds([True] * 3 + [False] * 3)
        self.assertEqual(flags, [True] * 3 + [False] * 3)
        self.assertEqual(changed, [])
        self.assertEqual(notes, [])


class LoneStaticWebTests(unittest.TestCase):
    def test_lone_png_becomes_animated_in_the_same_pack(self):
        import io

        files = [(f"a{i}.gif", _unique_gif(i)) for i in range(4)] + [("s.png", _unique_png(9))]
        data = {"title": "Mix", "files": [(io.BytesIO(blob), name) for name, blob in files]}
        response = app.test_client().post("/api/build", data=data, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200, response.data)
        result = response.get_json()
        self.assertEqual(len(result["packs"]), 1)
        pack = result["packs"][0]
        self.assertEqual(pack["count"], 5)
        self.assertTrue(pack["animated"])
        self.assertTrue(all(s["animated"] for s in pack["stickers"]))
        self.assertFalse(any("pack has only" in w for w in result["warnings"]))
        promoted = [s for s in pack["stickers"] if s["source"] == "s.png"][0]
        self.assertTrue(promoted["animated"])


class AsAnimatedTests(unittest.TestCase):
    def test_as_animated_has_two_frames_and_valid_duration(self):
        from stickerforge.converter import as_animated, convert

        still = convert(_unique_png(4), keep_animation=False).data
        animated = as_animated(still)
        self.assertEqual(webp_frame_durations(animated), [500, 500])
