"""Tag reading and writing via mutagen.  Shared by scan and tag — the single
place that touches mutagen for metadata I/O.
"""

import os
import subprocess
import sys

from mutagen import File as MutagenFile
from mutagen.easyid3 import EasyID3
from mutagen.easymp4 import EasyMP4
from mutagen.flac import FLAC
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis

# Fields we care about, in display order
TAG_FIELDS = ("title", "artist", "album", "date", "tracknumber", "albumartist", "genre")


def read_tags(filepath: str) -> dict[str, str]:
    """Read tags from *filepath* using mutagen.

    Returns a dict mapping field name to string value.  Fields that are
    absent or empty are omitted from the dict.
    """
    try:
        audio = MutagenFile(filepath)
    except Exception:
        return {}

    if audio is None:
        return {}

    result: dict[str, str] = {}

    if isinstance(audio, (FLAC, OggVorbis, OggOpus)):
        tags = audio.tags
        if tags is None:
            return result
        for key in TAG_FIELDS:
            vals = tags.get(key)
            if vals:
                result[key] = vals[0] if isinstance(vals, list) else str(vals)
    elif isinstance(audio, MP3):
        try:
            easy = EasyID3(filepath)
        except Exception:
            return result
        for key in TAG_FIELDS:
            vals = easy.get(key)
            if vals:
                result[key] = vals[0] if isinstance(vals, list) else str(vals)
    elif isinstance(audio, MP4):
        try:
            easy = EasyMP4(filepath)
        except Exception:
            return result
        for key in TAG_FIELDS:
            vals = easy.get(key)
            if vals:
                result[key] = vals[0] if isinstance(vals, list) else str(vals)

    return result


def _probe_audio_codec(filepath: str) -> str | None:
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


def _remux_audio(filepath: str) -> str | None:
    """Remux audio from *filepath* into a tag-friendly container.

    Uses ffmpeg to copy the first audio stream (no re-encoding) into a
    container that mutagen can write tags to.  Returns the path to the
    new file, or None on failure.
    """
    codec = _probe_audio_codec(filepath)
    if not codec:
        return None

    ext_map = {
        "opus": ".ogg",
        "vorbis": ".ogg",
    }
    ext = ext_map.get(codec)
    if not ext:
        return None

    base = os.path.splitext(filepath)[0]
    new_path = base + ext

    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", filepath, "-c:a", "copy", "-vn", new_path],
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError:
        return None

    return new_path


def write_tags(filepath: str, metadata: dict[str, str]) -> str:
    """Write *metadata* to *filepath* using mutagen.

    Returns the filepath that was actually written to (may differ from the
    input if a format conversion was needed).
    """
    audio = MutagenFile(filepath)

    if audio is None:
        new_path = _remux_audio(filepath)
        if new_path is None:
            print(f"error: unsupported audio format: {filepath}", file=sys.stderr)
            sys.exit(1)
        os.remove(filepath)
        filepath = new_path
        audio = MutagenFile(filepath)
        if audio is None:
            print(f"error: unsupported audio format after remux: {filepath}", file=sys.stderr)
            sys.exit(1)

    if isinstance(audio, (FLAC, OggVorbis, OggOpus)):
        if audio.tags is None:
            audio.add_tags()
        for key in TAG_FIELDS:
            if key in metadata:
                audio.tags[key] = [metadata[key]]
        audio.save()
    elif isinstance(audio, MP3):
        easy = EasyID3(filepath)
        for key in TAG_FIELDS:
            if key in metadata:
                easy[key] = [metadata[key]]
        easy.save()
    elif isinstance(audio, MP4):
        easy = EasyMP4(filepath)
        for key in TAG_FIELDS:
            if key in metadata:
                easy[key] = [metadata[key]]
        easy.save()
    else:
        print(f"error: unsupported audio format: {filepath}", file=sys.stderr)
        sys.exit(1)

    return filepath
