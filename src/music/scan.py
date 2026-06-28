"""Walk a directory for audio files and report codec, bitrate, cover art,
metadata, and a lossy-transcode verdict.

Uses ffprobe and ffmpeg for all analysis — file extensions are ignored for
codec detection.  Prints one line per file with ANSI 256-color output.
"""

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .constants import (
    AUDIO_EXTENSIONS,
    BLUE,
    DSD,
    GOLD,
    GREEN,
    GREY,
    LOSSLESS,
    LOSSY_ANCIENT,
    LOSSY_HIGH,
    LOSSY_STANDARD,
    MAGENTA,
    RED,
    TAG_NAMES,
)
from .verdict import VERDICT_W, compute_verdict

# Column widths
CODEC_W = 7
RATE_W = 7
COVER_W = 5
TAGS_W = 20


def _c(code: int) -> str:
    return f"\033[38;5;{code}m"


def _bold() -> str:
    return "\033[1m"


def _dim() -> str:
    return "\033[2m"


def _rst() -> str:
    return "\033[0m"


def colored(text: str, code: int, *, bold: bool = False, dim: bool = False) -> str:
    parts = [_c(code)]
    if bold:
        parts.append(_bold())
    if dim:
        parts.append(_dim())
    parts.append(text)
    parts.append(_rst())
    return "".join(parts)


def codec_color(codec: str) -> int:
    """Return a 256-color code for a codec name (gaming quality tiers)."""
    cl = codec.lower()
    if cl in DSD or cl in LOSSLESS:
        return MAGENTA
    if cl in LOSSY_HIGH:
        return GOLD
    if cl in LOSSY_STANDARD:
        return BLUE
    if cl in LOSSY_ANCIENT:
        return RED
    return GREY


def bitrate_color(kbps: int, codec: str) -> int:
    """Return a colour for a bitrate, contextualised by codec type."""
    cl = codec.lower()
    if cl in LOSSLESS or cl in DSD:
        return MAGENTA  # bitrate is just a compression metric here
    if kbps >= 256:
        return GOLD
    if kbps >= 192:
        return BLUE
    if kbps >= 128:
        return GREEN
    if kbps >= 64:
        return GREY
    return RED


def probe(filepath: Path) -> dict[str, Any] | None:
    """Run ffprobe on *filepath* and return parsed JSON, or None on failure."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(filepath),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired, json.JSONDecodeError, OSError:
        return None


def find_audio_stream(streams: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the first real audio stream, skipping attached-pic masquerading as audio."""
    for s in streams:
        if s.get("codec_type") == "audio" and not s.get("disposition", {}).get("attached_pic"):
            return s
    for s in streams:
        if s.get("codec_type") == "audio":
            return s
    return None


def has_cover_art(streams: list[dict[str, Any]]) -> bool:
    """Return True if any stream looks like embedded cover art."""
    for s in streams:
        if s.get("disposition", {}).get("attached_pic") == 1:
            return True
        if s.get("codec_type") == "video" and s.get("codec_name") in {"mjpeg", "png", "bmp", "gif"}:
            return True
    return False


def get_tag(tags: dict[str, str], *names: str) -> str | None:
    """Return the first matching tag value, trying each *name* in order (case-insensitive fallback)."""
    for name in names:
        if name in tags and tags[name].strip():
            return tags[name].strip()
    name_lower = [n.lower() for n in names]
    for k, v in tags.items():
        if k.lower() in name_lower and v.strip():
            return v.strip()
    return None


def fmt_codec(codec: str) -> str:
    """Format a codec name: truncated to CODEC_W, right-justified, coloured."""
    display = codec.upper()[:CODEC_W]
    return colored(f"{display:>{CODEC_W}s}", codec_color(codec), bold=True)


def fmt_bitrate(bitrate_bps: int | None, codec: str) -> str:
    """Format a bitrate: right-justified to RATE_W, coloured by quality tier."""
    if bitrate_bps is None:
        return colored(f"{'?':>{RATE_W}s}", 240)
    kbps = int(bitrate_bps / 1000)
    text = f"{kbps}k"
    return colored(f"{text:>{RATE_W}s}", bitrate_color(kbps, codec), bold=True)


def fmt_cover(present: bool) -> str:
    """Format cover art indicator."""
    if present:
        return colored(f"{'yes':>{COVER_W}s}", GREEN)
    else:
        return colored(f"{'no':>{COVER_W}s}", GREY)


def fmt_tags(tags: dict[str, str]) -> str:
    """Build the tags column: show only missing tag names, or a dim '·' when all present."""
    missing = []
    for label, names in TAG_NAMES:
        if get_tag(tags, *names) is None:
            missing.append(label)

    if not missing:
        return colored(f"{'·':<{TAGS_W}s}", GREEN, dim=True)

    text = ", ".join(missing)
    return colored(f"{text:<{TAGS_W}s}", RED, bold=True)


def fmt_verdict(text: str, color: int, *, dim: bool = False) -> str:
    """Format the verdict column: left-justified, coloured."""
    return colored(f"{text:<{VERDICT_W}s}", color, bold=not dim, dim=dim)


def collect_files(paths: list[str]) -> list[tuple[Path, str]]:
    """Turn a mixed list of dirs and file paths into (Path, display_name) pairs.

    For directories, recurses through files matching AUDIO_EXTENSIONS and shows
    paths relative to that directory.  For individual files the extension filter
    is skipped — pass anything and ffprobe will sort it out.
    """
    result: list[tuple[Path, str]] = []
    seen: set[Path] = set()

    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if not f.is_file():
                    continue
                if f.suffix.lower() not in AUDIO_EXTENSIONS:
                    continue
                key = f.resolve()
                if key not in seen:
                    seen.add(key)
                    result.append((f, str(f.relative_to(p))))
        elif p.is_file():
            key = p.resolve()
            if key not in seen:
                seen.add(key)
                result.append((p, p.name))

    return result


def _process_file(args: tuple[Path, str]) -> str | None:
    fp, display_path = args
    data = probe(fp)
    if data is None:
        return None

    streams = data.get("streams", [])
    fmt = data.get("format", {})
    tags = fmt.get("tags", {})

    audio = find_audio_stream(streams)
    if audio is None:
        return None

    codec = audio.get("codec_name", "?")
    bitrate_str = audio.get("bit_rate") or fmt.get("bit_rate")
    bitrate = int(bitrate_str) if bitrate_str else None
    cover = has_cover_art(streams)

    sample_rate_str = audio.get("sample_rate")
    sample_rate = int(sample_rate_str) if sample_rate_str else None
    verdict_text, verdict_color, verdict_dim = compute_verdict(codec, fp, sample_rate)

    return (
        f"  {fmt_codec(codec)}  {fmt_bitrate(bitrate, codec)}  "
        f"{fmt_verdict(verdict_text, verdict_color, dim=verdict_dim)}  "
        f"{fmt_cover(cover)}  "
        f"{fmt_tags(tags)}  "
        f"{colored(display_path, 51)}"
    )


def main() -> None:
    jobs = os.cpu_count() or 4
    paths: list[str] = []
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-j", "--jobs"):
            i += 1
            if i < len(args):
                jobs = int(args[i])
        elif a.startswith("-j"):
            jobs = int(a[2:])
        elif a.startswith("--jobs="):
            jobs = int(a.split("=", 1)[1])
        else:
            paths.append(a)
        i += 1

    if not paths:
        paths = ["."]

    try:
        subprocess.run(
            ["ffprobe", "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    except subprocess.CalledProcessError, FileNotFoundError:
        print("error: ffprobe not found. Install ffmpeg.", file=sys.stderr)
        sys.exit(1)

    files = collect_files(paths)

    if not files:
        print("No audio files found.")
        return

    header = (
        f"  {'CODEC':>{CODEC_W}s}  {'RATE':>{RATE_W}s}  {'VERDICT':<{VERDICT_W}s}  "
        f"{'COVER':>{COVER_W}s}  {'MISSING':<{TAGS_W}s}  PATH"
    )
    print(colored(header, 15, bold=True))
    print(colored("  " + "─" * (len(header) - 2), 240))

    with ThreadPoolExecutor(max_workers=jobs) as ex:
        for line in ex.map(_process_file, files):
            if line is not None:
                print(line)
