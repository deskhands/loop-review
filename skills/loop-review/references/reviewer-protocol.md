# Reviewer protocol

Every worker round starts in a fresh context and is read-only.

Mandatory rules:

1. Read every applicable `AGENTS.md` listed in the prompt completely before judging the target.
2. Treat `AGENTS.md` requirements as binding constraints. Explicitly enforce simplicity, scope control, and anti-overengineering rules when present.
3. Inspect the original target/repository. A prior finding is only a claim to verify.
4. Do not modify files, invoke skills, delegate to subagents, or use shell execution.
5. A blocking finding requires concrete evidence. Prefer exact file and line references where possible.
6. Continue looking for missed issues while adjudicating existing findings.
7. Report only actionable defects or material design risks. Do not promote style preferences into blockers.
8. Reserve `rule_refs` exclusively for binding rules from the applicable `AGENTS.md` files listed in the prompt. Put ADRs, design documents, source files, tests, requirements, and other project documents in `evidence`, never in `rule_refs`. If a finding is not an applicable `AGENTS.md` violation, return `rule_refs: []`.
9. Return the required JSON object only.

For an `AGENTS.md` violation, create a blocking finding with a `rule_refs` entry and mark the corresponding policy as `VIOLATION`.
