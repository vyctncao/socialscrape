import json
from types import SimpleNamespace

from socialscrape.platforms import linkbio as b


def test_profile_links_are_scoped_to_the_explicit_profile_identity(client, monkeypatch):
    calls = []

    async def run(platform, fn):
        assert platform == "linkbio"
        return fn(None)

    def fetch(ydl, url, hosts):
        calls.append(url)
        identity = json.dumps({"id": "-7", "username": "sample"})
        body = f"""<img id="profile_picture_catch_error" data-username="sample" rel='{identity}'>
        <a data-uid="-7" data-href="https://creator.example">Published site</a>
        <a data-uid="-8" data-href="https://unrelated.example">Recommendation</a>
        <a href="https://lnk.bio/login">Log in</a>"""
        return SimpleNamespace(body=body.encode())

    monkeypatch.setattr(b._public, "run", run)
    monkeypatch.setattr(b._public, "fetch", fetch)
    body = client.get("/v1/linkbio", params={"url": "https://lnk.bio/sample"}).json()
    assert body["handle"] == "sample" and body["id"] == "-7"
    assert body["links"] == [
        {"url": "https://creator.example", "text": "Published site"}
    ]
    assert body["instagram"] is None and calls == ["https://lnk.bio/sample"]


def test_other_hosts_and_paths_do_not_become_profile_fetches(client, monkeypatch):
    async def run(*args):
        raise AssertionError("network must not be reached")

    monkeypatch.setattr(b._public, "run", run)
    for url in [
        "https://lnk.bio.evil.example/sample",
        "https://lnk.bio/sample/extra",
        "https://user@lnk.bio/sample",
    ]:
        assert client.get("/v1/linkbio", params={"url": url}).status_code == 400
