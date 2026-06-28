# Music Tooling

## What this is

Two CLI tools for managing a local audio library:

- **`scan`** — walk a directory tree, probe every audio file with ffprobe, and
  print a colour-coded table of codec, bitrate, cover art, missing tags, and a
  lossy-transcode verdict.  Given a single file, prints a detailed per-file
  breakdown instead of the table.
- **`tag`** — fingerprint a single file with `fpcalc`, look up its MusicBrainz
  metadata via the AcoustID API (enriched with track number, genre, and album
  artist from MusicBrainz), interactively select a match with arrow keys, and
  write tags to the file with mutagen. Also supports `--read` to inspect
  existing tags without any network call.

Both run through `uv run scan ...` / `uv run tag ...` via `[project.scripts]`.

## Separation of concerns

`scan` and `tag` are independent tools with a hard boundary:

| | `scan` | `tag` |
|---|---|---|
| **Domain** | Local analysis | Metadata lookup + mutation |
| **Reads tags with** | ffprobe (JSON output) | mutagen |
| **Writes tags?** | No (read-only) | Yes (mutagen) |
| **Network?** | No (local only) | Yes (AcoustID + MusicBrainz) |
| **Key dependency** | ffmpeg/ffprobe | fpcalc (chromaprint) |

- `scan.py` owns the table-scan UI, ffprobe integration, and verdict display.
  It never imports mutagen.
- `tag.py` owns fingerprinting, API lookups, interactive selection, and tag
  mutation. It never shells out to ffprobe/ffmpeg.

They share only the truly generic:
- `constants.py` — codec sets, ANSI colour codes, tag-name mappings
- `verdict.py` — lossy-transcode detection (used by scan; importable by tag)
- `ui.py` — ANSI terminal formatting helpers (colored, bold, dim, cursor)

## Layout

```
src/music/
├── __init__.py    # empty
├── constants.py   # codec sets, ANSI colour codes, tag names, extensions
├── verdict.py     # multi-point spectral brickwall detector
├── ui.py          # ANSI 256-color terminal formatting (colored, bold, dim)
├── scan.py        # probe, format, collect, main() entry point
└── tag.py         # AcoustID fingerprint → MusicBrainz lookup → mutagen write
```

| Module | Purpose |
|---|---|
| `constants.py` | Shared data: codec categories (`LOSSY_ALL`, `LOSSLESS`, `DSD`), 256-color ANSI codes (`MAGENTA`, `GOLD`, …), `TAG_NAMES`, `AUDIO_EXTENSIONS`. No logic. |
| `verdict.py` | Lossy-transcode detection via multi-point spectral slope profile. Module docstring is the canonical methodology reference. Exports `compute_verdict(codec, filepath, sample_rate, **thresholds)` → `(text, color, dim)`. One ffmpeg call per file (`asplit=6` in a `filter_complex` graph with 5 highpass bands at 15/17/19/20.5/21.5 kHz). Thresholds are module-level constants (`BRICKWALL_DROP_DB`, `LOW_ENERGY_DB`, `NO_HF_DB`), overridable via kwargs and CLI flags. |
| `ui.py` | Shared ANSI formatting: `colored(text, code, bold, dim)`, `bold(text)`, `dim(text)`, `cursor_up(n)`, `clear_line()`, `clear_below()`. No business logic. |
| `scan.py` | The `scan` CLI. `probe()` wraps ffprobe. `collect_files()` walks dirs. `_process_one()` does the per-file work (probe → format → verdict). `main()` runs a `ThreadPoolExecutor` over files (default `cpu_count` workers, override with `-j N`). Column widths and ANSI formatting live here. Single-file mode (`_print_file_detail()`) prints a full breakdown: streams, all tags, verdict. Supports `--brickwall-threshold`, `--low-energy-db`, `--no-hf-db`. |
| `tag.py` | The `tag` CLI. Fingerprints via `fpcalc`, looks up AcoustID, enriches with MusicBrainz (`_fetch_musicbrainz()`), interactive arrow-key selector with live diff (`interactive_select()`), writes tags with mutagen. Supports `--read` (inspect local tags, no network) and `-y` (skip write confirmation). |

## Commands

```bash
uv run scan ~/Music/FLAC                    # scan a directory
uv run scan song.flac                       # single-file detailed breakdown
uv run scan -j 4 ~/Music/FLAC               # 4-thread scan
uv run scan --brickwall-threshold 30 .       # tune sensitivity
uv run tag ~/Music/song.flac                # fingerprint + lookup + write
uv run tag --read ~/Music/song.flac         # inspect existing tags (no network)
uv run tag -y ~/Music/song.flac             # skip write confirmation
uv run ruff format . && uv run ruff check . && uv run ty check   # lint + type-check
```

External prerequisites: `ffmpeg` / `ffprobe` (for scan), `fpcalc` from
[chromaprint](https://acoustid.org/chromaprint) (for tag). The scan tool
checks for ffprobe at startup; tag checks for fpcalc on first use.

## Architecture notes

### Verdict flow

```
scan.main()
  └─ ThreadPoolExecutor.map(_process_one, files)
       └─ probe(fp)            # ffprobe → JSON
       └─ compute_verdict()    # from verdict.py
            ├─ LOSSY_ALL?  → "N/A (Lossy Codec)"
            ├─ DSD?        → "True Lossless"
            └─ LOSSLESS?   → analyze_steepness()
                 └─ single ffmpeg call: asplit=6 + astats × 6
                      ├─ overall RMS   (no filter)
                      ├─ RMS above 15  kHz
                      ├─ RMS above 17  kHz
                      ├─ RMS above 19  kHz
                      ├─ RMS above 20.5 kHz
                      └─ RMS above 21.5 kHz
```

The six astats outputs arrive in stderr in any order.  `analyze_steepness`
sorts the six overall-RMS values descending: highest = overall, then ascending
highpass frequency.  This is always correct because RMS can only decrease as
you apply more aggressive highpass filters.  The maximum adjacent drop across
all band pairs is checked against `BRICKWALL_DROP_DB` — a brickwall
concentrates its energy gap into a single pair, while a natural roll-off
distributes the drop evenly.

### Why asplit=N

A single ffmpeg call with `asplit=N` in a `filter_complex` graph decodes the
file once instead of N times — roughly N× faster than sequential calls.

### Thread safety

`_process_one()` touches no shared mutable state.  `subprocess.run` releases
the GIL.  `ThreadPoolExecutor.map()` preserves input ordering in the output
iterator — results print in file-system order.

### Multi-point slope profile

The detector measures RMS at five frequency bands (15k, 17k, 19k, 20.5k,
21.5k Hz) plus an unfiltered overall level.  A single two-point drop (e.g.
18→21.5 kHz) misses 128 kbps MP3 brickwalls at 16 kHz.  Five bands catch
brickwalls at any common encoder cutoff — the steepest adjacent drop is the
smoking gun.

### Verdict thresholds

All in `verdict.py` as module-level constants, overridable via CLI flags on `scan`:

| Constant | Flag | Default | Meaning |
|---|---|---|---|
| `BRICKWALL_DROP_DB` | `--brickwall-threshold` | 25 dB | Min drop between adjacent HF bands to flag a codec brickwall |
| `LOW_ENERGY_DB` | `--low-energy-db` | -50 dB | Overall RMS below this = track too quiet to analyse |
| `NO_HF_DB` | `--no-hf-db` | -72 dB | RMS in 15 kHz band below this = no HF content at all |

### Tag flow

```
tag.main()
  └─ --read?  → _print_tags()  (local only, exit)
  └─ get_audio_fingerprint()   # fpcalc → (duration, fingerprint)
  └─ fetch_acoustid_metadata() # AcoustID API → results
  └─ interactive_select()      # arrow keys, live diff (or _fallback_select if !isatty)
  └─ extract_metadata()        # AcoustID → {title, artist, album, date}
  └─ _fetch_musicbrainz()      # MusicBrainz API → {tracknumber, genre, albumartist}
  └─ format_diff()             # current vs new diff
  └─ prompt / _write_tags()    # mutagen write (EasyID3 / EasyMP4 / FLAC / OggVorbis)
```
