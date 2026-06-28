"""Lossy-transcode detection via spectral brickwall analysis.

Lossy encoders (MP3, AAC, Vorbis, Opus) apply a steep low-pass filter at a
bitrate-dependent cutoff (e.g. 16 kHz for 128 kbps MP3, 20.5 kHz for 320 kbps).
A genuine lossless file has a gradual natural roll-off; a lossy-to-lossless
transcode preserves the encoder's abrupt brickwall.

We detect the brickwall with a multi-point slope profile:

1. Measure RMS at 5 frequency bands (15k, 17k, 19k, 20.5k, 21.5k Hz) plus an
   unfiltered overall RMS — all in a single ffmpeg call via asplit=6.
2. Compute the drop between each adjacent pair of bands.
3. If any adjacent drop exceeds BRICKWALL_DROP_DB (25 dB), that is a codec
   brickwall — not a natural roll-off — and the file is flagged as
   "Suspected Transcode (Lossy)".

The analysis frequencies are constant regardless of sample rate because the
encoder cutoff does not move with the container — a 96 kHz FLAC transcoded
from MP3 still has its brickwall at ~20 kHz, not at 40 kHz.

Design decisions:

- **Multi-point over two-point** — A single-drop test at 18→21.5 kHz misses
  128 kbps MP3 brickwalls at 16 kHz.  Measuring at 15/17/19/20.5/21.5 kHz
  catches brickwalls at any common encoder cutoff.
- **RMS over Peak** — Peak level is thrown off by a single click or transient.
  RMS measures sustained energy, which is what spectral content actually means.
- **Maximum adjacent drop** — A brickwall concentrates its energy gap into a
  single adjacent band pair; a natural roll-off distributes the drop evenly.
- **Fixed analysis frequencies** — Codified in BRICKWALL_DROP_DB / LOW_ENERGY_DB
  / NO_HF_DB so the thresholds are easy to tune after field experience.
- **First 60 seconds only (-t 60)** — Keeps scans fast; the spectral signature
  is consistent across a track.

Guards:

- Overall RMS < LOW_ENERGY_DB (-50 dB) -> "Inconclusive (Low Energy)".
  Catches silence, ambient tracks, and long silent intros.
- RMS(highpass@15k) < NO_HF_DB (-72 dB) -> "Inconclusive (Low Energy)".
  No HF content at all to measure (naturally bass-heavy track, or a very
  low-bitrate transcode below our analysis window).
- Lossy codecs -> "N/A (Lossy Codec)".  The file already declares itself lossy.
- DSD -> "True Lossless".  DSD cannot realistically be a lossy transcode.
"""

import re
import subprocess
from pathlib import Path

from .constants import DSD, GREY, LOSSLESS, LOSSY_ALL, MAGENTA, RED

VERDICT_W = 29

# Thresholds (all dB, negative)
BRICKWALL_DROP_DB = 25  # min drop between adjacent HF bands to flag as a codec brickwall
LOW_ENERGY_DB = -50  # overall RMS below this = track too quiet to analyse
NO_HF_DB = -72  # RMS in 15 kHz band below this = no HF content to measure

# Highpass frequencies for the multi-point slope profile (Hz)
HP_FREQS = (15000, 17000, 19000, 20500, 21500)
NUM_BANDS = len(HP_FREQS) + 1  # +1 for unfiltered overall


def analyze_steepness(filepath: Path, sample_rate: int) -> dict[str, float | None] | None:
    """Run the multi-point HF steepness test (see module docs for methodology).

    Uses a single ffmpeg call with asplit=N so the file is decoded once.
    Returns {'rms_overall', 'rms_15000', 'rms_17000', 'rms_19000',
             'rms_20500', 'rms_21500'} in dB, or None on failure.
    """
    if sample_rate < 44100:
        return None

    # Build the filter graph: asplit=N for each band, each measured with astats
    labels = [chr(ord("a") + i) for i in range(NUM_BANDS)]
    splits = "".join(f"[{c}]" for c in labels)
    chains = ["[a]astats[aout]"]  # unfiltered
    for i, freq in enumerate(HP_FREQS):
        c = labels[i + 1]
        chains.append(f"[{c}]highpass=f={freq},astats[{c}out]")

    filter_graph = f"asplit={NUM_BANDS}{splits};" + ";".join(chains)

    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "info",
                "-t",
                "60",
                "-i",
                str(filepath),
                "-filter_complex",
                filter_graph,
                "-map",
                "[aout]",
                *[item for c in labels[1:] for item in ("-map", f"[{c}out]")],
                "-f",
                "null",
                "/dev/null",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired, OSError:
        return None

    matches = re.findall(r"RMS\s+level\s+dB:\s*(-?\d+\.?\d*|-inf)", result.stderr)
    if len(matches) % NUM_BANDS != 0:
        return None

    per_astats = len(matches) // NUM_BANDS  # channels + 1
    if per_astats < 2:
        return None

    def _parse(idx: int) -> float:
        v = matches[idx]
        return -200.0 if "inf" in v else float(v)

    # Each astats outputs per_astats lines (per-channel then overall).  The
    # groups can complete in any order, but overall RMS always decreases as
    # the highpass frequency increases.  Grab the last (overall) line from
    # each group and sort descending to assign bands.
    values = [_parse(i * per_astats - 1) for i in range(1, NUM_BANDS + 1)]
    values.sort(reverse=True)  # highest RMS first = unfiltered, then ascending HP frequency

    return {
        "rms_overall": values[0],
        "rms_15000": values[1],
        "rms_17000": values[2],
        "rms_19000": values[3],
        "rms_20500": values[4],
        "rms_21500": values[5],
    }


def compute_verdict(
    codec: str,
    filepath: Path,
    sample_rate: int | None,
    *,
    brickwall_threshold: float = BRICKWALL_DROP_DB,
    low_energy_threshold: float = LOW_ENERGY_DB,
    no_hf_threshold: float = NO_HF_DB,
) -> tuple[str, int, bool]:
    """Return (verdict_text, color_code, dim) for an audio file.

    Lossy codec      -> "N/A (Lossy Codec)"
    DSD              -> "True Lossless"
    Unknown codec    -> "Unknown Codec"
    Low sample rate  -> "Inconclusive (Low SR)"
    Low overall RMS  -> "Inconclusive (Low Energy)"
    No HF content    -> "Inconclusive (Low Energy)"
    Steep brickwall  -> "Suspected Transcode (Lossy)"
    Otherwise        -> "True Lossless"
    """
    cl = codec.lower()

    if cl in LOSSY_ALL:
        return ("N/A (Lossy Codec)", GREY, True)

    if cl in DSD:
        return ("True Lossless", MAGENTA, False)

    if cl not in LOSSLESS:
        return ("Unknown Codec", GREY, True)

    if sample_rate is None or sample_rate < 44100:
        return ("Inconclusive (Low SR)", GREY, True)

    spec = analyze_steepness(filepath, sample_rate)
    if spec is None:
        return ("Error", RED, False)

    rms_overall = spec["rms_overall"]
    rms_15000 = spec["rms_15000"]
    rms_21500 = spec["rms_21500"]
    assert rms_15000 is not None
    assert rms_21500 is not None

    if rms_overall is not None and rms_overall < low_energy_threshold:
        return ("Inconclusive (Low Energy)", GREY, True)

    if rms_15000 < no_hf_threshold:
        return ("Inconclusive (Low Energy)", GREY, True)

    # Check the maximum adjacent drop across all band pairs
    band_values = [
        rms_overall,
        spec["rms_15000"],
        spec["rms_17000"],
        spec["rms_19000"],
        spec["rms_20500"],
        spec["rms_21500"],
    ]
    max_drop = max((band_values[i] or -200) - (band_values[i + 1] or -200) for i in range(len(band_values) - 1))
    if max_drop > brickwall_threshold:
        return ("Suspected Transcode (Lossy)", RED, False)

    return ("True Lossless", MAGENTA, False)
