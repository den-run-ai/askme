#!/usr/bin/env python3
"""Classify only what the archived diagnostic probe actually retained.

The frozen producer's `ok` accepts the first call without checking cardinality,
the expected tool, or the argument schema. Check the retained shape and key
metadata instead. Successful argument values were not retained in full, so
even a key-valid single call is schema-unverified, never proven AskMe acceptance.

An uncapped parse failure would warrant investigation; it would not, by itself,
identify an upstream grammar defect. A parse failure at the output cap is
reported separately as budget truncation. Neither class establishes recovery
or artifact completeness in AskMe.

Usage: uv run --locked --no-dev python .../analyze.py [records.jsonl ...]
"""

import json
import os
import statistics as st
import sys

EXPECTED_TOOLS = {
    "A_short_args": "read",
    "B_long_write_512": "write",
    "B_long_write_2048": "write",
    "C_delimiter_payload": "write",
}

# Key-only projection of ACTION_SPECS and the native decoder at b45bc873f0173c331e86b1cf3a0b26b19ebf6314.
# Keep archival interpretation independent of later runtime schema changes.
# Native arguments exclude `action`, which the decoder supplies from the name.
ARGUMENT_KEYS = {
    "read": (
        frozenset({"arg"}),
        frozenset({"arg", "offset", "limit", "cursor", "sha256", "reasoning"}),
    ),
    "write": (
        frozenset({"arg", "content"}),
        frozenset({"arg", "content", "append", "reasoning"}),
    ),
}


def classify(r):
    failure = r.get("failure")
    if failure in ("http_error", "transport"):
        return failure
    if r.get("http_status") != 200 or r.get("arm") not in EXPECTED_TOOLS:
        return "insufficient_evidence"
    count = r.get("n_tool_calls")
    if not isinstance(count, int) or isinstance(count, bool):
        return "insufficient_evidence"
    if count == 0:
        return "budget_truncation" if r.get("finish_reason") == "length" else "no_tool_call"
    if count != 1 or r.get("tool_name") != EXPECTED_TOOLS[r["arm"]]:
        return "action_contract_failure"
    if failure == "args_not_json" and r.get("args_parse_ok") is False:
        return (
            "budget_truncation" if r.get("finish_reason") == "length" else "uncapped_parse_failure"
        )
    if r.get("args_parse_ok") is not True or failure:
        return "insufficient_evidence"
    # The producer stores None here for a successfully parsed non-object.
    if "arg_keys" not in r:
        return "insufficient_evidence"
    keys = r["arg_keys"]
    if keys is None:
        return "action_contract_failure"
    # The producer emits sorted, unique string keys, or the None sentinel.
    # Other metadata cannot prove either a valid object or a schema violation.
    if not isinstance(keys, list) or any(not isinstance(key, str) for key in keys):
        return "insufficient_evidence"
    fields = set(keys)
    if len(fields) != len(keys) or keys != sorted(keys):
        return "insufficient_evidence"
    tool = EXPECTED_TOOLS[r["arm"]]
    required, allowed = ARGUMENT_KEYS[tool]
    if not required <= fields or not fields <= allowed:
        return "action_contract_failure"
    # Historical parse_action_envelope also enforces these key-only read rules.
    if tool == "read":
        if "cursor" in fields and not {"limit", "sha256"} <= fields:
            return "action_contract_failure"
        if "sha256" in fields and "cursor" not in fields:
            return "action_contract_failure"
    return "parse_ok_schema_unverified"


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    paths = sys.argv[1:] or [
        os.path.join(here, "peg_probe_results_run1.jsonl"),
        os.path.join(here, "peg_probe_results_run2.jsonl"),
    ]
    allrows = []
    for p in paths:
        with open(p, encoding="utf-8") as stream:
            rows = [json.loads(line) for line in stream if line.strip()]
        allrows.append((os.path.basename(p), rows))

    grand = {}
    for name, rows in allrows:
        print(f"\n=== {name} ({len(rows)} trials) ===")
        for arm in dict.fromkeys(r["arm"] for r in rows):
            rs = [r for r in rows if r["arm"] == arm]
            cls = {}
            for r in rs:
                c = classify(r)
                cls[c] = cls.get(c, 0) + 1
                grand[c] = grand.get(c, 0) + 1
            nlen = sum(1 for r in rs if r.get("finish_reason") == "length")
            lens = [r["content_len"] for r in rs if r.get("content_len")]
            extra = (
                f" | content {min(lens)}-{max(lens)} chars med {int(st.median(lens))}"
                if lens
                else ""
            )
            print(f"  {arm:22} n={len(rs)} {cls} hit_cap={nlen}/{len(rs)}{extra}")

    total = sum(grand.values())
    print(f"\n=== COMBINED (n={total}) ===")
    for k, v in sorted(grand.items(), key=lambda kv: -kv[1]):
        print(f"  {k:20} {v}")
    pf = grand.get("uncapped_parse_failure", 0)
    print(f"\nrecorded uncapped argument-parse failures: {pf}/{total}")
    print("Full argument values were not retained: AskMe schema acceptance is unverified.")


if __name__ == "__main__":
    main()
