import os

import pytest

# Whatever the shell exports, tests run with the cache off (they always hit the live code path), auth off and no
# cookies (the live suite stays anonymous). SOCIALSCRAPE_PROXY and SOCIALSCRAPE_TRANSCRIBE are left to the caller.
os.environ["SOCIALSCRAPE_CACHE_PATH"] = ""
os.environ["SOCIALSCRAPE_API_KEYS"] = ""
for _name in ("YOUTUBE", "TIKTOK", "INSTAGRAM", "FROM_BROWSER"):
    os.environ.pop(f"SOCIALSCRAPE_COOKIES_{_name}", None)


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from socialscrape.config import get_settings
    from socialscrape.main import create_app

    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture(scope="module")
def mlx_client():
    """A client of an app transcribing with mlx-whisper (model small); skips when mlx_whisper isn't installed."""
    from importlib.util import find_spec

    from tests.e2e import app_with

    if find_spec("mlx_whisper") is None:
        pytest.skip("mlx_whisper is not installed")
    with app_with({"SOCIALSCRAPE_TRANSCRIBE": "mlx-whisper", "SOCIALSCRAPE_WHISPER_MODEL": "small"},
                  raise_errors=False) as c:
        yield c


@pytest.fixture(autouse=True)
def _fresh_platform_memory(monkeypatch):
    """Each test starts with what the platforms keep per process empty: TikTok's item_list cooling windows and
    remembered region lookups, Instagram's kept post-query media and posts-tab captions, every account's health.
    Account checks are off (they would ask the platform): a test that needs one sets sessions.PROBES."""
    from socialscrape import sessions
    from socialscrape.platforms import instagram, tiktok

    monkeypatch.setattr(tiktok, "_cooling", {})
    monkeypatch.setattr(tiktok, "_regions", {})
    monkeypatch.setattr(instagram, "MEDIA", instagram._Kept())
    monkeypatch.setattr(instagram, "CAPTIONS", instagram._Kept())
    monkeypatch.setattr(sessions, "PROBES", {})
    sessions.reset()
    yield
    sessions.reset()
