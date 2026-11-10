"""Keep loopback protocol tests independent of host proxy settings."""

import pytest


@pytest.fixture(autouse=True)
def loopback_without_proxy(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost,::1")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost,::1")
