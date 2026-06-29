import json
from unittest.mock import MagicMock, patch

import pytest

from music.tag import (
    _fallback_select,
    _fetch_musicbrainz,
    _print_tags,
    extract_metadata,
    format_diff,
    format_match_line,
    get_audio_fingerprint,
)


class TestExtractMetadata:
    def test_full_record(self):
        result = {
            "recordings": [
                {
                    "title": "Song Title",
                    "artists": [{"name": "Artist One"}, {"name": "Artist Two"}],
                    "releases": [
                        {
                            "title": "Album Name",
                            "date": {"year": 2024},
                        }
                    ],
                }
            ]
        }
        meta = extract_metadata(result)
        assert meta["title"] == "Song Title"
        assert meta["artist"] == "Artist One, Artist Two"
        assert meta["album"] == "Album Name"
        assert meta["date"] == "2024"

    def test_no_recordings(self):
        assert extract_metadata({}) == {}
        assert extract_metadata({"recordings": []}) == {}

    def test_minimal_record(self):
        result = {"recordings": [{"title": "Just Title"}]}
        meta = extract_metadata(result)
        assert meta == {"title": "Just Title"}

    def test_artist_no_name(self):
        result = {"recordings": [{"artists": [{"role": "composer"}]}]}
        meta = extract_metadata(result)
        # artist key is present but empty — names are filtered by a.get("name")
        assert meta["artist"] == ""


class TestFormatMatchLine:
    def test_full_line(self):
        result = {
            "score": 0.95,
            "recordings": [
                {
                    "title": "The Song",
                    "artists": [{"name": "The Artist"}],
                    "releases": [{"title": "The Album", "date": {"year": 2023}}],
                }
            ],
        }
        line = format_match_line(1, result)
        assert "95%" in line
        assert "The Song" in line
        assert "The Artist" in line
        assert "The Album" in line
        assert "2023" in line

    def test_minimal(self):
        result = {"score": 0.5, "recordings": [{"title": "Song"}]}
        line = format_match_line(1, result)
        assert "50%" in line
        assert "Song" in line
        assert "?" in line  # no artist


class TestFormatDiff:
    def test_added_field(self):
        current = {}
        new = {"title": "New Title"}
        lines = format_diff(current, new)
        assert len(lines) == 1
        assert "+" in lines[0]

    def test_changed_field(self):
        current = {"title": "Old"}
        new = {"title": "New"}
        lines = format_diff(current, new)
        assert len(lines) == 1
        assert "→" in lines[0]

    def test_no_changes(self):
        current = {"title": "Same"}
        new = {"title": "Same"}
        lines = format_diff(current, new)
        assert len(lines) == 0

    def test_mixed(self):
        current = {"title": "Old Title", "artist": "Same Artist"}
        new = {"title": "New Title", "artist": "Same Artist", "album": "New Album"}
        lines = format_diff(current, new)
        assert len(lines) == 2
        # title changed, album added, artist unchanged


class TestFetchAcoustidMetadata:
    def test_success(self, monkeypatch):
        monkeypatch.setenv("ACOUSTID_API_KEY", "test-key")
        api_response = json.dumps({"status": "ok", "results": [{"score": 0.95}]})

        with patch("urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.read.return_value = api_response.encode()
            mock_resp.__enter__.return_value = mock_resp
            mock_open.return_value = mock_resp

            from music.tag import fetch_acoustid_metadata

            results = fetch_acoustid_metadata(200.0, "fp123")
            assert results == [{"score": 0.95}]

    def test_no_api_key(self, monkeypatch):
        monkeypatch.delenv("ACOUSTID_API_KEY", raising=False)
        with pytest.raises(SystemExit):
            from music.tag import fetch_acoustid_metadata

            fetch_acoustid_metadata(200.0, "fp123")

    def test_api_error_status(self, monkeypatch):
        monkeypatch.setenv("ACOUSTID_API_KEY", "test-key")
        api_response = json.dumps({"status": "error", "error": {"message": "Bad fingerprint"}})

        with patch("urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.read.return_value = api_response.encode()
            mock_resp.__enter__.return_value = mock_resp
            mock_open.return_value = mock_resp

            from music.tag import fetch_acoustid_metadata

            results = fetch_acoustid_metadata(200.0, "fp123")
            assert results is None

    def test_http_error(self, monkeypatch):
        monkeypatch.setenv("ACOUSTID_API_KEY", "test-key")
        with patch("urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.status = 500
            mock_resp.__enter__.return_value = mock_resp
            mock_open.return_value = mock_resp

            from music.tag import fetch_acoustid_metadata

            results = fetch_acoustid_metadata(200.0, "fp123")
            assert results is None


class TestGetAudioFingerprint:
    def test_success(self):
        stdout = json.dumps({"duration": 200.5, "fingerprint": "abc123"})
        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.stdout = stdout
            mock_run.return_value = mock_result

            duration, fp = get_audio_fingerprint("test.flac")
            assert duration == 200.5
            assert fp == "abc123"

    def test_fpcalc_not_found(self):
        with patch("subprocess.run", side_effect=FileNotFoundError), pytest.raises(SystemExit):
            get_audio_fingerprint("test.flac")

    def test_fpcalc_error(self):
        import subprocess

        err = subprocess.CalledProcessError(1, "fpcalc")
        err.stderr = "fpcalc error message"
        with patch("subprocess.run", side_effect=err), pytest.raises(SystemExit):
            get_audio_fingerprint("test.flac")

    def test_fpcalc_json_error(self):
        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.stdout = "not valid json"
            mock_run.return_value = mock_result
            with pytest.raises(SystemExit):
                get_audio_fingerprint("test.flac")


class TestFetchMusicBrainz:
    def test_returns_genre_and_track(self):
        mb_json = {
            "genres": [
                {"name": "Rock", "count": 100},
                {"name": "Alternative", "count": 50},
                {"name": "Indie", "count": 30},
                {"name": "Pop", "count": 10},
            ],
            "releases": [
                {
                    "media": [
                        {
                            "tracks": [
                                {"number": "5", "recording": {"id": "rec-id"}},
                                {"number": "6", "recording": {"id": "other"}},
                            ]
                        }
                    ],
                    "artist-credit": [{"name": "Album Artist", "joinphrase": ""}],
                }
            ],
        }
        with patch("urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(mb_json).encode()
            mock_resp.__enter__.return_value = mock_resp
            mock_open.return_value = mock_resp

            meta = _fetch_musicbrainz("rec-id")
            assert meta["genre"] == "Rock, Alternative, Indie"
            assert meta["tracknumber"] == "5"
            assert meta["albumartist"] == "Album Artist"

    def test_handles_http_error(self):
        with patch("urllib.request.urlopen", side_effect=Exception):
            meta = _fetch_musicbrainz("rec-id")
            assert meta == {}

    def test_no_genres(self):
        mb_json = {"releases": []}
        with patch("urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(mb_json).encode()
            mock_resp.__enter__.return_value = mock_resp
            mock_open.return_value = mock_resp

            meta = _fetch_musicbrainz("rec-id")
            assert "genre" not in meta


class TestFallbackSelect:
    def test_valid_selection(self):
        results = [{"score": 0.9, "recordings": [{"title": "Song"}]}]
        with patch("builtins.input", return_value="1"):
            idx = _fallback_select(results)
            assert idx == 0

    def test_quit(self):
        results = [{"score": 0.9, "recordings": [{"title": "Song"}]}]
        with patch("builtins.input", return_value="q"), pytest.raises(SystemExit):
            _fallback_select(results)

    def test_invalid_selection_exits(self):
        results = [{"score": 0.9, "recordings": [{"title": "Song"}]}]
        with patch("builtins.input", return_value="not_a_number"), pytest.raises(SystemExit):
            _fallback_select(results)

    def test_out_of_range(self):
        results = [{"score": 0.9, "recordings": [{"title": "Song"}]}]
        with patch("builtins.input", return_value="99"), pytest.raises(SystemExit):
            _fallback_select(results)


class TestPrintTags:
    def test_prints_tags(self, capsys):
        with patch("music.tag.read_tags", return_value={"title": "T", "artist": "A"}):
            _print_tags("test.flac")
        captured = capsys.readouterr()
        assert "title" in captured.out
        assert "artist" in captured.out

    def test_no_tags(self, capsys):
        with patch("music.tag.read_tags", return_value={}):
            _print_tags("test.flac")
        captured = capsys.readouterr()
        assert "no tags" in captured.out
