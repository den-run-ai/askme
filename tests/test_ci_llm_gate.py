"""Unit tests for tests/ci_llm_gate.py (offline; no LLM or network needed)."""
import json

import ci_llm_gate


# --- fixtures ---

def _summary(model="google/gemma-4-26b-a4b-it", suite="hard",
             test="test_replan_build_with_dependency", total=1,
             pytest_passed=None, agent_complete=None, wall=(66.5,),
             cost=(0.00066688,), provider="", served=("SiliconFlow",)):
    """Build a dict shaped like a bench_harness summary.json."""
    pytest_passed = total if pytest_passed is None else pytest_passed
    agent_complete = total if agent_complete is None else agent_complete
    statuses = (["complete"] * agent_complete
                + ["incomplete"] * (total - agent_complete))
    return {
        "suite": suite, "backend": "openrouter", "trials": total,
        "model": model, "provider": provider,
        "allow_provider_fallbacks": None, "require_provider_parameters": None,
        "git_commit": "0" * 40, "git_dirty": False,
        "total_wall_s": sum(wall),
        "tests": {test: {
            "passed": pytest_passed, "pytest_passed": pytest_passed,
            "agent_complete": agent_complete, "total": total,
            "agent_status": statuses,
            "wall_s": list(wall), "replans": [0] * total,
            "local_replans": [0] * total, "local_replans_ok": [0] * total,
            "steps": [4] * total, "thinking_retries": [0] * total,
            "llm_calls": [8] * total,
            "prompt_tokens": [3976] * total, "completion_tokens": [375] * total,
            "total_tokens": [4351] * total,
            "openrouter_cost": list(cost),
            "served_models": [["google/gemma-4-26b-a4b-it-20260403"]] * total,
            "served_providers": [list(served)] * total,
        }},
    }


def _write(tmp_path, name, payload):
    path = tmp_path / name
    path.write_text(json.dumps(payload) if isinstance(payload, dict) else payload,
                    encoding="utf-8")
    return str(path)


class _Resp:
    def __init__(self, status_code):
        self.status_code = status_code


# --- preflight ---

class TestPreflight:
    def test_missing_key_fails_with_actionable_message(self):
        ok, message = ci_llm_gate.check_openrouter_key(
            env={}, get=lambda *a, **k: _Resp(200))
        assert not ok
        assert "OPENROUTER_API_KEY" in message
        assert "Openrouter" in message  # points at the deployment environment

    def test_blank_key_is_treated_as_missing(self):
        ok, _ = ci_llm_gate.check_openrouter_key(
            env={"OPENROUTER_API_KEY": "   "}, get=lambda *a, **k: _Resp(200))
        assert not ok

    def test_valid_key_passes_and_is_never_echoed(self):
        calls = {}

        def fake_get(url, headers=None, timeout=None):
            calls["url"] = url
            calls["auth"] = headers["Authorization"]
            return _Resp(200)

        ok, message = ci_llm_gate.check_openrouter_key(
            env={"OPENROUTER_API_KEY": "sk-or-v1-test"}, get=fake_get)
        assert ok
        assert calls["url"] == ci_llm_gate.OPENROUTER_MODELS_URL
        assert calls["auth"] == "Bearer sk-or-v1-test"
        assert "sk-or-v1-test" not in message

    def test_rejected_key_fails_with_status(self):
        ok, message = ci_llm_gate.check_openrouter_key(
            env={"OPENROUTER_API_KEY": "sk-bad"}, get=lambda *a, **k: _Resp(401))
        assert not ok
        assert "401" in message
        assert "sk-bad" not in message

    def test_network_error_fails_instead_of_skipping(self):
        def broken_get(*args, **kwargs):
            raise OSError("connection reset")

        ok, message = ci_llm_gate.check_openrouter_key(
            env={"OPENROUTER_API_KEY": "sk-x"}, get=broken_get)
        assert not ok
        assert "connection reset" in message

    def test_main_preflight_fails_without_key(self, monkeypatch, capsys):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        rc = ci_llm_gate.main(["preflight"])
        assert rc == 1
        assert "PREFLIGHT FAILED" in capsys.readouterr().out


# --- report gate ---

class TestReportGate:
    def test_all_cells_pass(self, tmp_path, capsys):
        paths = [
            _write(tmp_path, "build.json", _summary()),
            _write(tmp_path, "repair.json",
                   _summary(suite="medium", test="test_fix_python_syntax_error")),
        ]
        rc = ci_llm_gate.main(["report"] + paths + ["--expect-cells", "2"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "LLM GATE: PASS" in out
        assert "hard/test_replan_build_with_dependency" in out
        assert "medium/test_fix_python_syntax_error" in out

    def test_pytest_failure_fails_gate(self, tmp_path, capsys):
        path = _write(tmp_path, "s.json", _summary(pytest_passed=0))
        rc = ci_llm_gate.main(["report", path])
        out = capsys.readouterr().out
        assert rc == 1
        assert "LLM GATE: FAIL" in out
        assert "pytest 0/1" in out

    def test_agent_incomplete_fails_gate_even_when_pytest_passes(self, tmp_path):
        path = _write(tmp_path, "s.json", _summary(agent_complete=0))
        rc = ci_llm_gate.main(["report", path])
        assert rc == 1

    def test_partial_multi_trial_pass_fails_gate(self, tmp_path):
        path = _write(tmp_path, "s.json",
                      _summary(total=2, pytest_passed=1, agent_complete=2,
                               wall=(66.5, 70.1), cost=(0.0006, 0.0007)))
        rc = ci_llm_gate.main(["report", path])
        assert rc == 1

    def test_missing_cells_fail_gate(self, tmp_path, capsys):
        path = _write(tmp_path, "s.json", _summary())
        rc = ci_llm_gate.main(["report", path, "--expect-cells", "2"])
        assert rc == 1
        assert "expected 2 result cell(s), found 1" in capsys.readouterr().out

    def test_unreadable_summary_fails_gate(self, tmp_path):
        rc = ci_llm_gate.main(["report", str(tmp_path / "nope.json")])
        assert rc == 1

    def test_malformed_summary_fails_gate(self, tmp_path):
        path = _write(tmp_path, "bad.json", "{not json")
        rc = ci_llm_gate.main(["report", path])
        assert rc == 1

    def test_summary_without_tests_fails_gate(self, tmp_path):
        path = _write(tmp_path, "empty.json", {"suite": "hard", "tests": {}})
        rc = ci_llm_gate.main(["report", path])
        assert rc == 1

    def test_markdown_out_appends(self, tmp_path):
        path = _write(tmp_path, "s.json", _summary())
        out_file = tmp_path / "step_summary.md"
        assert ci_llm_gate.main(
            ["report", path, "--markdown-out", str(out_file)]) == 0
        assert ci_llm_gate.main(
            ["report", path, "--markdown-out", str(out_file)]) == 0
        text = out_file.read_text(encoding="utf-8")
        assert text.count("## LLM gate") == 2  # append, not overwrite
        assert "| Cell | Model | Provider |" in text

    def test_report_shows_served_providers(self, tmp_path, capsys):
        path = _write(tmp_path, "s.json",
                      _summary(provider="", served=("SiliconFlow",)))
        ci_llm_gate.main(["report", path])
        out = capsys.readouterr().out
        assert "auto → SiliconFlow" in out
