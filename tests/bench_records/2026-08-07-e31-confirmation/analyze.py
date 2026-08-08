#!/usr/bin/env python3
"""E31 confirmation analysis: preregistered outcome + mediators."""

import json
from collections import Counter
from math import comb
from pathlib import Path

S = Path("/private/tmp/claude-501/-Users-macmone-code-llama-cpp-agent/f3f6b745-a9d5-48cf-8830-36008a1b9319/scratchpad")

ARMS = {
    "heuristic": [S / "e31-heuristic"],
    "lifecycle": [S / "e31-lifecycle", S / "e31-lifecycle-b", S / "e31-lifecycle-c"],
}


def load(dirs):
    runs = []
    for d in dirs:
        for f in sorted(d.glob("test_webapp_fix*.jsonl")):
            evs = [json.loads(x) for x in f.read_text().splitlines() if x.strip()]
            if not any(e.get("event") == "run_end" for e in evs):
                continue  # partial/killed trial, never counted
            runs.append((f, evs))
    return runs


def analyze(evs):
    end = next(e for e in evs if e["event"] == "run_end")
    steps = [e for e in evs if e["event"] == "step"]
    skips = [e for e in evs if e["event"] == "step_skipped"]
    muts = [e for e in steps if e.get("action") in ("edit", "write") and e.get("ok")]
    # in-run evidence the repair worked: a successful test_app.py run after a mutation
    verified = False
    if muts:
        t_mut = muts[0]["ts"]
        verified = any(
            e.get("action") == "shell"
            and "test_app" in str(e.get("arg"))
            and e.get("ok")
            and e["ts"] > t_mut
            for e in steps
        )
    # mediators
    lifecycle_skips = sum(1 for e in skips if "lifecycle" in str(e.get("reason")))
    dup_verify_suppressed = sum(
        1
        for e in skips
        if "test_app" in str(e.get("arg"))
        and str(e.get("reason")) in ("duplicate_shell", "stuck_shell_repeat", "stuck_shell")
    )
    done_emitted = sum(1 for e in steps if e.get("action") == "done")
    goal_achieved_replan = sum(
        1
        for e in evs
        if e["event"] == "task_local_replan"
        and "goal achieved" in str(e.get("replacement", "")).lower()
    )
    return {
        "status": end["status"],
        "wall": end["wall_s"],
        "step_policy": evs[0].get("step_policy"),
        "mutations": len(muts),
        "verified_in_run": verified,
        "lifecycle_skips": lifecycle_skips,
        "verify_cmd_suppressed": dup_verify_suppressed,
        "done_emitted": done_emitted,
        "goal_achieved_replan": goal_achieved_replan,
        "task_complete": sum(1 for e in evs if e["event"] == "task_complete"),
    }


def fisher_two_sided(a, b, c, d):
    """Two-sided Fisher exact p for [[a,b],[c,d]]."""
    n = a + b + c + d
    row1, col1 = a + b, a + c

    def p(x):
        return comb(row1, x) * comb(n - row1, col1 - x) / comb(n, col1)

    obs = p(a)
    lo = max(0, col1 - (n - row1))
    hi = min(row1, col1)
    return sum(p(x) for x in range(lo, hi + 1) if p(x) <= obs + 1e-12)


results = {}
for arm, dirs in ARMS.items():
    runs = load(dirs)
    rows = [analyze(evs) for _, evs in runs]
    results[arm] = rows
    comp = sum(1 for r in rows if r["status"] == "complete")
    walls = sorted(r["wall"] for r in rows)
    med = walls[len(walls) // 2] if len(walls) % 2 else (walls[len(walls) // 2 - 1] + walls[len(walls) // 2]) / 2
    print(f"=== {arm}  (n={len(rows)})")
    print(f"  agent complete      {comp}/{len(rows)}")
    print(f"  policy recorded     {Counter(r['step_policy'] for r in rows)}")
    print(f"  wall median (range) {med:.1f}s ({walls[0]:.0f}-{walls[-1]:.0f})")
    print(f"  repair verified in-run  {sum(1 for r in rows if r['verified_in_run'])}/{len(rows)}")
    print("  --- mediators ---")
    print(f"  lifecycle-specific skips    {sum(r['lifecycle_skips'] for r in rows)}")
    print(f"  'done' actions emitted      {sum(r['done_emitted'] for r in rows)}")
    print(f"  verify cmd suppressed       {sum(r['verify_cmd_suppressed'] for r in rows)} "
          f"(runs affected: {sum(1 for r in rows if r['verify_cmd_suppressed'])})")
    print(f"  'goal achieved' replans     {sum(r['goal_achieved_replan'] for r in rows)}")
    print(f"  statuses                    {Counter(r['status'] for r in rows)}")
    print()

h, l = results["heuristic"], results["lifecycle"]
hc = sum(1 for r in h if r["status"] == "complete")
lc = sum(1 for r in l if r["status"] == "complete")
p = fisher_two_sided(lc, len(l) - lc, hc, len(h) - hc)
print(f"PRIMARY OUTCOME: lifecycle {lc}/{len(l)} vs heuristic {hc}/{len(h)}")
print(f"Fisher exact two-sided p = {p:.3f}  ->  {'REJECT null' if p < 0.05 else 'UNCONFIRMED (fail to reject at alpha=0.05)'}")

# suppression as a predictor of failure, pooled across arms
allr = h + l
tab = Counter((r["verify_cmd_suppressed"] > 0, r["status"] == "complete") for r in allr)
print()
print("Pooled: verification-command suppression vs outcome")
print(f"  suppressed & complete     {tab[(True, True)]}")
print(f"  suppressed & not complete {tab[(True, False)]}")
print(f"  clean     & complete      {tab[(False, True)]}")
print(f"  clean     & not complete  {tab[(False, False)]}")
p2 = fisher_two_sided(tab[(True, True)], tab[(True, False)], tab[(False, True)], tab[(False, False)])
print(f"  Fisher two-sided p = {p2:.4f}")
