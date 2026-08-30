#!/usr/bin/env python3
"""Diagnostic probe for llama.cpp #25986 against the running local server.

Not an AskMe evaluation and not a benchmark: this makes no task-outcome claim.
It asks one question — can the peg-gemma4 parser round-trip the tool-call
shapes AskMe's tools-only executor actually emits, and does exposure depend
on the write budget?

Faithful to AskMe: real `_ACTION_TOOLS` definitions, real SYSTEM_STEP,
tool_choice="auto", temperature 0.1, and a prior assistant tool_call +
role:tool round in history (the reporter's stated trigger condition).
"""

import json
import os
import sys
import time

import requests

_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
)
sys.path.insert(0, _REPO_ROOT)
import askme  # noqa: E402

URL = "http://localhost:8080/v1/chat/completions"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "peg_probe_results.jsonl")
TOOLS = askme._ACTION_TOOLS


def conversation(user_ask):
    """AskMe-shaped history: long system prompt, a completed prior tool round."""
    return [
        {"role": "system", "content": askme.SYSTEM_STEP},
        {
            "role": "user",
            "content": (
                "GOAL: build a small text-processing utility with tests.\n"
                "TASK: inspect the project layout before implementing.\n"
                'STATE: {"task_index":"1/3","step":"1/10","completed_tasks":[]}'
            ),
        },
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_0",
                    "type": "function",
                    "function": {"name": "tree", "arguments": json.dumps({"arg": "."})},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_0",
            "content": "src/\n  __init__.py\ntests/\n  test_util.py\nREADME.md",
        },
        {"role": "user", "content": user_ask},
    ]


ARMS = {
    # Control: short string arguments, the shape the reporter found reliable.
    "A_short_args": {
        "budget": 512,
        "ask": (
            "TASK: read the existing test file before implementing.\n"
            'STATE: {"task_index":"2/3","step":"2/10"}\n'
            "Call the read tool on tests/test_util.py."
        ),
    },
    # #25986 primary shape at the legacy-e4b-m1-16k-v1 write budget (512).
    "B_long_write_512": {
        "budget": 512,
        "ask": (
            "TASK: implement src/util.py.\n"
            'STATE: {"task_index":"2/3","step":"2/10"}\n'
            "Call the write tool to create src/util.py with a complete "
            "implementation: a module docstring, imports, a "
            "normalize(text) function that strips and collapses whitespace, "
            "a slugify(text) function, and a __main__ block that prints "
            "results. Use double quotes in strings and include comments."
        ),
    },
    # Same shape at a feature-scale write budget: does exposure grow with payload?
    "B_long_write_2048": {
        "budget": 2048,
        "ask": (
            "TASK: implement src/util.py.\n"
            'STATE: {"task_index":"2/3","step":"2/10"}\n'
            "Call the write tool to create src/util.py with a LONG, complete "
            "implementation (at least 60 lines): module docstring, imports, a "
            "TextNormalizer class with several methods, normalize(), slugify(), "
            "wrap(), a CLI argparse __main__ block, and inline comments. "
            "Use double quotes in strings throughout."
        ),
    },
    # Finding #2: the wire format has no escape for the string delimiter.
    "C_delimiter_payload": {
        "budget": 1024,
        "ask": (
            "TASK: document the tool-call wire format.\n"
            'STATE: {"task_index":"2/3","step":"2/10"}\n'
            "Call the write tool to create docs/format.md whose content "
            "explains that Gemma 4 delimits tool-call string arguments with "
            'the literal token sequence <|"|> on each side, and shows that '
            "exact token sequence inline at least twice as an example."
        ),
    },
}


def one_trial(arm, cfg, i):
    body = {
        "model": "gemma-4-e4b",
        "messages": conversation(cfg["ask"]),
        "temperature": 0.1,
        "max_tokens": cfg["budget"],
        "tools": TOOLS,
        "tool_choice": "auto",
    }
    rec = {"arm": arm, "trial": i, "budget": cfg["budget"]}
    t0 = time.time()
    try:
        r = requests.post(URL, json=body, timeout=300)
        rec["http_status"] = r.status_code
        rec["wall_s"] = round(time.time() - t0, 1)
        raw = r.text
        if r.status_code != 200:
            rec["ok"] = False
            rec["failure"] = "http_error"
            rec["body"] = raw[:4000]
            rec["peg_error"] = "peg-gemma4" in raw
            rec["request"] = body  # keep enough to emit a standalone repro
            return rec
        rj = r.json()
        ch = (rj.get("choices") or [{}])[0]
        msg = ch.get("message") or {}
        rec["finish_reason"] = ch.get("finish_reason")
        rec["usage"] = rj.get("usage")
        tcs = msg.get("tool_calls") or []
        rec["n_tool_calls"] = len(tcs)
        rec["had_text_content"] = bool((msg.get("content") or "").strip())
        rec["text_content_len"] = len(msg.get("content") or "")
        if not tcs:
            rec["ok"] = False
            rec["failure"] = "no_tool_call"
            rec["content"] = (msg.get("content") or "")[:2000]
            return rec
        fn = tcs[0].get("function") or {}
        rec["tool_name"] = fn.get("name")
        args_raw = fn.get("arguments")
        try:
            args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            rec["args_parse_ok"] = True
        except Exception as e:
            rec["ok"] = False
            rec["failure"] = "args_not_json"
            rec["args_parse_ok"] = False
            rec["error"] = str(e)
            rec["args_raw"] = (args_raw or "")[:3000]
            return rec
        rec["arg_keys"] = sorted(args.keys()) if isinstance(args, dict) else None
        content = args.get("content") if isinstance(args, dict) else None
        if content is not None:
            rec["content_len"] = len(content)
            rec["content_lines"] = content.count("\n") + 1
            rec["content_has_delim"] = '<|"|>' in content
            rec["content_head"] = content[:200]
            rec["content_tail"] = content[-200:]
        rec["ok"] = True
        return rec
    except Exception as e:
        rec["ok"] = False
        rec["failure"] = "transport"
        rec["error"] = repr(e)[:500]
        rec["wall_s"] = round(time.time() - t0, 1)
        return rec


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    only = sys.argv[2] if len(sys.argv) > 2 else None
    arms = {k: v for k, v in ARMS.items() if not only or k == only}
    results = []
    with open(OUT, "a") as fh:
        for arm, cfg in arms.items():
            for i in range(1, n + 1):
                rec = one_trial(arm, cfg, i)
                results.append(rec)
                fh.write(json.dumps(rec) + "\n")
                fh.flush()
                flag = "ok " if rec.get("ok") else "FAIL"
                extra = rec.get("failure") or (
                    f"len={rec.get('content_len')} lines={rec.get('content_lines')} "
                    f"delim={rec.get('content_has_delim')} fr={rec.get('finish_reason')}"
                )
                print(f"{flag} {arm} #{i} {rec.get('wall_s')}s {extra}", flush=True)
    print("\n===== SUMMARY =====", flush=True)
    for arm in arms:
        rs = [r for r in results if r["arm"] == arm]
        okc = sum(1 for r in rs if r.get("ok"))
        modes = {}
        for r in rs:
            if not r.get("ok"):
                modes[r.get("failure")] = modes.get(r.get("failure"), 0) + 1
        print(f"{arm}: {okc}/{len(rs)} clean  failures={modes or '-'}", flush=True)
    print(f"\nrecords: {OUT}", flush=True)


if __name__ == "__main__":
    main()
