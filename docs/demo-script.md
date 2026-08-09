# RecallLease demo video plan

Target duration: 2 minutes 25 seconds. Capture a continuous 1920×1080 browser
session with real pointer movement and visible state changes. Do not assemble
static screenshots.

Narration default: locally generated Qwen3-TTS **Aiden**, calm and restrained.
Leave short pauses after the revocation and final decision so the interface can
carry the proof.

Build the reviewed 103.7-second narration bed with
`./submission/video/build-narration.sh`. Its five scene-sized takes leave about
41 seconds of the target runtime for live interaction and silent visual proof.
See `submission/video/narration-provenance.md` for the pinned model, disclosure,
hash, loudness, and reverse-transcription checks.

## Shot and narration

### 0:00–0:18 — The memory failure nobody demos

Show the live RecallLease page in its ready state.

> Agent memory is usually judged by what it remembers. RecallLease focuses on
> what it must stop remembering: an expired permission, a revoked instruction,
> or a policy that has been replaced.

### 0:18–0:42 — The original lease

Move the pointer over **Permission recorded** and the requested publish action.

> This agent was allowed to publish a sanitized weekly status. The permission
> has a source, validity window, content hash, and tenant boundary. It is a
> lease, not an immortal prompt fragment.

### 0:42–1:10 — Record the revocation

Select **Run replay**. Keep the recording continuous while the timeline changes.

> A newer policy now revokes publication. CockroachDB updates both sides in one
> transaction: the old allow becomes superseded, and the denial becomes active.
> The lease line ends here.

### 1:10–1:40 — Fresh agent, persistent retrieval

Let the **Agent restarted** step activate and the evidence rows appear.

> Next, a fresh agent instance starts with no trusted process-local memory.
> AWS Lambda builds the requested action into a deterministic query vector, and
> CockroachDB performs distributed vector retrieval over only active, currently
> valid records. The older allow is still auditable, but it can no longer
> authorize anything.

### 1:40–2:08 — Prove the block

Pause on the `BLOCKED` result, then move to the receipt rail.

> The newest valid memory wins. RecallLease blocks the publish action and emits
> a content-addressed receipt: agent instance, query hash, memory-set digest,
> evidence count, and final decision. The same receipt is written to private S3
> storage for a short audit window.

### 2:08–2:25 — Close on the differentiator

Keep the full live interface visible.

> CockroachDB gives the agent durable recall. RecallLease adds durable
> revocation—so restarting an agent cannot resurrect permission that no longer
> exists.

## Capture gates

- Run the loopback browser app with the production CockroachDB and S3
  adapters. Verify real cloud writes before capture; the hosted Lambda URL stays
  IAM-only and is not opened to anonymous traffic.
- Generate a fresh `RECALLLEASE_LOOPBACK_CAPABILITY`, open the loopback page once
  with it in the URL fragment, and confirm the fragment disappears before
  recording. Never capture the address bar or capability value.
- The header must report the real cloud providers, not local simulator mode.
- The pointer visibly selects **Run replay** and the resulting rows must be live.
- The final decision must be `BLOCKED` and the receipt fields must be populated.
- No browser profile, notification, terminal, credential, database URL, account
  email, private path, phone number, or unrelated tab may appear.
- Final media must be under three minutes, publicly viewable, and use one
  continuous interaction-led screen recording.
