# Music Tooling

## What this is

Two CLI tools for managing a local audio library:

- **`scan`** — walk a directory tree, probe every audio file with ffprobe, and
  print a colour-coded table of codec, bitrate, cover art, missing tags, and a
  lossy-transcode verdict.
- **`tag`** — fingerprint a single file with `fpcalc` and look up its
  MusicBrainz metadata via the AcoustID API.

Both run through `uv run scan ...` / `uv run tag ...` via `[project.scripts]`.

## Layout

```
src/music/
├── __init__.py    # empty
├── constants.py   # codec sets, ANSI colour codes, tag names, extensions
├── verdict.py     # spectral brickwall detector + methodology docs
├── scan.py        # probe, format, collect, main() entry point
└── tag.py         # AcoustID fingerprint → MusicBrainz lookup
```

| Module | Purpose |
|---|---|
| `constants.py` | Shared data: codec categories (`LOSSY_ALL`, `LOSSLESS`, `DSD`), 256-color ANSI codes (`MAGENTA`, `GOLD`, …), `TAG_NAMES`, `AUDIO_EXTENSIONS`. No logic. |
| `verdict.py` | Lossy-transcode detection. Module docstring is the canonical methodology reference. Exports `compute_verdict(codec, filepath, sample_rate)` → `(text, color, dim)`. One ffmpeg call per file (`asplit=3` in a `filter_complex` graph). Thresholds are module-level constants: `BRICKWALL_DROP_DB`, `LOW_ENERGY_DB`, `NO_HF_DB`. |
| `scan.py` | The `scan` CLI. `probe()` wraps ffprobe. `collect_files()` walks dirs. `_process_file()` does the per-file work (probe → format → verdict). `main()` runs a `ThreadPoolExecutor` over files (default `cpu_count` workers, override with `-j N`). Column widths and ANSI formatting live here. |
| `tag.py` | The `tag` CLI. Thin wrapper around `fpcalc` + AcoustID HTTP API. Requires `ACOUSTID_API_KEY` in the environment. |

## Commands

```bash
uv run scan ~/Music/FLAC            # scan a directory
uv run scan -j 4 ~/Music/FLAC      # 4-thread scan
uv run tag ~/Music/song.flac        # fingerprint + MusicBrainz lookup
uv run ruff format . && uv run ruff check . && uv run ty check   # lint + type-check
```

External prerequisites: `ffmpeg` / `ffprobe` (for scan), `fpcalc` from
[chromaprint](https://acoustid.org/chromaprint) (for tag). The scan tool
checks for ffprobe at startup; tag checks for fpcalc on first use.

## Architecture notes

### Verdict flow

```
scan.main()
  └─ ThreadPoolExecutor.map(_process_file, files)
       └─ probe(fp)            # ffprobe → JSON
       └─ compute_verdict()    # from verdict.py
            ├─ LOSSY_ALL?  → "N/A (Lossy Codec)"
            ├─ DSD?        → "True Lossless"
            └─ LOSSLESS?   → analyze_steepness()
                 └─ single ffmpeg call: asplit=3 + astats × 3
                      ├─ overall RMS   (no filter)
                      ├─ RMS above 18 kHz
                      └─ RMS above 21.5 kHz
```

The three astats outputs arrive in stderr in any order.  `analyze_steepness`
sorts the three overall-RMS values descending: highest = overall, middle = low
band, lowest = high band.  This is always correct because RMS can only decrease
as you apply more aggressive highpass filters.

### Why asplit=3

Before: three separate ffmpeg calls per lossless file, each decoding the first
60 seconds independently → 3× decode cost.  Now: one call with `asplit=3` in a
`filter_complex` graph, decoded once.

### Thread safety

`_process_file()` touches no shared mutable state.  `subprocess.run` releases
the GIL.  `ThreadPoolExecutor.map()` preserves input ordering in the output
iterator — results print in file-system order.

### Verdict thresholds

All in `verdict.py` as module-level constants:

| Constant | Default | Meaning |
|---|---|---|
| `BRICKWALL_DROP_DB` | 25 dB | Min drop between HF bands to flag a codec brickwall |
| `LOW_ENERGY_DB` | -50 dB | Overall RMS below this = track too quiet to analyse |
| `NO_HF_DB` | -72 dB | RMS in 18 kHz band below this = no HF content at all |

## Potential improvements

See [SCAN.md](SCAN.md) for the backlog: multi-point slope profile, peak-hold,
spectral flatness, noise floor profiling, hi-res authenticity, and
user-tunable sensitivity.
