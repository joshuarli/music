"""Audio transcoding via ffmpeg.  Convert audio files to high-quality AAC
in an M4A container, preserving channel layout and copying metadata.
"""

import argparse
import json
import os
import subprocess
import sys
from typing import Any

# Prefer Apple AudioToolbox encoder on macOS (hardware-accelerated, higher quality)
_AAC_ENCODER = "aac_at" if sys.platform == "darwin" else "aac"

AAC_EXT = ".m4a"
_BITRATE = "320k"


def probe_audio(filepath: str) -> dict[str, Any] | None:
    """Return audio stream info (codec_name, channels, sample_rate) from ffprobe.

    Returns None if no audio stream could be found or ffprobe fails.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=codec_name,channels,sample_rate",
                "-of",
                "json",
                filepath,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(result.stdout)
        streams = data.get("streams", [])
        return streams[0] if streams else None
    except Exception:
        return None


def probe_codec(filepath: str) -> str | None:
    """Return the audio codec name from ffprobe, or None on failure."""
    info = probe_audio(filepath)
    return info.get("codec_name") if info else None


def transcode_to_aac(filepath: str, dst: str | None = None) -> str | None:
    """Transcode *filepath* to AAC at 320k in an M4A container.

    Preserves channel layout and copies metadata from the source.
    If *dst* is given, writes there; otherwise derives the path from
    *filepath* by replacing the extension with .m4a.
    Returns the path to the new file, or None on failure.
    """
    if dst is None:
        base = os.path.splitext(filepath)[0]
        dst = base + AAC_EXT

    try:
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-y",
                "-i",
                filepath,
                "-c:a",
                _AAC_ENCODER,
                "-b:a",
                _BITRATE,
                "-vn",
                "-map_metadata",
                "0",
                dst,
            ],
            stdout=subprocess.DEVNULL,
            check=True,
        )
    except subprocess.CalledProcessError:
        return None

    return dst


def main() -> None:
    p = argparse.ArgumentParser(description="Transcode audio files to high-quality AAC in an M4A container.")
    p.add_argument("file", help="audio file to transcode")
    p.add_argument("-o", "--output", help="output path (default: <input>.m4a)")
    args = p.parse_args()

    info = probe_audio(args.file)
    if info is None:
        print(f"error: no audio stream found in {args.file}", file=sys.stderr)
        sys.exit(1)

    codec = info.get("codec_name", "?")
    channels = info.get("channels")
    ch_map = {1: "mono", 2: "stereo", 6: "5.1", 8: "7.1"}
    ch_label = ch_map.get(channels, f"{channels}ch") if channels else "?"

    print(f"Source: {codec}, {ch_label}")

    result = transcode_to_aac(args.file, dst=args.output)
    if result is None:
        print("error: transcode failed", file=sys.stderr)
        sys.exit(1)

    print(f"Wrote: {result}")


if __name__ == "__main__":
    main()
