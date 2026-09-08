"""Requests traffic requires both live-test authorization and a live marker."""

from types import SimpleNamespace

import conftest
import pytest
import requests
from _test_support import mock_llm_response


def test_unmocked_http_is_blocked_before_transport(monkeypatch):
    sent = []

    def transport(*args, **kwargs):
        sent.append(True)
        raise AssertionError("Reached transport before the offline guard")

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", transport)
    with pytest.raises(pytest.fail.Exception, match="Unmocked HTTP blocked") as failure:
        requests.post(
            "http://127.0.0.1:8080/v1/chat/completions?key=synthetic-query-secret",
            headers={"Authorization": "Bearer synthetic-header-secret"},
            json={"content": "synthetic-body-secret"},
            timeout=1,
        )
    assert not sent
    assert "secret" not in str(failure.value)


def test_mocked_provider_boundary_remains_available(monkeypatch):
    import askme

    calls = []

    def post(*args, **kwargs):
        calls.append(kwargs["json"])
        return mock_llm_response({"task": ""})

    monkeypatch.setattr(requests, "post", post)
    assert askme.ask_llm([{"role": "user", "content": "Synthetic request"}]) == {"task": ""}
    assert len(calls) == 1


@pytest.mark.parametrize(
    "enabled,marked", [(False, False), (False, True), (True, False), (True, True)]
)
def test_only_explicitly_enabled_live_tests_can_reach_transport(monkeypatch, enabled, marked):
    sent = []

    def transport(*args, **kwargs):
        sent.append(True)
        return SimpleNamespace(status_code=204)

    context = SimpleNamespace(
        node=SimpleNamespace(get_closest_marker=lambda name: object() if marked else None)
    )
    monkeypatch.setattr(conftest, "live_llm_enabled", enabled)
    monkeypatch.setattr(requests.sessions.Session, "send", transport)
    conftest.block_unmocked_http.__wrapped__(context, monkeypatch)
    if enabled and marked:
        assert requests.post("http://127.0.0.1:8080/unused", timeout=1).status_code == 204
        assert sent == [True]
    else:
        with pytest.raises(pytest.fail.Exception, match="Unmocked HTTP blocked"):
            requests.post("http://127.0.0.1:8080/unused", timeout=1)
        assert not sent
