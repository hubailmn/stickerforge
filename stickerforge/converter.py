"""Convert images, GIFs and videos into WhatsApp-ready WebP stickers."""

from __future__ import annotations

import io
import os
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from PIL import Image, ImageSequence, features

from . import config, video

RESAMPLE = getattr(Image, "Resampling", Image).LANCZOS


@dataclass
class StickerResult:
    """A single converted sticker plus the metadata apps and the UI care about."""

    data: bytes
    width: int
    height: int
    animated: bool
    frames: int
    source_animated: bool
    warnings: List[str] = field(default_factory=list)
    source_kind: str = "image"

    @property
    def size(self) -> int:
        return len(self.data)

    @property
    def max_bytes(self) -> int:
        return config.ANIMATED_MAX_BYTES if self.animated else config.STATIC_MAX_BYTES

    @property
    def fits(self) -> bool:
        return self.size <= self.max_bytes


def webp_supported() -> bool:
    return bool(features.check("webp"))


def ensure_webp() -> None:
    if not webp_supported():
        raise RuntimeError(
            "This Pillow build has no WebP support. Reinstall with: "
            "pip install --force-reinstall --no-binary :all: Pillow"
        )


def _fit(frame: Image.Image, size: int, padding: int, upscale: bool = True) -> Image.Image:
    frame = frame.convert("RGBA")
    inner = max(size - 2 * padding, 1)
    scale = min(inner / frame.width, inner / frame.height)
    if scale < 1 or upscale:
        width = max(1, round(frame.width * scale))
        height = max(1, round(frame.height * scale))
        frame = frame.resize((width, height), RESAMPLE)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.alpha_composite(frame, ((size - frame.width) // 2, (size - frame.height) // 2))
    return canvas


def _limit_frames(
    frames: List[Image.Image], durations: List[int], max_frames: int
) -> Tuple[List[Image.Image], List[int]]:
    if not max_frames or len(frames) <= max_frames:
        return frames, durations
    step = len(frames) / max_frames
    keep = [int(i * step) for i in range(max_frames)]
    new_frames: List[Image.Image] = []
    new_durations: List[int] = []
    last = 0
    for index in keep:
        new_frames.append(frames[index])
        new_durations.append(sum(durations[last : index + 1]))
        last = index + 1
    if last < len(durations):
        new_durations[-1] += sum(durations[last:])
    return new_frames, new_durations


def _read_frames(im: Image.Image, max_frames: int) -> Tuple[List[Image.Image], List[int]]:
    frames: List[Image.Image] = []
    durations: List[int] = []
    try:
        for frame in ImageSequence.Iterator(im):
            frames.append(frame.convert("RGBA"))
            duration = frame.info.get("duration")
            try:
                duration = int(duration)
            except (TypeError, ValueError):
                duration = config.DEFAULT_FRAME_DURATION_MS
            durations.append(max(duration, config.MIN_FRAME_DURATION_MS))
    except Exception:
        frames = []
    if not frames:
        return [im.convert("RGBA")], [config.DEFAULT_FRAME_DURATION_MS]
    return _limit_frames(frames, durations, max_frames)


def _subsample(
    frames: List[Image.Image], durations: List[int], step: int
) -> Tuple[List[Image.Image], List[int]]:
    if step <= 1:
        return frames, durations
    subset = frames[::step]
    merged = [sum(durations[i : i + step]) for i in range(0, len(durations), step)]
    return subset, merged


def _save_webp(frames: List[Image.Image], durations: List[int], quality: int) -> bytes:
    buffer = io.BytesIO()
    if len(frames) == 1:
        frames[0].save(buffer, format="WEBP", quality=quality, method=config.WEBP_METHOD)
    else:
        frames[0].save(
            buffer,
            format="WEBP",
            save_all=True,
            append_images=frames[1:],
            duration=durations,
            loop=0,
            quality=quality,
            method=config.WEBP_METHOD,
            minimize_size=True,
        )
    return buffer.getvalue()


def _encode_ladder(
    frames: List[Image.Image],
    durations: List[int],
    max_bytes: int,
    ladder: Tuple[int, ...] = config.QUALITY_LADDER,
) -> Optional[bytes]:
    """Try a descending quality ladder, returning the first encode that fits.

    This is the fast path: most stickers fit on the first or second try, so we
    usually pay for a single WebP encode instead of a full bisection.
    """
    for quality in ladder:
        data = _save_webp(frames, durations, quality)
        if len(data) <= max_bytes:
            return data
    return None


def _bisect_quality(
    frames: List[Image.Image],
    durations: List[int],
    max_bytes: int,
    lo: int,
    hi: int,
) -> Optional[bytes]:
    """Return the highest-quality WebP that fits, or None if even `lo` is too big."""
    top = _save_webp(frames, durations, hi)
    if len(top) <= max_bytes:
        return top
    best = _save_webp(frames, durations, lo)
    if len(best) > max_bytes:
        return None
    low, high = lo, hi
    for _ in range(config.QUALITY_BISECT_STEPS):
        if high - low <= 2:
            break
        mid = (low + high) // 2
        candidate = _save_webp(frames, durations, mid)
        if len(candidate) <= max_bytes:
            best = candidate
            low = mid + 1
        else:
            high = mid - 1
    return best


def _encode_static(frame: Image.Image, max_bytes: int) -> bytes:
    best = _bisect_quality(
        [frame], [0], max_bytes, config.STATIC_QUALITY_MIN, config.STATIC_QUALITY_MAX
    )
    if best is not None:
        return best
    return _save_webp([frame], [0], config.STATIC_QUALITY_MIN)


def _encode_animated(
    frames: List[Image.Image], durations: List[int], max_bytes: int
) -> Tuple[bytes, int]:
    for step in config.FRAME_STEPS:
        subset, merged = _subsample(frames, durations, step)
        if len(subset) < 2:
            break
        data = _encode_ladder(subset, merged, max_bytes)
        if data is not None:
            return data, len(subset)
    fallback = _save_webp(frames, durations, config.QUALITY_MIN)
    return fallback, len(frames)


def _try_image(data: bytes) -> Optional[Image.Image]:
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
        return image
    except Exception:
        return None


def _finalize_animated(
    fitted: List[Image.Image],
    durations: List[int],
    clips: str,
    warnings: List[str],
    source_kind: str,
) -> StickerResult:
    encoded, count = _encode_animated(fitted, durations, config.ANIMATED_MAX_BYTES)
    if len(encoded) <= config.ANIMATED_MAX_BYTES:
        if count < len(fitted):
            warnings.append(
                f"{clips}frames reduced from {len(fitted)} to {count} to stay under "
                f"{config.ANIMATED_MAX_BYTES // 1024} KB"
            )
        return StickerResult(encoded, 512, 512, True, count, True, warnings, source_kind)
    static = _encode_static(fitted[0], config.STATIC_MAX_BYTES)
    if len(static) <= len(encoded):
        warnings.append("converted to a static sticker to stay within the 100 KB limit")
        return StickerResult(static, 512, 512, False, 1, True, warnings, source_kind)
    warnings.append(
        f"{clips}still exceeds the {config.ANIMATED_MAX_BYTES // 1024} KB animated limit; "
        "some importers may reject it"
    )
    return StickerResult(encoded, 512, 512, True, count, True, warnings, source_kind)


def _convert_image(
    im: Image.Image,
    padding: int,
    max_frames: int,
    keep_animation: bool,
    size: int,
) -> StickerResult:
    warnings: List[str] = []
    source_frames = getattr(im, "n_frames", 1) or 1
    source_animated = bool(getattr(im, "is_animated", False)) and source_frames > 1

    if source_animated and keep_animation:
        raw_frames, durations = _read_frames(im, max_frames)
        if len(raw_frames) < source_frames:
            warnings.append(
                f"frames reduced from {source_frames} to {len(raw_frames)} to bound size"
            )
        fitted = [_fit(frame, size, padding) for frame in raw_frames]
        return _finalize_animated(fitted, durations, "", warnings, "image")

    if source_animated:
        frames, _ = _read_frames(im, 1)
        frame = frames[0]
    else:
        frame = im.convert("RGBA")
    static = _encode_static(_fit(frame, size, padding), config.STATIC_MAX_BYTES)
    if len(static) > config.STATIC_MAX_BYTES:
        warnings.append(f"static sticker exceeds the {config.STATIC_MAX_BYTES // 1024} KB limit")
    if source_animated:
        warnings.append("animation flattened to a single frame")
    return StickerResult(static, size, size, False, 1, source_animated, warnings, "image")


def _convert_video(
    data: bytes,
    padding: int,
    max_frames: int,
    keep_animation: bool,
    size: int,
    trim_start: Optional[float],
    trim_end: Optional[float],
    fps: Optional[float],
) -> StickerResult:
    warnings: List[str] = []
    path = video.write_temp(data)
    try:
        try:
            meta = video.probe(path)
        except video.VideoError as exc:
            raise ValueError(f"unsupported or corrupt media: {exc}") from exc

        duration = meta["duration"]
        start = max(0.0, float(trim_start or 0.0))
        end = float(trim_end) if trim_end is not None else (duration or start + 1.0)
        if duration:
            end = min(end, duration)
        if end <= start:
            end = min(duration, start + config.WHATSAPP_MAX_DURATION_MS / 1000) or start + 1.0

        cap_seconds = config.WHATSAPP_MAX_DURATION_MS / 1000
        if end - start > cap_seconds:
            warnings.append(
                f"clip trimmed to {cap_seconds:.0f}s "
                f"(WhatsApp animated stickers cap at ~{cap_seconds:.0f}s)"
            )
            end = start + cap_seconds
        span = max(end - start, 0.05)

        target_fps = float(fps or min(meta["fps"], config.DEFAULT_TARGET_FPS))
        target_fps = max(config.MIN_TARGET_FPS, min(target_fps, config.WHATSAPP_MAX_FPS))

        if not keep_animation:
            frames = video.extract_frames(path, start, start + span, 1, 1, size)
            if not frames:
                raise ValueError("no frames could be extracted from the video")
            static = _encode_static(_fit(frames[0], size, padding), config.STATIC_MAX_BYTES)
            return StickerResult(static, size, size, False, 1, True, warnings, "video")

        wanted = min(max(int(round(span * target_fps)), 2), max_frames)
        frames = video.extract_frames(path, start, end, target_fps, wanted, size)
        if not frames:
            raise ValueError("no frames could be extracted from the video")
        if len(frames) == 1:
            static = _encode_static(_fit(frames[0], size, padding), config.STATIC_MAX_BYTES)
            return StickerResult(static, size, size, False, 1, True, warnings, "video")

        frame_ms = max(int(round(1000 / target_fps)), config.MIN_FRAME_DURATION_MS)
        durations = [frame_ms] * len(frames)
        fitted = [_fit(frame, size, padding) for frame in frames]
        return _finalize_animated(fitted, durations, "", warnings, "video")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def convert(
    data: bytes,
    padding: int = config.DEFAULT_PADDING,
    max_frames: int = config.MAX_FRAMES,
    keep_animation: bool = True,
    size: int = config.STICKER_SIZE,
    trim_start: Optional[float] = None,
    trim_end: Optional[float] = None,
    fps: Optional[float] = None,
) -> StickerResult:
    """Turn arbitrary media bytes into a 512x512 WhatsApp sticker."""
    ensure_webp()
    padding = max(0, min(int(padding), size // 2 - 1))
    max_frames = max(int(max_frames), 1)

    image = _try_image(data)
    if image is not None:
        return _convert_image(image, padding, max_frames, keep_animation, size)
    return _convert_video(
        data, padding, max_frames, keep_animation, size, trim_start, trim_end, fps
    )
