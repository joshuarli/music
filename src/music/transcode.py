"""Audio transcoding via ffmpeg.  Used by tag to convert opus/vorbis audio
into an AAC/M4A container that mutagen can write tags to (and iOS can play).
"""

import os
import subprocess
import sys

from mutagen import File as MutagenFile
from mutagen.mp4 import MP4

# Map ffprobe codec names to the mutagen type the transcoded file should become
_CODEC_TARGET: dict[str, type] = {
    "opus": MP4,
    "vorbis": MP4,
}

# Output extension for the AAC/M4A container
AAC_EXT = ".m4a"

# Prefer Apple AudioToolbox encoder on macOS (hardware-accelerated, higher quality)
_AAC_ENCODER = "aac_at" if sys.platform == "darwin" else "aac"


def probe_codec(filepath: str) -> str | None:
    """Return the audio codec name from ffprobe, or None on failure."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=codec_name",
                "-of",
                "csv=p=0",
                filepath,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip() or None
    except Exception:
        return None


def transcode_to_aac(filepath: str, dst: str | None = None) -> str | None:
    """Transcode *filepath* to AAC at 256k in an M4A container.

    If *dst* is given, writes there; otherwise derives the path from *filepath*.
    Returns the path to the new file, or None on failure.
    """
    codec = probe_codec(filepath)
    if not codec:
        return None

    target_type = _CODEC_TARGET.get(codec)
    if target_type is None:
        return None

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
                "256k",
                "-vn",
                dst,
            ],
            stdout=subprocess.DEVNULL,
            check=True,
        )
    except subprocess.CalledProcessError:
        return None

    # Validate the transcoded file opens as the expected mutagen type
    if not isinstance(MutagenFile(dst), target_type):
        return None

    return dst
