"""Suite-wide fixtures."""
import pytest


@pytest.fixture(autouse=True)
def _instant_humanizer(monkeypatch):
    """Humanizer sleeps are no-ops in tests — timing stays instant.

    Bridge modules bind `asleep` at import; test_bridges re-execs its module
    inside each test, so it picks up this patch automatically.
    """
    async def _noop(seconds=0.0):
        return None
    monkeypatch.setattr("godquant.companion.humanize.asleep", _noop)
