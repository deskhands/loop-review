# Code review rubric

Review the supplied change, then inspect surrounding code/callers/tests as needed.

Prioritize:
- correctness and regressions
- concurrency and state consistency
- error handling and resource lifecycle
- security and unsafe input/data handling
- API/behavior compatibility
- edge cases and missing tests for changed behavior
- unnecessary complexity or abstraction that violates project rules
- every applicable `AGENTS.md`

Do not act as a style linter. A finding must describe a concrete impact and evidence.
