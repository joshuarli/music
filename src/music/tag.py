"""Fingerprint an audio file, look up its MusicBrainz metadata via AcoustID,
and write selected tags to the file with mutagen.
"""

import argparse
import json
import os
import subprocess
import sys
import termios
import urllib.parse
import urllib.request
from typing import Any

from mutagen import File as MutagenFile
from mutagen.easyid3 import EasyID3
from mutagen.easymp4 import EasyMP4
from mutagen.flac import FLAC
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4
from mutagen.oggvorbis import OggVorbis

TAG_FIELDS = ("title", "artist", "album", "date")


def get_audio_fingerprint(file_path):
    try:
        result = subprocess.run(
            ["fpcalc", "-json", file_path],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(result.stdout)
        return data.get("duration"), data.get("fingerprint")
    except subprocess.CalledProcessError as e:
        print(f"Error executing fpcalc: {e.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print("Error: 'fpcalc' binary not found. Please install chromaprint.", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError:
        print("Error: Failed to parse JSON from fpcalc output.", file=sys.stderr)
        sys.exit(1)


def fetch_acoustid_metadata(duration, fingerprint):
    """Queries AcoustID API for MusicBrainz recordings and releases."""
    try:
        api_key = os.environ["ACOUSTID_API_KEY"]
    except KeyError:
        print("error: ACOUSTID_API_KEY not set in environment", file=sys.stderr)
        sys.exit(1)

    params = {
        "client": api_key,
        "duration": int(duration),
        "fingerprint": fingerprint,
        "meta": "recordings releases",
    }

    url = f"https://api.acoustid.org/v2/lookup?{urllib.parse.urlencode(params)}"

    try:
        with urllib.request.urlopen(url) as response:
            if response.status != 200:
                print(f"API HTTP Error: {response.status}", file=sys.stderr)
                return None

            res_data = json.loads(response.read().decode("utf-8"))

            if res_data.get("status") != "ok":
                error_msg = res_data.get("error", {}).get("message", "Unknown API error")
                print(f"AcoustID API Error: {error_msg}", file=sys.stderr)
                return None

            return res_data.get("results", [])

    except Exception as e:
        print(f"Network or parsing error: {e}", file=sys.stderr)
        sys.exit(1)


def extract_metadata(result: dict[str, Any]) -> dict[str, str]:
    """Pull title, artist, album, date from one AcoustID result.

    Returns a dict with only the keys that could be extracted.
    """
    meta: dict[str, str] = {}
    recordings = result.get("recordings")
    if not recordings:
        return meta

    rec = recordings[0]
    if "title" in rec:
        meta["title"] = rec["title"]

    artists = rec.get("artists")
    if artists:
        meta["artist"] = ", ".join(a["name"] for a in artists if a.get("name"))

    releases = rec.get("releases")
    if releases:
        rel = releases[0]
        if "title" in rel:
            meta["album"] = rel["title"]
        date = rel.get("date", {})
        year = date.get("year") if isinstance(date, dict) else None
        if year:
            meta["date"] = str(year)

    return meta


def format_match_line(idx: int, result: dict[str, Any]) -> str:
    """Format one match as a single summary line."""
    score = result.get("score", 0) * 100
    meta = extract_metadata(result)
    title = meta.get("title", "?")
    artist = meta.get("artist", "?")
    album = meta.get("album", "")
    year = meta.get("date", "")

    album_str = f" [{album}]" if album else ""
    year_str = f" ({year})" if year else ""

    return f"  #{idx}  {score:3.0f}%  {title} — {artist}{album_str}{year_str}"


def _format_tags(filepath: str) -> dict[str, str]:
    """Read current tags from *filepath* using mutagen.

    Returns a dict mapping field name to string value (empty fields omitted).
    """
    try:
        audio = MutagenFile(filepath)
    except Exception:
        return {}

    if audio is None:
        return {}

    result: dict[str, str] = {}

    if isinstance(audio, (FLAC, OggVorbis)):
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


def format_diff(current: dict[str, str], new: dict[str, str]) -> list[str]:
    """Return formatted diff lines for fields that differ between current and new tags."""
    lines: list[str] = []
    all_keys = sorted(set(current.keys()) | set(new.keys()))
    if not all_keys:
        return lines

    # Determine column widths for alignment
    key_w = max(len(k) for k in all_keys)
    cur_w = max((len(current.get(k, "(none)")) for k in all_keys), default=0)
    new_w = max((len(new.get(k, "(none)")) for k in all_keys), default=0)

    for key in all_keys:
        cur_val = current.get(key)
        new_val = new.get(key)
        if cur_val == new_val:
            continue
        cur_display = cur_val or "(none)"
        new_display = new_val or "(none)"
        arrow = "+" if cur_val is None else "→"
        lines.append(f"    {key:<{key_w}s}  {cur_display:<{cur_w}s} {arrow} {new_display:<{new_w}s}")

    return lines


def _write_tags(filepath: str, metadata: dict[str, str]) -> None:
    """Write *metadata* to *filepath* using mutagen."""
    audio = MutagenFile(filepath)
    if audio is None:
        print(f"error: unsupported audio format: {filepath}", file=sys.stderr)
        sys.exit(1)

    if isinstance(audio, (FLAC, OggVorbis)):
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


def _raw_mode_enter() -> list[Any]:
    """Switch terminal to raw mode. Returns previous termios settings."""
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    new = termios.tcgetattr(fd)
    # Disable echo and canonical mode; read 1 byte at a time, no timeout
    new[3] = new[3] & ~(termios.ECHO | termios.ICANON)
    new[6][termios.VMIN] = 1
    new[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, new)
    return old


def _raw_mode_exit(old: list[Any]) -> None:
    """Restore terminal settings."""
    termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, old)


def _read_key() -> str | None:
    """Read a single keypress in raw mode.

    Returns 'up', 'down', 'enter', 'q', 'ctrl-c', or None for unhandled keys.
    """
    fd = sys.stdin.fileno()
    b = os.read(fd, 1)
    if b != b"\x1b":
        if b in (b"\r", b"\n"):
            return "enter"
        if b in (b"q", b"Q"):
            return "q"
        if b == b"\x03":
            return "ctrl-c"
        return None

    # Escape sequence — set a brief timeout so we don't hang on lone Esc
    new = termios.tcgetattr(fd)
    new[6][termios.VTIME] = 1  # 100 ms
    termios.tcsetattr(fd, termios.TCSANOW, new)
    try:
        b2 = os.read(fd, 1)
        if b2 == b"[":
            b3 = os.read(fd, 1)
            if b3 == b"A":
                return "up"
            if b3 == b"B":
                return "down"
    except Exception:
        pass
    finally:
        new[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, new)
    return None


def interactive_select(results: list[dict[str, Any]], filepath: str) -> int:
    """Arrow-key navigable match selector with live diff.

    Returns the selected index into *results*.
    """
    old_termios = _raw_mode_enter()
    selected = 0
    n = len(results)
    current_tags = _format_tags(filepath)

    def _build_lines(idx: int) -> list[str]:
        lines: list[str] = []
        lines.append("")
        for i, r in enumerate(results):
            line = format_match_line(i + 1, r)
            if i == idx:
                lines.append(f"\033[1m▶{line}\033[0m")
            else:
                lines.append(f"  {line}")
        lines.append("")

        meta = extract_metadata(results[idx])
        diff = format_diff(current_tags, meta)
        if diff:
            lines.append("  Changes:")
            lines.extend(diff)
        else:
            lines.append("  (no changes — tags already match)")
        lines.append("")

        lines.append("  \033[2m↑/↓ navigate  Enter select  q quit\033[0m")
        return lines

    def _render(lines: list[str]) -> int:
        for line in lines:
            sys.stdout.write(line + "\033[K\n")
        sys.stdout.flush()
        return len(lines)

    block_height = _render(_build_lines(selected))

    while True:
        key = _read_key()
        if key == "up":
            selected = max(0, selected - 1)
        elif key == "down":
            selected = min(n - 1, selected + 1)
        elif key == "enter":
            _raw_mode_exit(old_termios)
            return selected
        elif key in ("q", "ctrl-c"):
            _raw_mode_exit(old_termios)
            print("\nAborted.")
            sys.exit(0)
        else:
            continue

        # Move cursor up to start of interactive block and re-render
        sys.stdout.write(f"\033[{block_height}A")
        block_height = _render(_build_lines(selected))


def _fallback_select(results: list[dict[str, Any]]) -> int:
    """Non-TTY fallback: print numbered list and prompt for input."""
    for i, r in enumerate(results, 1):
        print(format_match_line(i, r))

    print()
    try:
        choice = input(f"Select [1-{len(results)}, q=quit]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        sys.exit(0)

    if choice.lower() == "q":
        print("Aborted.")
        sys.exit(0)

    try:
        idx = int(choice) - 1
        if 0 <= idx < len(results):
            return idx
    except ValueError:
        pass

    print(f"Invalid selection: {choice}")
    sys.exit(1)


def main() -> None:
    p = argparse.ArgumentParser(
        description="Fingerprint an audio file with fpcalc and look up its "
        "MusicBrainz metadata via the AcoustID API. "
        "Requires ACOUSTID_API_KEY in the environment.",
    )
    p.add_argument("file", help="audio file to fingerprint")
    p.add_argument("-y", "--yes", action="store_true", help="skip write confirmation")
    args = p.parse_args()

    print(f"Analyzing {args.file}...")
    duration, fingerprint = get_audio_fingerprint(args.file)

    if not (duration and fingerprint):
        return

    results = fetch_acoustid_metadata(duration, fingerprint) or []

    # Filter out results with no extractable metadata
    results = [r for r in results if extract_metadata(r)]

    if not results:
        print("No matches found for this audio fingerprint.")
        return

    if not sys.stdout.isatty():
        selected_idx = _fallback_select(results)
    else:
        selected_idx = interactive_select(results, args.file)

    selected = results[selected_idx]
    metadata = extract_metadata(selected)
    current_tags = _format_tags(args.file)
    diff = format_diff(current_tags, metadata)

    print()
    if not diff:
        print("No changes to write (tags already match).")
        return

    if not args.yes:
        response = input("Write these tags? [y/N]: ").strip().lower()
        if response not in ("y", "yes"):
            print("Aborted.")
            return

    _write_tags(args.file, metadata)
    print(f"Wrote tags to {args.file}.")


if __name__ == "__main__":
    main()
