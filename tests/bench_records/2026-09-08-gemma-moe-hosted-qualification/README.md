# Hosted preflight v1 — retained registration failure

This frozen preflight stopped after its first planner request because the
registered expected top-level response model used the catalog's dated display
name. The correct DeepInfra route returned the canonical requested model ID.
The guard stopped further requests; no native action probe or coding task ran.
The completed reply was valid planner JSON and cost **$0.00004135**.

The original [qualification](qualification.json), registration and HTTP body
remain unchanged. A separately published [v2 protocol](../../serving_protocols/2026-09-08-gemma-moe-hosted-v2.json)
corrects only the response-ID expectation on the same selected route. This is
a registration error, not evidence of a model or provider failure. It is included
in the study cost and must not be hidden as an uncounted preliminary attempt.
