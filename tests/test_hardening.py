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
