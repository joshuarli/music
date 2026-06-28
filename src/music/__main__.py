"""spotify-playlist — fetch a Spotify playlist and resolve tracks via MusicBrainz.

Usage:
    python -m src.music <spotify_playlist_url>
    python -m src.music https://open.spotify.com/playlist/5nuGQ6fwPD2r0SMzoX2RAg

Output: {playlist_id}.json with playlist metadata, resolved, and unresolved tracks.
"""

import json
import sys

from src.music.http import RateLimiter, Session
from src.music.resolver import resolve_tracks
from src.music.spotify import SpotifyPublicPlaylist


def _extract_playlist_id(raw: str) -> str:
    if "open.spotify.com" in raw and "playlist" in raw:
        return raw.split("playlist/")[1].split("?")[0].split("/")[0]
    return raw


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m src.music <spotify_playlist_url>")
        sys.exit(1)

    url = sys.argv[1]
    playlist_id = _extract_playlist_id(url)

    print(f"Fetching playlist {playlist_id} ...")
    sp = SpotifyPublicPlaylist()
    meta, tracks = sp.get_playlist(url)
    print(f"  {meta['name']} by {meta['owner_name']} — {len(tracks)} tracks")

    print("Resolving via MusicBrainz ...")
    mb_session = Session(rate_limiter=RateLimiter())
    resolved, unresolved = resolve_tracks(mb_session, tracks)

    output = {
        "playlist_id": meta["playlist_id"],
        "playlist_name": meta["name"],
        "playlist_owner": meta["owner_name"],
        "description": meta.get("description", ""),
        "resolved": resolved,
        "unresolved": unresolved,
    }

    output_path = f"{playlist_id}.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n{output_path}: {len(resolved)} resolved, {len(unresolved)} unresolved")


if __name__ == "__main__":
    main()
