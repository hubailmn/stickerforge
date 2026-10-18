"""Central limits and defaults for StickerForge.

Limits mirror the WhatsApp preset used by laggykiller/sticker-convert
(resources/compression.json): 512x512, 100 KB static / 500 KB animated,
1..30 fps, quality 0..100, duration <= 10000 ms.
"""

import os

STICKER_SIZE = 512
COVER_SIZE = 96
STATIC_MAX_BYTES = 100 * 1024
ANIMATED_MAX_BYTES = 500 * 1024
MAX_STICKERS = 30  # WhatsApp: a pack holds 3 to 30 stickers
MIN_STICKERS = 3
MAX_SOURCES = 120  # sources accepted per request; packs are split automatically
MAX_FRAMES = 90
DEFAULT_PADDING = 8
MIN_FRAME_DURATION_MS = 20
DEFAULT_FRAME_DURATION_MS = 80

WHATSAPP_MAX_DURATION_MS = 10000
WHATSAPP_MAX_FPS = 30
MIN_TARGET_FPS = 5
DEFAULT_TARGET_FPS = 15

QUALITY_MIN = 0
QUALITY_MAX = 90
STATIC_QUALITY_MIN = 10
STATIC_QUALITY_MAX = 95
QUALITY_BISECT_STEPS = 6
WEBP_METHOD = 4  # final encode: best compression
SEARCH_METHOD = 0  # quality search: ~4x faster than method 4
SEARCH_QUALITY_MIN = 8
PROBE_QUALITY = 8  # lowest search quality: the feasibility limit
# Skip frame steps whose estimated size is more than this many times the budget.
FRAME_BUDGET_SLACK = 1.2
FRAME_STEPS = (1, 2, 3, 4, 6, 8, 12)

FETCH_TIMEOUT = 30
FETCH_WORKERS = 8  # concurrent downloads/probes per request pool
BUILD_WORKERS = max(1, min(3, os.cpu_count() or 1))  # concurrent conversions
STORE_MAX_BYTES = 768 * 1024 * 1024  # total in-memory cache for sources + packs
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
USER_AGENT = "StickerForge/1.0"

ALLOW_PRIVATE_HOSTS = os.environ.get("STICKERFORGE_ALLOW_PRIVATE_HOSTS", "0") == "1"
FFMPEG_PATH = os.environ.get("STICKERFORGE_FFMPEG", "")
