#!/usr/bin/env python3
"""CI preflight and result gate for OpenRouter-backed LLM tests.

Used by .github/workflows/llm.yml. Two subcommands:

  preflight   Fail loudly when OPENROUTER_API_KEY is missing or rejected by
              OpenRouter. Backend-dependent pytest tests skip silently when
              the credential is absent (see tests/conftest.py); in CI a
              missing credential must be an error, not a green no-op run.

  report      Aggregate one or more bench_harness summary.json files,
              enforce the Berkeley smoke pass rule per result cell — every
              trial must be a pytest pass AND an agent completion (the
              independent acceptance checks are asserted inside the pytest
              tests themselves) — and emit a markdown table suitable for
              GITHUB_STEP_SUMMARY. Exits nonzero on any failure by default.
              --advisory-cell-failures suppresses only valid cell outcome
              failures; malformed evidence and cell-count mismatches remain
              blocking.

The pass rule mirrors talks/berkeley-agentic-ai-summit-2026/evals/README.md:
a reported pass requires pytest success, agent_complete, and the
deterministic acceptance check (embedded in the pytest assertions).
"""

import argparse
import json
import os
import statistics
import sys
from typing import Any

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
BERKELEY_CAPABILITY_PROFILE = "generic-feature-scale-v1"


# --- preflight ---


def check_openrouter_key(env=None, get=None):
    """Return (ok, message). The message never contains the key itself."""
    env = os.environ if env is None else env
    key = (env.get("OPENROUTER_API_KEY") or "").strip()
    if not key:
        return False, (
            "OPENROUTER_API_KEY is empty. In GitHub Actions this usually means "
            "the job is missing `environment: Openrouter`, or that environment "
            "does not define OPENROUTER_API_KEY as a variable or secret "
            "(fork pull requests cannot read either)."
        )
    if get is None:
        import requests

        get = requests.get
    try:
        resp = get(OPENROUTER_MODELS_URL, headers={"Authorization": "Bearer " + key}, timeout=30)
    except Exception as exc:
        return False, "OpenRouter preflight request failed: {!r}".format(exc)
    status = getattr(resp, "status_code", None)
    if status != 200:
        return False, "OpenRouter rejected the key: HTTP {}".format(status)
    return True, "OpenRouter key accepted (HTTP 200 from /models)."


# --- report ---


def load_summaries(paths):
    """Return a list of (path, summary_dict_or_None, error_or_None)."""
    loaded: list[tuple[str, dict[str, Any] | None, str | None]] = []
    for path in paths:
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except OSError as exc:
            loaded.append((path, None, "unreadable summary: {}".format(exc)))
            continue
        except json.JSONDecodeError as exc:
            loaded.append((path, None, "malformed summary JSON: {}".format(exc)))
            continue
        if not isinstance(data, dict):
            loaded.append((path, None, "summary root is not an object"))
            continue
        tests = data.get("tests")
        if not isinstance(tests, dict) or not tests:
            loaded.append((path, None, "summary contains no test results"))
            continue
        loaded.append((path, data, None))
    return loaded


def evaluate(loaded, expect_cells=None):
    """Return report rows plus cell-outcome and evidence-integrity failures."""

    def result_count(result, name, fallback=None):
        if name in result:
            value = result[name]
        elif fallback is not None and fallback in result:
            value = result[fallback]
        else:
            raise ValueError("test result is missing {}".format(name))
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("{} is not an integer".format(name))
        return value

    def summary_string(summary, name):
        value = summary.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError("summary {} is not a non-empty string".format(name))
        return value

    def trial_list(result, name, total):
        value = result.get(name)
        if not isinstance(value, list):
            raise ValueError("{} is not a list".format(name))
        if len(value) != total:
            raise ValueError("{} length does not match total".format(name))
        return value

    rows = []
    cell_failures = []
    integrity_failures = []
    for path, data, error in loaded:
        if error is not None:
            integrity_failures.append("{}: {}".format(path, error))
            continue
        try:
            model = summary_string(data, "model")
            backend = summary_string(data, "backend")
            capability_profile = summary_string(data, "capability_profile")
            expected_served_model = summary_string(data, "expected_served_model")
            git_commit = summary_string(data, "git_commit")
            reasoning_policy = summary_string(data, "reasoning_policy")
            reasoning_effort = data.get("reasoning_effort")
            if not isinstance(reasoning_effort, str):
                raise ValueError("summary reasoning_effort is not a string")
            provider = data.get("provider")
            if backend != "openrouter":
                raise ValueError("summary backend is not 'openrouter'")
            if not isinstance(provider, str):
                raise ValueError("summary provider is not a string")
            allow_provider_fallbacks = data.get("allow_provider_fallbacks")
            require_provider_parameters = data.get("require_provider_parameters")
            if provider:
                if not isinstance(allow_provider_fallbacks, bool):
                    raise ValueError(
                        "summary allow_provider_fallbacks is not a boolean for a pinned provider"
                    )
                if not isinstance(require_provider_parameters, bool):
                    raise ValueError(
                        "summary require_provider_parameters is not a boolean for a pinned provider"
                    )
            if len(git_commit) != 40 or any(
                character not in "0123456789abcdef" for character in git_commit.lower()
            ):
                raise ValueError("summary git_commit is not a 40-character hexadecimal SHA")
            if data.get("git_dirty") is not False:
                raise ValueError("summary git_dirty is not false")
            if capability_profile != BERKELEY_CAPABILITY_PROFILE:
                raise ValueError(
                    "summary capability_profile is {!r}; expected {!r}".format(
                        capability_profile, BERKELEY_CAPABILITY_PROFILE
                    )
                )
            if reasoning_policy != "gated":
                raise ValueError("summary reasoning_policy is not 'gated'")
            if reasoning_effort not in ("", "low", "medium", "high"):
                raise ValueError("summary reasoning_effort is invalid")
        except ValueError as exc:
            integrity_failures.append("{}: malformed summary contract: {}".format(path, exc))
            continue
        for test_name in sorted(data["tests"]):
            result = data["tests"][test_name]
            cell = "{}/{}".format(data.get("suite", "?"), test_name)
            try:
                if not isinstance(result, dict):
                    raise ValueError("test result is not an object")
                total = result_count(result, "total")
                pytest_passed = result_count(result, "pytest_passed", fallback="passed")
                agent_complete = result_count(result, "agent_complete")
                if "pytest_passed" in result and "passed" in result:
                    legacy_passed = result_count(result, "passed")
                    if legacy_passed != pytest_passed:
                        raise ValueError("pytest_passed disagrees with passed")
                if total <= 0:
                    raise ValueError("total must be positive")
                if not 0 <= pytest_passed <= total:
                    raise ValueError("pytest_passed is outside [0, total]")
                if not 0 <= agent_complete <= total:
                    raise ValueError("agent_complete is outside [0, total]")
                valid_trials = result_count(result, "valid_trials")
                valid_passes = result_count(result, "valid_passes")
                if valid_trials != total:
                    raise ValueError("valid_trials does not equal total")
                if valid_passes != pytest_passed:
                    raise ValueError("valid_passes disagrees with pytest_passed")
                route_valid = trial_list(result, "route_valid", total)
                if any(not isinstance(value, bool) for value in route_valid):
                    raise ValueError("route_valid must contain only booleans")
                if not all(route_valid):
                    raise ValueError("route_valid contains an invalid trial")
                contract_valid = trial_list(result, "contract_valid", total)
                if any(not isinstance(value, bool) for value in contract_valid):
                    raise ValueError("contract_valid must contain only booleans")
                if not all(contract_valid):
                    raise ValueError("contract_valid contains an invalid trial")
                usage_complete = trial_list(result, "usage_complete", total)
                if any(not isinstance(value, bool) for value in usage_complete):
                    raise ValueError("usage_complete must contain only booleans")
                if not all(usage_complete):
                    raise ValueError("usage_complete contains an incomplete trial")
                config_hashes = trial_list(result, "config_hashes", total)
                if any(
                    not isinstance(value, str)
                    or len(value) != 16
                    or any(character not in "0123456789abcdef" for character in value.lower())
                    for value in config_hashes
                ):
                    raise ValueError("config_hashes must contain only 16-character hex digests")
                if len(set(config_hashes)) != 1:
                    raise ValueError("config_hashes differ within one result cell")
                requested_models = trial_list(result, "requested_models", total)
                if requested_models != [model] * total:
                    raise ValueError("requested_models do not exactly match summary model")
                recorded_backends = trial_list(result, "recorded_backends", total)
                if recorded_backends != [backend] * total:
                    raise ValueError("recorded_backends do not exactly match summary backend")
                requested_providers = trial_list(result, "requested_providers", total)
                if requested_providers != [provider] * total:
                    raise ValueError("requested_providers do not exactly match summary provider")
                recorded_reasoning_efforts = trial_list(result, "recorded_reasoning_efforts", total)
                if recorded_reasoning_efforts != [reasoning_effort] * total:
                    raise ValueError(
                        "recorded_reasoning_efforts do not exactly match summary reasoning_effort"
                    )
                recorded_reasoning_policies = trial_list(
                    result, "recorded_reasoning_policies", total
                )
                if recorded_reasoning_policies != [reasoning_policy] * total:
                    raise ValueError(
                        "recorded_reasoning_policies do not exactly match summary reasoning_policy"
                    )
                recorded_allow_fallbacks = trial_list(
                    result, "recorded_allow_provider_fallbacks", total
                )
                recorded_require_parameters = trial_list(
                    result, "recorded_require_provider_parameters", total
                )
                expected_allow_fallbacks = (
                    False if allow_provider_fallbacks is None else allow_provider_fallbacks
                )
                expected_require_parameters = (
                    True if require_provider_parameters is None else require_provider_parameters
                )
                if recorded_allow_fallbacks != [expected_allow_fallbacks] * total:
                    raise ValueError(
                        "recorded_allow_provider_fallbacks do not match the summary contract"
                    )
                if recorded_require_parameters != [expected_require_parameters] * total:
                    raise ValueError(
                        "recorded_require_provider_parameters do not match the summary contract"
                    )
                capability_profiles = trial_list(result, "capability_profiles", total)
                if capability_profiles != [capability_profile] * total:
                    raise ValueError(
                        "capability_profiles do not exactly match summary capability_profile"
                    )
                served_models = trial_list(result, "served_models", total)
                if served_models != [[expected_served_model]] * total:
                    raise ValueError(
                        "served_models do not exactly match summary expected_served_model"
                    )
                token_requested_models = trial_list(result, "token_requested_models", total)
                if token_requested_models != [[model]] * total:
                    raise ValueError("token_requested_models do not exactly match summary model")
                token_requested_model_valid = trial_list(
                    result, "token_requested_model_valid", total
                )
                if any(not isinstance(value, bool) for value in token_requested_model_valid):
                    raise ValueError("token_requested_model_valid must contain only booleans")
                if not all(token_requested_model_valid):
                    raise ValueError("token_requested_model_valid contains an invalid trial")
                served_model_sources = trial_list(result, "served_model_sources", total)
                observed_source_names = {"openrouter_metadata", "response"}
                if any(
                    not isinstance(sources, list)
                    or not sources
                    or any(source not in observed_source_names for source in sources)
                    for sources in served_model_sources
                ):
                    raise ValueError("served_model_sources contain a non-observed source")
                served_model_provenance_valid = trial_list(
                    result, "served_model_provenance_valid", total
                )
                if any(not isinstance(value, bool) for value in served_model_provenance_valid):
                    raise ValueError("served_model_provenance_valid must contain only booleans")
                if not all(served_model_provenance_valid):
                    raise ValueError("served_model_provenance_valid contains an invalid trial")
                served_provider_observed = trial_list(result, "served_provider_observed", total)
                if any(not isinstance(value, bool) for value in served_provider_observed):
                    raise ValueError("served_provider_observed must contain only booleans")
                served_providers_by_trial = trial_list(result, "served_providers", total)
                if any(
                    not isinstance(observed, list)
                    or any(not isinstance(served, str) or not served for served in observed)
                    for observed in served_providers_by_trial
                ):
                    raise ValueError("served_providers contain malformed provider evidence")
                if provider:
                    if not all(served_provider_observed) or any(
                        not observed for observed in served_providers_by_trial
                    ):
                        raise ValueError("served_provider_observed contains an invalid trial")
                    if not allow_provider_fallbacks and any(
                        any(served.casefold() != provider.casefold() for served in observed)
                        for observed in served_providers_by_trial
                    ):
                        raise ValueError(
                            "served_providers do not exactly match the pinned provider"
                        )
                log_parse_errors = result.get("log_parse_errors") or []
                if not isinstance(log_parse_errors, list):
                    raise ValueError("log_parse_errors is not a list")
                if len(log_parse_errors) not in (0, total):
                    raise ValueError("log_parse_errors length does not match total")
                if any(parse_error is not None for parse_error in log_parse_errors):
                    raise ValueError("benchmark JSONL contains a parse error")
                walls = [w for w in (result.get("wall_s") or []) if w is not None]
                cost = sum(float(c or 0) for c in (result.get("openrouter_cost") or []))
                served = sorted(
                    {p for trial in (result.get("served_providers") or []) for p in trial}
                )
            except (TypeError, ValueError) as exc:
                integrity_failures.append("{}: malformed test result: {}".format(cell, exc))
                continue
            passed = total > 0 and pytest_passed == total and agent_complete == total
            # Effort-pinned cells (always-on reasoners like gpt-oss-20b) differ
            # only by reasoning effort; keep the rows distinguishable.
            model_label = model
            if data.get("reasoning_effort"):
                model_label = "{}@{}".format(model, data["reasoning_effort"])
            rows.append(
                {
                    "cell": cell,
                    "model": model_label,
                    "provider": data.get("provider") or "auto",
                    "served_providers": served,
                    "pytest_passed": pytest_passed,
                    "agent_complete": agent_complete,
                    "total": total,
                    "median_wall_s": statistics.median(walls) if walls else None,
                    "cost": cost,
                    "ok": passed,
                }
            )
            if not passed:
                cell_failures.append(
                    "{} [{}]: pytest {}/{}, agent complete {}/{}".format(
                        cell, model_label, pytest_passed, total, agent_complete, total
                    )
                )
    if expect_cells is not None and len(rows) != expect_cells:
        integrity_failures.append(
            "expected {} result cell(s), found {}".format(expect_cells, len(rows))
        )
    return rows, cell_failures, integrity_failures


def render_markdown(rows, failures):
    lines = ["## LLM gate — {} cell(s), {} failure(s)".format(len(rows), len(failures)), ""]
    if rows:
        lines.append(
            "| Cell | Model | Provider | Pytest | Agent complete | Median wall (s) | Cost ($) |"
        )
        lines.append("|---|---|---|---|---|---|---|")
        for row in rows:
            provider = row["provider"]
            if row["served_providers"]:
                provider = "{} → {}".format(provider, ", ".join(row["served_providers"]))
            wall = "—" if row["median_wall_s"] is None else "{:.1f}".format(row["median_wall_s"])
            lines.append(
                "| {} {} | {} | {} | {}/{} | {}/{} | {} | {:.5f} |".format(
                    "✅" if row["ok"] else "❌",
                    row["cell"],
                    row["model"],
                    provider,
                    row["pytest_passed"],
                    row["total"],
                    row["agent_complete"],
                    row["total"],
                    wall,
                    row["cost"],
                )
            )
    if failures:
        lines.extend(["", "**Failures:**", ""])
        lines.extend("- {}".format(failure) for failure in failures)
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("preflight", help="fail unless OPENROUTER_API_KEY works")
    report = sub.add_parser("report", help="gate bench_harness summary.json files")
    report.add_argument("summaries", nargs="+", help="paths to summary.json files")
    report.add_argument(
        "--expect-cells",
        type=int,
        default=None,
        help="fail unless exactly this many result cells were found",
    )
    report.add_argument(
        "--markdown-out",
        default="",
        help="append the markdown table to this file (e.g. $GITHUB_STEP_SUMMARY)",
    )
    report.add_argument(
        "--advisory-cell-failures",
        action="store_true",
        help="return success for valid cell outcome failures; evidence errors still fail",
    )
    args = parser.parse_args(argv)

    if args.command == "preflight":
        ok, message = check_openrouter_key()
        print(("PREFLIGHT OK: " if ok else "PREFLIGHT FAILED: ") + message)
        return 0 if ok else 1

    rows, cell_failures, integrity_failures = evaluate(
        load_summaries(args.summaries), expect_cells=args.expect_cells
    )
    failures = integrity_failures + cell_failures
    markdown = render_markdown(rows, failures)
    print(markdown)
    if args.markdown_out:
        with open(args.markdown_out, "a", encoding="utf-8") as fh:
            fh.write(markdown)
    if integrity_failures:
        print("LLM GATE: FAIL (evidence integrity)")
        return 1
    if cell_failures:
        if args.advisory_cell_failures:
            print("LLM GATE: ADVISORY CELL FAILURE")
            return 0
        print("LLM GATE: FAIL")
        return 1
    print("LLM GATE: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
