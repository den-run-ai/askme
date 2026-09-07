# Why the first audit workflow lost four agents

The first orchestration (`askme-release-state-audit`, run `wf_bbb2a4bd-9c4`) started six
agents; four failed, and the run was stopped before the other two reached their output stage.
Every agent that reached the emit stage failed. Noted here because the failure shape is issue
#95's, in miniature.

## What the transcripts show

Each failed agent made **exactly 5** `StructuredOutput` attempts — a retry cap — and every
attempt was rejected with the same validator message:

```
Output does not match required schema: root: must have required property 'area',
root: must have required property 'summary', root: must have required property 'findings',
root: must NOT have additional properties, root: must NOT have additional properties
```

The payloads were re-validated offline against the schema as authored. All five were valid:

| attempt | top-level keys | size | `findings` items |
|---|---|---:|---|
| 1 | area, summary, findings + 3 optional | 8565 ch | all six required keys, legal enums, no extras |
| 2 | area, summary, findings + 3 optional | 7567 ch | same |
| 3 | area, summary, findings | 3052 ch | same |
| 4 | area, summary, findings | 1810 ch | same |
| 5 | area, summary, findings + 3 optional | 8174 ch | same |

Attempts 3 and 4 show the agent stripping its answer down — from 8.5 KB to 1.8 KB, dropping
every optional field — trying to satisfy a validator that kept rejecting it. It had the content
and could not get it accepted. The error names three properties that were present and repeats
the additional-properties clause twice, which reads like a union validator reporting every
branch, with the authored root-level `additionalProperties: false` leaving no branch
satisfiable.

## What can and cannot be concluded

Provable: the payloads satisfied the authored schema; the reruns — `additionalProperties: false`
removed, `required` cut to three keys, an explicit `not_checked` array added so a partial answer
is a legitimate return — went 19 for 19 with zero failures.

Not provable: the exact validator mechanism, because the validator's own schema is not echoed in
the subagent transcripts. Root-level `additionalProperties: false` is the strongest suspect;
several things were changed at once, so that is an inference.

An earlier explanation given during the session — that the agents ran out of tool budget and
emitted partial answers — was wrong and is corrected by the table above.

## Why it is in this bundle

The agents did the work. The work was correct and complete. They tried five times to report
it, each attempt was suppressed by a rule that behaved exactly as written, and the run was
recorded as a total loss. That is the mechanism in `askme.py` that issue #95 documents and
that run 140 reproduced on a hosted model — retry-and-suppress included — encountered in the
harness used to audit it.
