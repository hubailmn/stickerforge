"""Flask app: analyze sources, build, preview, and download sticker packs."""

from __future__ import annotations

import hashlib
import io
import os
import re
import secrets
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from flask import Flask, abort, jsonify, render_template, request, send_file
from PIL import Image

from . import config, video
from .converter import RESAMPLE, convert, ensure_webp
from .fetcher import FetchError, fetch
from .pack import build_archive, build_cover, plan_pack_sizes, sticker_filename


class ExpiringStore:
    """Bounded in-memory store that expires entries and caps total bytes.

    Entries are kept in insertion (= creation) order, so expiry and eviction
    both work from the front in O(1) per entry.
    """

    def __init__(
        self,
        ttl: int = 3600,
        max_items: int = 256,
        max_bytes: int = config.STORE_MAX_BYTES,
    ):
        self._items: "OrderedDict[str, tuple]" = OrderedDict()  # key -> (created, nbytes, value)
        self._lock = threading.Lock()
        self._ttl = ttl
        self._max_items = max_items
        self._max_bytes = max_bytes
        self._total_bytes = 0

    def _remove(self, key: str) -> None:
        _, nbytes, _ = self._items.pop(key)
        self._total_bytes -= nbytes

    def _evict(self) -> None:
        now = time.time()
        while self._items:
            key, (created, _, _) = next(iter(self._items.items()))
            expired = now - created > self._ttl
            over = len(self._items) > self._max_items or self._total_bytes > self._max_bytes
            if not (expired or over):
                break
            self._remove(key)

    def put(self, key: str, value: dict, nbytes: int = 0) -> None:
        with self._lock:
            if key in self._items:
                self._remove(key)
            self._items[key] = (time.time(), nbytes, value)
            self._total_bytes += nbytes
            self._evict()

    def get(self, key: str):
        with self._lock:
            entry = self._items.get(key)
            if entry is None:
                return None
            if time.time() - entry[0] > self._ttl:
                self._remove(key)
                return None
            return entry[2]


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "pack").lower()).strip("-")
    return slug or "pack"


def _png_on_canvas(frame: Image.Image, size: int) -> bytes:
    scale = min(size / frame.width, size / frame.height)
    width = max(1, round(frame.width * scale))
    height = max(1, round(frame.height * scale))
    frame = frame.resize((width, height), RESAMPLE)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.alpha_composite(frame, ((size - width) // 2, (size - height) // 2))
    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG")
    return buffer.getvalue()


def _image_thumb(data: bytes, size: int = 256) -> Optional[bytes]:
    try:
        image = Image.open(io.BytesIO(data))
        try:
            image.seek(0)
        except Exception:
            pass
        frame = image.convert("RGBA")
    except Exception:
        return None
    return _png_on_canvas(frame, size)


def _serialize(record: dict, warnings=None) -> dict:
    by_sid = {s["sid"]: s for s in record["stickers"]}
    items = []
    for sid in record["order"]:
        sticker = by_sid[sid]
        items.append(
            {
                "sid": sid,
                "preview_url": f"/api/pack/{record['id']}/sticker/{sid}.webp",
                "size": sticker["size"],
                "animated": sticker["animated"],
                "frames": sticker["frames"],
                "width": sticker["width"],
                "height": sticker["height"],
                "max_bytes": sticker["max_bytes"],
                "fits": sticker["fits"],
                "source": sticker["source"],
                "kind": sticker["kind"],
                "warnings": sticker["warnings"],
                "cover": sid == record["cover"],
            }
        )
    return {
        "pack_id": record["id"],
        "title": record["title"],
        "author": record["author"],
        "count": len(items),
        "max_stickers": config.MAX_STICKERS,
        "total_bytes": sum(s["size"] for s in record["stickers"]),
        "animated": bool(record.get("animated")),
        "cover_url": f"/api/pack/{record['id']}/cover.png",
        "download_url": f"/api/pack/{record['id']}/download",
        "stickers": items,
        "warnings": warnings or [],
    }


def _pack_bytes(record: dict) -> int:
    return sum(len(s["data"]) for s in record["stickers"])


def _cover_png(record: dict) -> bytes:
    """Pack cover, rendered once per cover choice and cached on the record."""
    by_sid = {s["sid"]: s for s in record["stickers"]}
    source = by_sid.get(record["cover"]) or record["stickers"][0]
    cached = record.get("_cover")
    if cached and cached[0] == source["sid"]:
        return cached[1]
    png = build_cover(source["data"])
    record["_cover"] = (source["sid"], png)
    return png


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["MAX_CONTENT_LENGTH"] = config.MAX_DOWNLOAD_BYTES * config.MAX_STICKERS
    packs = ExpiringStore()
    sources = ExpiringStore()
    # Conversion is CPU-bound (WebP encoding). A shared, bounded pool keeps
    # concurrent builds from oversubscribing the CPU or memory.
    convert_pool = ThreadPoolExecutor(max_workers=config.BUILD_WORKERS, thread_name_prefix="convert")
    # Fetching and probing are mostly I/O, so they get their own pool.
    io_pool = ThreadPoolExecutor(max_workers=config.FETCH_WORKERS, thread_name_prefix="fetch")

    def analyze_bytes(data: bytes, label: str) -> dict:
        image = None
        try:
            image = Image.open(io.BytesIO(data))
            image.load()
        except Exception:
            image = None

        if image is not None:
            frames = getattr(image, "n_frames", 1) or 1
            animated = bool(getattr(image, "is_animated", False)) and frames > 1
            thumb = _image_thumb(data)
            return {
                "kind": "image",
                "animated": animated,
                "frames": frames,
                "duration": round(frames * 0.08, 2) if animated else 0.0,
                "fps": 0.0,
                "width": image.width,
                "height": image.height,
                "thumb": thumb,
                "label": label,
            }

        path = video.write_temp(data)
        try:
            meta = video.probe(path)
            thumb = video.thumbnail(path, at=min(meta["duration"] / 2, 1.0))
        except video.VideoError as exc:
            raise FetchError(f"{label}: not a supported image or video ({exc})") from exc
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        return {
            "kind": "video",
            "animated": True,
            "frames": 0,
            "duration": round(meta["duration"], 2),
            "fps": round(meta["fps"], 2),
            "width": meta["width"],
            "height": meta["height"],
            "thumb": thumb,
            "label": label,
        }

    def load_input(kind: str, item) -> bytes:
        """Read one input. URLs are web input, so local paths are never allowed."""
        if kind == "url":
            return fetch(item, allow_local=False)
        return item.read(config.MAX_DOWNLOAD_BYTES + 1)

    def analyze_input(kind: str, item):
        label = item if kind == "url" else item.filename
        try:
            data = load_input(kind, item)
            if len(data) > config.MAX_DOWNLOAD_BYTES:
                raise FetchError("upload exceeds size limit")
            return label, data, analyze_bytes(data, label), None
        except (FetchError, ValueError) as exc:
            return label, None, None, str(exc)

    def gather_inputs():
        raw = [p.strip() for p in re.split(r"[\s,]+", request.form.get("urls", "")) if p.strip()]
        urls = list(dict.fromkeys(raw))  # same link twice counts once, order kept
        uploads = [f for f in request.files.getlist("files") if f and f.filename]
        return [("url", u) for u in urls] + [("upload", f) for f in uploads]

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.post("/api/analyze")
    def api_analyze():
        inputs = gather_inputs()
        if not inputs:
            return jsonify(error="Add at least one image, GIF, or video URL/file."), 400
        warnings = []
        if len(inputs) > config.MAX_SOURCES:
            warnings.append(
                f"Only the first {config.MAX_SOURCES} sources were kept "
                f"({len(inputs) - config.MAX_SOURCES} ignored)."
            )
            inputs = inputs[: config.MAX_SOURCES]

        results = []
        # map() keeps input order while the downloads overlap.
        for label, data, info, error in io_pool.map(lambda pair: analyze_input(*pair), inputs):
            if error is not None:
                warnings.append(error)
                continue
            sid = secrets.token_hex(8)
            thumb = info.pop("thumb", None)
            sources.put(
                sid,
                {"data": data, "thumb": thumb, "info": info},
                nbytes=len(data) + len(thumb or b""),
            )
            results.append({"sid": sid, **info, "thumb_url": f"/api/source/{sid}/thumb.png"})
        if not results:
            return jsonify(error="Nothing could be read.", warnings=warnings), 400
        return jsonify({"sources": results, "warnings": warnings, "max_stickers": config.MAX_STICKERS})

    @app.get("/api/source/<sid>/thumb.png")
    def api_source_thumb(sid):
        record = sources.get(sid)
        if not record or not record.get("thumb"):
            abort(404)
        response = send_file(io.BytesIO(record["thumb"]), mimetype="image/png")
        response.headers["Cache-Control"] = "no-store"
        return response

    def convert_job(job):
        try:
            return convert(
                job["data"],
                padding=job["padding"],
                max_frames=job["max_frames"],
                keep_animation=job["keep_animation"],
                trim_start=job.get("trim_start"),
                trim_end=job.get("trim_end"),
                fps=job.get("fps"),
            ), None
        except (FetchError, ValueError) as exc:
            return None, str(exc)

    def build_packs(title, author, jobs, warnings):
        """Convert jobs and split them into WhatsApp-sized packs.

        Identical inputs are converted once. Animated and static stickers never
        share a pack (WhatsApp rejects mixed packs), and each pack holds at most
        MAX_STICKERS stickers.
        """
        seen_content = {}
        unique_jobs = []
        for job in jobs:
            digest = hashlib.sha256(job["data"]).hexdigest()
            if digest in seen_content:
                warnings.append(f"{job['label']}: duplicate of {seen_content[digest]}, skipped")
                continue
            seen_content[digest] = job["label"]
            unique_jobs.append(job)

        stickers = []
        # pool.map preserves job order, so packs keep the requested order.
        for job, (result, error) in zip(unique_jobs, convert_pool.map(convert_job, unique_jobs)):
            if error is not None:
                warnings.append(f"{job['label']}: {error}")
                continue
            stickers.append(
                {
                    "sid": secrets.token_hex(8),
                    "data": result.data,
                    "size": result.size,
                    "animated": result.animated,
                    "frames": result.frames,
                    "width": result.width,
                    "height": result.height,
                    "max_bytes": result.max_bytes,
                    "fits": result.fits,
                    "source": job["label"],
                    "kind": result.source_kind,
                    "warnings": result.warnings,
                }
            )
            for warning in result.warnings:
                warnings.append(f"{job['label']}: {warning}")
        if not stickers:
            return []

        groups = []
        for animated in (True, False):
            members = [s for s in stickers if s["animated"] is animated]
            cursor = 0
            for size in plan_pack_sizes(len(members)):
                groups.append((animated, members[cursor : cursor + size]))
                cursor += size

        records = []
        for index, (animated, members) in enumerate(groups, start=1):
            if len(members) < config.MIN_STICKERS:
                warnings.append(
                    f"A {'animated' if animated else 'static'} pack has only {len(members)} "
                    f"sticker(s); WhatsApp needs at least {config.MIN_STICKERS} per pack."
                )
            pack_title = title if len(groups) == 1 else f"{title} ({index}/{len(groups)})"
            record = {
                "id": secrets.token_hex(8),
                "title": pack_title,
                "author": author,
                "stickers": members,
                "order": [s["sid"] for s in members],
                "cover": members[0]["sid"],
                "animated": animated,
            }
            packs.put(record["id"], record, nbytes=_pack_bytes(record))
            records.append(record)
        return records

    def as_int(value, default):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @app.post("/api/build")
    def api_build():
        if request.is_json:
            payload = request.get_json(silent=True) or {}
            title = (payload.get("title") or "My Pack").strip()[:64] or "My Pack"
            author = (payload.get("author") or "").strip()[:64]
            padding = as_int(payload.get("padding"), config.DEFAULT_PADDING)
            max_frames = as_int(payload.get("max_frames"), config.MAX_FRAMES)
            default_anim = bool(payload.get("keep_animation", True))
            warnings = []
            jobs = []
            for entry in payload.get("sources", []):
                record = sources.get(entry.get("sid", ""))
                if not record:
                    warnings.append(f"{entry.get('label', 'source')}: expired, re-analyze")
                    continue
                info = record["info"]
                jobs.append(
                    {
                        "data": record["data"],
                        "label": info["label"],
                        "padding": padding,
                        "max_frames": max_frames,
                        "keep_animation": bool(entry.get("keep_animation", default_anim)),
                        "trim_start": entry.get("trim_start"),
                        "trim_end": entry.get("trim_end"),
                        "fps": entry.get("fps"),
                    }
                )
        else:
            title = (request.form.get("title") or "My Pack").strip()[:64] or "My Pack"
            author = (request.form.get("author") or "").strip()[:64]
            padding = as_int(request.form.get("padding"), config.DEFAULT_PADDING)
            max_frames = as_int(request.form.get("max_frames"), config.MAX_FRAMES)
            keep_animation = request.form.get("keep_animation", "1").lower() not in ("0", "false", "")
            warnings = []
            jobs = []
            inputs = gather_inputs()
            if len(inputs) > config.MAX_SOURCES:
                warnings.append(f"Only the first {config.MAX_SOURCES} sources were used.")
                inputs = inputs[: config.MAX_SOURCES]
            for kind, item in inputs:
                label = item if kind == "url" else item.filename
                try:
                    data = load_input(kind, item)
                except FetchError as exc:
                    warnings.append(f"{label}: {exc}")
                    continue
                jobs.append(
                    {
                        "data": data,
                        "label": label,
                        "padding": padding,
                        "max_frames": max_frames,
                        "keep_animation": keep_animation,
                    }
                )

        if not jobs:
            return jsonify(error="Add at least one image, GIF, or video.", warnings=warnings), 400
        records = build_packs(title, author, jobs, warnings)
        if not records:
            return jsonify(error="No stickers could be created.", warnings=warnings), 400
        return jsonify(
            {
                "packs": [_serialize(record) for record in records],
                "total_stickers": sum(len(r["stickers"]) for r in records),
                "max_per_pack": config.MAX_STICKERS,
                "warnings": warnings,
            }
        )

    @app.get("/api/pack/<pack_id>/sticker/<sid>.webp")
    def api_sticker(pack_id, sid):
        record = packs.get(pack_id)
        if not record:
            abort(404)
        for sticker in record["stickers"]:
            if sticker["sid"] == sid:
                response = send_file(io.BytesIO(sticker["data"]), mimetype="image/webp")
                response.headers["Cache-Control"] = "no-store"
                return response
        abort(404)

    @app.get("/api/pack/<pack_id>/cover.png")
    def api_cover(pack_id):
        record = packs.get(pack_id)
        if not record:
            abort(404)
        response = send_file(io.BytesIO(_cover_png(record)), mimetype="image/png")
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/api/pack/<pack_id>/order")
    def api_order(pack_id):
        record = packs.get(pack_id)
        if not record:
            abort(404)
        payload = request.get_json(silent=True) or {}
        known = {s["sid"] for s in record["stickers"]}
        order = [sid for sid in payload.get("order", []) if sid in known]
        seen = set(order)
        order.extend(s["sid"] for s in record["stickers"] if s["sid"] not in seen)
        record["order"] = order
        cover = payload.get("cover")
        if cover in known:
            record["cover"] = cover
        return jsonify(_serialize(record))

    @app.get("/api/pack/<pack_id>/download")
    def api_download(pack_id):
        record = packs.get(pack_id)
        if not record:
            abort(404)
        by_sid = {s["sid"]: s for s in record["stickers"]}
        ordered = [by_sid[sid] for sid in record["order"] if sid in by_sid]
        sticker_files = [
            (sticker_filename(i), sticker["data"]) for i, sticker in enumerate(ordered)
        ]
        archive = build_archive(
            record["title"],
            record["author"],
            sticker_files,
            _cover_png(record),
        )
        return send_file(
            io.BytesIO(archive),
            mimetype="application/zip",
            as_attachment=True,
            download_name=f"{_slugify(record['title'])}.wastickers",
        )

    @app.get("/api/health")
    def api_health():
        return jsonify(ffmpeg=video.ffmpeg_available(), webp=True)

    @app.errorhandler(413)
    def too_large(_):
        return jsonify(error="Upload too large."), 413

    return app


app = create_app()


def main() -> None:
    ensure_webp()
    host = os.environ.get("STICKERFORGE_HOST", "127.0.0.1")
    port = int(os.environ.get("STICKERFORGE_PORT", "5000"))
    debug = os.environ.get("STICKERFORGE_DEBUG", "0") == "1"
    print(f"StickerForge running at http://{host}:{port}")
    print(f"ffmpeg: {'available' if video.ffmpeg_available() else 'NOT FOUND (video disabled)'}")
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    main()
