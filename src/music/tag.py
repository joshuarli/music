"""Fingerprint an audio file and look up its MusicBrainz metadata via AcoustID."""

import argparse
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request


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


def print_results(results):
    """Parses and pretty-prints the track pipeline results."""
    if not results:
        print("No matches found for this audio fingerprint.")
        return

    for idx, result in enumerate(results, 1):
        score = result.get("score", 0) * 100
        print(f"\n--- Match Profile #{idx} (Score: {score:.1f}%) ---")

        recordings = result.get("recordings", [])
        if not recordings:
            print("  No MusicBrainz Recording IDs linked to this fingerprint.")
            continue

        for rec in recordings:
            print(f"\n🎵 MusicBrainz Recording ID: {rec.get('id')}")
            print(f"   Title: {rec.get('title')}")

            if "artists" in rec:
                artists = ", ".join([a["name"] for a in rec["artists"]])
                print(f"   Artist(s): {artists}")

            releases = rec.get("releases", [])
            if releases:
                print("   📦 Associated Releases:")
                for rel in releases:
                    year = rel.get("date", {}).get("year", "Unknown Year")
                    print(f"     - [Release ID: {rel.get('id')}] {rel.get('title')} ({year})")


def main() -> None:
    p = argparse.ArgumentParser(
        description="Fingerprint an audio file with fpcalc and look up its "
        "MusicBrainz metadata via the AcoustID API. "
        "Requires ACOUSTID_API_KEY in the environment.",
    )
    p.add_argument("file", help="audio file to fingerprint")
    args = p.parse_args()

    print(f"Analyzing {args.file}...")
    duration, fingerprint = get_audio_fingerprint(args.file)

    if duration and fingerprint:
        results = fetch_acoustid_metadata(duration, fingerprint)
        print_results(results)


if __name__ == "__main__":
    main()
