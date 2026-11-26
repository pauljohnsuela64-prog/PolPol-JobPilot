"""Browser boundary for the loopback-only workspace API."""
import re

from fastapi import HTTPException, Request

AUTHORITY = re.compile(r"(?:localhost|127\.0\.0\.1|\[::1\])(?::([0-9]{1,5}))?", re.I)


def local_authority(value: str) -> bool:
    match = AUTHORITY.fullmatch(value)
    return bool(match and (match[1] is None or 1 <= int(match[1]) <= 65535))


def private_request(request: Request):
    headers = request.headers
    hosts = headers.getlist("host")
    origins = headers.getlist("origin")
    sites = headers.getlist("sec-fetch-site")
    if (len(hosts) != 1 or not local_authority(hosts[0]) or len(origins) > 1
            or len(sites) > 1 or (sites and sites[0] not in {"same-origin", "none"})):
        raise HTTPException(403, "Use the local JobPilot window.")
    if origins:
        origin = origins[0]
        # Vite may forward a different Host while the browser sees a same-origin fetch.
        if not origin.startswith("http://") or not local_authority(origin[7:]):
            raise HTTPException(403, "Use the local JobPilot window.")
        if origin[7:].lower() != hosts[0].lower() and sites != ["same-origin"]:
            raise HTTPException(403, "Use the local JobPilot window.")
