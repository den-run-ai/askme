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
              GITHUB_STEP_SUMMARY. Exits nonzero on any failure, malformed
              input, or a cell-count mismatch against --expect-cells.

The pass rule mirrors talks/berkeley-agentic-ai-summit-2026/evals/README.md:
a reported pass requires pytest success, agent_complete, and the
deterministic acceptance check (embedded in the pytest assertions).
"""
import argparse
import json
import os
import statistics
import sys

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"


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
        resp = get(OPENROUTER_MODELS_URL,
                   headers={"Authorization": "Bearer " + key}, timeout=30)
    except Exception as exc:
        return False, "OpenRouter preflight request failed: {!r}".format(exc)
    status = getattr(resp, "status_code", None)
    if status != 200:
        return False, "OpenRouter rejected the key: HTTP {}".format(status)
    return True, "OpenRouter key accepted (HTTP 200 from /models)."


# --- report ---

def load_summaries(paths):
    """Return a list of (path, summary_dict_or_None, error_or_None)."""
    loaded = []
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
        tests = data.get("tests")
        if not isinstance(tests, dict) or not tests:
            loaded.append((path, None, "summary contains no test results"))
            continue
        loaded.append((path, data, None))
    return loaded


def evaluate(loaded, expect_cells=None):
    """Apply the pass rule to loaded summaries. Returns (rows, failures)."""
    rows = []
    failures = []
    for path, data, error in loaded:
        if error is not None:
            failures.append("{}: {}".format(path, error))
            continue
        for test_name in sorted(data["tests"]):
            result = data["tests"][test_name]
            total = int(result.get("total", 0))
            pytest_passed = int(result.get("pytest_passed", result.get("passed", 0)))
            agent_complete = int(result.get("agent_complete", 0))
            walls = [w for w in (result.get("wall_s") or []) if w is not None]
            cost = sum(float(c or 0) for c in (result.get("openrouter_cost") or []))
            served = sorted({p for trial in (result.get("served_providers") or [])
                             for p in trial})
            cell = "{}/{}".format(data.get("suite", "?"), test_name)
            passed = total > 0 and pytest_passed == total and agent_complete == total
            rows.append({
                "cell": cell,
                "model": data.get("model") or "?",
                "provider": data.get("provider") or "auto",
                "served_providers": served,
                "pytest_passed": pytest_passed,
                "agent_complete": agent_complete,
                "total": total,
                "median_wall_s": statistics.median(walls) if walls else None,
                "cost": cost,
                "ok": passed,
            })
            if not passed:
                failures.append(
                    "{} [{}]: pytest {}/{}, agent complete {}/{}".format(
                        cell, data.get("model") or "?",
                        pytest_passed, total, agent_complete, total))
    if expect_cells is not None and len(rows) != expect_cells:
        failures.append(
            "expected {} result cell(s), found {}".format(expect_cells, len(rows)))
    return rows, failures


def render_markdown(rows, failures):
    lines = ["## LLM gate — {} cell(s), {} failure(s)".format(len(rows), len(failures)), ""]
    if rows:
        lines.append("| Cell | Model | Provider | Pytest | Agent complete | Median wall (s) | Cost ($) |")
        lines.append("|---|---|---|---|---|---|---|")
        for row in rows:
            provider = row["provider"]
            if row["served_providers"]:
                provider = "{} → {}".format(provider, ", ".join(row["served_providers"]))
            wall = "—" if row["median_wall_s"] is None else "{:.1f}".format(row["median_wall_s"])
            lines.append("| {} {} | {} | {} | {}/{} | {}/{} | {} | {:.5f} |".format(
                "✅" if row["ok"] else "❌", row["cell"], row["model"], provider,
                row["pytest_passed"], row["total"],
                row["agent_complete"], row["total"], wall, row["cost"]))
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
    report.add_argument("--expect-cells", type=int, default=None,
                        help="fail unless exactly this many result cells were found")
    report.add_argument("--markdown-out", default="",
                        help="append the markdown table to this file "
                             "(e.g. $GITHUB_STEP_SUMMARY)")
    args = parser.parse_args(argv)

    if args.command == "preflight":
        ok, message = check_openrouter_key()
        print(("PREFLIGHT OK: " if ok else "PREFLIGHT FAILED: ") + message)
        return 0 if ok else 1

    rows, failures = evaluate(load_summaries(args.summaries),
                              expect_cells=args.expect_cells)
    markdown = render_markdown(rows, failures)
    print(markdown)
    if args.markdown_out:
        with open(args.markdown_out, "a", encoding="utf-8") as fh:
            fh.write(markdown)
    print("LLM GATE: " + ("PASS" if not failures else "FAIL"))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
