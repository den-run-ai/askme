"""Read-only validation of the one durable claim for the retired v1 protocol."""

import hashlib
import json
import os
import sys
from pathlib import Path


def validate_claim(claim, protocol_path, environment):
    protocol_bytes = Path(protocol_path).read_bytes()
    protocol = json.loads(protocol_bytes)
    expected = {
        "protocol": protocol["protocol"],
        "harness_revision": protocol["harness_revision"],
        "protocol_sha256": hashlib.sha256(protocol_bytes).hexdigest(),
        "run_id": int(environment.get("GITHUB_RUN_ID", "0")),
        "run_attempt": int(environment.get("GITHUB_RUN_ATTEMPT", "0")),
        "workflow_revision": environment.get("TRIAL_WORKFLOW_REVISION"),
    }
    if environment.get("GITHUB_REPOSITORY") != "den-run-ai/askme":
        raise ValueError("This claim belongs only to den-run-ai/askme")
    if expected["run_id"] <= 0 or expected["run_attempt"] != 1:
        raise ValueError("Only the claimed first attempt may run")
    for field, value in expected.items():
        if claim.get(field) != value:
            raise ValueError(f"Protocol already claimed by another run or revision: {field}")
    return expected


if __name__ == "__main__":
    claim_path, protocol_path = sys.argv[1:]
    claim = json.loads(Path(claim_path).read_text())
    print(json.dumps(validate_claim(claim, protocol_path, os.environ), sort_keys=True))
