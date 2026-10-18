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
