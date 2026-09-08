"""Characterize configuration/composition ordering without running an agent."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

import askme
import loop


@pytest.mark.parametrize(
    ("changes", "expected_calls", "message"),
    [
        ({"max_steps": 0}, [], "max_steps"),
        ({"goal_context_chars": 0}, ["resolve"], "goal_context_chars"),
        ({"step_policy": "invalid"}, ["resolve", "client", "clock"], "step_policy"),
        (
            {"write_pressure_observations": -1},
            ["resolve", "client", "clock"],
            "write_pressure_observations",
        ),
    ],
)
def test_setup_preserves_validation_and_factory_order(tmp_path, changes, expected_calls, message):
    calls = []
    settings = askme.LLMSettings.from_env({})

    def resolve(selected):
        calls.append("resolve")
        assert selected is settings
        return askme._resolve_run_llm_settings(selected)

    def client(**kwargs):
        calls.append("client")
        return object()

    def clock():
        calls.append("clock")
        return lambda: 0.0

    hooks = replace(
        askme._loop_hooks(), resolve_llm_settings=resolve, make_client=client, clock_factory=clock
    )
    with pytest.raises(ValueError, match=message):
        loop._RunController(
            "greet",
            str(tmp_path),
            config=askme.RunConfig(llm=settings, **changes),
            defaults=askme._loop_defaults(),
            hooks=hooks,
        )
    assert calls == expected_calls
    assert list(tmp_path.iterdir()) == []


def test_workspace_mismatch_rejects_after_client_but_before_clock(tmp_path):
    calls = []
    hooks = replace(
        askme._loop_hooks(),
        make_client=lambda **kwargs: calls.append("client"),
        clock_factory=lambda: calls.append("clock"),
    )
    with pytest.raises(ValueError, match="different directory"):
        loop._RunController(
            "greet",
            str(tmp_path),
            config=askme.RunConfig(llm=askme.LLMSettings.from_env({})),
            dependencies=askme.RunDependencies(
                action_executor=SimpleNamespace(working_dir=str(tmp_path / "other"))
            ),
            defaults=askme._loop_defaults(),
            hooks=hooks,
        )
    assert calls == ["client"]
    assert list(tmp_path.iterdir()) == []


def test_injected_settings_property_is_read_once_and_keeps_provenance(tmp_path):
    settings = askme.LLMSettings.from_env({})
    reads = []

    class Client:
        @property
        def settings(self):
            reads.append("settings")
            return settings

    controller = askme._RunController(
        "greet",
        str(tmp_path),
        config=askme.RunConfig(allow_network=False, compile_repair=False, observe_tail_reserve=0),
        dependencies=askme.RunDependencies(llm_client=Client()),
    )
    metadata = controller.config_metadata()
    assert reads == ["settings"]
    assert metadata["llm_provenance"] == "injected_client_settings"
    assert metadata["policy"]["allow_network"] is False
    assert metadata["compile_repair"] is False
    assert metadata["guards"]["observe_tail_reserve"] == 0
    assert controller._budgets is controller._config_payload["budgets"]
    metadata["budgets"]["step_tokens"] = -1
    assert controller.config_metadata()["budgets"]["step_tokens"] > 0
