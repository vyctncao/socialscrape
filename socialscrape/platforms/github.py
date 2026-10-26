"""Anonymous GitHub REST and public HTML, mapped to the documented contract.

No ambient GitHub token, CLI login or browser cookie is used. API requests use
fixed public paths; HTML fragments use the same XHR header as the profile page.
"""

from __future__ import annotations

import calendar
import json
import re
from datetime import date, datetime, timezone
from typing import Literal
from urllib.parse import unquote, urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup, Tag
from fastapi import APIRouter, Query
from yt_dlp.networking import Request
from yt_dlp.networking.exceptions import HTTPError

from ..errors import (
    BadRequest,
    Forbidden,
    NotFound,
    UpstreamBlocked,
    UpstreamError,
    require_one,
)
from ..registry import sc_get
from ..ytdlp import with_ydl

router = APIRouter()
WEB = "https://github.com"
API = "https://api.github.com"
MAX_BYTES = 8 * 1024 * 1024
_USER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
_REPO = re.compile(r"[A-Za-z0-9_.-]{1,100}$")


def _parts(value: str) -> list[str]:
    try:
        parsed = urlsplit(value)
        parts = [unquote(p) for p in parsed.path.strip("/").split("/")]
        if (
            parsed.scheme not in ("http", "https")
            or parsed.hostname not in ("github.com", "www.github.com")
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 80, 443)
        ):
            raise ValueError
        return parts
    except ValueError:
        raise BadRequest("url must be a public github.com URL") from None


def _handle(handle: str | None = None, url: str | None = None) -> str:
    require_one(handle=handle, url=url)
    name = (handle or "").strip().removeprefix("@")
    if url:
        parts = _parts(url)
        if len(parts) != 1 or not _USER.fullmatch(parts[0]):
            raise BadRequest("url must be a GitHub profile URL")
        if name and name.lower() != parts[0].lower():
            raise BadRequest("handle and url refer to different GitHub users")
        name = parts[0]
    if not _USER.fullmatch(name):
        raise BadRequest("handle must be a GitHub username")
    return name


def _repo_ref(url: str) -> tuple[str, str]:
    parts = _parts(url)
    if len(parts) == 2:
        owner, name = parts[0], parts[1].removesuffix(".git")
        if _USER.fullmatch(owner) and _REPO.fullmatch(name) and name not in (".", ".."):
            return owner, name
    raise BadRequest("url must identify a GitHub repository")


def _read(
    ydl, path: str, params: dict | None = None, *, html: bool = False, xhr: bool = False
):
    origin = WEB if html else API
    headers = {
        "Accept": "text/html" if html else "application/vnd.github+json",
        "Cookie": "",
    }
    if not html:
        headers["X-GitHub-Api-Version"] = "2026-03-10"
    if xhr:
        headers["X-Requested-With"] = "XMLHttpRequest"
    request = Request(
        origin + path + ("?" + urlencode(params) if params else ""), headers=headers
    )
    try:
        with ydl.urlopen(request) as response:
            raw, response_headers = response.read(MAX_BYTES + 1), response.headers
    except HTTPError as exc:
        if exc.status == 404:
            raise NotFound("GitHub public resource not found") from exc
        if exc.status in (403, 429):
            body = exc.response.read(65536).decode("utf-8", "replace")
            if (
                exc.status == 429
                or exc.response.headers.get("X-RateLimit-Remaining") == "0"
                or "rate limit" in body.lower()
            ):
                raise UpstreamBlocked(
                    "GitHub is rate-limiting this connection"
                ) from exc
            raise Forbidden(
                "GitHub does not expose this resource to anonymous requests"
            ) from exc
        if exc.status in (400, 422):
            raise BadRequest("GitHub rejected the requested filters or page") from exc
        raise
    if len(raw) > MAX_BYTES:
        raise UpstreamError("GitHub response exceeded the size limit")
    if html:
        soup = BeautifulSoup(raw, "html.parser")
        if soup.title and soup.title.get_text(" ", strip=True).lower().startswith(
            "sign in"
        ):
            raise Forbidden("GitHub requires sign-in for this resource")
        return soup
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise UpstreamError("GitHub returned invalid JSON") from exc
    if (
        not isinstance(data, (list, dict))
        or isinstance(data, dict)
        and data.get("message")
    ):
        raise UpstreamError("GitHub returned no usable public resource data")
    return data, response_headers


async def _run(fn):
    return await with_ydl("github", fn, cookiefile=None, cookiesfrombrowser=None)


def _next(headers, page: int) -> int | None:
    return (
        page + 1
        if any(
            re.search(r';\s*rel="next"', part)
            for part in headers.get("Link", "").split(",")
        )
        else None
    )


def _user(data: dict, *, brief: bool = False) -> dict:
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("id"), int)
        or isinstance(data.get("id"), bool)
        or not isinstance(data.get("login"), str)
        or not _USER.fullmatch(data["login"])
    ):
        raise UpstreamError("GitHub returned a user without its identity")
    keys = ("id", "node_id", "login", "avatar_url", "type", "site_admin")
    if not brief:
        keys += (
            "name",
            "company",
            "blog",
            "location",
            "email",
            "bio",
            "twitter_username",
            "public_repos",
            "public_gists",
            "followers",
            "following",
            "created_at",
            "updated_at",
        )
    return {
        **{key: data.get(key) for key in keys},
        "url": data.get("html_url") or f"{WEB}/{data['login']}",
    }


def _repo(data: dict) -> dict:
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("id"), int)
        or isinstance(data.get("id"), bool)
        or not isinstance(data.get("full_name"), str)
        or not isinstance(data.get("name"), str)
        or not isinstance(data.get("owner"), dict)
    ):
        raise UpstreamError("GitHub returned a repository without its identity")
    if data.get("private") is not False:
        raise Forbidden("Only public GitHub repositories are available")
    owner = _user(data["owner"], brief=True)
    if data["full_name"].lower() != f"{owner['login']}/{data['name']}".lower():
        raise UpstreamError("GitHub repository identity and owner disagree")
    keys = (
        "id",
        "node_id",
        "name",
        "full_name",
        "private",
        "description",
        "fork",
        "homepage",
        "language",
        "forks_count",
        "watchers_count",
        "size",
        "default_branch",
        "open_issues_count",
        "topics",
        "archived",
        "disabled",
        "visibility",
        "pushed_at",
        "created_at",
        "updated_at",
    )
    license = data.get("license")
    return {
        **{key: data.get(key) for key in keys},
        "url": data.get("html_url") or f"{WEB}/{data['full_name']}",
        "owner": {
            key: owner[key] for key in ("id", "login", "avatar_url", "url", "type")
        },
        "stars_count": data.get("stargazers_count"),
        "license": {key: license.get(key) for key in ("key", "name", "spdx_id")}
        if isinstance(license, dict)
        else None,
    }


def _list(value) -> list:
    if not isinstance(value, list):
        raise UpstreamError("GitHub returned no public list")
    return value


@sc_get(
    router,
    "/v1/github/user",
    source="GitHub public REST GET /users/{username}",
    notes="Anonymous public profile fields only. Hidden optional fields remain null; a different identity is an error.",
)
async def user(handle: str | None = None, url: str | None = None):
    name = _handle(handle, url)

    def go(ydl):
        data, _ = _read(ydl, f"/users/{name}")
        result = _user(data)
        if result["login"].lower() != name.lower():
            raise UpstreamError("GitHub returned a different public user")
        return result

    return await _run(go)


@sc_get(
    router,
    "/v1/github/user/repositories",
    source="GitHub public REST GET /users/{username}/repos",
    notes="Public repositories only. Cursor is the next native REST page; no items are trimmed away.",
)
async def repositories(
    handle: str | None = None,
    url: str | None = None,
    kind: Literal["owner", "all", "member"] = Query("owner", alias="type"),
    sort: Literal["created", "updated", "pushed", "full_name"] = "updated",
    direction: Literal["asc", "desc"] = "desc",
    cursor: int = Query(1, ge=1),
):
    name = _handle(handle, url)

    def go(ydl):
        data, headers = _read(
            ydl,
            f"/users/{name}/repos",
            {
                "type": kind,
                "sort": sort,
                "direction": direction,
                "page": cursor,
                "per_page": 30,
            },
        )
        return {
            "repositories": [_repo(item) for item in _list(data)],
            "cursor": _next(headers, cursor),
        }

    return await _run(go)


async def _graph(kind: str, handle, url, cursor):
    name = _handle(handle, url)

    def go(ydl):
        data, headers = _read(
            ydl, f"/users/{name}/{kind}", {"page": cursor, "per_page": 30}
        )
        return {
            kind: [_user(item, brief=True) for item in _list(data)],
            "cursor": _next(headers, cursor),
        }

    return await _run(go)


@sc_get(
    router,
    "/v1/github/user/followers",
    source="GitHub public REST GET /users/{username}/followers",
    notes="Anonymous public follower list, 30 items per page. REST Link headers determine the next page; rate limits return 503.",
)
async def followers(
    handle: str | None = None, url: str | None = None, cursor: int = Query(1, ge=1)
):
    return await _graph("followers", handle, url, cursor)


@sc_get(
    router,
    "/v1/github/user/following",
    source="GitHub public REST GET /users/{username}/following",
    notes="Anonymous public following list, 30 items per page. REST Link headers determine the next page; rate limits return 503.",
)
async def following(
    handle: str | None = None, url: str | None = None, cursor: int = Query(1, ge=1)
):
    return await _graph("following", handle, url, cursor)


@sc_get(
    router,
    "/v1/github/repository",
    source="GitHub public REST GET /repos/{owner}/{repo}",
    notes="Public repository metadata with canonical web URLs and stars_count mapped from stargazers_count. Private data is refused.",
)
async def repository(url: str = Query(...)):
    owner, name = _repo_ref(url)

    def go(ydl):
        data, _ = _read(ydl, f"/repos/{owner}/{name}")
        result = _repo(data)
        if result["full_name"].lower() != f"{owner}/{name}".lower():
            raise UpstreamError("GitHub returned a different public repository")
        return {"repository": result}

    return await _run(go)


def _date(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
        return value
    except ValueError:
        raise BadRequest(f"{name} must be a date in YYYY-MM-DD format") from None


@sc_get(
    router,
    "/v1/github/user/pull-requests",
    source="GitHub public REST search/issues with is:pr author filter",
    notes="GitHub search exposes at most 1000 matches; use since/until to narrow larger histories. "
    "An incomplete upstream search is an error rather than a complete empty history.",
)
async def pull_requests(
    handle: str = Query(...),
    since: str | None = None,
    until: str | None = None,
    cursor: int = Query(1, ge=1),
):
    name, since, until = _handle(handle), _date(since, "since"), _date(until, "until")
    if since and until and since > until:
        raise BadRequest("since must be on or before until")
    query = (
        f"is:pr author:{name}"
        + (f" created:>={since}" if since else "")
        + (f" created:<={until}" if until else "")
    )

    def go(ydl):
        data, headers = _read(
            ydl,
            "/search/issues",
            {
                "q": query,
                "sort": "created",
                "order": "desc",
                "page": cursor,
                "per_page": 30,
            },
        )
        if (
            not isinstance(data, dict)
            or data.get("incomplete_results") is not False
            or not isinstance(data.get("items"), list)
        ):
            raise UpstreamError("GitHub returned an incomplete pull-request search")
        items = []
        for item in data["items"]:
            if (
                not isinstance(item, dict)
                or not item.get("pull_request")
                or not item.get("html_url")
            ):
                raise UpstreamError(
                    "GitHub returned a search result without its pull-request identity"
                )
            parts = urlsplit(item["html_url"]).path.strip("/").split("/")
            if len(parts) != 4 or parts[2] != "pull":
                raise UpstreamError("GitHub returned an invalid pull-request identity")
            items.append(
                {
                    "title": item.get("title"),
                    "repo": "/".join(parts[:2]),
                    "state": item.get("state"),
                    "created_at": item.get("created_at"),
                    "url": item["html_url"],
                }
            )
        next_page = _next(headers, cursor)
        return {
            "pull_requests": items,
            "cursor": next_page,
            "has_more": next_page is not None,
        }

    return await _run(go)


def _text(node) -> str | None:
    return (" ".join(node.stripped_strings) or None) if isinstance(node, Tag) else None


def _count(text: str | None) -> int | None:
    if text is None:
        return None
    match = re.search(r"([\d,]+(?:\.\d+)?)\s*([kKmM]?)", text)
    return (
        round(
            float(match[1].replace(",", ""))
            * {"": 1, "k": 1000, "m": 1000000}[match[2].lower()]
        )
        if match
        else None
    )


def _trending_path(language, developers=False):
    language = (language or "").strip().lower()
    if language and not re.fullmatch(r"[a-z0-9][a-z0-9+.# -]{0,59}", language):
        raise BadRequest("language must be a GitHub language name")
    # quote encodes '/', '#' and spaces; no caller-provided URL is fetched.
    from urllib.parse import quote

    return "/trending" + ("/developers" if developers else "") + (
        "/" + quote(language, safe="") if language else ""
    ), language


def _trend_rows(soup):
    rows = soup.select("article.Box-row")
    if not rows and not soup.select_one("main .blankslate"):
        raise UpstreamError("GitHub did not return a readable trending list")
    return rows


@sc_get(
    router,
    "/v1/github/trending/repositories",
    source="GitHub public /trending HTML",
    notes="Public trending page with language, period and spoken-language filters. Displayed abbreviated counts are approximate.",
)
async def trending_repositories(
    language: str | None = None,
    since: Literal["daily", "weekly", "monthly"] = "daily",
    spoken_language_code: str | None = None,
):
    path, language = _trending_path(language)
    if spoken_language_code and not re.fullmatch(
        r"[a-z]{2}(?:-[A-Z]{2})?", spoken_language_code
    ):
        raise BadRequest("spoken_language_code must be a language code")

    def go(ydl):
        soup = _read(
            ydl,
            path,
            {
                "since": since,
                **(
                    {"spoken_language_code": spoken_language_code}
                    if spoken_language_code
                    else {}
                ),
            },
            html=True,
        )
        items = []
        for rank, row in enumerate(_trend_rows(soup), 1):
            link = row.select_one("h2 a[href]")
            if not link:
                raise UpstreamError("GitHub trending repository has no identity")
            owner, name = _repo_ref(urljoin(WEB, link["href"]))
            full = f"{owner}/{name}"
            color = row.select_one(".repo-language-color")
            color = (
                re.search(
                    r"background-color:\s*(#[0-9a-fA-F]{3,8})", color.get("style", "")
                )
                if color
                else None
            )
            today = _text(row.select_one("span.float-sm-right"))
            builders = [
                {
                    "username": image.get("alt", "").removeprefix("@"),
                    "avatar_url": image.get("src"),
                }
                for image in row.select("a img.avatar-user")
                if image.get("alt", "").startswith("@")
            ]
            items.append(
                {
                    "rank": rank,
                    "owner": owner,
                    "repo": name,
                    "full_name": full,
                    "url": f"{WEB}/{full}",
                    "description": _text(row.select_one("p")),
                    "language": _text(
                        row.select_one('[itemprop="programmingLanguage"]')
                    ),
                    "language_color": color[1] if color else None,
                    "stars_count": _count(
                        _text(row.select_one(f'a[href="/{full}/stargazers"]'))
                    ),
                    "forks_count": _count(
                        _text(row.select_one(f'a[href="/{full}/forks"]'))
                    ),
                    "stars_today": _count(today),
                    "stars_today_text": today,
                    "built_by": builders,
                }
            )
        return {
            "repositories": items,
            "language": language or None,
            "since": since,
            "spoken_language_code": spoken_language_code,
        }

    return await _run(go)


@sc_get(
    router,
    "/v1/github/trending/developers",
    source="GitHub public /trending/developers HTML",
    notes="Public developer ranking and featured repository for the requested language and period; no account credentials.",
)
async def trending_developers(
    language: str | None = None, since: Literal["daily", "weekly", "monthly"] = "daily"
):
    path, language = _trending_path(language, True)

    def go(ydl):
        soup = _read(ydl, path, {"since": since}, html=True)
        items = []
        for rank, row in enumerate(_trend_rows(soup), 1):
            link = row.select_one("h1 a[href]")
            if not link:
                raise UpstreamError("GitHub trending developer has no identity")
            name = _handle(url=urljoin(WEB, link["href"]))
            popular = row.find("article")
            repo_link = popular.select_one("h1 a[href]") if popular else None
            repo = None
            if repo_link:
                owner, repo_name = _repo_ref(urljoin(WEB, repo_link["href"]))
                repo = {
                    "owner": owner,
                    "repo": repo_name,
                    "full_name": f"{owner}/{repo_name}",
                    "url": f"{WEB}/{owner}/{repo_name}",
                    "name": repo_name,
                    "description": _text(
                        repo_link.find_parent("article").select_one("div.f6.mt-1")
                    ),
                }
            avatar = row.select_one("img.avatar-user")
            items.append(
                {
                    "rank": rank,
                    "username": name,
                    "name": _text(link),
                    "url": f"{WEB}/{name}",
                    "avatar_url": avatar.get("src") if avatar else None,
                    "popular_repository": repo,
                }
            )
        return {"developers": items, "language": language or None, "since": since}

    return await _run(go)


def _year(value: int | None) -> int:
    now = datetime.now(timezone.utc)
    if value is None:
        return now.year
    if not 1970 <= value <= now.year:
        raise BadRequest("year must be between 1970 and the current year")
    return value


@sc_get(
    router,
    "/v1/github/user/contributions",
    source="GitHub public contribution calendar HTML",
    notes="Counts and intensity come from public calendar cells and tooltips; unknown counts remain null.",
)
async def contributions(
    handle: str | None = None, url: str | None = None, year: int | None = None
):
    name, year = _handle(handle, url), _year(year)

    def go(ydl):
        soup = _read(
            ydl,
            f"/users/{name}/contributions",
            {"from": f"{year}-01-01", "to": f"{year}-12-31"},
            html=True,
        )
        cells = soup.select("[data-date]")
        if not cells:
            raise UpstreamError("GitHub did not return a public contribution calendar")
        tips = {node.get("for"): _text(node) for node in soup.select("tool-tip[for]")}
        days = []
        for cell in cells:
            day = _date(cell.get("data-date"), "calendar date")
            if not day or not day.startswith(f"{year}-"):
                continue
            tooltip = tips.get(cell.get("id")) or cell.get("aria-label") or ""
            count = (
                0 if tooltip.lower().startswith("no contributions") else _count(tooltip)
            )
            intensity = cell.get("data-level")
            days.append(
                {
                    "date": day,
                    "contribution_count": count,
                    "color": cell.get("fill"),
                    "intensity": int(intensity) if str(intensity).isdigit() else None,
                }
            )
        if not days or len({day["date"] for day in days}) != len(days):
            raise UpstreamError(
                "GitHub returned an incomplete or duplicate contribution calendar"
            )
        total = _count(_text(soup.select_one("#js-contribution-activity-description")))
        return {
            "contributions": {
                "username": name,
                "year": year,
                "total_contributions": total,
                "days": sorted(days, key=lambda day: day["date"]),
            }
        }

    return await _run(go)


@sc_get(
    router,
    "/v1/github/user/activity",
    source="GitHub public profile contribution activity XHR HTML",
    notes="One calendar month per page, newest first within the requested year. Public timeline only.",
)
async def activity(
    handle: str | None = None,
    url: str | None = None,
    year: int | None = None,
    cursor: int = Query(1, ge=1, le=12),
):
    name, year = _handle(handle, url), _year(year)
    today = datetime.now(timezone.utc)
    month = (today.month if today.year == year else 12) - cursor + 1
    if month < 1:
        raise BadRequest("cursor is beyond the beginning of the requested year")
    start, end = (
        f"{year}-{month:02d}-01",
        f"{year}-{month:02d}-{calendar.monthrange(year, month)[1]}",
    )

    def go(ydl):
        soup = _read(
            ydl,
            f"/{name}",
            {
                "tab": "contributions",
                "from": start,
                "to": end,
                "action": "show",
                "controller": "profiles",
                "user_id": name,
            },
            html=True,
            xhr=True,
        )
        container = soup.select_one(".contribution-activity-listing")
        if not container:
            raise UpstreamError(
                "GitHub did not return the requested public activity timeline"
            )
        items = []
        for item in container.select(".TimelineItem"):
            body = item.select_one(".TimelineItem-body")
            if not body:
                continue
            summary = _text(body.select_one("summary .color-fg-default, h4"))
            if not summary:
                continue
            details = []
            for detail in body.select("ul > li") or [body]:
                repo = detail.select_one('a[data-hovercard-type="repository"]')
                timestamp = detail.select_one("relative-time, time")
                details.append(
                    {
                        "text": _text(detail),
                        "repository": {
                            "text": _text(repo),
                            "url": urljoin(WEB, repo["href"]),
                        }
                        if repo
                        else None,
                        "links": [
                            {"text": _text(link), "url": urljoin(WEB, link["href"])}
                            for link in detail.select("a[href]")
                            if link["href"].startswith(("/", "https://github.com/"))
                        ],
                        "language": _text(
                            detail.select_one('[itemprop="programmingLanguage"]')
                        ),
                        "date_text": _text(timestamp),
                        "date_description": timestamp.get("datetime")
                        if timestamp
                        else None,
                    }
                )
            items.append({"summary": summary, "details": details})
        text = _text(container) or ""
        no_activity = (
            "no activity" in text.lower()
            or "doesn't have any public activity" in text.lower()
        )
        if not items and not no_activity:
            raise UpstreamError(
                "GitHub activity markup contained no readable public activity or empty-state message"
            )
        return {
            "username": name,
            "year": year,
            "month": f"{calendar.month_name[month]} {year}",
            "activity": items,
            "no_activity": no_activity and not items,
            "message": text if no_activity and not items else None,
            "cursor": cursor + 1 if month > 1 else None,
        }

    return await _run(go)
