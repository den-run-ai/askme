"""Check existing record availability; never retain response metadata."""

import json
import os
import urllib.error
import urllib.request

GENERATIONS = {
    "gemma-askme": "gen-1789365565-06396776OGdFArYlvRet",
    "gemma-pi": "gen-1789365586-pkLVnOIW6MB8Q36GLjSD",
    "qwen-pi": "gen-1789365621-SZqnhNiEe7yFj6bvC1w7",
    "qwen-askme": "gen-1789365657-nbv4pOcqy7jTsFYO2PQ2",
}
EXPECTED = {
    "gemma": ("google/gemma-4-31b-it", "google/gemma-4-31b-it-20260402", "CoreWeave"),
    "qwen": ("qwen/qwen3.6-27b", "qwen/qwen3.6-27b-20260422", "SiliconFlow"),
}


def main():
    key = os.environ["OPENROUTER_API_KEY"]
    if not key:
        raise ValueError("missing credential")
    result = {"source_run_id": 34811259408, "new_model_calls": 0, "checks": []}
    for cell, generation_id in GENERATIONS.items():
        request = urllib.request.Request(
            "https://openrouter.ai/api/v1/generation?id=" + generation_id,
            headers={"Authorization": f"Bearer {key}", "User-Agent": "AskMe-audit/1.0"},
            method="GET",
        )
        row = {"cell": cell}
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                row["http_status"] = response.status
                raw = response.read(1_000_001)
                if len(raw) > 1_000_000:
                    raise ValueError("response exceeds cap")
                parsed = json.loads(raw)
                row["metadata_object_present"] = isinstance(parsed, dict) and isinstance(
                    parsed.get("data"), dict
                )
                if row["metadata_object_present"]:
                    data = parsed["data"]
                    alias, dated, provider = EXPECTED[cell.split("-")[0]]
                    row["model_matches_dated"] = data.get("model") == dated
                    row["model_matches_requested_alias"] = data.get("model") == alias
                    row["provider_matches_expected"] = data.get("provider_name") == provider
        except urllib.error.HTTPError as exc:
            row["http_status"] = exc.code
        except Exception as exc:
            row["error_type"] = type(exc).__name__
        result["checks"].append(row)
    encoded = json.dumps(result, indent=2)
    if key in encoded:
        raise ValueError("credential present in audit output")
    print(encoded)


if __name__ == "__main__":
    main()
