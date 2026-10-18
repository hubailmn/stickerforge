"""Build .wastickers archives that sticker apps can import."""

from __future__ import annotations

import io
import zipfile
from typing import Iterable, List, Tuple

from PIL import Image

from . import config
from .converter import RESAMPLE

StickerFile = Tuple[str, bytes]


def sticker_filename(index: int) -> str:
    """Zero-padded so importers that sort by name keep the intended order."""
    return f"{index:03d}.webp"


def build_cover(image_bytes: bytes, size: int = config.COVER_SIZE) -> bytes:
    """Render a square PNG pack cover from the first frame of a sticker."""
    image = Image.open(io.BytesIO(image_bytes))
    try:
        image.seek(0)
    except Exception:
        pass
    frame = image.convert("RGBA")
    scale = min(size / frame.width, size / frame.height)
    width = max(1, round(frame.width * scale))
    height = max(1, round(frame.height * scale))
    frame = frame.resize((width, height), RESAMPLE)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.alpha_composite(frame, ((size - width) // 2, (size - height) // 2))
    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG")
    return buffer.getvalue()


def build_archive(
    title: str,
    author: str,
    stickers: Iterable[StickerFile],
    cover: bytes,
) -> bytes:
    """Assemble the .wastickers zip: author.txt, title.txt, cover.png, *.webp."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("author.txt", author or "")
        archive.writestr("title.txt", title or "")
        archive.writestr("cover.png", cover)
        for name, data in stickers:
            archive.writestr(name, data)
    return buffer.getvalue()


def names_for(count: int) -> List[str]:
    return [sticker_filename(i) for i in range(count)]


def plan_pack_sizes(count: int, max_size: int = config.MAX_STICKERS) -> List[int]:
    """Split `count` stickers into the fewest packs of at most `max_size`.

    Sizes are balanced so no pack is left with only a sticker or two:
    31 stickers become [16, 15], not [30, 1].
    """
    if count <= 0:
        return []
    packs = -(-count // max_size)  # ceiling division
    base, extra = divmod(count, packs)
    return [base + 1] * extra + [base] * (packs - extra)


def resolve_kinds(animated_flags: List[bool]) -> Tuple[List[bool], List[int], List[str]]:
    """Decide the final animated/static kind of each sticker so every pack can import.

    WhatsApp needs at least MIN_STICKERS per pack and never mixes kinds, so a
    lone sticker of one kind is moved into the other kind when that is possible.
    Returns (new_flags, changed_indexes, notes).
    """
    flags = list(animated_flags)
    static_idx = [i for i, animated in enumerate(flags) if not animated]
    animated_idx = [i for i, animated in enumerate(flags) if animated]
    changed: List[int] = []
    notes: List[str] = []

    if 0 < len(static_idx) < config.MIN_STICKERS and animated_idx:
        # Static -> animated keeps the picture; it just gets a short two-frame loop.
        for index in static_idx:
            flags[index] = True
            changed.append(index)
        notes.append(
            f"{len(static_idx)} static sticker(s) became short animations so they can join "
            "the animated pack (WhatsApp packs cannot mix static and animated stickers)"
        )
    elif 0 < len(animated_idx) < config.MIN_STICKERS and len(static_idx) >= config.MIN_STICKERS:
        # Animated -> static is lossy, so it is only done when unavoidable.
        for index in animated_idx:
            flags[index] = False
            changed.append(index)
        notes.append(
            f"{len(animated_idx)} animated sticker(s) were saved as static so they can form a "
            f"valid pack (WhatsApp needs at least {config.MIN_STICKERS} per pack)"
        )
    return flags, changed, notes
