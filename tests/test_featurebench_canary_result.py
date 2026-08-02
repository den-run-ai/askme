import json
from pathlib import Path

RESULT_PATH = (
    Path(__file__).parent / "featurebench" / "results" / "2026-07-13-gemma-4-31b-canary.json"
)


def test_published_canary_result_is_internally_consistent():
    result = json.loads(RESULT_PATH.read_text(encoding="utf-8"))

    assert result["label"] == "one-task FeatureBench fast adapter canary"
    assert result["protocol_id"] == "askme-featurebench-fast-adapter-canary-v2"
    assert result["qualification"]["valid"] is True
    assert result["qualification"]["gold_control"]["resolved_instances"] == 1
    assert result["qualification"]["harmless_nonempty_control"] == {
        "completed_instances": 1,
        "patch_successfully_applied": True,
        "resolved": False,
        "error_instances": 0,
        "wall_time_seconds": 56,
    }

    usage = result["usage"]
    assert usage["prompt_tokens"] + usage["completion_tokens"] == usage["total_tokens"]
    assert usage["responses"] == 28
    assert usage["openrouter_cost_usd"] == 0.01585905

    assert result["cell"]["served_models"] == ["google/gemma-4-31b-it-20260402"]
    assert result["cell"]["served_providers"] == ["SiliconFlow"]
    assert result["agent"]["status"] == "exhausted"
    assert result["agent"]["reported_completion"] is False
    assert result["agent"]["patch_bytes"] == 0
    assert result["official_acceptance"] == {
        "featurebench_eval_completed": True,
        "resolved": False,
        "patch_successfully_applied": False,
        "empty_patch": True,
        "error_instances": 0,
    }
    assert result["audit"]["strict_served_model_match"] is True
    assert result["audit"]["api_key_leaks"] == 0

    interpretation = result["interpretation"]
    for unsupported_claim in (
        "not a FeatureBench score",
        "model-family comparison",
        "model-size result",
        "causal estimate",
    ):
        assert unsupported_claim in interpretation
