"""CI must not require a local database or a paid model endpoint."""

import httpx
import psycopg
import pytest


@pytest.fixture(autouse=True)
def block_external_services(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline tests must not connect to PostgreSQL or a real HTTP endpoint")
    monkeypatch.setattr(psycopg, 'connect', forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', forbidden)
