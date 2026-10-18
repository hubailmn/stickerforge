"""Command-line entry point for building .wastickers packs."""

from __future__ import annotations

import argparse
import sys

from . import config
from .converter import as_animated, as_static, convert
from .fetcher import FetchError, fetch
from .pack import build_archive, build_cover, plan_pack_sizes, resolve_kinds, sticker_filename


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

    sources = list(dict.fromkeys(sources))  # same link twice counts once

    converted = []  # (animated, data, cover-worthy source label)
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
        converted.append((result.animated, result.data))

    if not converted:
        print("error: no stickers were produced", file=sys.stderr)
        return 1

    # A lone sticker of one kind would be an unimportable pack: move it to the
    # other kind where that is possible.
    flags, changed, notes = resolve_kinds([animated for animated, _ in converted])
    for index in changed:
        data = converted[index][1]
        converted[index] = (flags[index], as_animated(data) if flags[index] else as_static(data))
    for note in notes:
        print(f"note: {note}", file=sys.stderr)

    # WhatsApp packs are 3-30 stickers and never mix animated with static.
    groups = []
    for animated in (True, False):
        members = [data for is_animated, data in converted if is_animated is animated]
        cursor = 0
        for size in plan_pack_sizes(len(members)):
            groups.append((animated, members[cursor : cursor + size]))
            cursor += size

    written = []
    for number, (animated, members) in enumerate(groups, start=1):
        if len(members) < config.MIN_STICKERS:
            print(
                f"warning: a {'animated' if animated else 'static'} pack has only {len(members)} "
                f"sticker(s); WhatsApp needs at least {config.MIN_STICKERS}",
                file=sys.stderr,
            )
        title = args.title if len(groups) == 1 else f"{args.title} ({number}/{len(groups)})"
        sticker_files = [(sticker_filename(i), data) for i, data in enumerate(members)]
        archive = build_archive(title, args.author, sticker_files, build_cover(members[0]))
        path = args.output
        if len(groups) > 1:
            stem, dot, ext = args.output.rpartition(".")
            path = f"{stem}-{number}.{ext}" if dot else f"{args.output}-{number}"
        with open(path, "wb") as handle:
            handle.write(archive)
        written.append(path)
        print(f"wrote {path} ({len(members)} stickers, {len(archive) // 1024} KB)")

    if failures:
        print(f"{failures} source(s) skipped", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
