"""Lossy-transcode detection via spectral brickwall analysis.

Lossy encoders (MP3, AAC, Vorbis, Opus) apply a steep low-pass filter at a
bitrate-dependent cutoff (e.g. 16 kHz for 128 kbps MP3, 20.5 kHz for 320 kbps).
A genuine lossless file has a gradual natural roll-off; a lossy-to-lossless
transcode preserves the encoder's abrupt brickwall.

We detect the brickwall with a two-point steepness test:

1. Measure RMS energy above 18 kHz  (highpass + astats).
2. Measure RMS energy above 21.5 kHz (same, close to Nyquist for 44.1 kHz).
3. If the drop between the two bands exceeds BRICKWALL_DROP_DB (25 dB),
   that is a codec brickwall — not a natural roll-off — and the file is
   flagged as "Suspected Transcode (Lossy)".

The analysis frequencies are constant regardless of sample rate because the
encoder cutoff does not move with the container — a 96 kHz FLAC transcoded
from MP3 still has its brickwall at ~20 kHz, not at 40 kHz.

Design decisions (vs. a naive single-highpass approach):

- **RMS over Peak** — Peak level is thrown off by a single click or transient.
  RMS measures sustained energy, which is what spectral content actually means.
- **Two-point steepness test** — Avoids false positives on electronic music or
  vintage masters that genuinely have no high-frequency content.  A natural
  roll-off is gradual; a codec brickwall drops 40-80 dB across just a few kHz.
- **Fixed analysis frequencies** — Codified in BRICKWALL_DROP_DB / LOW_ENERGY_DB
  / NO_HF_DB so the thresholds are easy to tune after field experience.
- **First 60 seconds only (-t 60)** — Keeps scans fast; the spectral signature
  is consistent across a track.

Guards:

- Overall RMS < LOW_ENERGY_DB (-50 dB) -> "Inconclusive (Low Energy)".
  Catches silence, ambient tracks, and long silent intros.
- RMS(highpass@18k) < NO_HF_DB (-72 dB) -> "Inconclusive (Low Energy)".
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
BRICKWALL_DROP_DB = 25  # min drop between HF bands to flag as a codec brickwall
LOW_ENERGY_DB = -50  # overall RMS below this = track too quiet to analyse
NO_HF_DB = -72  # RMS in lower HF band below this = no HF content to measure


def analyze_steepness(filepath: Path, sample_rate: int) -> dict[str, float | None] | None:
    """Run the two-point HF steepness test (see module docs for methodology).

    Uses a single ffmpeg call with asplit=3 so the file is decoded once.
    Returns {'rms_overall', 'rms_low', 'rms_high'} in dB, or None on failure.
    """
    if sample_rate < 44100:
        return None

    filter_graph = "asplit=3[a][b][c];[a]astats[aout];[b]highpass=f=18000,astats[bout];[c]highpass=f=21500,astats[cout]"

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
                "-map",
                "[bout]",
                "-map",
                "[cout]",
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
    if len(matches) % 3 != 0:
        return None

    per_astats = len(matches) // 3  # channels + 1
    if per_astats < 2:
        return None

    # Each astats outputs per_astats lines (per-channel then overall).
    # The three filter chains can complete in any order, but overall RMS
    # is always the highest value (no highpass), low band is in the middle,
    # and high band is the lowest.  Grab the last (overall) line from each
    # group and sort by value to assign them.
    def _parse(idx: int) -> float:
        v = matches[idx]
        return -200.0 if "inf" in v else float(v)

    values = [
        _parse(1 * per_astats - 1),
        _parse(2 * per_astats - 1),
        _parse(3 * per_astats - 1),
    ]
    values.sort(reverse=True)  # highest RMS first

    return {"rms_overall": values[0], "rms_low": values[1], "rms_high": values[2]}


def compute_verdict(codec: str, filepath: Path, sample_rate: int | None) -> tuple[str, int, bool]:
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
    rms_low = spec["rms_low"]
    rms_high = spec["rms_high"]
    assert rms_low is not None
    assert rms_high is not None

    if rms_overall is not None and rms_overall < LOW_ENERGY_DB:
        return ("Inconclusive (Low Energy)", GREY, True)

    if rms_low < NO_HF_DB:
        return ("Inconclusive (Low Energy)", GREY, True)

    drop = rms_low - rms_high
    if drop > BRICKWALL_DROP_DB:
        return ("Suspected Transcode (Lossy)", RED, False)

    return ("True Lossless", MAGENTA, False)
