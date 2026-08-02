#!/usr/bin/env python3
"""Compare two bench_cache_multiturn.py result files side-by-side.

Usage:
    # Run benchmarks (restart server between with different flags):
    python3 tests/bench_cache_multiturn.py phase5 --trials 3
    # ... restart server with --swa-full --cache-reuse 256 ...
    python3 tests/bench_cache_multiturn.py phase6 --trials 3

    # Compare:
    python3 tests/bench_cache_compare.py /tmp/bench_cache_phase5.json /tmp/bench_cache_phase6.json
"""
import json
import sys


def load(path):
    with open(path) as f:
        return json.load(f)


def main():
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <result_a.json> <result_b.json>")
        sys.exit(1)

    a = load(sys.argv[1])
    b = load(sys.argv[2])

    label_a = a["label"]
    label_b = b["label"]
    sum_a = {r["name"]: r for r in a["summary"]}
    sum_b = {r["name"]: r for r in b["summary"]}

    has_timings = "prompt_n" in a["summary"][0]

    print(f"\n{'='*80}")
    print(f"Cache Benchmark Comparison: {label_a} vs {label_b}")
    print(f"Trials: {a['trials']} vs {b['trials']}, {a['requests_per_trial']} requests each")
    print(f"{'='*80}\n")

    if has_timings:
        hdr = f"{'Request':<10s} | {'prompt_n':>10s} {'prompt_ms':>10s} {'decode t/s':>10s} {'wall_s':>8s} | {'prompt_n':>10s} {'prompt_ms':>10s} {'decode t/s':>10s} {'wall_s':>8s} | {'wall Δ':>8s}"
        sep_a = f"{'--- ' + label_a + ' ---':^44s}"
        sep_b = f"{'--- ' + label_b + ' ---':^44s}"
        print(f"{'':10s} | {sep_a} | {sep_b} |")
        print(hdr)
        print("-" * len(hdr))

        total_a = total_b = 0
        for name in [r["name"] for r in a["summary"]]:
            ra = sum_a[name]
            rb = sum_b[name]
            total_a += ra["wall_s"]
            total_b += rb["wall_s"]
            delta = rb["wall_s"] - ra["wall_s"]
            delta_str = f"{delta:+.3f}s"
            print(
                f"{name:<10s} | "
                f"{ra.get('prompt_n', 0):>10.0f} {ra.get('prompt_ms', 0):>10.1f} {ra.get('decode_tok_s', 0):>10.2f} {ra['wall_s']:>8.3f} | "
                f"{rb.get('prompt_n', 0):>10.0f} {rb.get('prompt_ms', 0):>10.1f} {rb.get('decode_tok_s', 0):>10.2f} {rb['wall_s']:>8.3f} | "
                f"{delta_str:>8s}"
            )

        print(f"\n{'Total wall (median):':<28s} {total_a:>8.2f}s  vs  {total_b:>8.2f}s  (Δ = {total_b - total_a:+.2f}s, {((total_b/total_a)-1)*100:+.1f}%)")
    else:
        hdr = f"{'Request':<10s} | {'prompt_tok':>10s} {'wall_s':>8s} | {'prompt_tok':>10s} {'wall_s':>8s} | {'wall Δ':>8s}"
        print(hdr)
        print("-" * len(hdr))

        total_a = total_b = 0
        for name in [r["name"] for r in a["summary"]]:
            ra = sum_a[name]
            rb = sum_b[name]
            total_a += ra["wall_s"]
            total_b += rb["wall_s"]
            delta = rb["wall_s"] - ra["wall_s"]
            print(
                f"{name:<10s} | "
                f"{ra.get('prompt_tokens', 0):>10.0f} {ra['wall_s']:>8.3f} | "
                f"{rb.get('prompt_tokens', 0):>10.0f} {rb['wall_s']:>8.3f} | "
                f"{delta:+.3f}s"
            )

        print(f"\n{'Total wall (median):':<28s} {total_a:>8.2f}s  vs  {total_b:>8.2f}s  (Δ = {total_b - total_a:+.2f}s)")

    # Cache reuse analysis
    if has_timings:
        print("\n--- Cache Reuse Analysis ---")
        for label, summary in [(label_a, a["summary"]), (label_b, b["summary"])]:
            exec_rows = [r for r in summary if r["name"].startswith("step")]
            if len(exec_rows) >= 2:
                first = exec_rows[0]["prompt_n"]
                rest = [r["prompt_n"] for r in exec_rows[1:]]
                avg_rest = sum(rest) / len(rest)
                if first > 0:
                    saved = (1 - avg_rest / first) * 100
                    print(f"  {label}: executor prompt_n first={first:.0f} → avg_rest={avg_rest:.0f} ({saved:+.0f}% eval reduction)")
                else:
                    print(f"  {label}: executor prompt_n first={first:.0f}, rest={rest}")

    # Decode speed comparison
    if has_timings:
        print("\n--- Decode Speed ---")
        for label, summary in [(label_a, a["summary"]), (label_b, b["summary"])]:
            speeds = [r.get("decode_tok_s", 0) for r in summary if r.get("decode_tok_s", 0) > 0]
            if speeds:
                print(f"  {label}: {min(speeds):.1f} – {max(speeds):.1f} tok/s (median {sorted(speeds)[len(speeds)//2]:.1f})")

    print()


if __name__ == "__main__":
    main()

