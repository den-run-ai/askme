# Design: Redundant Task Auto-Skip

## Problem

When the planner creates tasks that overlap with already-completed work, the local
model (Gemma 4 E4B) wastes hundreds of seconds trying to express "this is already
done" — it generates verbose reasoning text that exhausts `max_tokens` without
producing valid JSON.

**Observed in:** `test_fix_python_syntax_error` (ARCHITECTURE.md lines 188-201)
- Task 1 fixed the syntax error and ran `python3 greet.py` successfully
- Task 2 "Correct the syntax error in greet.py" is redundant
- Model read the file (saw it was correct), then burned ~370s across 3 JSON parse
  failures with thinking escalation, before finally emitting `{"action": "done"}`

**Root cause:** The planner prompt says "Never create a task for work already in
completed_tasks" but small models don't reliably follow this instruction. The
executor then gets stuck because the correct response (`done`) is hard for the
model to produce when its reasoning wants to explain *why* the task is already done.

## Design

### Where: `_run_loop()`, line 550 — inside the `for i, task in enumerate(tasks)` loop

Insert a check **before** entering the step loop (line 562). If the new task is
a near-duplicate of a single completed task, skip it immediately.

### Detection: Pairwise near-duplicate heuristic (no LLM call)

Compare the new task against **each completed task individually**. A skip triggers
only when one specific completed task is a near-duplicate — not when unrelated
completed tasks happen to cover all the keywords in aggregate.

```python
_SYNONYMS = {
    "fix": "fix", "correct": "fix", "repair": "fix",
    "create": "create", "write": "create", "make": "create",
    "compile": "compile", "build": "compile",
    "run": "run", "execute": "run",
    "verify": "verify", "check": "verify", "test": "verify",
}

_STOP = {"the", "and", "then", "that", "this", "with", "from", "for",
         "its", "all", "not", "but", "into", "also"}


def _task_keywords(text):
    """Extract normalized keyword set from a task description."""
    words = re.findall(r'[a-z0-9_.]+', text.lower())
    result = set()
    for w in words:
        if len(w) >= 3 and w not in _STOP:
            result.add(_SYNONYMS.get(w, w))
    return result


def _task_is_redundant(task, completed_tasks):
    """Check if task is a near-duplicate of any single completed task.

    Returns the matched completed task description, or None.
    Compares against each completed task individually — never unions.
    """
    task_kw = _task_keywords(task)
    if len(task_kw) < 3:
        return None  # too few keywords to judge — don't skip

    for ct in completed_tasks:
        ct_kw = _task_keywords(ct)
        if not ct_kw:
            continue
        # One-way only: new task ⊆ completed task
        # Reverse (completed ⊆ new) is unsafe — new task may add work
        if task_kw.issubset(ct_kw):
            return ct
    return None
```

**Why pairwise, not union:** Union matching is too permissive. Completed tasks
`["create main.c", "compile tests"]` would make `"compile main.c"` look redundant
because `{"compile", "main.c"}` ⊆ `{"create", "main.c", "compile", "tests"}`.
But no single completed task actually compiled main.c. Pairwise comparison avoids
this class of false positive entirely.

### Integration point

```python
# In _run_loop(), after line 557 (log task start), before line 559 (task_done = False):

# Auto-skip: if task is a near-duplicate of one completed task, skip
if state["completed_tasks"]:
    matched = _task_is_redundant(task, state["completed_tasks"])
    if matched:
        log(f"  Auto-skip: near-duplicate of completed '{matched[:60]}'")
        history.append({"event": "skip", "task": i, "reason": "redundant",
                        "matched": matched})
        continue
```

**Skipped tasks are NOT added to `completed_tasks`.** This is critical:

- `completed_tasks` is authoritative — both planner and executor treat it as
  ground truth ("completed_tasks are DONE — never redo their work")
- If a false-positive skip occurs, the task must remain eligible for replanning
- A skip is a history-only event, not a completion claim

The skip only suppresses execution for the current plan. On replan, the planner
sees `completed_tasks` without the skipped task, so it can reintroduce the work
if needed.

### Why keyword overlap, not an LLM call

| Approach | Latency | Token cost | Risk |
|----------|---------|------------|------|
| Pairwise keyword match | ~0ms | 0 | False positive: skips a near-duplicate that wasn't actually done |
| LLM "is this done?" | 5-45s | ~50-200 | Adds latency to every task; parse failures possible |
| Embedding similarity | ~0ms (precomputed) | 0 | Needs embedding model or library dependency |

The keyword approach is the right fit because:
1. **Zero latency** — critical on a 7 tok/s local model where every LLM call costs 5-45s
2. **The problem is narrow** — the planner creates near-identical task descriptions
   (e.g., "Fix syntax error in greet.py" vs "Correct the syntax error in greet.py")
3. **No new dependencies**

### Risk: false positive skip

If the heuristic skips a task that wasn't actually done:

1. The skipped task is NOT in `completed_tasks` — it remains "not done"
2. If a later task depends on the skipped work, that task fails
3. The failure triggers replan
4. The planner sees the skipped task is NOT in `completed_tasks`, so it can
   reintroduce it in the new plan
5. On replan, the new plan's tasks go through the same skip check — but now the
   `completed_tasks` set is different, so the same false positive may not recur

This is safe because the skip doesn't corrupt state. It's a speculative
optimization that can be undone by the existing replan mechanism.

### Edge cases

| Scenario | Behavior | Correct? |
|----------|----------|----------|
| Completed: "Fix syntax error in greet.py", New: "Correct the syntax error in greet.py" | Skip (keywords: {fix, syntax, error, greet.py} match after synonym normalization) | Yes — same fix |
| Completed: "Create main.c", New: "Compile main.c" | No skip ({create, main.c} vs {compile, main.c} — neither is a subset) | Yes — different action |
| Completed: ["Create main.c", "Compile tests"], New: "Compile main.c" | No skip — pairwise: {create, main.c} vs {compile, main.c} no subset; {compile, tests} vs {compile, main.c} no subset | Yes — union would wrongly skip |
| Completed: "Fix a.py", New: "Fix b.py" | No skip ({fix, a.py} vs {fix, b.py} — different file) | Yes |
| Completed: "Write and compile hello.c", New: "Create hello.c" | Skip ({create→create, hello.c} ⊆ {create→create, compile, hello.c}) | Yes — write already created it |
| New task has ≤2 keywords (e.g. "fix bug", "update file") | No skip (min 3 keywords required) | Yes — too generic to match safely |

### Test plan

Unit tests (mock, no LLM) — new `TestRedundantTaskSkip` class:

1. **test_redundant_exact_match** — same task description → skip
2. **test_redundant_synonym_match** — "fix X" vs "correct X" → skip
3. **test_redundant_subset_match** — "create X" vs "write and compile X" → skip (subset)
4. **test_not_redundant_different_action** — "create X" vs "compile X" → don't skip
5. **test_not_redundant_different_file** — "fix a.py" vs "fix b.py" → don't skip
6. **test_not_redundant_cross_task_union** — ["create main.c", "compile tests"] vs "compile main.c" → don't skip (the union trap)
7. **test_skip_requires_min_keywords** — ≤2-keyword task → never skip (e.g. "fix bug" with completed "fix bug in parser.py")
8. **test_generic_words_no_false_skip** — "update file" should not skip even when prior task contains both words (e.g. "update file permissions") — blocked by min-3 guard
9. **test_skip_only_when_completed_nonempty** — empty completed_tasks → never skip
10. **test_skip_not_in_completed_tasks** — skipped task must NOT appear in completed_tasks
11. **test_skip_history_includes_matched_task** — verify history event has `{"event": "skip", "matched": "..."}` with the specific completed task that triggered the match
12. **test_skip_does_not_block_replan** — after false-positive skip, replan can reintroduce the task (skipped task not in completed_tasks, so planner is free to re-plan it)

The key invariant tests are #6 (union trap), #7/#8 (generic-word guard), #10
(state integrity), and #12 (replan recovery).

Integration test (with LLM):
- Run `test_fix_python_syntax_error` and verify reduced wall time
- Or: add a dedicated test with known-redundant planner output

### Implementation checklist

- [ ] Add `_task_keywords()` and `_task_is_redundant()` (~25 lines, after `summarize_errors`)
- [ ] Add auto-skip check in `_run_loop()` task loop (~6 lines, no state mutation)
- [ ] Add `"skip"` event type to history (history-only, not in completed_tasks)
- [ ] Add unit tests in `test_agent_recovery.py` (new `TestRedundantTaskSkip`, ~11 tests)
- [ ] Update CLAUDE.md: mention auto-skip in Architecture section
- [ ] Update ARCHITECTURE.md: document the heuristic and its limitations
- [ ] Run unit tests, then integration tests to measure improvement
