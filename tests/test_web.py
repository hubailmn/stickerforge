import io
import unittest
import zipfile

from stickerforge.web import app


def make_gif_bytes(frame_count=6):
    from PIL import Image, ImageDraw

    frames = []
    for index in range(frame_count):
        image = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        x = int(150 * index / max(frame_count - 1, 1))
        draw.ellipse([x, 60, x + 45, 105], fill=(0, 128, 255, 255))
        frames.append(image)
    buffer = io.BytesIO()
    frames[0].save(
        buffer,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=90,
        loop=0,
        disposal=2,
    )
    return buffer.getvalue()


class WebTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_index_renders(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"StickerForge", response.data)

    def test_analyze_then_json_build(self):
        data = {"urls": "", "files": (io.BytesIO(make_gif_bytes(5)), "demo.gif")}
        response = self.client.post(
            "/api/analyze", data=data, content_type="multipart/form-data"
        )
        self.assertEqual(response.status_code, 200, response.data)
        payload = response.get_json()
        self.assertEqual(len(payload["sources"]), 1)
        source = payload["sources"][0]
        self.assertEqual(source["kind"], "image")
        self.assertTrue(source["animated"])

        thumb = self.client.get(source["thumb_url"])
        self.assertEqual(thumb.status_code, 200)
        self.assertEqual(thumb.mimetype, "image/png")

        build = self.client.post(
            "/api/build",
            json={
                "title": "From analyze",
                "author": "Tester",
                "sources": [{"sid": source["sid"], "keep_animation": True}],
            },
        )
        self.assertEqual(build.status_code, 200, build.data)
        result = build.get_json()
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["title"], "From analyze")

    def test_health(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertIn("ffmpeg", response.get_json())

    def test_build_from_upload_preview_and_download(self):
        data = {
            "title": "Demo",
            "author": "Tester",
            "urls": "",
            "padding": "8",
            "max_frames": "30",
            "keep_animation": "1",
            "files": (io.BytesIO(make_gif_bytes()), "demo.gif"),
        }
        response = self.client.post(
            "/api/build", data=data, content_type="multipart/form-data"
        )
        self.assertEqual(response.status_code, 200, response.data)
        payload = response.get_json()
        self.assertEqual(payload["title"], "Demo")
        self.assertEqual(payload["count"], 1)
        sid = payload["stickers"][0]["sid"]

        preview = self.client.get(f"/api/pack/{payload['pack_id']}/sticker/{sid}.webp")
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.mimetype, "image/webp")

        cover = self.client.get(payload["cover_url"])
        self.assertEqual(cover.status_code, 200)
        self.assertEqual(cover.mimetype, "image/png")

        download = self.client.get(payload["download_url"])
        self.assertEqual(download.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(download.data)) as opened:
            self.assertIn("title.txt", opened.namelist())
            self.assertIn("cover.png", opened.namelist())
            self.assertIn("000.webp", opened.namelist())

    def test_build_without_sources_is_rejected(self):
        response = self.client.post("/api/build", data={"urls": "", "title": "x"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.get_json())

    def test_reorder_and_set_cover(self):
        data = {
            "title": "Two",
            "author": "",
            "urls": "",
            "files": [
                (io.BytesIO(make_gif_bytes(4)), "a.gif"),
                (io.BytesIO(make_gif_bytes(5)), "b.gif"),
            ],
        }
        response = self.client.post(
            "/api/build", data=data, content_type="multipart/form-data"
        )
        payload = response.get_json()
        self.assertEqual(payload["count"], 2)
        first, second = payload["stickers"][0]["sid"], payload["stickers"][1]["sid"]

        reordered = self.client.post(
            f"/api/pack/{payload['pack_id']}/order",
            json={"order": [second, first], "cover": second},
        )
        self.assertEqual(reordered.status_code, 200)
        result = reordered.get_json()
        self.assertEqual(result["stickers"][0]["sid"], second)
        self.assertTrue(result["stickers"][0]["cover"])


if __name__ == "__main__":
    unittest.main()
