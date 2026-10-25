"""Keep the live gate from declaring unknown metrics or repeated pages healthy."""
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from scripts.validate_replacement import number, pagination_issues, validate


@pytest.mark.parametrize("value", [None, True, False, "", "NaN", float("inf"), -1, {}])
def test_unknown_or_invalid_counter_cannot_pass(value):
    assert not number(value)


def test_zero_is_valid_but_cannot_erase_a_previously_positive_view_count():
    body = {"id": "abcdefghijk", "viewCountInt": 0, "likeCountInt": 0,
            "commentCountInt": 0, "publishDate": "2026-01-01T00:00:00Z"}
    fixture = {"url": "https://youtube.com/shorts/abcdefghijk", "previous_views": 500}
    assert validate("video", "youtube", body, fixture) == ["post.positive_counter_became_zero"]
    assert validate("video", "youtube", {**body, "viewCountInt": None}, fixture) == ["post.viewCountInt"]
    assert "post.identity" in validate("video", "youtube", {**body, "id": "other"}, fixture)


def test_malformed_envelope_and_missing_date_fail_the_gate():
    assert validate("feed", "youtube", [], {}) == ["response.object_required"]
    assert validate("feed", "youtube", {"shorts": [None]}, {}) == ["feed[0].object_required"]
    item = {"code": "abc", "media_type": 2, "play_count": 0, "caption": None}
    assert validate("feed", "instagram", {"items": [item]}, {}) == ["feed[0].date"]
    assert validate("feed", "instagram", {"items": [{**item, "taken_at": 1760000000}]}, {}) == []


def test_repeated_ids_and_cursor_cycles_are_failures():
    body = {"shorts": [{"id": "abc"}], "continuationToken": "next"}
    assert pagination_issues("youtube", body, {"abc"}, {"next"}) == [
        "feed.repeated_ids_across_pages", "feed.cursor_cycle"]
    assert validate("feed", "instagram", {"items": [], "more_available": True}, {}) == ["feed.next_cursor"]


def test_live_runner_follows_the_instagram_cursor_with_fallback_disabled(tmp_path):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            calls.append((self.path, self.headers.get("x-socialscrape-fallback")))
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.answer({"success": False, "error": "not_found"}, 404)

        def do_GET(self):
            calls.append((self.path, self.headers.get("x-socialscrape-fallback")))
            second = "next_max_id=page2" in self.path
            self.answer({"success": True, "items": [{"code": "second" if second else "first",
                         "taken_at": 1760000000, "media_type": 2, "play_count": 0, "caption": None}],
                         "next_max_id": None if second else "page2"})

        def answer(self, body, status=200):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        cohort = tmp_path / "cohort.json"
        cohort.write_text(json.dumps({"profiles": [{"platform": "instagram", "handle": "sample"}]}))
        output = tmp_path / "results"
        result = subprocess.run([sys.executable, str(Path(__file__).parents[1] / "scripts/validate_replacement.py"),
                                 "--cohort", str(cohort), "--output", str(output), "--base-url",
                                 f"http://127.0.0.1:{server.server_port}", "--feeds-per-platform", "1",
                                 "--pages-per-feed", "2"], capture_output=True, text=True, timeout=10)
        assert result.returncode == 1  # The missing profile must not be greenwashed by healthy feed pages.
        rows = [json.loads(line) for line in (output / "results.jsonl").read_text().splitlines()]
        assert [r["outcome"] for r in rows if r["kind"] == "feed"] == ["passed", "passed"]
        assert all(fallback == "off" for _, fallback in calls)
        assert any("next_max_id=page2" in path for path, _ in calls)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
