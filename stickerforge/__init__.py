"""StickerForge: build WhatsApp sticker packs from GIFs and images."""

from .converter import StickerResult, convert, webp_supported
from .pack import build_archive, build_cover, sticker_filename

__version__ = "1.0.0"

__all__ = [
    "StickerResult",
    "convert",
    "webp_supported",
    "build_archive",
    "build_cover",
    "sticker_filename",
    "__version__",
]
