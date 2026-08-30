#!/usr/bin/env python3
"""Classify retained probe records.

The probe's inline `ok` flag is deliberately naive — it means "one tool call
came back and its arguments parsed as JSON". That conflates two very different
outcomes, so the retained records are classified here instead:

  parser_failure    - malformed output while the model stopped on its own
                      (finish_reason is not "length"). This is the #25986
                      class: the grammar produced something unparseable.
  budget_truncation - arguments are an unterminated prefix because generation
                      hit max_tokens (finish_reason == "length"). Expected and
                      handled: AskMe classifies this as a truncated write and
                      retries with the payload-sized budget (STEP_WRITE_TOKENS).
  clean             - one tool call, arguments parsed.

Only `parser_failure` bears on #25986. Every record carries `finish_reason`,
so this classification is reproducible from the raw data.

Usage: uv run --locked --no-dev python .../analyze.py [records.jsonl ...]
"""

import json
import os
import statistics as st
import sys


def classify(r):
    if r.get("ok"):
        return "clean"
    if r.get("failure") in ("args_not_json", "no_tool_call"):
        return "budget_truncation" if r.get("finish_reason") == "length" else "parser_failure"
    return r.get("failure") or "unknown"


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    paths = sys.argv[1:] or [
        os.path.join(here, "peg_probe_results_run1.jsonl"),
        os.path.join(here, "peg_probe_results_run2.jsonl"),
    ]
    allrows = []
    for p in paths:
        if not os.path.exists(p):
            print(f"(skipping missing {os.path.basename(p)})")
            continue
        rows = [json.loads(line) for line in open(p) if line.strip()]
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
    pf = grand.get("parser_failure", 0)
    print(f"\nparser failures bearing on #25986: {pf}/{total}")


if __name__ == "__main__":
    main()
