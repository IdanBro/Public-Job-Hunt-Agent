import pytest


@pytest.fixture(autouse=True)
def no_live_referrer_sheet(monkeypatch):
    """A developer's configured sheet must never make the tests call Google."""
    monkeypatch.delenv("JOBAGENT_REFERRER_SHEET", raising=False)
