import os

import pytest

from conftest import disposable_url

SAFE = "postgresql://tester@127.0.0.1:55439/jobpilot_test"


@pytest.mark.parametrize("url", [
    "", SAFE.replace("55439", "5433"), SAFE.replace(":55439", ""),
    SAFE.replace("jobpilot_test", "jobpilot"), SAFE.replace("127.0.0.1", "remote.invalid"),
    SAFE + "?hostaddr=192.0.2.1", "service=release", SAFE.replace("tester@", ""),
])
def test_unsafe_test_targets_rejected(url):
    with pytest.raises(pytest.UsageError):
        disposable_url(url, None)


def test_application_database_cannot_be_reused():
    with pytest.raises(pytest.UsageError):
        disposable_url(SAFE, SAFE.replace("tester@", "other@"))


def test_unit_tests_have_no_application_connection():
    assert "DATABASE_URL" not in os.environ
    assert disposable_url(SAFE, None) == SAFE
