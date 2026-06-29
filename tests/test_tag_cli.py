import json
from unittest.mock import MagicMock, patch

import pytest

from music.tag import (
    _fallback_search,
    _fallback_select,
    _fetch_musicbrainz,
    _mb_recording_to_result,
    _normalize_filename,
    _print_tags,
    _search_musicbrainz,
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
        assert len(lines) == 2
        assert "(none)" in lines[0]
        assert "+" in lines[1]

    def test_changed_field(self):
        current = {"title": "Old"}
        new = {"title": "New"}
        lines = format_diff(current, new)
        assert len(lines) == 2
        assert "Old" in lines[0]
        assert "→" in lines[1]

    def test_no_changes(self):
        current = {"title": "Same"}
        new = {"title": "Same"}
        lines = format_diff(current, new)
        assert len(lines) == 0

    def test_mixed(self):
        current = {"title": "Old Title", "artist": "Same Artist"}
        new = {"title": "New Title", "artist": "Same Artist", "album": "New Album"}
        lines = format_diff(current, new)
        assert len(lines) == 4
        # title changed (2 lines), album added (2 lines), artist unchanged


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


class TestNormalizeFilename:
    def test_removes_youtube_id(self):
        result = _normalize_filename(
            "/path/to/09 - 5 Pieces, Op. 75, The Trees： No. 5. Kuusi (The Spruce) [visNHcEI2XM].webm"
        )
        assert "[visNHcEI2XM]" not in result
        assert ".webm" not in result

    def test_removes_track_number(self):
        result = _normalize_filename("09 - Song Title [abc123def45].mp3")
        assert not result.startswith("09")
        assert "Song Title" in result

    def test_normalizes_fullwidth_punctuation(self):
        # NFKC normalization converts fullwidth characters to ASCII
        result = _normalize_filename("Song ＂Title＂： Subtitle.flac")
        assert "＂" not in result  # fullwidth quotation mark
        assert "：" not in result  # fullwidth colon

    def test_collapses_whitespace(self):
        result = _normalize_filename("01 -   Multiple   Spaces  .mp3")
        assert "  " not in result

    def test_simple_filename(self):
        result = _normalize_filename("Bohemian Rhapsody.flac")
        assert result == "Bohemian Rhapsody"

    def test_filename_without_youtube_id(self):
        result = _normalize_filename("05 - My Song.mp3")
        assert result == "My Song"


class TestMbRecordingToResult:
    def test_full_recording(self):
        mb_rec = {
            "id": "abc-123",
            "title": "Test Song",
            "score": 95,
            "artist-credit": [
                {"name": "Artist One", "joinphrase": " & "},
                {"name": "Artist Two", "joinphrase": ""},
            ],
            "releases": [
                {"title": "Test Album", "date": "2024"},
                {"title": "Other Album", "date": "2023-05"},
            ],
        }
        result = _mb_recording_to_result(mb_rec)
        assert result["score"] == 0.95
        rec = result["recordings"][0]
        assert rec["id"] == "abc-123"
        assert rec["title"] == "Test Song"
        assert rec["artists"] == [{"name": "Artist One"}, {"name": "Artist Two"}]
        assert rec["releases"] == [
            {"title": "Test Album", "date": {"year": 2024}},
            {"title": "Other Album", "date": {"year": 2023}},
        ]

    def test_minimal_recording(self):
        mb_rec = {"id": "xyz", "title": "Minimal"}
        result = _mb_recording_to_result(mb_rec)
        assert result["score"] == 1.0  # default score when not provided
        rec = result["recordings"][0]
        assert rec["title"] == "Minimal"
        assert rec["artists"] == []
        assert rec["releases"] == []

    def test_release_without_date(self):
        mb_rec = {
            "id": "abc",
            "title": "Song",
            "releases": [{"title": "Album"}],
        }
        result = _mb_recording_to_result(mb_rec)
        assert result["recordings"][0]["releases"] == [{"title": "Album"}]

    def test_release_with_date_only(self):
        mb_rec = {
            "id": "abc",
            "title": "Song",
            "releases": [{"date": "2024"}],
        }
        result = _mb_recording_to_result(mb_rec)
        # Release with only a date is kept (the date is useful metadata)
        assert result["recordings"][0]["releases"] == [{"date": {"year": 2024}}]


class TestSearchMusicBrainz:
    def test_returns_results(self):
        mb_response = {
            "recordings": [
                {"id": "rec-1", "title": "Song A", "score": 90},
                {"id": "rec-2", "title": "Song B", "score": 80},
            ]
        }
        with patch("urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(mb_response).encode()
            mock_resp.__enter__.return_value = mock_resp
            mock_open.return_value = mock_resp

            results = _search_musicbrainz('recording:"Test"')
            assert len(results) == 2
            assert results[0]["recordings"][0]["title"] == "Song A"

    def test_handles_network_error(self):
        with patch("urllib.request.urlopen", side_effect=Exception):
            results = _search_musicbrainz('recording:"Test"')
            assert results == []

    def test_empty_response(self):
        mb_response = {"recordings": []}
        with patch("urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(mb_response).encode()
            mock_resp.__enter__.return_value = mock_resp
            mock_open.return_value = mock_resp

            results = _search_musicbrainz('recording:"Nonexistent"')
            assert results == []


class TestFallbackSearch:
    def test_metadata_search_succeeds(self):
        """When file has metadata, searches MusicBrainz with recording + artist."""
        mb_response = {"recordings": [{"id": "mb-1", "title": "Found Song", "score": 95}]}
        with patch("urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(mb_response).encode()
            mock_resp.__enter__.return_value = mock_resp
            mock_open.return_value = mock_resp

            tags = {
                "title": "Found Song",
                "artist": "Test Artist",
            }
            results = _fallback_search("/some/file.mp3", tags)
            assert len(results) == 1
            assert results[0]["recordings"][0]["title"] == "Found Song"

    def test_falls_back_to_filename(self):
        """When metadata search returns nothing, tries normalized filename."""
        # First call (metadata search) returns empty, second (filename) returns results
        call_count = [0]

        def side_effect(req):
            call_count[0] += 1
            mock_resp = MagicMock()
            if call_count[0] == 2:
                mock_resp.read.return_value = json.dumps(
                    {"recordings": [{"id": "mb-2", "title": "Filename Song", "score": 90}]}
                ).encode()
            else:
                mock_resp.read.return_value = json.dumps({"recordings": []}).encode()
            mock_resp.__enter__.return_value = mock_resp
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=side_effect):
            tags = {
                "title": "Some Title",
                "artist": "Some Artist",
            }
            results = _fallback_search("/path/01 - Actual Song Name.mp3", tags)
            assert len(results) == 1
            assert results[0]["recordings"][0]["title"] == "Filename Song"

    def test_no_metadata_and_bad_filename(self):
        """When nothing works, returns empty list."""
        mb_response = {"recordings": []}
        with patch("urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(mb_response).encode()
            mock_resp.__enter__.return_value = mock_resp
            mock_open.return_value = mock_resp

            results = _fallback_search("/path/01 - .mp3", {})
            assert results == []

    def test_empty_tags_no_filename_match(self):
        """No metadata in file, filename normalizes to empty/nothing useful."""
        mb_response = {"recordings": []}
        with patch("urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(mb_response).encode()
            mock_resp.__enter__.return_value = mock_resp
            mock_open.return_value = mock_resp

            results = _fallback_search("/path/01 - .mp3", {"title": "Unknown"})
            assert results == []
