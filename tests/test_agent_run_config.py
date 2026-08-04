"""Immutable, hash-logged per-run configuration (issue #68).

Every outcome-affecting setting — validation mode, the #41 compile-repair
arm, guard thresholds, capability budgets, and run limits — resolves once at
run start into a frozen surface whose hash is logged at run_start and
returned in the config metadata.
"""

import json
from unittest.mock import patch

import pytest
from _test_support import mock_response, mock_response_raw

import askme
from askme import (
    CapabilityProfile,
    GuardThresholds,
    LLMSettings,
    RunConfig,
    RunDependencies,
    _config_hash,
    _run_loop,
    get_capability_profile,
    run_result,
)

PLAN_DONE = [{"tasks": ["greet"]}, {"action": "done"}]


class ScriptedClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def ask(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        return self.replies.pop(0)


class ConfiguredScriptedClient(ScriptedClient):
    def __init__(self, replies, settings):
        super().__init__(replies)
        self.settings = settings


def _quiet_deps(**kwargs):
    kwargs.setdefault("log_sink", lambda msg: None)
    kwargs.setdefault("event_sink", lambda event: None)
    return RunDependencies(**kwargs)


def _settings(**overrides):
    fields = {
        "backend": "local",
        "api": "http://localhost:8080/v1/chat/completions",
        "model": "local-model",
        "api_key": "",
        "provider": "",
        "allow_fallbacks": True,
        "require_parameters": False,
        "reasoning_effort": "",
        "timeout": 120,
    }
    fields.update(overrides)
    return LLMSettings(**fields)


class TestGuardThresholds:
    def test_negative_thresholds_are_rejected(self):
        with pytest.raises(ValueError, match="observe_tail_reserve"):
            GuardThresholds(
                write_pressure_observations=3,
                observe_tail_reserve=-1,
                rewrite_pressure_writes=2,
                rewrite_skip_writes=3,
                max_task_local_replans=1,
            )

    def test_non_integer_thresholds_are_rejected(self):
        with pytest.raises(ValueError, match="rewrite_skip_writes"):
            GuardThresholds(
                write_pressure_observations=3,
                observe_tail_reserve=3,
                rewrite_pressure_writes=2,
                rewrite_skip_writes=True,
                max_task_local_replans=1,
            )

    def test_invalid_config_threshold_is_rejected_at_run_start(self, tmp_path):
        with pytest.raises(ValueError, match="write_pressure_observations"):
            run_result(
                "greet",
                working_dir=str(tmp_path),
                config=RunConfig(write_pressure_observations=-2),
            )


class TestConfigResolution:
    def test_default_resolution_follows_module_globals(self, tmp_path):
        controller = askme._RunController("greet", str(tmp_path))
        assert controller.final_validate == askme.FINAL_VALIDATE
        assert controller.compile_repair is askme.COMPILE_REPAIR_ENABLED
        assert controller.guards == GuardThresholds(
            write_pressure_observations=askme.WRITE_PRESSURE_OBSERVATIONS,
            observe_tail_reserve=askme.OBSERVE_TAIL_RESERVE,
            rewrite_pressure_writes=askme.REWRITE_PRESSURE_WRITES,
            rewrite_skip_writes=askme.REWRITE_SKIP_WRITES,
            max_task_local_replans=askme.MAX_TASK_LOCAL_REPLANS,
        )
        assert controller.run_state.rewrite_skip_writes == askme.REWRITE_SKIP_WRITES
        assert controller.run_state.rewrite_pressure_writes == askme.REWRITE_PRESSURE_WRITES

    def test_pinned_thresholds_reach_run_and_attempt_state(self, tmp_path):
        controller = askme._RunController(
            "greet",
            str(tmp_path),
            config=RunConfig(
                write_pressure_observations=1,
                observe_tail_reserve=0,
                rewrite_pressure_writes=5,
                rewrite_skip_writes=6,
                max_task_local_replans=0,
            ),
        )
        assert controller.guards.max_task_local_replans == 0
        assert controller.run_state.rewrite_skip_writes == 6
        assert controller.run_state.rewrite_pressure_writes == 5
        attempt = controller._new_attempt("implement feature")
        assert attempt.write_pressure_observations == 1
        attempt.observe_executed = 1
        assert attempt.write_pressure() is True

    def test_from_env_pins_validation_and_repair(self):
        config = RunConfig.from_env({"AGENT_FINAL_VALIDATE": "always", "AGENT_COMPILE_REPAIR": "0"})
        assert config.final_validate == "always"
        assert config.compile_repair is False

    @pytest.mark.parametrize(
        ("override", "message"),
        [
            ({"timeout": 0}, "timeout"),
            ({"timeout": True}, "timeout"),
            ({"replan_timeout": 0}, "replan_timeout"),
            ({"replan_timeout": False}, "replan_timeout"),
            ({"max_retries": -1}, "max_retries"),
            ({"max_retries": True}, "max_retries"),
            ({"step_token_budget": 0}, "step_token_budget"),
            ({"step_write_tokens": False}, "step_write_tokens"),
            ({"reasoning_token_floors": (1, 2)}, "reasoning_token_floors"),
            ({"reasoning_token_floors": (1, 2, 0)}, "reasoning_token_floors"),
            ({"reasoning_token_floors": (1, 2, True)}, "reasoning_token_floors"),
        ],
    )
    def test_invalid_resolved_llm_request_policy_is_rejected(self, tmp_path, override, message):
        with pytest.raises(ValueError, match=message):
            askme._RunController(
                "greet", str(tmp_path), config=RunConfig(llm=_settings(**override))
            )

    @pytest.mark.parametrize(
        ("name", "message"),
        [
            ("PLANNER_MAX_TOKENS", "planner_tokens"),
            ("TASK_REPLAN_MAX_TOKENS", "task_replan_tokens"),
            ("VALIDATION_MAX_TOKENS", "validation_tokens"),
        ],
    )
    def test_invalid_module_capability_budget_is_rejected(self, tmp_path, name, message):
        with patch.object(askme, name, 0):
            with pytest.raises(ValueError, match=message):
                askme._RunController("greet", str(tmp_path))

    @pytest.mark.parametrize(
        ("source", "provenance"),
        [
            ("pinned", "pinned_config"),
            ("injected", "injected_client_settings"),
        ],
    )
    def test_selected_llm_settings_ignore_invalid_module_fallback(
        self, tmp_path, source, provenance
    ):
        settings = _settings(
            timeout=30,
            replan_timeout=60,
            max_retries=1,
            step_token_budget=256,
            step_write_tokens=512,
            reasoning_token_floors=(1024, 1536, 2048),
        )
        kwargs = {"config": RunConfig(llm=settings)}
        if source == "injected":
            client = ScriptedClient([])
            client.settings = settings
            kwargs = {"dependencies": _quiet_deps(llm_client=client)}

        with (
            patch.object(askme, "LLM_TIMEOUT", 0),
            patch.object(askme, "STEP_TOKENS", 0),
        ):
            metadata = askme._RunController("greet", str(tmp_path), **kwargs).config_metadata()

        assert metadata["llm_provenance"] == provenance
        assert metadata["timeout_s"] == 30
        assert metadata["budgets"]["step_tokens"] == 256

    def test_pinned_final_validate_runs_without_touching_globals(self, tmp_path):
        """conftest disables validation globally; the pinned run still runs it."""
        client = ScriptedClient(
            [
                {"tasks": ["greet"]},
                {"action": "write", "arg": "hi.txt", "content": "hi"},
                {"action": "done"},
                {"valid": True},
            ]
        )
        assert askme.FINAL_VALIDATE == "0"
        result = run_result(
            "greet",
            working_dir=str(tmp_path),
            config=RunConfig(final_validate="always"),
            dependencies=_quiet_deps(llm_client=client),
        )
        assert result["status"] == "complete"
        assert result["outcome"]["validation"] == "passed"
        assert not client.replies
        assert client.calls[-1]["reasoning_trigger"] == "final_validator"

    def test_pinned_validate_off_skips_validation(self, tmp_path):
        client = ScriptedClient(
            [
                {"tasks": ["greet"]},
                {"action": "write", "arg": "hi.txt", "content": "hi"},
                {"action": "done"},
            ]
        )
        with patch.object(askme, "FINAL_VALIDATE", "always"):
            result = run_result(
                "greet",
                working_dir=str(tmp_path),
                config=RunConfig(final_validate="0"),
                dependencies=_quiet_deps(llm_client=client),
            )
        assert result["status"] == "complete"
        assert result["outcome"]["validation"] == "skipped"
        assert not client.replies

    @patch("askme.replan_task", return_value=askme.TaskReplanResult(None, "unknown"))
    @patch("askme.execute")
    @patch("askme.ask_llm")
    def test_pinned_compile_repair_off_disables_the_repair_arm(
        self, mock_llm, mock_execute, mock_replan, tmp_path
    ):
        """The #41 off arm pins per run while the module global stays on."""
        src = tmp_path / "main.c"
        original = 'int main(){ printf("hi"); return 0; }\n'
        src.write_text(original)
        mock_llm.side_effect = [
            {"tasks": ["compile main.c"]},
            {"action": "shell", "arg": "cc -o main main.c"},
            {"action": "fail", "reasoning": "compile error persists"},
        ]
        mock_execute.return_value = {
            "ok": False,
            "output": "main.c:1:13: error: implicit declaration of function 'printf'",
            "error_type": "compile_error",
        }
        assert askme.COMPILE_REPAIR_ENABLED is True
        result = run_result(
            "compile main.c",
            working_dir=str(tmp_path),
            config=RunConfig(compile_repair=False, max_replans=1, max_tasks=1, max_steps=3),
        )
        assert result["status"] == "exhausted"
        assert src.read_text() == original
        assert not any(s.get("deterministic_repair") for s in result["state"]["all_steps"])
        assert result["config"]["compile_repair"] is False

    def test_pinned_rewrite_skip_threshold_changes_the_guard(self, tmp_path):
        client = ScriptedClient(
            [
                {"tasks": ["implement feature in big.py"]},
                {"action": "write", "arg": "big.py", "content": "v1\n"},
                {"action": "write", "arg": "big.py", "content": "v2\n"},  # skipped
                {"action": "done"},
            ]
        )
        events = []
        result = run_result(
            "implement feature in big.py",
            working_dir=str(tmp_path),
            config=RunConfig(rewrite_skip_writes=1, max_replans=1),
            dependencies=_quiet_deps(llm_client=client, event_sink=events.append),
        )
        assert result["status"] == "complete"
        assert (tmp_path / "big.py").read_text() == "v1\n"
        assert any(e.get("reason") == "rewrite_loop" for e in events)

    def test_zero_task_local_replans_skips_the_mini_planner(self, tmp_path):
        client = ScriptedClient(
            [
                {"tasks": ["greet"]},
                {"action": "fail", "reasoning": "cannot"},
            ]
        )
        with patch("askme.replan_task") as mock_replan:
            result = run_result(
                "greet",
                working_dir=str(tmp_path),
                config=RunConfig(max_task_local_replans=0, max_replans=1, max_steps=2),
                dependencies=_quiet_deps(llm_client=client),
            )
        assert result["status"] == "exhausted"
        mock_replan.assert_not_called()

    def test_pinned_step_token_budget_reaches_the_executor_call(self, tmp_path):
        client = ScriptedClient(list(PLAN_DONE))
        client.settings = _settings(step_token_budget=99)
        result = run_result(
            "greet",
            working_dir=str(tmp_path),
            dependencies=_quiet_deps(llm_client=client),
        )
        assert result["status"] == "complete"
        step_calls = [c for c in client.calls if c.get("expect") == "action"]
        assert step_calls and step_calls[0]["max_tokens"] == 99
        assert result["config"]["budgets"]["step_tokens"] == 99


class TestConfigHash:
    def test_hash_is_canonical_over_key_order(self):
        assert _config_hash({"a": 1, "b": [1, 2]}) == _config_hash({"b": [1, 2], "a": 1})
        assert _config_hash({"a": 1}) != _config_hash({"a": 2})

    def _metadata(self, tmp_path, config=None):
        return askme._RunController("greet", str(tmp_path), config=config).config_metadata()

    def test_metadata_carries_hash_and_resolved_settings(self, tmp_path):
        metadata = self._metadata(tmp_path)
        assert metadata["config_hash"]
        assert metadata["final_validate"] == askme.FINAL_VALIDATE
        assert metadata["compile_repair"] is askme.COMPILE_REPAIR_ENABLED
        assert metadata["guards"]["max_task_local_replans"] == askme.MAX_TASK_LOCAL_REPLANS
        assert metadata["budgets"]["planner_max_tokens"] == askme.PLANNER_MAX_TOKENS
        assert metadata["budgets"]["step_tokens"] == askme.STEP_TOKENS
        assert metadata["budgets"]["step_write_tokens"] == askme.STEP_WRITE_TOKENS
        assert metadata["capability_profile"] == askme._DEFAULT_CAPABILITY_PROFILE.describe()

    def test_profile_owns_all_model_output_and_reasoning_budgets(self, tmp_path):
        profile = CapabilityProfile(
            name="test-profile-v1",
            step_tokens=101,
            step_write_tokens=202,
            planner_tokens=303,
            task_replan_tokens=404,
            validation_tokens=505,
            reasoning_token_floors=(707, 808, 909),
        )
        metadata = self._metadata(
            tmp_path,
            config=RunConfig(llm=_settings(capability_profile=profile)),
        )
        assert metadata["capability_profile"] == profile.describe()
        assert metadata["budgets"] == {
            "step_tokens": 101,
            "step_write_tokens": 202,
            "planner_max_tokens": 303,
            "task_replan_max_tokens": 404,
            "final_validation_max_tokens": 505,
            "llm_max_retries": askme.MAX_LLM_RETRIES,
            "reasoning_token_floors": {"low": 707, "medium": 808, "high": 909},
        }
        assert metadata["limits"]["goal_context_chars"] == askme.GOAL_CONTEXT_CHARS

    def test_direct_helpers_use_the_injected_clients_profile_budgets(self, tmp_path):
        profile = CapabilityProfile(
            name="direct-helper-profile-v1",
            step_tokens=101,
            step_write_tokens=202,
            planner_tokens=303,
            task_replan_tokens=404,
            validation_tokens=505,
        )
        settings = _settings(capability_profile=profile)
        state = {
            "completed_tasks": [],
            "completed_step_groups": [],
            "errors": [],
            "all_steps": [],
        }

        planner = ConfiguredScriptedClient([{"tasks": []}], settings)
        askme.get_plan("plan work", state, client=planner)
        replanner = ConfiguredScriptedClient([{"task": "implement alternative"}], settings)
        askme.replan_task("fix behavior", ["[unknown] failed"], [], state, "goal", client=replanner)
        validator = ConfiguredScriptedClient([{"valid": True}], settings)
        askme._validate_completion("deliver artifact", state, tmp_path, client=validator)

        assert planner.calls[0]["max_tokens"] == 303
        assert replanner.calls[0]["max_tokens"] == 404
        assert validator.calls[0]["max_tokens"] == 505

    def test_same_profile_has_same_budgets_across_backends(self, tmp_path):
        profile = get_capability_profile("generic-feature-scale-v1")
        local = self._metadata(
            tmp_path,
            config=RunConfig(llm=_settings(capability_profile=profile)),
        )
        remote = self._metadata(
            tmp_path,
            config=RunConfig(
                llm=_settings(
                    backend="openrouter",
                    api="https://example.test/v1/chat/completions",
                    provider="ProviderA",
                    capability_profile=profile,
                )
            ),
        )
        assert local["budgets"] == remote["budgets"]
        assert local["capability_profile"] == remote["capability_profile"]
        assert local["config_hash"] != remote["config_hash"]  # transport identity still differs

    def test_profile_changes_hash_on_the_same_backend(self, tmp_path):
        legacy = self._metadata(
            tmp_path,
            config=RunConfig(
                llm=_settings(capability_profile=get_capability_profile("legacy-e4b-m1-16k-v1"))
            ),
        )
        general = self._metadata(
            tmp_path,
            config=RunConfig(
                llm=_settings(capability_profile=get_capability_profile("generic-feature-scale-v1"))
            ),
        )
        assert legacy["config_hash"] != general["config_hash"]
        assert legacy["budgets"] != general["budgets"]

    def test_hash_is_stable_for_identical_configuration(self, tmp_path):
        assert self._metadata(tmp_path)["config_hash"] == self._metadata(tmp_path)["config_hash"]

    @pytest.mark.parametrize(
        "override",
        [
            {"final_validate": "always"},
            {"compile_repair": False},
            {"step_policy": "lifecycle"},
            {"rewrite_skip_writes": 9},
            {"max_steps": 4},
            {"reasoning_policy": "off"},
        ],
    )
    def test_hash_changes_with_outcome_affecting_settings(self, tmp_path, override):
        default_hash = self._metadata(tmp_path)["config_hash"]
        changed_hash = self._metadata(tmp_path, config=RunConfig(**override))["config_hash"]
        assert default_hash != changed_hash

    def test_credentials_never_reach_the_hash_or_metadata(self, tmp_path):
        with_key = self._metadata(tmp_path, config=RunConfig(llm=_settings(api_key="sk-secret")))
        without_key = self._metadata(tmp_path, config=RunConfig(llm=_settings(api_key="")))
        assert with_key["config_hash"] == without_key["config_hash"]
        assert "sk-secret" not in json.dumps(with_key)

    @pytest.mark.parametrize(
        "override",
        [
            {"api": "http://localhost:9999/v1/chat/completions"},
            {"timeout": 15},
        ],
    )
    def test_endpoint_identity_and_timeout_reach_the_hash(self, tmp_path, override):
        """PR #72 review: two runs differing only in endpoint or deadline
        must not share provenance."""
        base = self._metadata(tmp_path, config=RunConfig(llm=_settings()))
        changed = self._metadata(tmp_path, config=RunConfig(llm=_settings(**override)))
        assert base["config_hash"] != changed["config_hash"]
        assert changed["api"] == override.get("api", base["api"])
        assert changed["timeout_s"] == override.get("timeout", base["timeout_s"])

    @pytest.mark.parametrize(
        ("override", "section", "key", "expected"),
        [
            ({"replan_timeout": 45}, "timeouts_s", "planner_replan", 45),
            ({"max_retries": 4}, "retry_budgets", "planner", 4),
            ({"step_token_budget": 333}, "budgets", "step_tokens", 333),
            ({"step_write_tokens": 777}, "budgets", "step_write_tokens", 777),
            (
                {"reasoning_token_floors": (111, 222, 333)},
                "budgets",
                "reasoning_token_floors",
                {"low": 111, "medium": 222, "high": 333},
            ),
        ],
    )
    def test_request_policy_and_capability_budgets_reach_hash(
        self, tmp_path, override, section, key, expected
    ):
        base = self._metadata(tmp_path, config=RunConfig(llm=_settings()))
        changed = self._metadata(tmp_path, config=RunConfig(llm=_settings(**override)))
        assert changed[section][key] == expected
        assert changed["config_hash"] != base["config_hash"]

    def test_reasoning_floor_is_recorded_and_applied_to_http_budget(self, tmp_path):
        settings = _settings(
            backend="openrouter",
            api="https://example.test/v1/chat/completions",
            model="vendor/reasoner",
            provider="ProviderA",
            reasoning_effort="low",
            step_token_budget=16,
            reasoning_token_floors=(111, 222, 333),
        )
        bodies = []

        def post(url, json=None, headers=None, timeout=None):
            bodies.append(json)
            reply = {"tasks": ["greet"]} if len(bodies) == 1 else {"action": "done"}
            return mock_response(reply)

        with patch("askme.requests.post", side_effect=post):
            result = run_result(
                "greet",
                working_dir=str(tmp_path),
                config=RunConfig(llm=settings, final_validate="0"),
                dependencies=_quiet_deps(),
            )

        assert result["status"] == "complete"
        assert bodies[1]["max_tokens"] == 111  # requested step budget 16, low floor 111
        assert result["config"]["budgets"]["reasoning_token_floors"] == {
            "low": 111,
            "medium": 222,
            "high": 333,
        }

    def test_local_reasoning_floor_is_recorded_and_applied(self, tmp_path):
        profile = CapabilityProfile(
            name="local-floor-v1",
            step_tokens=16,
            step_write_tokens=32,
            reasoning_token_floors=(11, 22, 33),
        )
        settings = _settings(capability_profile=profile, max_retries=1)
        replies = [
            mock_response({"tasks": ["greet"]}),
            mock_response_raw("not json"),
            mock_response({"action": "done"}),
        ]
        bodies = []

        def post(url, json=None, headers=None, timeout=None):
            bodies.append(json)
            return replies.pop(0)

        with patch("askme.requests.post", side_effect=post):
            result = run_result(
                "greet",
                working_dir=str(tmp_path),
                config=RunConfig(llm=settings, final_validate="0"),
                dependencies=_quiet_deps(),
            )

        assert result["status"] == "complete"
        assert [body["max_tokens"] for body in bodies] == [768, 16, 22]
        assert result["config"]["budgets"]["reasoning_token_floors"] == {
            "low": 11,
            "medium": 22,
            "high": 33,
        }

    def test_final_validation_budget_reaches_hash(self, tmp_path):
        base = self._metadata(tmp_path)
        with patch.object(askme, "VALIDATION_MAX_TOKENS", askme.VALIDATION_MAX_TOKENS + 1):
            changed = self._metadata(tmp_path)
        assert changed["budgets"]["final_validation_max_tokens"] == (
            base["budgets"]["final_validation_max_tokens"] + 1
        )
        assert changed["config_hash"] != base["config_hash"]

    def test_llm_provenance_marks_opaque_injected_clients(self, tmp_path):
        """PR #72 review: a duck-typed client without settings cannot borrow
        the module snapshot's identity silently — the payload labels it."""
        default = askme._RunController("greet", str(tmp_path)).config_metadata()
        assert default["llm_provenance"] == "module_snapshot"
        pinned = askme._RunController(
            "greet", str(tmp_path), config=RunConfig(llm=_settings())
        ).config_metadata()
        assert pinned["llm_provenance"] == "pinned_config"
        opaque_client = ScriptedClient([])
        opaque = askme._RunController(
            "greet",
            str(tmp_path),
            dependencies=_quiet_deps(llm_client=opaque_client),
        ).config_metadata()
        assert opaque["llm_provenance"] == "injected_opaque"
        assert opaque["config_hash"] != default["config_hash"]
        settings_client = ScriptedClient([])
        settings_client.settings = _settings(model="scripted-model")
        with_settings = askme._RunController(
            "greet",
            str(tmp_path),
            dependencies=_quiet_deps(llm_client=settings_client),
        ).config_metadata()
        assert with_settings["llm_provenance"] == "injected_client_settings"
        assert with_settings["model"] == "scripted-model"

    def test_run_start_event_pins_the_hash_and_config(self, tmp_path):
        log_path = tmp_path / "run.jsonl"
        responses = [{"tasks": ["greet"]}, {"action": "done"}]
        with (
            patch.object(askme, "RUN_LOG_PATH", str(log_path)),
            patch("askme.ask_llm", side_effect=responses),
        ):
            result = _run_loop("greet", str(tmp_path), max_replans=1)
        assert result["status"] == "complete"
        run_start = json.loads(log_path.read_text().splitlines()[0])
        assert run_start["event"] == "run_start"
        assert run_start["config_hash"] == result["config"]["config_hash"]
        assert run_start["final_validate"] == askme.FINAL_VALIDATE
        assert run_start["guards"] == result["config"]["guards"]
        assert run_start["budgets"] == result["config"]["budgets"]
        assert run_start["policy"] == result["config"]["policy"]

    def test_metadata_is_a_defensive_copy(self, tmp_path):
        controller = askme._RunController("greet", str(tmp_path))
        first = controller.config_metadata()
        first["guards"]["rewrite_skip_writes"] = 99
        first["limits"]["max_steps"] = 99
        second = controller.config_metadata()
        assert second["guards"]["rewrite_skip_writes"] == askme.REWRITE_SKIP_WRITES
        assert second["limits"]["max_steps"] == askme.MAX_STEPS


class TestConfigClosure:
    """Issue #69: no outcome-affecting module global is read after run
    construction — resolved budgets are threaded into every LLM-backed
    helper call the controller makes."""

    def test_resolved_budgets_reach_every_llm_call_site(self, tmp_path):
        client = ScriptedClient(
            [
                {"tasks": ["greet"]},
                {"action": "fail", "reasoning": "cannot"},
                {"task": ""},  # task-local replan rejected: empty
            ]
        )
        result = run_result(
            "greet",
            working_dir=str(tmp_path),
            config=RunConfig(max_replans=1, max_tasks=2, max_steps=2),
            dependencies=_quiet_deps(llm_client=client),
        )
        assert result["status"] == "exhausted"
        budgets = result["config"]["budgets"]
        by_expect = {c.get("expect"): c["max_tokens"] for c in client.calls}
        assert by_expect["plan"] == budgets["planner_max_tokens"]
        assert by_expect["action"] == budgets["step_tokens"]
        assert by_expect["task_replan"] == budgets["task_replan_max_tokens"]
        # The plan schema receives the run's task limit (PR #75 review).
        plan_call = next(c for c in client.calls if c.get("expect") == "plan")
        assert plan_call["expect_context"] == {"max_tasks": 2}

    def test_mid_run_global_mutation_cannot_change_budgets(self, tmp_path):
        """A hostile mid-run change to the module budget globals must not
        reach later calls of the same run; only the construction-time
        resolution (recorded in the hash-logged payload) applies."""
        original = {
            "planner": askme.PLANNER_MAX_TOKENS,
            "step": askme.STEP_TOKENS,
            "replan": askme.TASK_REPLAN_MAX_TOKENS,
        }

        class MutatingClient(ScriptedClient):
            def ask(self, messages, **kwargs):
                reply = super().ask(messages, **kwargs)
                askme.PLANNER_MAX_TOKENS = 1
                askme.STEP_TOKENS = 1
                askme.TASK_REPLAN_MAX_TOKENS = 1
                return reply

        client = MutatingClient(
            [
                {"tasks": ["greet"]},
                {"action": "fail", "reasoning": "cannot"},
                {"task": ""},  # task-local replan rejected: empty
            ]
        )
        with (
            patch.object(askme, "PLANNER_MAX_TOKENS", askme.PLANNER_MAX_TOKENS),
            patch.object(askme, "STEP_TOKENS", askme.STEP_TOKENS),
            patch.object(askme, "TASK_REPLAN_MAX_TOKENS", askme.TASK_REPLAN_MAX_TOKENS),
        ):
            result = run_result(
                "greet",
                working_dir=str(tmp_path),
                config=RunConfig(max_replans=1, max_steps=2),
                dependencies=_quiet_deps(llm_client=client),
            )
        assert result["status"] == "exhausted"
        by_expect = {c.get("expect"): c["max_tokens"] for c in client.calls}
        assert by_expect["plan"] == original["planner"]
        assert by_expect["action"] == original["step"]
        assert by_expect["task_replan"] == original["replan"]

    def test_default_run_freezes_transport_policy_and_all_call_budgets(self, tmp_path):
        """The compatibility facade uses one construction-time snapshot.

        Mutating every public LLM global after the first HTTP request cannot
        redirect or resize later requests, and the recorded config describes
        the values that actually reached the transport.
        """
        frozen = {
            "api": "https://frozen.example/v1/chat/completions",
            "model": "vendor/model-a",
            "provider": "ProviderA",
            "api_key": "key-a",
            "timeout": 31,
            "replan_timeout": 47,
            "max_retries": 1,
            "step_tokens": 91,
            "step_write_tokens": 93,
            "planner_tokens": 95,
            "task_replan_tokens": 97,
            "validation_tokens": 99,
        }
        replies = [
            mock_response({"tasks": ["greet"]}),
            mock_response_raw("not json"),  # executor retry proves its budget stayed at one
            mock_response({"action": "fail", "reasoning": "cannot"}),
            mock_response({"task": ""}),  # rejected task-local replan
            mock_response({"tasks": ["create greeting in hi.txt"]}),
            mock_response({"action": "write", "arg": "hi.txt", "content": "hi"}),
            mock_response({"action": "done"}),
            mock_response({"valid": True}),
        ]
        calls = []

        def post(url, json=None, headers=None, timeout=None):
            calls.append(
                {
                    "url": url,
                    "model": json["model"],
                    "provider": json.get("provider"),
                    "authorization": (headers or {}).get("Authorization"),
                    "timeout": timeout,
                    "max_tokens": json["max_tokens"],
                }
            )
            if len(calls) == 1:
                # These mutations happen after run_start/config hashing but
                # before every executor/replanner/validator request.
                askme.API = "https://mutated.example/v1/chat/completions"
                askme.MODEL = "vendor/model-b"
                askme.OPENROUTER_PROVIDER = "ProviderB"
                askme.OPENROUTER_API_KEY = "key-b"
                askme.OPENROUTER_ALLOW_FALLBACKS = True
                askme.OPENROUTER_REQUIRE_PARAMETERS = False
                askme.LLM_TIMEOUT = 1
                askme.LLM_TIMEOUT_REPLAN = 2
                askme.MAX_LLM_RETRIES = 0
                askme.STEP_TOKENS = 1
                askme.STEP_WRITE_TOKENS = 1
                askme.PLANNER_MAX_TOKENS = 1
                askme.TASK_REPLAN_MAX_TOKENS = 1
                askme.VALIDATION_MAX_TOKENS = 1
            return replies.pop(0)

        with (
            patch.multiple(
                askme,
                LLM_BACKEND="openrouter",
                API=frozen["api"],
                MODEL=frozen["model"],
                OPENROUTER_PROVIDER=frozen["provider"],
                OPENROUTER_API_KEY=frozen["api_key"],
                OPENROUTER_ALLOW_FALLBACKS=False,
                OPENROUTER_REQUIRE_PARAMETERS=True,
                OPENROUTER_REASONING_EFFORT="",
                LLM_TIMEOUT=frozen["timeout"],
                LLM_TIMEOUT_REPLAN=frozen["replan_timeout"],
                MAX_LLM_RETRIES=frozen["max_retries"],
                STEP_TOKENS=frozen["step_tokens"],
                STEP_WRITE_TOKENS=frozen["step_write_tokens"],
                PLANNER_MAX_TOKENS=frozen["planner_tokens"],
                TASK_REPLAN_MAX_TOKENS=frozen["task_replan_tokens"],
                VALIDATION_MAX_TOKENS=frozen["validation_tokens"],
                REASONING_TOKEN_FLOORS=(1024, 1536, 2048),
            ),
            patch("askme.requests.post", side_effect=post),
        ):
            result = run_result(
                "implement greeting",
                working_dir=str(tmp_path),
                config=RunConfig(
                    reasoning_policy="off",
                    final_validate="always",
                    max_replans=2,
                    max_steps=2,
                ),
            )

        assert result["status"] == "complete"
        assert not replies
        assert {call["url"] for call in calls} == {frozen["api"]}
        assert {call["model"] for call in calls} == {frozen["model"]}
        assert {call["authorization"] for call in calls} == {f"Bearer {frozen['api_key']}"}
        assert {tuple(call["provider"]["order"]) for call in calls} == {(frozen["provider"],)}
        assert all(call["provider"]["allow_fallbacks"] is False for call in calls)
        assert all(call["provider"]["require_parameters"] is True for call in calls)
        assert [call["timeout"] for call in calls] == [31, 31, 31, 31, 47, 31, 31, 31]
        assert [call["max_tokens"] for call in calls] == [95, 91, 91, 97, 95, 91, 91, 99]

        config = result["config"]
        assert config["api"] == frozen["api"]
        assert config["model"] == frozen["model"]
        assert config["provider"] == frozen["provider"]
        assert config["timeouts_s"] == {
            "planner_initial": 31,
            "planner_replan": 47,
            "executor": 31,
            "task_replan": 31,
            "final_validation": 31,
        }
        assert config["retry_budgets"] == {
            "planner": 1,
            "executor": 1,
            "task_replan": 0,
            "final_validation": 0,
        }
        assert config["budgets"] == {
            "step_tokens": 91,
            "step_write_tokens": 93,
            "planner_max_tokens": 95,
            "task_replan_max_tokens": 97,
            "final_validation_max_tokens": 99,
            "llm_max_retries": 1,
            "reasoning_token_floors": {"low": 1024, "medium": 1536, "high": 2048},
        }
        assert frozen["api_key"] not in json.dumps(config)
