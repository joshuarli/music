"""Fingerprint an audio file, look up its MusicBrainz metadata via AcoustID,
and write selected tags to the file with mutagen.

Also supports a --read mode to inspect existing tags without any network call.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import unicodedata
import urllib.parse
import urllib.request
from typing import Any

from .tags import TAG_FIELDS, read_tags, write_tags
from .ui import bold, dim, select_interactive


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


def _fetch_musicbrainz(recording_mbid: str) -> dict[str, str]:
    """Query MusicBrainz API for track number, genre, and album artist.

    Returns a dict with only the keys that could be extracted.
    """
    meta: dict[str, str] = {}
    url = f"https://musicbrainz.org/ws/2/recording/{recording_mbid}?inc=genres+artists+releases&fmt=json"
    req = urllib.request.Request(url, headers={"User-Agent": "music-tag/0.1"})

    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        return meta

    # Genre — take top 3 by count
    genres = data.get("genres", [])
    if genres:
        top = sorted(genres, key=lambda g: g.get("count", 0), reverse=True)
        meta["genre"] = ", ".join(g["name"] for g in top[:3] if g.get("name"))

    # Track number — search releases for a track whose recording ID matches
    releases = data.get("releases", [])
    for rel in releases:
        for medium in rel.get("media", []):
            for track in medium.get("tracks", []):
                if track.get("recording", {}).get("id") == recording_mbid:
                    tn = track.get("number")
                    if tn:
                        meta["tracknumber"] = str(tn)
                    break
            if "tracknumber" in meta:
                break
        if "tracknumber" in meta:
            break

    # Album artist — from the first release's artist-credit
    if releases:
        ac = releases[0].get("artist-credit", [])
        if ac:
            meta["albumartist"] = "".join(a.get("name", "") + a.get("joinphrase", "") for a in ac)

    return meta


_YT_ID_RE = re.compile(r"\s*\[[A-Za-z0-9_-]{11}\]$")
_TRACK_NUM_RE = re.compile(r"^\d{1,3}\s*[-–—]\s*")


def _normalize_filename(filepath: str) -> str:
    """Extract a plausible song title from a filename.

    Removes the YouTube ID + extension suffix, leading track number, and
    replaces fullwidth punctuation with ASCII equivalents.
    """
    name = os.path.splitext(os.path.basename(filepath))[0]

    # Remove [youtube-id] suffix if present (before the extension)
    name = _YT_ID_RE.sub("", name)

    # Remove leading track number (e.g. "09 - ")
    name = _TRACK_NUM_RE.sub("", name)

    # Normalize fullwidth / confusable punctuation to ASCII
    name = unicodedata.normalize("NFKC", name)

    # Collapse whitespace
    name = " ".join(name.split())

    return name.strip()


def _search_musicbrainz(query: str) -> list[dict[str, Any]]:
    """Search MusicBrainz recordings and return results in AcoustID-compatible format."""
    params = urllib.parse.urlencode({"query": query, "fmt": "json", "limit": "10"})
    url = f"https://musicbrainz.org/ws/2/recording/?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "music-tag/0.1"})

    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        return []

    recordings = data.get("recordings", [])
    return [_mb_recording_to_result(r) for r in recordings if r.get("title")]


def _mb_recording_to_result(recording: dict[str, Any]) -> dict[str, Any]:
    """Convert a MusicBrainz search recording to AcoustID result format."""
    artist_credit = recording.get("artist-credit", [])
    artists = [{"name": a["name"]} for a in artist_credit if a.get("name")]

    releases = recording.get("releases", [])
    mb_releases = []
    for rel in releases:
        r: dict[str, Any] = {}
        if rel.get("title"):
            r["title"] = rel["title"]
        date = rel.get("date")
        if date:
            # MB dates are strings like "2024" or "2024-05"
            year = date.split("-")[0] if isinstance(date, str) else None
            if year:
                r["date"] = {"year": int(year)}
        if r:
            mb_releases.append(r)

    score = recording.get("score", 100) / 100.0

    return {
        "score": score,
        "recordings": [
            {
                "id": recording.get("id"),
                "title": recording.get("title"),
                "artists": artists,
                "releases": mb_releases,
            }
        ],
    }


def _fallback_search(filepath: str, current_tags: dict[str, str]) -> list[dict[str, Any]]:
    """Fallback when AcoustID fingerprint lookup returns no matches.

    Strategy:
    1. Search MusicBrainz using file metadata (title, artist).
    2. Search MusicBrainz using a normalized filename as the song title.
    """
    # Strategy 1: metadata from file tags.
    # Only recording + artist — the album name from file tags rarely matches
    # MusicBrainz canonical release titles, so including it blocks valid hits.
    query_parts = []
    if current_tags.get("title"):
        query_parts.append(f"recording:({current_tags['title']})")
    if current_tags.get("artist"):
        query_parts.append(f"artist:({current_tags['artist']})")

    if query_parts:
        query = " AND ".join(query_parts)
        print("  No fingerprint match. Searching MusicBrainz with file metadata...")
        results = _search_musicbrainz(query)
        if results:
            print(f"  Found {len(results)} result(s) via metadata search.")
            return results

    # Strategy 2: normalized filename as title (plain query, not phrase-locked)
    title = _normalize_filename(filepath)
    if title:
        print(f"  Searching MusicBrainz for: {title}")
        results = _search_musicbrainz(title)
        if results:
            print(f"  Found {len(results)} result(s) via filename search.")
            return results

    return []


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


def _print_tags(filepath: str) -> None:
    """Pretty-print all tags from *filepath*."""
    tags = read_tags(filepath)
    if not tags:
        print("  (no tags)")
        return

    key_w = max(len(k) for k in tags)
    for key in TAG_FIELDS:
        val = tags.get(key)
        if val:
            print(f"  {bold(key):<{key_w + 11}s} {val}")


def format_diff(current: dict[str, str], new: dict[str, str]) -> list[str]:
    """Return formatted diff lines for fields that differ between current and new tags."""
    lines: list[str] = []
    all_keys = sorted(set(current.keys()) | set(new.keys()))
    if not all_keys:
        return lines

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


def interactive_select(results: list[dict[str, Any]], filepath: str) -> int:
    """Arrow-key navigable match selector with live diff.

    Returns the selected index into *results*.
    """
    current_tags = read_tags(filepath)

    def render(idx: int) -> list[str]:
        lines: list[str] = [""]
        for i, r in enumerate(results):
            line = format_match_line(i + 1, r)
            if i == idx:
                lines.append(bold(f"▶{line}"))
            else:
                lines.append(f"  {line}")
        lines.append("")

        meta = extract_metadata(results[idx])
        diff = format_diff(current_tags, meta)
        if diff:
            lines.append("  Changes (AcoustID):")
            lines.extend(diff)
        else:
            lines.append("  (no changes — tags already match)")
        lines.append("")
        lines.append(dim("  ↑/↓ navigate  Enter write  s skip  q quit"))
        return lines

    return select_interactive(render, len(results))


def _fallback_select(results: list[dict[str, Any]]) -> int:
    """Non-TTY fallback: print numbered list and prompt for input."""
    for i, r in enumerate(results, 1):
        print(format_match_line(i, r))

    print()
    try:
        choice = input(f"Select [1-{len(results)}, q=quit]: ").strip()
    except EOFError, KeyboardInterrupt:
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
    p.add_argument("-f", "--force", action="store_true", help="re-tag even if metadata is already complete")
    p.add_argument("--read", action="store_true", help="print current tags and exit (no network)")
    args = p.parse_args()

    if args.read:
        print(f"Tags in {args.file}:")
        _print_tags(args.file)
        return

    current_tags = read_tags(args.file)
    if not args.force:
        missing = [k for k in TAG_FIELDS if k not in current_tags]
        if not missing:
            print(f"All tags already complete, skipping {args.file}. Use --force to override.")
            return

    print(f"Analyzing {args.file}...")
    duration, fingerprint = get_audio_fingerprint(args.file)

    if not (duration and fingerprint):
        return

    results = fetch_acoustid_metadata(duration, fingerprint) or []

    # Filter out results with no extractable metadata
    results = [r for r in results if extract_metadata(r)]

    if not results:
        results = _fallback_search(args.file, current_tags)
        if not results:
            print("No matches found for this audio fingerprint.")
            return

    if not sys.stdout.isatty():
        selected_idx = _fallback_select(results)
    else:
        selected_idx = interactive_select(results, args.file)

    selected = results[selected_idx]
    metadata = extract_metadata(selected)

    # Enrich with MusicBrainz data (track number, genre, album artist)
    recordings = selected.get("recordings", [])
    if recordings:
        mbid = recordings[0].get("id")
        if mbid:
            mb_meta = _fetch_musicbrainz(mbid)
            metadata.update(mb_meta)

    current_tags = read_tags(args.file)
    diff = format_diff(current_tags, metadata)

    print()
    if not diff:
        print("No changes to write (tags already match).")
        return

    if not sys.stdout.isatty() and not args.yes:
        response = input("Write these tags? [y/N]: ").strip().lower()
        if response not in ("y", "yes"):
            print("Aborted.")
            return

    actual_path = write_tags(args.file, metadata)
    print(f"Wrote tags to {actual_path}.")


if __name__ == "__main__":
    main()
