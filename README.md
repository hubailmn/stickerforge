# StickerForge

Build WhatsApp sticker packs from **images, GIFs, and videos** — on your computer,
with a live WhatsApp preview and a video timeline trimmer — then export a
`.wastickers` file you can import on Android **or iPhone**.

## Why this exists

WhatsApp's "copy a link → import pack" flow is **Android only**. iOS has no pack
import inside WhatsApp itself. StickerForge produces a `.wastickers` archive — the
open format that apps like **Sticker Maker** and **Wemoji** import and push into
WhatsApp on iPhone.

StickerForge handles the fiddly parts:

- Fits every sticker to **512×512** with transparent padding
- Encodes to **animated WebP** under the **500 KB** cap (static: 100 KB)
- Caps animated stickers to **~10 seconds** and re-encodes with a **bisection quality
  search** (the same approach sticker-convert uses) so you keep the most quality that fits
- **Trims video/MP4** to the segment you want, at the frame rate you choose
- Caps frame counts, merges durations, and flattens to static only as a last resort
- Shows an **in-browser phone mockup** of your pack before exporting

## Limits it enforces

| | Value |
|---|---|
| Size | 512×512 px, transparent |
| Animated | ≤ 500 KB, ≤ 10 s, 5–30 fps |
| Static | ≤ 100 KB |
| Max stickers / pack | 30 |
| Padding | 8 px |

These mirror WhatsApp's own bounds (as encoded in `sticker-convert`'s WhatsApp
preset). Tune them in `stickerforge/config.py`.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`imageio-ffmpeg` ships a static **ffmpeg** binary, so video support works without
Homebrew. To use your own ffmpeg instead, set `STICKERFORGE_FFMPEG=/path/to/ffmpeg`
(or just have `ffmpeg` on `PATH`; it takes priority).

Verify WebP support:

```bash
python -c "from PIL import features; print(features.check('webp'), features.check('webp_anim'))"
```

## Web app (paste links → trim → preview → export)

```bash
python -m stickerforge.web
```

Open <http://127.0.0.1:5000>:

1. Paste image/GIF/video URLs **or** upload files, then **Add sources**.
2. For each video, scrub the timeline and set **start** / **end** (or use the
   playhead buttons), choose a target **fps**, and watch the clip length stay under
   the 10 s badge.
3. Set a title/author (and optionally padding / max frames), then **Build pack**.
4. The phone mockup fills with your stickers — click one to "send" it, drag to
   reorder, star one to make the pack cover.
5. **Download .wastickers**.

Environment variables: `STICKERFORGE_HOST`, `STICKERFORGE_PORT`, `STICKERFORGE_DEBUG`,
`STICKERFORGE_FFMPEG`, `STICKERFORGE_ALLOW_PRIVATE_HOSTS` (`1` to fetch from localhost/LAN).

## Command line

```bash
python -m stickerforge.cli -t "Cats" -a "you" -o cats.wastickers \
  https://example.com/cat1.gif https://example.com/cat2.png

python -m stickerforge.cli -t "Clips" -o clips.wastickers \
  --trim-start 2.0 --trim-end 5.0 --fps 12 movie.mp4

python -m stickerforge.cli -l sources.txt -t "Mix" -o mix.wastickers
```

Options: `--padding`, `--max-frames`, `--static`, `--trim-start`, `--trim-end`, `--fps`.

## Importing on iPhone

1. AirDrop the `.wastickers` file to your iPhone (or iCloud/Files).
2. Install **Sticker Maker** (sticker.ly) or **Wemoji**.
3. Share the file into that app, then **Add to WhatsApp**.

Android works the same way; WhatsApp's own link-import is Android-only and not required.

## What's inside a `.wastickers` file

```
author.txt      plain-text author name
title.txt       plain-text pack title
cover.png       96×96 pack cover
000.webp        first sticker (512×512)
001.webp        second sticker
...
```

## Tests

```bash
python -m unittest discover -s tests -v
```

Video tests are skipped automatically when ffmpeg is unavailable.

## Project layout

```
stickerforge/
  config.py       limits and defaults
  converter.py    image/GIF/video -> WebP, bisection quality search, frame capping
  video.py        ffmpeg probe / frame extraction / thumbnails
  fetcher.py      URL and local-file retrieval with guards
  pack.py         .wastickers archive + cover generation
  cli.py          command-line interface
  web.py          Flask API + app factory
  templates/      index.html
  static/         app.js, styles.css
tests/            unittest suite
```

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/analyze` | multipart (`urls`, `files`) → probe each source, store it, return metadata + thumbnails |
| `POST` | `/api/build` | JSON `{title, author, padding, max_frames, sources:[{sid, trim_start, trim_end, fps, keep_animation}]}` → pack JSON (also accepts a legacy multipart form) |
| `GET` | `/api/source/<sid>/thumb.png` | source thumbnail |
| `GET` | `/api/pack/<id>/sticker/<sid>.webp` | sticker preview |
| `GET` | `/api/pack/<id>/cover.png` | pack cover |
| `POST` | `/api/pack/<id>/order` | `{"order": [sid...], "cover": sid}` |
| `GET` | `/api/pack/<id>/download` | download `.wastickers` |
| `GET` | `/api/health` | ffmpeg/webp availability |

Sources and packs are held in memory and expire after one hour.

## Credits

Limits and the bisection compression approach are informed by
[laggykiller/sticker-convert](https://github.com/laggykiller/sticker-convert) (GPL-2.0).
StickerForge is an independent, MIT-licensed implementation and shares no code with it.

## License

MIT
