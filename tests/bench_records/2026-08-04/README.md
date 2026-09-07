# E25 transport A/B records — 2026-08-04

Paired json-vs-tools executor-transport bench (issue #68 / E25). Easy+medium
suites, 3 trials per test per arm, `gemma-4-e4b` alias (E4B QAT Q4_0 on
llama.cpp b9618 `c34b92235`, gemma4-setup.md flags incl. `--reasoning off`),
`legacy-e4b-m1-16k-v1` capability profile, expected served model pinned.
Summary tables and findings: docs/PERFORMANCE.md "E25 Transport A/B".

Provenance and limitations:

1. Both arms executed AskMe revision `d0c2826b` (PR #91 head). The tools arm
   and the json medium suite ran from a dedicated clean `git worktree` at that
   commit; the json easy suite ran earlier from the primary tree at the same
   commit while it was still clean. A first attempt at the json medium /
   tools cells from the primary tree was invalidated (`INVALID-CONTRACT`,
   sub-second trials) after unrelated edits landed in that tree mid-run;
   those logs were discarded and the cells rerun from the worktree. The
   retained summaries record per-trial `contract_valid` and `config_hashes`.
2. ~~The hard suite was deliberately skipped.~~ **Hard-suite addendum
   (2026-08-04, post-merge):** both arms later ran the hard suite from the
   same worktree at `d0c2826b` (`json/hard/`, `tools/hard/`). json 7/9
   pytest (build 1/3 with two 1200s harness timeouts), tools 6/9 (build 0/3;
   the other two tests 3/3 each). Neither arm reproduced E23's 9/9. Two
   tools build walls (5791.9s, 16607.2s) are infrastructure-corrupted:
   agent-spawned commands hung and blocked the harness's 1200s subprocess
   kill by holding inherited pipes — treat trial 1's 975.4s as the only
   honest tools build wall. All three tools build failures share one
   semantic loop (repeated `cc -o main main.c msg.h`; clang rejects the
   header as a second output) that thinking escalation never broke; the
   deliverable never ran. See the PERFORMANCE.md E25 hard addendum.
3. One-day, one-machine, n=18 per arm: the verdict recorded in
   PERFORMANCE.md is non-inferiority within observed variance, not a
   reliability estimate for either transport.
