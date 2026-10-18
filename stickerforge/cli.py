"""Command-line entry point for building .wastickers packs."""

from __future__ import annotations

import argparse
import sys

from . import config
from .converter import convert
from .fetcher import FetchError, fetch
from .pack import build_archive, build_cover, sticker_filename


def _read_list_file(path: str):
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line and not line.startswith("#"):
                yield line


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stickerforge",
        description="Build a WhatsApp .wastickers pack from GIFs and images.",
    )
    parser.add_argument("sources", nargs="*", help="URLs or local file paths")
    parser.add_argument("-u", "--url", action="append", default=[], dest="urls")
    parser.add_argument("-l", "--list", dest="list_file", help="file with one source per line")
    parser.add_argument("-t", "--title", default="My Pack")
    parser.add_argument("-a", "--author", default="")
    parser.add_argument("-o", "--output", default="pack.wastickers")
    parser.add_argument("--padding", type=int, default=config.DEFAULT_PADDING)
    parser.add_argument("--max-frames", type=int, default=config.MAX_FRAMES)
    parser.add_argument("--static", action="store_true", help="flatten animation")
    parser.add_argument("--trim-start", type=float, default=None, help="video trim start (s)")
    parser.add_argument("--trim-end", type=float, default=None, help="video trim end (s)")
    parser.add_argument("--fps", type=float, default=None, help="target fps for videos")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    sources = list(args.urls) + list(args.sources)
    if args.list_file:
        sources.extend(_read_list_file(args.list_file))
    if not sources:
        print("error: no sources given", file=sys.stderr)
        return 2

    if len(sources) > config.MAX_STICKERS:
        print(
            f"note: WhatsApp packs hold {config.MAX_STICKERS} stickers; "
            f"using the first {config.MAX_STICKERS}",
            file=sys.stderr,
        )
        sources = sources[: config.MAX_STICKERS]

    sticker_files = []
    cover_source = None
    failures = 0
    for index, source in enumerate(sources):
        try:
            raw = fetch(source)
            result = convert(
                raw,
                padding=args.padding,
                max_frames=args.max_frames,
                keep_animation=not args.static,
                trim_start=args.trim_start,
                trim_end=args.trim_end,
                fps=args.fps,
            )
        except (FetchError, ValueError) as exc:
            print(f"[{index + 1}/{len(sources)}] {source}: SKIPPED ({exc})", file=sys.stderr)
            failures += 1
            continue
        for warning in result.warnings:
            print(f"  warning: {warning}", file=sys.stderr)
        kind = f"animated {result.frames}f" if result.animated else "static"
        print(f"[{index + 1}/{len(sources)}] {source}: {kind}, {result.size // 1024} KB")
        if cover_source is None:
            cover_source = result.data
        sticker_files.append((sticker_filename(len(sticker_files)), result.data))

    if not sticker_files:
        print("error: no stickers were produced", file=sys.stderr)
        return 1

    archive = build_archive(
        args.title, args.author, sticker_files, build_cover(cover_source)
    )
    with open(args.output, "wb") as handle:
        handle.write(archive)
    print(
        f"wrote {args.output} "
        f"({len(sticker_files)} stickers, {len(archive) // 1024} KB)"
        + (f", {failures} skipped" if failures else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
