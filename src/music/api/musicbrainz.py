"""MusicBrainz API client — query recordings, fetch genre/artist/track metadata.

API docs: https://musicbrainz.org/doc/MusicBrainz_API
"""

import json
import urllib.parse
import urllib.request
from typing import Any

_USER_AGENT = "music-tag/0.1"


def _recording_to_result(recording: dict[str, Any]) -> dict[str, Any]:
    """Convert a MusicBrainz search recording to AcoustID-compatible result format."""
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


def search(query: str) -> list[dict[str, Any]]:
    """Search MusicBrainz recordings and return results in AcoustID-compatible format."""
    params = urllib.parse.urlencode({"query": query, "fmt": "json", "limit": "10"})
    url = f"https://musicbrainz.org/ws/2/recording/?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})

    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        return []

    recordings = data.get("recordings", [])
    return [_recording_to_result(r) for r in recordings if r.get("title")]


def fetch_recording(recording_mbid: str) -> dict[str, str]:
    """Query MusicBrainz API for track number, genre, and album artist.

    Returns a dict with only the keys that could be extracted.
    """
    meta: dict[str, str] = {}
    url = f"https://musicbrainz.org/ws/2/recording/{recording_mbid}?inc=genres+artists+releases&fmt=json"
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})

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
