"""Normalize public job feeds into one validated listing format."""

import html
import json
import math
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field, HttpUrl, TypeAdapter, ValidationError

Provider = Literal["greenhouse", "lever", "lever-eu"]
Arrangement = Literal["remote", "hybrid", "on-site"]
HOSTS = {
    "greenhouse": "boards-api.greenhouse.io",
    "lever": "api.lever.co",
    "lever-eu": "api.eu.lever.co",
}
MAX_RESPONSE_BYTES = 12 * 1024 * 1024
BOARD_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$"
ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}"
SAFE_HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", re.I)
http_url = TypeAdapter(HttpUrl)
rate_until: dict[str, float] = {}


class FeedError(Exception):
    """A short message safe to show to the user."""


class FeedRateLimit(FeedError):
    def __init__(self, seconds):
        super().__init__("Provider rate limit reached. Try refreshing later.")
        self.seconds = seconds


class Salary(BaseModel):
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    interval: str | None
    minimum: float = Field(ge=0, le=1_000_000_000_000, allow_inf_nan=False)
    maximum: float = Field(ge=0, le=1_000_000_000_000, allow_inf_nan=False)


class Posting(BaseModel):
    external_id: str
    title: str
    company: str
    location: str | None = None
    arrangement: Arrangement | None = None
    salary: Salary | None = None
    url: str
    posted_at: datetime | None = None
    description: str = ""


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data):
        self.parts.append(data)


def text(value, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(re.sub(r"[\x00\ud800-\udfff]", "", value).split())[:limit].strip()


def plain_html(value) -> str:
    if not isinstance(value, str):
        return ""
    parser = PlainText()
    parser.feed(html.unescape(value[:60_000]))
    return text(" ".join(parser.parts), 24_000)


def safe_posting_url(value, allowed_hosts=None) -> str:
    # These links go straight to the browser. The backend never fetches them.
    if not isinstance(value, str) or len(value) > 2048 or re.search(r"[\s\x00-\x1f\x7f\\]", value):
        return ""
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if (parsed.scheme != "https" or not SAFE_HOST.fullmatch(host) or parsed.port is not None
                or parsed.username is not None or host.endswith((".local", ".localhost", ".internal", ".test"))
                or (allowed_hosts is not None and host not in allowed_hosts)):
            return ""
        http_url.validate_python(value)
    except (ValueError, ValidationError):
        return ""
    return value


def arrangement(value) -> Arrangement | None:
    normalized = text(value, 36).lower()
    return {"remote": "remote", "hybrid": "hybrid", "on-site": "on-site",
            "onsite": "on-site", "on site": "on-site"}.get(normalized)


def published(value) -> datetime | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        date = datetime.fromisoformat(value)
        return date if date.tzinfo else None
    except ValueError:
        return None


def salary(currency, interval, minimum, maximum) -> Salary | None:
    if (type(minimum) not in (int, float) or type(maximum) not in (int, float)
            or not 0 <= minimum <= maximum <= 1_000_000_000_000):
        return None
    try:
        return Salary(currency=text(currency, 10).upper(), interval=text(interval, 40).lower() or None,
                      minimum=minimum, maximum=maximum)
    except ValidationError:
        return None


def normalized_post(item: dict, company: str, *, title: str, url: str, location: str,
                    work: Arrangement | None, compensation: Salary | None,
                    posted_at: datetime | None, description: str) -> Posting | None:
    job_id = item.get("id")
    if type(job_id) is int and 0 < job_id < 10**20:
        job_id = str(job_id)
    if not title or not url or not isinstance(job_id, str) or not re.fullmatch(ID_PATTERN, job_id):
        return None
    return Posting(external_id=job_id, title=title, company=text(item.get("company_name"), 120) or company,
                   location=location or None, arrangement=work, salary=compensation, url=url,
                   posted_at=posted_at, description=description)


def lever_post(item: dict, company: str) -> Posting | None:
    hosts = {"jobs.lever.co", "jobs.eu.lever.co"}
    url = safe_posting_url(item.get("hostedUrl"), hosts) or safe_posting_url(item.get("applyUrl"), hosts)
    categories = item.get("categories")
    locations = categories if isinstance(categories, dict) else {}
    all_locations = locations.get("allLocations")
    location = text(locations.get("location"), 240)
    if isinstance(all_locations, list):
        joined = ", ".join(filter(None, (text(place, 120) for place in all_locations[:20])))
        location = text(joined, 240) or location
    pay = item.get("salaryRange")
    compensation = salary(pay.get("currency"), pay.get("interval"), pay.get("min"), pay.get("max")) if isinstance(pay, dict) else None
    sections = item.get("lists")
    requirements = " ".join(plain_html(section.get("content")) for section in sections[:20]
                            if isinstance(section, dict)) if isinstance(sections, list) else ""
    description = text(" ".join((text(item.get("descriptionPlain"), 24_000) or plain_html(item.get("description")),
                                 requirements, text(item.get("additionalPlain"), 6000))), 24_000)
    return normalized_post(item, company, title=text(item.get("text"), 120), url=url, location=location,
                           work=arrangement(item.get("workplaceType")), compensation=compensation,
                           posted_at=published(item.get("publishedAt")), description=description)


def greenhouse_post(item: dict, company: str) -> Posting | None:
    location_data = item.get("location")
    location = text(location_data.get("name"), 240) if isinstance(location_data, dict) else ""
    metadata = item.get("metadata")
    workplace = {value for entry in metadata[:100]
                 if isinstance(entry, dict)
                 and text(entry.get("name"), 80).lower() in {"workplace type", "work arrangement", "workplace"}
                 and (value := arrangement(entry.get("value")))} if isinstance(metadata, list) else set()
    # A Greenhouse range has no documented pay period. Display only an unambiguous range.
    ranges = item.get("pay_input_ranges")
    pay = ranges[0] if isinstance(ranges, list) and len(ranges) == 1 else None
    compensation = None
    if isinstance(pay, dict):
        low, high = pay.get("min_cents"), pay.get("max_cents")
        if type(low) is int and type(high) is int and 0 <= low <= high <= 10**14:
            compensation = salary(pay.get("currency_type"), None, low / 100, high / 100)
    return normalized_post(item, company, title=text(item.get("title"), 120),
                           url=safe_posting_url(item.get("absolute_url")), location=location,
                           work=next(iter(workplace)) if len(workplace) == 1 else None, compensation=compensation,
                           posted_at=published(item.get("first_published")), description=plain_html(item.get("content")))

def retry_delay(value: str | None) -> float:
    if value:
        try:
            seconds = float(value)
            if math.isfinite(seconds):
                return max(1, min(seconds, 86_400))
        except ValueError:
            try:
                seconds = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
                return max(1, min(seconds, 86_400))
            except (TypeError, ValueError, OverflowError):
                pass
    return 60


def read_json(client: httpx.Client, provider: Provider, path: str, params: dict, deadline: float):
    host = HOSTS[provider]
    for attempt in range(2):
        if rate_until.get(host, 0) > time.monotonic():
            raise FeedError("Provider rate limit reached. Try refreshing later.")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise FeedError("Provider took too long to respond.")
        try:
            # Fixed HTTPS hosts, no redirects, and only a vetted board slug in the path.
            with client.stream("GET", f"https://{host}{path}", params=params, timeout=min(5, remaining),
                               follow_redirects=False, headers={"Accept": "application/json", "Accept-Encoding": "identity"}) as response:
                if response.status_code == 429:
                    seconds = retry_delay(response.headers.get("Retry-After"))
                    rate_until[host] = time.monotonic() + seconds
                    raise FeedRateLimit(seconds)
                if response.status_code == 404:
                    raise FeedError("Board not found. Check the board identifier and provider.")
                if response.status_code >= 500:
                    if attempt == 0 and deadline - time.monotonic() > 0.5:
                        time.sleep(0.25)
                        continue
                    raise FeedError("Provider is temporarily unavailable.")
                if response.status_code != 200:
                    raise FeedError("Provider refused the request or sent an unexpected redirect.")
                if response.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
                    raise FeedError("Provider did not return a JSON job feed.")
                body = bytearray()
                for chunk in response.iter_bytes():
                    if time.monotonic() > deadline:
                        raise FeedError("Provider took too long to respond.")
                    if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise FeedError("Provider response exceeded the safe size limit.")
                    body.extend(chunk)
                try:
                    return json.loads(body)
                except (ValueError, UnicodeError, RecursionError):
                    raise FeedError("Provider returned invalid JSON.") from None
        except httpx.TransportError:
            if attempt == 0 and deadline - time.monotonic() > 0.5:
                time.sleep(0.25)
                continue
            raise FeedError("Could not reach the provider. Try again later.") from None
        except httpx.HTTPError:
            raise FeedError("Could not read the provider response.") from None
    raise FeedError("Provider is temporarily unavailable.")


def greenhouse(client: httpx.Client, board: str, deadline: float) -> list:
    data = read_json(client, "greenhouse", f"/v1/boards/{board}/jobs", {"content": "true"}, deadline)
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(jobs, list):
        raise FeedError("Greenhouse returned an unexpected job feed.")
    if len(jobs) > 2500:
        raise FeedError("This board exceeds the 2,500-post Greenhouse limit.")
    return jobs


def lever(client: httpx.Client, provider: Provider, board: str, deadline: float) -> list:
    jobs = []
    for skip in range(0, 1100, 100):
        page = read_json(client, provider, f"/v0/postings/{board}",
                         {"mode": "json", "skip": skip, "limit": 100}, deadline)
        if not isinstance(page, list) or len(page) > 100:
            raise FeedError("Lever returned an unexpected job feed.")
        if skip == 1000 and page:
            raise FeedError("This board exceeds the 1,000-post Lever limit.")
        jobs.extend(page)
        if len(page) < 100:
            return jobs
    return jobs


def fetch_board(client: httpx.Client, provider: Provider, board: str, company: str) -> tuple[list[Posting], int]:
    if provider not in HOSTS or not re.fullmatch(BOARD_PATTERN, board):
        raise FeedError("Unsupported provider or board identifier.")
    deadline = time.monotonic() + 15
    items = greenhouse(client, board, deadline) if provider == "greenhouse" else lever(client, provider, board, deadline)
    normalize = greenhouse_post if provider == "greenhouse" else lever_post
    postings = {}
    rejected = 0
    for item in items:
        result = normalize(item, company) if isinstance(item, dict) else None
        if result is None:
            rejected += 1
        else:
            postings[result.external_id] = result
    if rejected and not postings:
        raise FeedError("The feed contained no valid listings.")
    return list(postings.values()), rejected
