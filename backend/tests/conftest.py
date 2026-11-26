"""Isolate all pytest application connections and file writes from normal startup."""
import os
import re

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict


def disposable_url(url, normal):
    message = ("Use an explicit local PostgreSQL test URL with user, numeric loopback host, "
               "port other than 5433, and a separate database ending in _test. "
               "Use no query parameters or service settings.")
    if not url.startswith(("postgresql://", "postgres://")) or any(c in url for c in "?#"):
        raise pytest.UsageError(message)
    try:
        target = conninfo_to_dict(url)
        app = conninfo_to_dict(normal) if normal else {}
    except psycopg.Error:
        raise pytest.UsageError(message) from None
    port = target.get("port", "")
    name = target.get("dbname", "")
    if (target.keys() - {"host", "user", "password", "port", "dbname"}
            or target.get("host") not in {"127.0.0.1", "::1"} or not target.get("user")
            or not re.fullmatch(r"[0-9]{1,5}", port) or not 1 <= int(port) <= 65535
            or int(port) == 5433 or not re.fullmatch(r"[a-z][a-z0-9_]*_test", name)
            or (normal and (not app.get("dbname") or app.get("service") or app["dbname"] == name))):
        raise pytest.UsageError(message)
    return url


@pytest.fixture(scope="session", autouse=True)
def isolated_environment(tmp_path_factory):
    url = os.environ.get("JOBPILOT_TEST_DATABASE_URL")
    if url is not None:
        disposable_url(url, os.getenv("DATABASE_URL"))
    with pytest.MonkeyPatch.context() as env:
        env.delenv("DATABASE_URL", raising=False)
        # In particular, PGHOSTADDR and PGSERVICE can redirect a libpq connection.
        for key in list(os.environ):
            if key.startswith("PG") and key not in {"PGPASSWORD", "PGPASSFILE"}:
                env.delenv(key)
        env.setenv("JOBPILOT_RESUME_DIR", str(tmp_path_factory.mktemp("resumes")))
        yield url


@pytest.fixture
def test_database(isolated_environment, monkeypatch):
    if not isolated_environment:
        pytest.skip("Set JOBPILOT_TEST_DATABASE_URL to a migrated disposable database")
    monkeypatch.setenv("DATABASE_URL", isolated_environment)
    return isolated_environment
