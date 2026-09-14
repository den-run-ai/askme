"""Read-only post-run billing/route reconciliation; no inference capability."""

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

GENERATIONS = {
    "gemma-askme": "gen-1789365565-06396776OGdFArYlvRet",
    "gemma-pi": "gen-1789365586-pkLVnOIW6MB8Q36GLjSD",
    "qwen-pi": "gen-1789365621-SZqnhNiEe7yFj6bvC1w7",
    "qwen-askme": "gen-1789365657-nbv4pOcqy7jTsFYO2PQ2",
}
FIELDS = {
    "id",
    "model",
    "provider_name",
    "total_cost",
    "tokens_prompt",
    "tokens_completion",
    "native_tokens_prompt",
    "native_tokens_completion",
    "native_tokens_reasoning",
    "finish_reason",
    "created_at",
    "upstream_id",
    "is_byok",
    "cache_discount",
}


def main():
    key = os.environ["OPENROUTER_API_KEY"]
    if not key:
        raise ValueError("missing credential")
    result = {"source_run_id": 34811259408, "new_model_calls": 0, "generations": []}
    for cell, generation_id in GENERATIONS.items():
        request = urllib.request.Request(
            "https://openrouter.ai/api/v1/generation?id=" + generation_id,
            headers={"Authorization": f"Bearer {key}", "User-Agent": "AskMe-audit/1.0"},
            method="GET",
        )
        row = {"cell": cell, "generation_id": generation_id}
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                row["http_status"] = response.status
                raw = response.read(1_000_001)
                if len(raw) > 1_000_000:
                    raise ValueError("response exceeds cap")
                data = json.loads(raw)["data"]
                row["audit"] = {
                    k: v
                    for k, v in data.items()
                    if k in FIELDS and (v is None or type(v) in (str, int, float, bool))
                }
        except urllib.error.HTTPError as exc:
            row["http_status"] = exc.code
        except Exception as exc:
            row["error_type"] = type(exc).__name__
        result["generations"].append(row)
    encoded = json.dumps(result, indent=2)
    if key in encoded:
        raise ValueError("credential present in audit output")
    Path(sys.argv[1]).write_text(encoded + "\n")
    print(encoded)


if __name__ == "__main__":
    main()
