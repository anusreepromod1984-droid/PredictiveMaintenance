"""Keep the API unlocked for unit tests unless a test explicitly enables auth."""

import pytest

from src.config import settings


@pytest.fixture(autouse=True)
def _open_api_for_tests(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_ENABLED", False)
    monkeypatch.setattr(settings, "OEM_MAIL_TO", "")
