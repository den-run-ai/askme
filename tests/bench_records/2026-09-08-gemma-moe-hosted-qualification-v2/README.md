# Hosted preflight v2 — qualified

The separately frozen [v2 protocol](../../serving_protocols/2026-09-08-gemma-moe-hosted-v2.json)
passed both task-independent checks on OpenRouter/DeepInfra Gemma 4 26B-A4B MoE:
valid planner JSON in 3.198 seconds and the exact native write call in 1.538
seconds. No coding actions executed. Both replies matched the selected route,
canonical API model ID and frozen cost/latency limits. Total cost: **$0.00017242**.

[Qualification](qualification.json), registration, requests, complete HTTP
bodies and cost ledgers are retained. The failed v1 identity registration is
[preserved separately](../2026-09-08-gemma-moe-hosted-qualification/README.md).
Passing this small serving contract is not evidence of coding reliability.
It authorized only the separately registered
[one Requests attempt](../2026-09-08-requests-gemma-moe-hosted/README.md).
