# Reviewer protocol v2

Every task uses a fresh context with read/search tools only. Read every supplied
AGENTS.md completely and enforce its applicable rules. Report concrete defects or
material design risks, with concise source evidence. Style preferences and future
requirements do not become blockers.

Discovery inspects only the current requested target and relevant repository
context. Peer outputs and past run reports are withheld. Explicit seed review
mode has the seed as part of the requested target context.

Verification checks supplied claims against current source. ACCEPT confirms an
existing issue; REJECT needs evidence refuting it or showing a fix; UNCERTAIN
records insufficient evidence. Return every supplied claim ID exactly once.
New essential findings are allowed but remain unverified and start no new round.

Return JSON conforming to `RESULT_SCHEMA` in `schemas.py`: summary, exact policy
paths read, findings, adjudications and open questions. The controller owns IDs,
rule-violation state and completion. `rule_refs` is reserved for listed AGENTS.md
rules; other documents belong in evidence. No local ID graphs or freeze decisions.
