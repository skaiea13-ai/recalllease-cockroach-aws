# RecallLease interface fidelity ledger

Reference: `recalllease-primary-screen-concept.png`

Verified implementation captures:

- `recalllease-local-desktop-final.jpg` — 1265 × 712 desktop replay
- `recalllease-local-mobile-final.jpg` — 390 × 844 mobile timeline
- `recalllease-local-mobile-decision-final.png` — 390 × 700 mobile decision

## Comparison points

| Point | Reference intent | Verified implementation |
| --- | --- | --- |
| Information architecture | Timeline, decision, and receipt in three columns | Same three-column desktop hierarchy; mobile stacks the same regions in semantic order |
| Memory lifecycle | Blue grant line ends at a coral revocation, followed by a fresh agent | Same marker sequence, terminated dashed revocation line, and distinct restarted-agent marker |
| Evidence ordering | Newest policy first, older grant second | Two-row policy chain renders revocation first and the superseded grant second |
| Decision emphasis | Coral outlined `BLOCKED` card dominates the central panel | Same danger color, shield icon, headline, rule, and explanatory reason |
| Receipt proof | Right rail exposes decision, agent, hashes, count, and providers | Same right rail with query hash, memory-set digest, full receipt digest, count, and accurate adapter labels |
| Controls | Centered primary replay and secondary reset actions | Same paired controls on desktop; full-width stacked controls on mobile |
| Visual language | True white surface, graphite text, cobalt structure, coral danger | Matching palette, thin rules, monospaced evidence, compact radius, and no decorative gradients |
| Responsive behavior | Preserve proof hierarchy on smaller screens | At 390 × 844, media query is active, `scrollWidth` stays within the viewport, and replay still reaches `BLOCKED` with two records |

## Deliberate copy differences

- The concept's static security subtitle is replaced by a runtime label that
  distinguishes local deterministic proof from the CockroachDB/AWS cloud path.
- `Retrieved memory evidence` became `Policy chain evidence` because the visual
  includes the superseded grant for audit context even though only active memory
  may authorize an action.
- The concept's illustrative 24-hour TTL became the deployed demo's bounded
  two-hour session TTL.
- Provider names remain honest in local mode and switch to CockroachDB Cloud,
  Lambda-local embeddings, and S3 only when those adapters are actually active.
