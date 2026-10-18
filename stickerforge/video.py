"""Video handling (mp4/mov/webm) built on a bundled ffmpeg binary."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from typing import List, Optional

from PIL import Image

from . import config

_fmpeg_cache: Optional[str] = None


class VideoError(Exception):
    """Raised when a video cannot be probed or decoded."""


def ffmpeg_exe() -> str:
    """Locate ffmpeg: env override, then PATH, then the bundled imageio-ffmpeg."""
    global _fmpeg_cache
    if _fmpeg_cache:
        return _fmpeg_cache
    if config.FFMPEG_PATH and os.path.exists(config.FFMPEG_PATH):
        _fmpeg_cache = config.FFMPEG_PATH
        return _fmpeg_cache
    system = shutil.which("ffmpeg")
    if system:
        _fmpeg_cache = system
        return _fmpeg_cache
    try:
        import imageio_ffmpeg

        _fmpeg_cache = imageio_ffmpeg.get_ffmpeg_exe()
        return _fmpeg_cache
    except Exception as exc:  # pragma: no cover - environment dependent
        raise VideoError(
            "ffmpeg not found. Install it with `pip install imageio-ffmpeg` "
            "or `brew install ffmpeg`."
        ) from exc


def ffmpeg_available() -> bool:
    try:
        ffmpeg_exe()
        return True
    except VideoError:
        return False


def write_temp(data: bytes, suffix: str = ".mp4") -> str:
    handle = tempfile.NamedTemporaryFile(prefix="sf-src-", suffix=suffix, delete=False)
    try:
        handle.write(data)
    finally:
        handle.close()
    return handle.name


def _probe_with_ffmpeg(path: str):
    proc = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-i", path],
        capture_output=True,
        text=True,
    )
    text = proc.stderr or ""
    duration = 0.0
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if match:
        duration = (
            int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))
        )
    fps = config.DEFAULT_TARGET_FPS
    match = re.search(r"(\d+(?:\.\d+)?)\s*fps", text)
    if match:
        try:
            fps = float(match.group(1))
        except ValueError:
            pass
    width = height = 0
    match = re.search(r"(\d{2,5})x(\d{2,5})", text)
    if match:
        width, height = int(match.group(1)), int(match.group(2))
    return duration, fps, width, height


def probe(path: str) -> dict:
    """Return width, height, fps and duration (seconds) for a video file."""
    # One ffmpeg process reports everything we need (ffmpeg_exe() already falls
    # back to the bundled imageio-ffmpeg binary).
    duration, fps, width, height = _probe_with_ffmpeg(path)

    if not width or not height:
        raise VideoError("could not read video metadata")
    try:
        duration = float(duration or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    try:
        fps = float(fps or config.DEFAULT_TARGET_FPS)
    except (TypeError, ValueError):
        fps = float(config.DEFAULT_TARGET_FPS)
    return {"width": int(width), "height": int(height), "fps": fps, "duration": duration}


def extract_frames(
    path: str,
    start: float,
    end: float,
    fps: float,
    max_frames: int,
    size: int = config.STICKER_SIZE,
) -> List[Image.Image]:
    """Decode [start, end) to a list of RGBA PIL frames scaled to fit `size`."""
    outdir = tempfile.mkdtemp(prefix="sf-frames-")
    try:
        span = max(end - start, 0.05)
        command = [
            ffmpeg_exe(),
            "-v",
            "error",
            "-y",
            "-ss",
            f"{max(start, 0):.3f}",
            "-i",
            path,
            "-t",
            f"{span:.3f}",
            "-vf",
            f"fps={max(fps, 1):.4f},scale={size}:{size}:force_original_aspect_ratio=decrease",
            "-frames:v",
            str(max(int(max_frames), 1)),
            os.path.join(outdir, "f_%05d.png"),
        ]
        proc = subprocess.run(command, capture_output=True, text=True)
        if proc.returncode != 0:
            raise VideoError(f"ffmpeg failed: {(proc.stderr or '').strip()[:300]}")
        frames: List[Image.Image] = []
        for name in sorted(os.listdir(outdir)):
            with Image.open(os.path.join(outdir, name)) as image:
                frames.append(image.convert("RGBA").copy())
        return frames
    finally:
        shutil.rmtree(outdir, ignore_errors=True)


def thumbnail(path: str, at: float = 0.0, size: int = 256) -> Optional[bytes]:
    outdir = tempfile.mkdtemp(prefix="sf-thumb-")
    try:
        output = os.path.join(outdir, "thumb.png")
        command = [
            ffmpeg_exe(),
            "-v",
            "error",
            "-y",
            "-ss",
            f"{max(at, 0):.3f}",
            "-i",
            path,
            "-frames:v",
            "1",
            "-vf",
            f"scale={size}:{size}:force_original_aspect_ratio=decrease",
            output,
        ]
        proc = subprocess.run(command, capture_output=True, text=True)
        if proc.returncode != 0 or not os.path.exists(output):
            return None
        with open(output, "rb") as handle:
            return handle.read()
    finally:
        shutil.rmtree(outdir, ignore_errors=True)
