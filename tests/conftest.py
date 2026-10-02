import pytest

import server
from backend.vast import ALL_ENV_NAMES


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    for name in (*ALL_ENV_NAMES, "SCOUT_CATALOG_PATH"):
        monkeypatch.delenv(name, raising=False)
    server._vast_clients.clear()
    server._clip_sources.clear()
    yield
    server._vast_clients.clear()
    server._clip_sources.clear()
