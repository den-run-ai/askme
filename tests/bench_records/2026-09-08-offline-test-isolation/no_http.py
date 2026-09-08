"""Temporary audit plugin: fail before any requests socket sends data."""

from urllib.parse import urlsplit

import pytest
import requests


def pytest_configure(config):
    def blocked_send(session, request, **kwargs):
        endpoint = urlsplit(request.url)
        pytest.fail(
            "OFFLINE HTTP TRIPWIRE blocked "
            + request.method
            + " "
            + str(endpoint.hostname)
            + endpoint.path,
            pytrace=True,
        )

    requests.sessions.Session.send = blocked_send
